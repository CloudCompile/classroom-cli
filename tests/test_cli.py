"""CLI tests: command wiring, exit codes, and the identity gate.

Browser access is stubbed, so these run offline. The identity gate is the
critical one: no Classroom data should be read unless the account checks out.
"""
import json

import pytest
from typer.testing import CliRunner

from classroom import cli as cli_mod
from classroom import identity, profile as profile_mod, scrape

runner = CliRunner()
SCHOOL = "student@school.example"

COURSES = [
    scrape.Course(name="Geometry Honors", href="/c/AAAA", section="3rd Hour",
                  teacher="Rivera, Alex"),
    scrape.Course(name="World History", href="/c/BBBB", section="2", teacher="Ortiz, Sam"),
]


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text(f"account={SCHOOL}\n")
    monkeypatch.setenv("CLASSROOM_CONFIG", str(cfg))
    monkeypatch.delenv("CLASSROOM_ACCOUNT", raising=False)
    return cfg


@pytest.fixture
def ok(monkeypatch):
    """Patch _open to succeed, recording calls."""
    calls = {}

    def fake_open(headed=False):
        calls["opened"] = True
        return object(), object()

    monkeypatch.setattr(cli_mod, "_open", fake_open)
    return calls


@pytest.fixture
def no_browser(monkeypatch):
    """_open raises the way it does when nobody has signed in."""
    def fake_open(headed=False):
        raise SystemExit(2)

    monkeypatch.setattr(cli_mod, "_open", fake_open)
    return True


def test_help_lists_commands():
    r = runner.invoke(cli_mod.app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("login", "whoami", "courses", "todo", "missing", "done", "classwork", "show"):
        assert cmd in r.output


def test_courses(ok, monkeypatch):
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    r = runner.invoke(cli_mod.app, ["courses"])
    assert r.exit_code == 0, r.output
    assert "Geometry Honors" in r.output
    assert "Rivera, Alex" in r.output


def test_courses_json(ok, monkeypatch):
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    r = runner.invoke(cli_mod.app, ["courses", "--json"])
    data = json.loads(r.stdout)
    assert [c["name"] for c in data["courses"]] == ["Geometry Honors", "World History"]


def test_missing(ok, monkeypatch):
    items = [scrape.Assignment(title="Essay", course="English", href="/c/A/a/1/details"),
             scrape.Assignment(title="Old lab", course="Biology", href="/c/B/a/2/details")]
    monkeypatch.setattr(scrape, "missing", lambda tab: items)
    r = runner.invoke(cli_mod.app, ["missing"])
    assert r.exit_code == 0, r.output
    assert "Essay" in r.output and "2 items" in r.output


def test_missing_detail_calls_scrape(ok, monkeypatch):
    items = [scrape.Assignment(title="Essay", course="English", href="/c/A/a/1/details")]
    seen = []
    monkeypatch.setattr(scrape, "missing", lambda tab: items)
    monkeypatch.setattr(scrape, "detail", lambda tab, a: seen.append(a.title) or a)
    r = runner.invoke(cli_mod.app, ["missing", "--detail"])
    assert r.exit_code == 0
    assert seen == ["Essay"]


def test_classwork_requires_course(ok, monkeypatch):
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    r = runner.invoke(cli_mod.app, ["classwork"])
    assert r.exit_code == 1
    assert "--course is required" in r.output
    assert "Geometry Honors" in r.output, "lists valid choices"


def test_classwork_unknown_course(ok, monkeypatch):
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    r = runner.invoke(cli_mod.app, ["classwork", "-c", "Astronomy"])
    assert r.exit_code == 1
    assert "no course matching" in r.output


def test_classwork_renders(ok, monkeypatch):
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    monkeypatch.setattr(scrape, "classwork",
                        lambda tab, c: [scrape.ClassworkItem(title="4.3 Notes", kind="material",
                                                             dates="Posted Nov 16")])
    r = runner.invoke(cli_mod.app, ["classwork", "-c", "geom"])
    assert r.exit_code == 0, r.output
    assert "4.3 Notes" in r.output and "Material" in r.output


def test_show_no_match(ok, monkeypatch):
    monkeypatch.setattr(scrape, "todo", lambda tab: [])
    monkeypatch.setattr(scrape, "missing", lambda tab: [])
    r = runner.invoke(cli_mod.app, ["show", "-t", "nothing"])
    assert r.exit_code == 1
    assert "no matching assignment" in r.output


def test_show_pick_out_of_range(ok, monkeypatch):
    items = [scrape.Assignment(title="Essay", course="English", href="/c/A/a/1/details")]
    monkeypatch.setattr(scrape, "todo", lambda tab: items)
    monkeypatch.setattr(scrape, "missing", lambda tab: [])
    r = runner.invoke(cli_mod.app, ["show", "-t", "essay", "--pick", "5"])
    assert r.exit_code == 1
    assert "out of range" in r.output


def test_logout_requires_reset_flag():
    r = runner.invoke(cli_mod.app, ["logout"])
    assert r.exit_code == 1
    assert "--reset" in r.output


def test_logout_reset(monkeypatch):
    called = []
    monkeypatch.setattr(profile_mod, "reset_profile", lambda: called.append(True))
    r = runner.invoke(cli_mod.app, ["logout", "--reset"])
    assert r.exit_code == 0
    assert called == [True]


# ── the identity gate, end to end through _open ──────────────────────────

class StubBrowser:
    def json(self, path, method="GET"):
        return {}

    def classroom_tab(self):
        return None

    def open_tab(self, url):
        return {"webSocketDebuggerUrl": "ws://x", "id": "1"}


@pytest.fixture
def real_open(monkeypatch):
    """Exercise the real _open with a stubbed browser and identity."""
    monkeypatch.setattr(cli_mod.profile_mod, "Browser", lambda port: StubBrowser())
    monkeypatch.setattr(cli_mod.cdp, "work_tab", lambda b, url, settle=3: object())
    return None


def test_open_refuses_wrong_account(real_open, monkeypatch):
    def wrong(tab, allowed=None):
        raise identity.WrongAccount("signed in as me@gmail.com; only " + SCHOOL + " is allowed")

    monkeypatch.setattr(cli_mod.identity, "verify", wrong)
    r = runner.invoke(cli_mod.app, ["courses"])
    assert r.exit_code == 4, "wrong account must be a hard stop"
    assert "refusing to continue" in r.output
    assert "me@gmail.com" in r.output


def test_open_handles_signed_out(real_open, monkeypatch):
    def signed_out(tab, allowed=None):
        raise identity.NotSignedIn("not signed in")

    monkeypatch.setattr(cli_mod.identity, "verify", signed_out)
    r = runner.invoke(cli_mod.app, ["missing"])
    assert r.exit_code == 2
    assert "not signed in" in r.output
    assert "classroom login" in r.output


def test_open_proceeds_when_identity_ok(real_open, monkeypatch):
    monkeypatch.setattr(cli_mod.identity, "verify",
                        lambda tab, allowed=None: identity.Identity(SCHOOL, True))
    monkeypatch.setattr(scrape, "courses", lambda tab: COURSES)
    r = runner.invoke(cli_mod.app, ["courses"])
    assert r.exit_code == 0, r.output
    assert SCHOOL in r.output


# ── regressions found against the live site ──────────────────────────────

def test_parse_reads_rows_after_course_lookup(monkeypatch):
    """scrape.courses() navigates the tab to /h; parse must re-navigate.

    Found live: the week rows were read while the tab sat on the home page,
    which yields zero rows and an empty parse with no error.
    """
    from classroom import attachments as at, schedule as sched

    events = []

    class Tab:
        def navigate(self, url, wait=0):
            events.append(("navigate", url))

        def js(self, expr, timeout=0):
            return []

    monkeypatch.setattr(cli_mod, "_open", lambda headed=False: (object(), Tab()))
    monkeypatch.setattr(cli_mod, "_pick_course_item",
                        lambda tab, course, match: (scrape.Course(name="Geo", href="/c/AA"), "Week"))
    monkeypatch.setattr(at, "classwork_rows", lambda tab: [])
    called = {}
    monkeypatch.setattr(sched, "parse_schedules", lambda texts: called.setdefault("t", texts) or [])

    r = runner.invoke(cli_mod.app, ["parse", "-c", "geo", "-m", "Week", "--json"])
    assert r.exit_code == 0, r.output
    navigations = [u for kind, u in events if kind == "navigate"]
    assert any("/w/AA/t/all" in u for u in navigations), \
        "must navigate to the classwork page before reading rows"

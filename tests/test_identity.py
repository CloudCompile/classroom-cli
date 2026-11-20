"""Identity guard tests: the whole reason this CLI is safe to use.

The rule: never read Classroom data unless the signed-in account is exactly
the configured school account.
"""
import re

import pytest

from classroom import identity, profile as profile_mod


class FakeTab:
    """Just enough of cdp.Tab: url() plus js() returning staged values."""

    def __init__(self, urls, emails=None):
        self._urls = list(urls)
        self._emails = emails or []
        self.navigated = []

    def url(self):
        return self._urls[0]

    def navigate(self, url, wait=3.0):
        self.navigated.append(url)
        if len(self._urls) > 1:
            self._urls.pop(0)

    def js(self, expr, timeout=40.0):
        # the real probe returns a list of email strings; mimic that shape
        out = []
        for item in self._emails:
            out.extend(re.findall(r"[^@\s()<>]+@[^@\s()<>]+\.[a-z]{2,}", item, re.I))
        return [e.lower() for e in out]


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text("account=student@school.example\n")
    monkeypatch.setenv("CLASSROOM_CONFIG", str(cfg))
    monkeypatch.delenv("CLASSROOM_ACCOUNT", raising=False)
    return cfg


SCHOOL = "student@school.example"


def test_expected_account_from_config():
    assert profile_mod.expected_account() == SCHOOL


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("CLASSROOM_ACCOUNT", "other@school.example")
    assert profile_mod.expected_account() == "other@school.example"


def test_signed_out_detected_by_url():
    tab = FakeTab(["https://accounts.google.com/v3/signin/identifier"], [])
    with pytest.raises(identity.NotSignedIn):
        identity.detect(tab)


def test_signed_out_redirect_to_info_page():
    # Google sends signed-out users to the account *info* page, not signin
    tab = FakeTab(["https://classroom.google.com/h", "https://www.google.com/account/about/"], [])
    with pytest.raises(identity.NotSignedIn):
        identity.detect(tab)


def test_detect_reads_email_from_aria_label():
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"],
                  ([f"Google Account: Test Student\n({SCHOOL})"]))
    ident = identity.detect(tab)
    assert ident.email == SCHOOL


def test_verify_accepts_the_school_account():
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"], [SCHOOL])
    assert identity.verify(tab).email == SCHOOL


def test_verify_rejects_personal_account():
    """The key safety property: a personal account must never be read."""
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"],
                  ["someone@gmail.com"])
    with pytest.raises(identity.WrongAccount) as e:
        identity.verify(tab)
    msg = str(e.value)
    assert "someone@gmail.com" in msg and SCHOOL in msg


def test_verify_rejects_other_school_account():
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"],
                  ["another.student@school.example"])
    with pytest.raises(identity.WrongAccount):
        identity.verify(tab)


def test_verify_refuses_when_nothing_is_pinned(tmp_path, monkeypatch):
    cfg = tmp_path / "empty"
    cfg.write_text("# nothing\n")
    monkeypatch.setenv("CLASSROOM_CONFIG", str(cfg))
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"], [SCHOOL])
    with pytest.raises(profile_mod.ClassroomError) as e:
        identity.verify(tab)
    assert "account=" in str(e.value)


def test_verify_is_case_insensitive():
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"],
                  [SCHOOL.upper()])
    assert identity.verify(tab).email == SCHOOL


def test_ambiguous_page_with_no_email_errors():
    tab = FakeTab(["https://classroom.google.com/h", "https://myaccount.google.com/"], [])
    with pytest.raises(profile_mod.ClassroomError) as e:
        identity.detect(tab)
    assert "could not read the account address" in str(e.value)


def test_probe_never_clicks():
    """Clicking the avatar triggers navigation mid-evaluation (seen live)."""
    from classroom import identity as ident
    assert ".click()" not in ident._PROBE_JS

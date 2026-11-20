"""Parser tests over snapshots captured from the real Classroom pages.

The snapshots are the raw output of the in-page extractors, so these tests
exercise the same data the CLI receives, without needing a browser.
"""
import json
import os
from datetime import date

import pytest

from classroom import scrape

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def snap():
    with open(os.path.join(HERE, "fixtures", "pages.json"), encoding="utf-8") as fh:
        return json.load(fh)


# ── due-date parsing ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Yesterday, 5:59 AM", date(2026, 11, 17)),
    ("Yesterday", date(2026, 11, 17)),
    ("Friday, Nov 13", date(2026, 11, 13)),
    ("Mon Nov 23", date(2026, 11, 23)),
    ("Nov 13", date(2026, 11, 13)),
    ("Due Nov 21", date(2026, 11, 21)),
    ("11/21/2026", date(2026, 11, 21)),
    ("11/21", date(2026, 11, 21)),
])
def test_parse_due_absolute(text, expected):
    assert scrape.parse_due(text, today=date(2026, 11, 18)) == expected


@pytest.mark.parametrize("text,expected", [
    ("Monday, 6:50 PM", date(2026, 11, 16)),      # most recent Monday
    ("Saturday, 5:59 AM", date(2026, 11, 14)),
    ("Today, 5:59 AM", date(2026, 11, 18)),
    ("Monday", date(2026, 11, 16)),
])
def test_parse_due_weekday(text, expected):
    assert scrape.parse_due(text, today=date(2026, 11, 18)) == expected


def test_parse_due_same_weekday_is_today():
    # 2026-11-18 is a Wednesday
    assert scrape.parse_due("Wednesday, 5:59 AM", today=date(2026, 11, 18)) == date(2026, 11, 18)


def test_parse_due_junk_is_none():
    for text in ("", "No due date", "Nothing due", "Woohoo, no work due soon!"):
        assert scrape.parse_due(text) is None


def test_parse_due_rolls_forward_for_stale_dates():
    # a bare "Nov 13" seen in September must be next month's, not last year's
    assert scrape.parse_due("Nov 13", today=date(2026, 9, 1)) == date(2026, 11, 13)


# ── points / grades ──────────────────────────────────────────────────────

def test_parse_points_fraction():
    assert scrape.parse_points("8/10") == (8.0, 10.0)
    assert scrape.parse_points("3/3") == (3.0, 3.0)


def test_parse_points_total_only():
    assert scrape.parse_points("3 points") == (None, 3.0)
    assert scrape.parse_points("") == (None, None)


# ── captured pages ───────────────────────────────────────────────────────

def _courses(snap):
    out = []
    for r in snap["courses"]:
        out.append(scrape.Course(name=r["name"], href=r["href"],
                                 course_id=r["course_id"], section=r["section"],
                                 teacher=r["teacher"], next=r["next"]))
    return out


def test_courses_count_and_fields(snap):
    cs = _courses(snap)
    assert len(cs) == 7
    geo = [c for c in cs if c.name == "Geometry Honors"][0]
    assert geo.section == "3rd Hour"
    assert geo.teacher == "Rivera, Alex"
    assert geo.href.startswith("/c/")


def test_course_teacher_never_picks_the_section(snap):
    """Regression: the teacher column used to show the section instead."""
    for c in _courses(snap):
        if c.teacher:
            assert "," in c.teacher, f"{c.name}: teacher {c.teacher!r} has no comma"


def test_no_work_courses_hide_filler(snap):
    for c in _courses(snap):
        assert "Woohoo" not in c.next


def test_missing_items(snap):
    items = [r for r in snap["missing"]]
    assert len(items) == 10
    titles = [i["title"] for i in items]
    assert "Persuasive Essay Draft" in titles
    assert all(i["course"] for i in items)
    assert all(i["href"].startswith("/c/") and "/a/" in i["href"] for i in items)


def test_todo_items(snap):
    titles = [i["title"] for i in snap["todo"]]
    assert "Unit 3 Summative" in titles
    assert "Biome Poster Project" in titles


def test_done_can_be_empty(snap):
    assert isinstance(snap["done"], list)


def test_assignment_model_from_snapshot(snap):
    raw = [r for r in snap["missing"] if r["title"].startswith("Edpuzzle")][0]
    a = scrape.Assignment(title=raw["title"], course=raw["course"], href=raw["href"],
                          due_text=raw["due_text"], due=scrape.parse_due(raw["due_text"]))
    assert a.due == date(2026, 11, 13)
    assert a.course == "Environmental Science - 7"
    assert a.course_id and a.url.startswith("https://classroom.google.com/c/")
    d = a.to_dict()
    assert d["due"] == "2026-11-13"
    assert json.dumps(d)  # serialisable


def test_detail_snapshot(snap):
    d = snap["detail"]
    assert d["title"] == "Biome Poster Project"
    assert "Assigned" in d["states"]
    assert d["due_text"].startswith("Due ")
    assert scrape.parse_due(d["due_text"]) is not None
    assert any("Docs" in x for x in d["attachments"])


def test_detail_title_is_not_nav_chrome(snap):
    """Regression: the title used to be the nav item "Classroom"."""
    assert snap["detail"]["title"] not in ("Classroom", "Home", "")


def test_links_are_absolute_and_not_chrome(snap):
    for link in snap["detail"]["links"]:
        assert link.startswith("http")

"""Weekly-schedule parsing, tested on real teacher documents.

`fixtures/schedules.json` holds text extracted from the Geometry Honors
weekly-schedule docs (Week 4, 6, 13) via the Drive viewer. They were chosen
because they are the messy ones: no due markers, due dates that wrap onto the
next day's heading, and a typo in the source.
"""
import json
import os
from datetime import date

import pytest

from classroom import schedule

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def weeks():
    with open(os.path.join(HERE, "fixtures", "schedules.json"), encoding="utf-8") as fh:
        return schedule.parse_schedules(json.load(fh))


def by_title(weeks, frag):
    return [w for w in weeks if frag in w.title][0]


# ── Week 13: the clean, common case ──────────────────────────────────────

def test_week13_header(weeks):
    w = by_title(weeks, "Week 13")
    assert w.week_of == date(2026, 11, 16)
    assert w.term == "1st Semester"


def test_week13_every_class_day_has_a_due_date(weeks):
    w = by_title(weeks, "Week 13")
    dated = [a for a in w.assignments if not a.needs_review]
    assert len(dated) == 4
    assert [a.due for a in dated] == [date(2026, 11, 17), date(2026, 11, 18),
                                      date(2026, 11, 19), date(2026, 11, 20)]


def test_week13_titles_are_just_the_assignment(weeks):
    w = by_title(weeks, "Week 13")
    titles = [a.title for a in w.assignments]
    assert "4.3 SSS & SAS" in titles
    assert not any("pg.305" in t for t in titles), "the reading list must not be a title"


def test_week13_flags_the_no_homework_day(weeks):
    w = by_title(weeks, "Week 13")
    flagged = [a for a in w.assignments if a.needs_review]
    assert len(flagged) == 1
    assert "Check for Learning" in flagged[0].title
    assert "no due date" in flagged[0].reason


# ── Week 4: due dates in three different styles ──────────────────────────

def test_week4_due_colon_form(weeks):
    w = by_title(weeks, "Week 4")
    a = [x for x in w.assignments if x.title.startswith("1.4 Angle")][0]
    assert a.due == date(2026, 9, 18)
    assert a.due_text.lower() == "due friday"


def test_week4_due_wraps_to_next_week(weeks):
    """Friday's 'Due: Tuesday' means the Tuesday after, not before."""
    w = by_title(weeks, "Week 4")
    a = [x for x in w.assignments if x.title.startswith("1.5 Angle")][0]
    assert a.due == date(2026, 9, 22)


def test_week4_borrowed_weekday_for_wrapped_due(weeks):
    """'Both Due at the START OF CLASS on' + next line 'Wednesday'."""
    w = by_title(weeks, "Week 4")
    a = [x for x in w.assignments if x.title.startswith("1.1-1.3 Review")][0]
    assert a.due == date(2026, 9, 16)


def test_week4_in_class_work_is_flagged_not_invented(weeks):
    w = by_title(weeks, "Week 4")
    flagged = [a.title for a in w.assignments if a.needs_review]
    assert any("Assessment" in t for t in flagged)
    assert all(a.due is None for a in w.assignments if a.needs_review)


# ── Week 6: project week, almost nothing has a date ──────────────────────

def test_week6_only_the_real_deadline_is_dated(weeks):
    w = by_title(weeks, "Week 6")
    dated = [a for a in w.assignments if not a.needs_review]
    assert len(dated) == 1
    assert dated[0].due == date(2026, 10, 5)
    assert "Final Draft" in dated[0].title


def test_week6_reproduces_the_source_typo(weeks):
    """The doc says Wednesday 10/30 (should be 9/30). We mirror the document."""
    w = by_title(weeks, "Week 6")
    days = [a.day for a in w.assignments]
    assert date(2026, 10, 30) in days, "typo is preserved, not silently corrected"


def test_week6_no_invented_dates(weeks):
    w = by_title(weeks, "Week 6")
    assert sum(1 for a in w.assignments if a.due) == 1


# ── general properties ───────────────────────────────────────────────────

def test_all_weeks_parse_and_are_ordered(weeks):
    assert len(weeks) == 3
    assert [w.week_of for w in weeks] == [date(2026, 9, 14), date(2026, 9, 28),
                                          date(2026, 11, 16)]


def test_due_always_after_the_listed_day(weeks):
    for w in weeks:
        for a in w.assignments:
            if a.due and a.day:
                assert a.due >= a.day, f"{a.title}: due {a.due} before its day {a.day}"


def test_every_entry_is_either_dated_or_flagged(weeks):
    for w in weeks:
        for a in w.assignments:
            assert a.due is not None or a.needs_review, a.title


def test_serialises(weeks):
    data = weeks[0].to_dict()
    assert set(data) == {"title", "week_of", "term", "assignments", "unparsed"}
    assert json.dumps(data)


def test_empty_and_headerless_documents():
    assert schedule.parse_week("").assignments == []
    w = schedule.parse_week("Some random note\nwith no days")
    assert w.assignments == []
    assert w.unparsed


def test_week_of_title_fallback():
    # no "Week of" line; the title carries the date
    w = schedule.parse_week("Monday\n11/16\nThing Due Tuesday:", title="Week 13: 11-16-26")
    assert w.week_of == date(2026, 11, 16)
    assert w.assignments[0].due == date(2026, 11, 17)

"""Turn a teacher's free-form weekly schedule into structured assignments.

The real corpus (Geometry Honors, 13 weeks) is hand-typed by a teacher, so it
is inconsistent:

    Monday / 11/16 / 4.3 SSS & SAS Due Tuesday: / pg.305 # 5, 6, 11, 16-19 ...
    Thursday / 9/17 / 1.4 Angle Measure / 1.4 Worksheet / Due: Friday
    Friday / 9/18 / pg. 51: #8-18 even ... / Due: Tuesday
    Monday / 9/28 / Chapter 1 Test Work on project research/ideas   (no marker)
    Thursday / 10/1 / ... Both Due at the START OF CLASS on Wednesday

Design rule: extract only what is unambiguous, and report the rest as
`needs_review` rather than guessing. A wrong homework date in someone's
planner is worse than a line saying "check this one".

This is a *heuristic for one teacher's format*, not a general NLP parser. It
keys on the day/date pairs (the table's left column) which are the most
stable part of the document, and treats everything between two day markers as
that day's entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_DAY_RE = re.compile(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)$", re.I)
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$")
_WEEK_OF_RE = re.compile(r"Week of\s+([A-Za-z]+)\s+(\d{1,2})(?:\s*[-\u2013]\s*(\d{1,2}))?,?\s*(\d{4})", re.I)
_QF_RE = re.compile(r"\b(\d(?:st|nd|rd|th))\s+(Quarter|Semester)\b", re.I)

# "Due Tuesday:", "Due: Friday", "Due at the START OF CLASS on Wednesday",
# "Both Due at the START OF CLASS on Wednesday", "Due start of class Monday"
# Real variants seen:
#   "Due Tuesday:"   "Due: Friday"   "due start of class Monday"
#   "Both Due at the START OF CLASS on Wednesday"   "Due: Tuesday"
#   "Final Draft of Project due start of class Monday"
_DUE_RE = re.compile(
    r"\bdu(?:e|ed)?\b[^.;]{0,60}?"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}/\d{1,2})\b",
    re.I)

# An entry that ends mid-sentence wanting a day: "... Due at the START OF CLASS on"
_DANGLING_DUE_RE = re.compile(r"\bdue\b[^.;]{0,60}$", re.I)


class _BorrowedMatch:
    """Stands in for a due match whose weekday came from the next chunk."""

    def __init__(self, day: date):
        self._day = day
        self._text = DAYS[day.weekday()]

    def group(self, n: int = 0):
        return self._text

    def start(self) -> int:
        return 10 ** 6          # treat as "after all the entry text"

    def end(self) -> int:
        return 10 ** 6


_NOISE = re.compile(r"^(Page \d+ of \d+|1|\d{4}-\d{2}-\d{2}.*)$")


@dataclass
class ParsedAssignment:
    title: str
    due: date | None = None
    due_text: str = ""
    day: date | None = None          # the day's date, as listed
    details: str = ""
    needs_review: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "due": self.due.isoformat() if self.due else "",
            "due_text": self.due_text,
            "day": self.day.isoformat() if self.day else "",
            "details": self.details,
            "needs_review": self.needs_review,
            "reason": self.reason,
        }


@dataclass
class ParsedWeek:
    title: str = ""
    week_of: date | None = None
    term: str = ""
    assignments: list[ParsedAssignment] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "week_of": self.week_of.isoformat() if self.week_of else "",
            "term": self.term,
            "assignments": [a.to_dict() for a in self.assignments],
            "unparsed": self.unparsed,
        }


def _clean_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or _NOISE.match(s):
            continue
        out.append(s)
    return out


def _week_start(title: str, lines: list[str]) -> tuple[date | None, str]:
    """Find the Monday the schedule covers, plus the course/week title."""
    for line in lines[:8]:
        m = _WEEK_OF_RE.search(line)
        if m:
            month = _month(m.group(1))
            if month:
                year = int(m.group(4))
                try:
                    return date(year, month, int(m.group(2))), ""
                except ValueError:
                    pass
    # fall back to the "Week 13: 11-16-26" header
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{2,4})", title or "")
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day), ""
        except ValueError:
            pass
    return None, ""


def _month(name: str) -> int | None:
    name = name.lower()[:3]
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    return months.get(name)


def _next_weekday(from_day: date, name: str) -> date:
    """The next occurrence of a weekday *after* `from_day` (Same-day means +7)."""
    want = DAYS.index(name.lower())
    delta = (want - from_day.weekday()) % 7
    return from_day + timedelta(days=delta or 7)


def _split_days(lines: list[str], week_start: date | None) -> list[tuple[date | None, list[str]]]:
    """Group the document into (day, lines) chunks using the day/date column."""
    # A teacher will happily write "... Both Due at the START OF CLASS on" and
    # then put "Wednesday" on the next line as the *next* day's heading, which
    # makes the due date belong to the day that follows. Keep a trailing
    # dangling "due ..." so parse_week can borrow the next chunk's day name.
    chunks: list[tuple[date | None, list[str]]] = []
    current_day: date | None = None
    current: list[str] = []
    pending_day: str | None = None

    def flush():
        nonlocal current, current_day
        if current or current_day:
            chunks.append((current_day, current))
        current = []

    for i, line in enumerate(lines):
        if _DAY_RE.match(line):
            flush()
            pending_day = line.lower()
            current_day = None
            # the date usually follows immediately ("Monday" / "11/16")
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            m = _DATE_RE.match(nxt)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                year = int(m.group(3)) if m.group(3) else (
                    week_start.year if week_start else date.today().year)
                if year < 100:
                    year += 2000
                try:
                    current_day = date(year, month, day)
                except ValueError:
                    current_day = None
            if current_day is None and week_start and pending_day:
                want = DAYS.index(pending_day)
                current_day = week_start + timedelta(days=(want - week_start.weekday()) % 7)
            continue
        if _DATE_RE.match(line) and pending_day:
            continue                      # consumed as part of the day marker
        current.append(line)
    flush()
    return [(d, c) for d, c in chunks if c]


def parse_week(text: str, title: str = "") -> ParsedWeek:
    """Parse one weekly-schedule document."""
    lines = _clean_lines(text)
    week = ParsedWeek(title=title)
    if lines:
        week.title = title or lines[0]
    # "Week of November 16, 2026" may be the first content line
    for line in lines[:3]:
        if _WEEK_OF_RE.search(line):
            m = _WEEK_OF_RE.search(line)
            month = _month(m.group(1))
            if month:
                try:
                    week.week_of = date(int(m.group(4)), month, int(m.group(2)))
                except ValueError:
                    pass
            break
    if week.week_of is None:
        week.week_of, _ = _week_start(week.title, lines)
    for line in lines:
        qf = _QF_RE.search(line)
        if qf:
            week.term = f"{qf.group(1)} {qf.group(2)}"
            break

    # Drop the header block ("Day Topic Assignment", course line) before the
    # first day marker.
    start = next((i for i, ln in enumerate(lines) if _DAY_RE.match(ln)), None)
    if start is None:
        week.unparsed = lines
        return week
    body = lines[start:]

    chunks = _split_days(body, week.week_of)
    for idx, (day, chunk) in enumerate(chunks):
        if not chunk:
            continue
        text_block = " ".join(chunk)
        due_m = _DUE_RE.search(text_block)
        if due_m is None and _DANGLING_DUE_RE.search(text_block) and idx + 1 < len(chunks):
            # "... Due at the START OF CLASS on" + the next chunk's weekday
            nxt_day = chunks[idx + 1][0]
            if nxt_day is not None:
                due_m = _BorrowedMatch(nxt_day)
        due_text = ""
        due: date | None = None
        if due_m:
            token = due_m.group(1).lower()
            due_text = f"Due {token}"
            if token.isdigit() or "/" in token:
                m = _DATE_RE.match(token)
                if m:
                    try:
                        due = date(day.year if day else date.today().year,
                                   int(m.group(1)), int(m.group(2)))
                    except (ValueError, AttributeError):
                        due = None
            elif day is not None:
                due = _next_weekday(day, token)
        # Everything before the first "Due" marker is the entry; after it is
        # usually the submission details.
        head = text_block
        detail = ""
        if due_m:
            head = text_block[:due_m.start()].strip(" :;,-")
            detail = text_block[due_m.end():].strip(" :;,-")

        title_text = _guess_title(head)
        if not title_text:
            week.unparsed.append(text_block)
            continue
        reason = ""
        needs_review = False
        if due is None:
            needs_review = True
            reason = "no due date in the entry" if not due_m else "due date not understood"
        week.assignments.append(ParsedAssignment(
            title=title_text, due=due, due_text=due_text, day=day,
            details=detail, needs_review=needs_review, reason=reason))
    return week


# "4.3 SSS & SAS Due Tuesday: pg.305 # 5, 6" -> title "4.3 SSS & SAS"
# "Chapter 1 Test Work on project research/ideas" -> keep as-is (ambiguous)
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")


def _guess_title(head: str) -> str:
    head = head.strip(" :;,-")
    if not head:
        return ""
    return head[:160]


def parse_schedules(texts: dict[str, str]) -> list[ParsedWeek]:
    """Parse several documents (title -> text)."""
    out = []
    for title, body in texts.items():
        out.append(parse_week(body, title=title))
    out.sort(key=lambda w: (w.week_of is None, w.week_of or date.max))
    return out

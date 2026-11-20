"""Read Classroom through its own pages.

Classroom's internal API is `batchexecute` RPC with obfuscated request ids and
positional-JSON responses; the class names in the DOM are hashed too
(`MHxtic`, `XQMoHb`). Both change without notice. So this leans on the two
things that are *meaningful* rather than cosmetic:

* URLs — `/c/<course>`, `/c/<course>/a/<assignment>/details`,
  `/w/<course>/t/all`, `/a/not-turned-in|missing|done/<course>`
* ARIA/leaf text and a few `jsname`/`data-course-id` hooks

Every extractor runs in the page and returns plain JSON, so the parsing is
testable by feeding the same JS a saved snapshot.

Classroom is a single-page app: navigating and then reading immediately reads
the *previous* view. Each reader therefore navigates, waits for a URL match,
and re-reads until the content stops changing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .cdp import Tab

BASE = "https://classroom.google.com"

# ── JS helpers ───────────────────────────────────────────────────────────

# Collect the non-empty leaf texts of a subtree: survives re-styling, and is
# what we actually want (label, title, course, due date...).
_LEAVES = r"""
const leaves = el => [...el.querySelectorAll('*')]
  .filter(e => !e.children.length)
  .map(e => (e.textContent || '').replace(/\u00a0/g, ' ').trim())
  .filter(Boolean);
"""

JS_COURSES = r"""(() => {
  %s
  const out = [];
  for (const li of document.querySelectorAll('li[data-course-id]')) {
    const link = li.querySelector('a[href^="/c/"]');
    if (!link) continue;
    const L = leaves(li);
    // "Due Saturday" / "No work due soon" followed by the item name
    const dueIdx = L.findIndex(t => /^(Due |No work due soon)/.test(t));
    // Between the name and the due line: "Teacher" or "Section ~ Teacher".
    const middle = dueIdx > 0 ? L.slice(1, dueIdx) : L.slice(1, 3);
    const isTeacher = t => /,/.test(t);           // "Rivera, Alex"
    out.push({
      name: L[0] || '',
      section: middle.filter(t => !isTeacher(t)).join(' '),
      teacher: middle.filter(isTeacher).join(' ') || middle[middle.length - 1] || '',
      href: link.getAttribute('href'),
      course_id: li.getAttribute('data-course-id'),
      next: (() => {
        if (dueIdx < 0) return '';
        if (/^No work due soon/.test(L[dueIdx])) return '';
        const tail = L.slice(dueIdx + 1).filter(t => !/^[A-Z],/.test(t) && !/^Woohoo/.test(t));
        return [L[dueIdx], tail[0] || ''].filter(Boolean).join(' · ');
      })(),
    });
  }
  return out;
})()""" % _LEAVES

# To-do / missing / done rows. Each item is an <a href="/c/<c>/a/<a>/details">
# whose leaves are [icon label, title, course, due...].
JS_ASSIGNMENTS = r"""(() => {
  %s
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll('a[href*="/a/"][href*="details"]')) {
    const href = a.getAttribute('href');
    if (seen.has(href)) continue;
    let li = a; while (li && li.tagName !== 'LI') li = li.parentElement;
    const scope = li || a;
    const L = leaves(a);
    const icon = L[0] && L[0].length < 20 ? L[0] : '';
    const title = (icon ? L[1] : L[0]) || '';
    if (!title) continue;
    const rest = icon ? L.slice(2) : L.slice(1);
    const course = rest[0] || '';
    const dueText = rest.slice(1).join(' ');
    // grade/points sometimes appear on done items
    const scopeText = (scope.innerText || '').replace(/\s+/g, ' ');
    const pts = scopeText.match(/(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/);
    const grade = scopeText.match(/\b([A-F])\b(?=\s|$)/);
    seen.add(href);
    out.push({
      href, icon, title, course, due_text: dueText,
      points: pts ? pts[1] + '/' + pts[2] : '',
      grade: grade ? grade[1] : '',
      turned_in: /Turn in|Assigned|Missing|Done/i.test(scopeText) ? '' : '',
    });
  }
  return out;
})()""" % _LEAVES

# Classwork rows: jsname="tdoU3e" carries "Material Assignment Posted Nov 16".
JS_CLASSWORK = r"""(() => {
  %s
  const out = [];
  for (const row of document.querySelectorAll('[jsname="tdoU3e"]')) {
    const L = leaves(row);
    const kind = (L[0] || '').toLowerCase();
    const icon = L[1] || '';
    const title = L[2] || '';
    const dates = [...new Set(L.filter(t => /^(Posted|Due|Published)\b/.test(t)))];
    out.push({kind, icon, title, dates, text: (row.innerText || '').replace(/\s+/g, ' ') });
  }
  return out;
})()""" % _LEAVES

JS_TOPICS = r"""(() => {
  const out = [];
  for (const el of document.querySelectorAll('[jsname], h2, h3')) {
    const t = (el.textContent || '').trim();
    if (!t || t.length > 80) continue;
    if (/^(Collapse topic|Collapse all sections|Topic filter|All topics)/i.test(t)) continue;
    if (!/^(Week \d|Unit |Topic|Chapter|Quarter|Term |Retake|[A-Z][\w ]{2,40}$)/.test(t)) continue;
    if (el.querySelector('[jsname="tdoU3e"]')) continue;
    out.push(t);
  }
  return [...new Set(out)];
})()"""

JS_DETAIL = r"""(() => {
  %s
  const body = (document.body.innerText || '');
  // The page's own <title> is "<assignment> - Classroom"; the visible headings
  // include nav ("Classroom", course name), so start from document.title.
  const pageTitle = (document.title || '').replace(/\s*-\s*Classroom$/, '').trim();
  const text = leaves(document.body);
  const points = body.match(/(\d+(?:\.\d+)?)\s*points?/i);
  const pointsLine = body.match(/(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/);
  const due = body.match(/Due\s+([A-Z][a-z]{2}\s+\d{1,2}(?:,\s*\d{4})?|\d{1,2}\/\d{1,2}(?:\/\d{2,4})?)/);
  const states = ['Assigned', 'Turned in', 'Missing', 'Graded', 'Returned', 'Not accepting work']
    .filter(s => new RegExp('\\b' + s + '\\b', 'i').test(body));
  const attachments = [...document.querySelectorAll('a[href*="docs.google.com"], a[href*="drive.google.com"]')]
    .map(a => (a.innerText || '').trim()).filter(Boolean);
  const links = [...document.querySelectorAll('a[href^="http"]')]
    .map(a => a.getAttribute('href')).filter(h => h && !/google\.com\/(?!docs|drive)/.test(h));
  return {
    title: pageTitle,
    points: pointsLine ? pointsLine[1] + '/' + pointsLine[2] : (points ? points[1] : ''),
    due_text: due ? 'Due ' + due[1] : '',
    states,
    attachments: [...new Set(attachments)].slice(0, 10),
    links: [...new Set(links)].slice(0, 10),
    text: text.slice(0, 200),
  };
})()""" % _LEAVES

# ── models ───────────────────────────────────────────────────────────────


@dataclass
class Course:
    name: str
    href: str
    course_id: str = ""
    section: str = ""
    teacher: str = ""
    next: str = ""

    @property
    def path(self) -> str:
        return self.href if self.href.startswith("/") else "/" + self.href


@dataclass
class Assignment:
    title: str
    course: str
    href: str
    due_text: str = ""
    due: date | None = None
    points: str = ""
    grade: str = ""
    kind: str = "assignment"
    states: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return BASE + self.href

    @property
    def course_id(self) -> str:
        m = re.match(r"/c/([^/]+)", self.href)
        return m.group(1) if m else ""

    @property
    def overdue(self) -> bool:
        return bool(self.due) and self.due < date.today()

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "course": self.course, "url": self.url,
            "due": self.due.isoformat() if self.due else "",
            "due_text": self.due_text, "points": self.points, "grade": self.grade,
            "kind": self.kind, "states": self.states,
            "attachments": self.attachments, "links": self.links,
        }


@dataclass
class ClassworkItem:
    title: str
    kind: str
    dates: str = ""
    icon: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "kind": self.kind, "dates": self.dates,
                "icon": self.icon}


# ── text parsing ─────────────────────────────────────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_due(text: str, today: date | None = None) -> date | None:
    """Best-effort due date from Classroom's relative/absolute text.

    Classroom renders "Saturday, 5:59 AM", "Yesterday, 5:59 AM",
    "Nov 13", "Fri, Nov 13" or "Due Nov 21" — and often twice over. Anything
    we can't pin down returns None rather than a wrong date.
    """
    today = today or date.today()
    if not text:
        return None
    low = text.lower()
    if "yesterday" in low:
        return today.fromordinal(today.toordinal() - 1)
    if "today" in low:
        return today
    # "“Monday, 6:50 PM” / “Saturday, 5:59 AM”: a bare weekday means the most
    # recent such day (no date part). Classroom renders both forms.
    wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
    if wd and not re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", low):
        want = ["monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"].index(wd.group(1))
        back = (today.weekday() - want) % 7
        return today.fromordinal(today.toordinal() - back)

    m = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", low)
    if m and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, month, int(m.group(2)))
        except ValueError:
            return None
        # "Nov 13" seen in January almost certainly means two Decembers ago;
        # Classroom only deals in near dates, so roll forward a year.
        if not m.group(3) and d < today.fromordinal(today.toordinal() - 120):
            try:
                d = date(year + 1, month, int(m.group(2)))
            except ValueError:
                return None
        return d
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def parse_points(text: str) -> tuple[float | None, float | None]:
    """'8/10' -> (8.0, 10.0); '3 points' -> (None, 3.0)."""
    if not text:
        return (None, None)
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"(\d+(?:\.\d+)?)\s*points?", text, re.I)
    if m:
        return (None, float(m.group(1)))
    return (None, None)


# ── page readers ─────────────────────────────────────────────────────────

_STABLE_GAP = 0.8


def _settle(tab: Tab, marker: str, retries: int = 10) -> None:
    """Wait until `marker` appears on the page (SPA views swap asynchronously)."""
    tab.wait_for(f"""document.body && document.body.innerText.includes({marker!r})""",
                 timeout=25)


def courses(tab: Tab) -> list[Course]:
    tab.navigate(f"{BASE}/h", wait=2.5)
    _settle(tab, "Classes")
    raw = tab.js(JS_COURSES) or []
    out = []
    for r in raw:
        if not r.get("name"):
            continue
        out.append(Course(name=r["name"], href=r.get("href", ""),
                          course_id=r.get("course_id", ""),
                          section=r.get("section", ""),
                          teacher=r.get("teacher", ""),
                          next=r.get("next", "")))
    return out


def _assignments_from(tab: Tab, path: str) -> list[Assignment]:
    tab.navigate(BASE + path, wait=2.5)
    # the to-do/missing views render items a beat after the chrome
    tab.wait_for("""document.querySelectorAll('a[href*="/a/"][href*="/details"]').length > 0 || /Nothing on your to-do list|Woohoo|Nothing due/.test(document.body.innerText)""",
                 timeout=20)
    raw = tab.js(JS_ASSIGNMENTS) or []
    out = []
    for r in raw:
        href = r.get("href") or ""
        out.append(Assignment(
            title=r.get("title", ""),
            course=r.get("course", ""),
            href=href,
            due_text=r.get("due_text", ""),
            due=parse_due(r.get("due_text", "")),
            points=r.get("points", ""),
            grade=r.get("grade", ""),
            kind=(r.get("icon") or "assignment"),
        ))
    return out


def missing(tab: Tab) -> list[Assignment]:
    """Work that Classroom considers not turned in."""
    return _assignments_from(tab, "/a/missing/all")


def todo(tab: Tab) -> list[Assignment]:
    """Assigned but not yet marked done (Classroom's To-do tab)."""
    return _assignments_from(tab, "/a/not-turned-in/all")


def done(tab: Tab) -> list[Assignment]:
    return _assignments_from(tab, "/a/done/all")


def course_assignments(course: Course) -> str:
    return f"/a/not-turned-in/{course.href.strip('/').split('/')[-1]}"


def classwork(tab: Tab, course: Course) -> list[ClassworkItem]:
    cid = course.href.rstrip("/").split("/")[-1]
    tab.navigate(f"{BASE}/w/{cid}/t/all", wait=2.5)
    _settle(tab, "Classwork")
    tab.wait_for("""document.querySelectorAll('[jsname="tdoU3e"]').length > 0 || /No work|Nothing/.test(document.body.innerText)""",
                 timeout=20)
    tab.wait_for("""document.querySelectorAll('[jsname="tdoU3e"]').length > 0
                    || /No work|Nothing posted/.test(document.body.innerText)""",
                 timeout=20)
    raw = tab.js(JS_CLASSWORK) or []
    return [ClassworkItem(title=r.get("title", ""), kind=r.get("kind", ""),
                          dates=" · ".join(r.get("dates") or []), icon=r.get("icon", ""))
            for r in raw if r.get("title")]


def detail(tab: Tab, assignment: Assignment) -> Assignment:
    """Fill in points/due/attachments/states from the assignment page."""
    tab.navigate(assignment.url, wait=2.5)
    tab.wait_for("""/Your work|Turn in|Assigned|No work/.test(document.body.innerText)""",
                 timeout=25)
    raw = tab.js(JS_DETAIL) or {}
    if raw.get("title"):
        assignment.title = raw["title"]
    if raw.get("points"):
        assignment.points = raw["points"]
    if raw.get("due_text"):
        assignment.due_text = assignment.due_text or raw["due_text"]
        assignment.due = assignment.due or parse_due(raw["due_text"])
    assignment.states = raw.get("states") or []
    assignment.attachments = raw.get("attachments") or []
    assignment.links = raw.get("links") or []
    return assignment

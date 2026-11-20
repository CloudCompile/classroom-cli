"""Notice when something new is posted in Classroom.

Classroom offers students no webhook, so this polls each course's stream and
diffs against a saved snapshot (`~/.config/classroom/state.json`).

Design notes from the real pages:

* The home page only lists work that's *due soon*; actual posts
  (announcements, materials, assignments) live in each course's stream, and
  those rows carry `data-stream-item-id`. So we scan course streams.
* Post text is regular enough to classify:
      book Material: "4.3 Notes, HW and KEY" ... posted a new material
      assignment Assignment: "OPTIONAL 2.7-2.9 Section Check" ...
      (no marker) -> announcement
* The first run records a baseline instead of replaying the whole backlog.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cdp import Tab
from .profile import config_path
from .scrape import BASE

JS_STREAM = r"""(() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('[data-stream-item-id]')) {
    const id = el.getAttribute('data-stream-item-id');
    if (!id || seen.has(id)) continue;
    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length < 20) continue;      // skip icon/menu wrappers
    seen.add(id);
    const link = el.querySelector('a[href*="/details"]');
    out.push({id, text: text.slice(0, 400),
              href: link ? link.getAttribute('href') : ''});
  }
  return out;
})()"""

# "book Material: "X" ..." / "assignment Assignment: "Y" ..."
_QUOTED = re.compile(r'^\W*?\w*\s*(Material|Assignment|Question):\s*"([^"]+)"')
# "Created Nov 16 Nov 16 more_vert More options <body>" for announcements
_AUTHOR = re.compile(r"Created\s+(\w{3}\s+\d{1,2}|Yesterday|\d{1,2}:\d{2}\s*[AP]M)")
# Skipping the author/chrome prefix: "Post by X X Created <when> <when> (Edited <when>)? more_vert More options"
_AUTHOR_BLOCK = re.compile(
    r"^(?:\d+\+?\s+)?Post by\s+(.+?)\s+Created\s+.*?More options\s*", re.S)
_TITLE_BLOCK = re.compile(r"posted a new \w+:\s*(.+?)(?:\s+Created\b|$)", re.S)
_COURSE_SEP = " · "


@dataclass
class Post:
    course: str
    post_id: str
    kind: str = "announcement"      # material | assignment | question | announcement
    title: str = ""
    created: str = ""
    href: str = ""
    text: str = ""

    @property
    def key(self) -> str:
        # course-scoped so identical titles in two classes stay distinct
        return f"{self.course}::{self.post_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"course": self.course, "post_id": self.post_id, "kind": self.kind,
                "title": self.title, "created": self.created, "href": self.href,
                "key": self.key, "text": self.text}


def parse_post(course: str, raw: dict[str, Any]) -> Post:
    text = raw.get("text") or ""
    m = _QUOTED.match(text)
    if m is None:
        m = re.search(r'(Material|Assignment|Question):\s*"([^"]+)"', text)
    if m:
        kind = m.group(1).lower()
        title = m.group(2).strip()
    else:
        kind = "announcement"
        # Announcements have no title field: the body follows the author block
        # ("Post by X X Created <when> ... more_vert More options <body>").
        body = _AUTHOR_BLOCK.sub("", text).strip()
        if not body:
            body = text
        body = re.sub(r"^(Created\b.*?More options\s*)", "", body).strip()
        title = body[:100].strip()
        if len(body) > 100:
            title = title.rsplit(" ", 1)[0] + "…"
    cm = _AUTHOR.search(text)
    return Post(course=course, post_id=str(raw.get("id") or ""), kind=kind,
                title=title, created=(cm.group(1) if cm else ""),
                href=raw.get("href") or "", text=text)


def state_path() -> str:
    return os.path.join(os.path.dirname(config_path()), "state.json")


def load_state() -> dict[str, Any]:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


@dataclass
class WatchResult:
    new: list[Post] = field(default_factory=list)
    baseline: bool = False
    scanned: int = 0
    courses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"new": [p.to_dict() for p in self.new], "baseline": self.baseline,
                "scanned": self.scanned, "courses": self.courses}


def scan_course(tab: Tab, course_name: str, course_href: str) -> list[Post]:
    tab.navigate(f"{BASE}{course_href}", wait=3)
    tab.wait_for("""document.querySelectorAll('[data-stream-item-id]').length > 0
                    || /No posts|Nothing/.test(document.body.innerText)""",
                 timeout=25)
    raw = tab.js(JS_STREAM) or []
    return [parse_post(course_name, r) for r in raw if r.get("id")]


def scan(tab: Tab, courses: list[Any]) -> list[Post]:
    """Every post across the given courses (classroom.scrape.Course objects)."""
    out: list[Post] = []
    for c in courses:
        try:
            out.extend(scan_course(tab, c.name, c.href))
        except Exception:            # noqa: BLE001 - one bad course shouldn't stop the scan
            continue
    return out


def diff(posts: list[Post], state: dict[str, Any], *, baseline: bool = False) -> WatchResult:
    seen: set[str] = set(state.get("seen") or [])
    if baseline or "seen" not in state:
        return WatchResult(new=posts, baseline=True, scanned=len(posts))
    fresh = [p for p in posts if p.key not in seen]
    return WatchResult(new=fresh, baseline=False, scanned=len(posts))


def commit(posts: list[Post], state: dict[str, Any]) -> dict[str, Any]:
    keys = list(state.get("seen") or [])
    known = set(keys)
    for p in posts:
        if p.key not in known:
            keys.append(p.key)
            known.add(p.key)
    state = dict(state)
    state["seen"] = keys[-8000:]           # keep the file bounded
    state["last_scan"] = datetime.now().isoformat(timespec="seconds")
    return state


def watch_once(tab: Tab, courses: list[Any], *, baseline: bool = False,
               save: bool = True) -> WatchResult:
    state = load_state()
    posts = scan(tab, courses)
    result = diff(posts, state, baseline=baseline)
    result.courses = len(courses)
    if save:
        save_state(commit(posts, state))
    return result


def format_new(posts: list[Post]) -> str:
    """Tab-separated lines, so a shell/agent trigger can parse them."""
    return "\n".join(f"{p.kind}\t{p.course}\t{p.title}\t{p.href}" for p in posts)


def watch_loop(tab: Tab, courses_fn, *, interval: float = 120.0,
               on_new=None, iterations: int | None = None,
               stop_when=lambda: False) -> None:
    """Poll, calling `on_new(posts)` only for genuinely new items."""
    rounds = 0
    first = True
    while True:
        courses = courses_fn()
        result = watch_once(tab, courses, baseline=first)
        first = False
        if result.baseline:
            pass                            # first pass is a baseline, never "new"
        elif result.new and on_new is not None:
            on_new(result.new)
        rounds += 1
        if iterations is not None and rounds >= iterations:
            return
        if stop_when():
            return
        time.sleep(interval)

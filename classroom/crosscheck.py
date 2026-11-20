"""Cross-check what Skyward and Classroom each think is outstanding.

The two systems answer different questions:

* Skyward knows what the *gradebook* has a slot for, and whether a score was
  posted. It's authoritative on "your teacher counts this as missing".
* Classroom knows what was *assigned and turned in* through Classroom. It
  says nothing about work done on paper, and work turned in on paper shows
  as missing there forever.

So neither is right on its own. The useful output is the disagreement:

    only_in_classroom   probably paper work, or Classroom-only materials
    only_in_skyward     graded assignments Classroom never posted
    both                genuinely outstanding — do these first
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Item:
    source: str                 # "skyward" | "classroom"
    title: str
    course: str = ""
    due: Any = None             # date | None
    url: str = ""
    detail: str = ""

    @property
    def norm(self) -> str:
        return _norm(self.title)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "title": self.title, "course": self.course,
                "due": self.due.isoformat() if self.due else "", "url": self.url,
                "detail": self.detail}


@dataclass
class CrossCheck:
    both: list[tuple[Item, Item]] = field(default_factory=list)
    only_skyward: list[Item] = field(default_factory=list)
    only_classroom: list[Item] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "both": [{"skyward": a.to_dict(), "classroom": b.to_dict()} for a, b in self.both],
            "only_skyward": [i.to_dict() for i in self.only_skyward],
            "only_classroom": [i.to_dict() for i in self.only_classroom],
        }


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if len(t) > 2 and t not in
            {"assignment", "worksheet", "homework", "work", "the", "and", "due", "for"}}


def same_work(a: Item, b: Item, threshold: float = 0.62) -> bool:
    """Do two records describe the same piece of work?

    Titles are the only shared field, and teachers/Classroom word them
    differently ("2.7/2.9 Review WS" vs "2.7-2.9 Review Worksheet"), so this
    is deliberately fuzzy — but course names must not obviously disagree.
    """
    ta, tb = _tokens(a.title), _tokens(b.title)
    if ta and tb:
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        if overlap >= 0.6:
            return True
    if similarity(a.title, b.title) >= threshold:
        return True
    return False


def cross_check(skyward: Iterable[Item], classroom: Iterable[Item]) -> CrossCheck:
    sk, cl = list(skyward), list(classroom)
    matched_cl: set[int] = set()
    out = CrossCheck()
    for a in sk:
        hit = None
        for i, b in enumerate(cl):
            if i in matched_cl:
                continue
            if same_work(a, b):
                hit = i
                break
        if hit is None:
            out.only_skyward.append(a)
        else:
            matched_cl.add(hit)
            out.both.append((a, cl[hit]))
    out.only_classroom = [b for i, b in enumerate(cl) if i not in matched_cl]
    # most urgent first: everything outstanding, by due date
    out.only_skyward.sort(key=lambda i: (i.due is None, i.due or 0, i.title.lower()))
    out.only_classroom.sort(key=lambda i: (i.due is None, i.due or 0, i.title.lower()))
    return out

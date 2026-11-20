"""Rich renderers for classroom output."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .scrape import Assignment, ClassworkItem, Course

console = Console()
err_console = Console(stderr=True)
quiet = False


def due_text(day: date | None, raw: str = "") -> Text:
    if not day:
        return Text(raw or "—", style="dim")
    today = date.today()
    delta = (day - today).days
    label = day.strftime("%a %b %-d")
    if delta < 0:
        return Text(f"{label} · {-delta}d late", style="bold red")
    if delta == 0:
        return Text(f"{label} · today", style="bold yellow")
    if delta <= 7:
        return Text(f"{label} · {delta}d", style="yellow")
    return Text(label, style="")


def render_courses(courses: list[Course]) -> None:
    if not courses:
        console.print("[dim]no classes[/]")
        return
    t = Table(box=None, pad_edge=False, header_style="dim")
    t.add_column("Class")
    t.add_column("Section", style="dim")
    t.add_column("Teacher", style="dim")
    t.add_column("Due next", style="dim")
    for c in courses:
        t.add_row(Text(c.name, style="bold"), c.section, c.teacher, c.next)
    console.print(t)


def render_assignments(items: list[Assignment], *, title: str, show_due: bool = False) -> None:
    console.print(Text(title, style="dim"))
    if not items:
        console.print("[dim]nothing here[/]")
        return
    t = Table(box=None, pad_edge=False, header_style="dim")
    if show_due:
        t.add_column("Due", no_wrap=True)
    t.add_column("Assignment")
    t.add_column("Class", style="dim")
    t.add_column("Points", style="dim", justify="right")
    for a in sorted(items, key=lambda x: (x.due is None, x.due or date.max, x.title.lower())):
        row: list[Any] = []
        if show_due:
            row.append(due_text(a.due, a.due_text))
        row.append(Text(a.title))
        row.append(a.course)
        row.append(a.points or "")
        t.add_row(*row)
    console.print(t)
    if items:
        console.print(f"[dim]{len(items)} item{'s' if len(items) != 1 else ''}[/]")


def render_classwork(course: Course, items: list[ClassworkItem]) -> None:
    console.print(Text(course.name, style="bold"), end="")
    if course.section:
        console.print(Text(f"  ·  {course.section}", style="dim"))
    else:
        console.print()
    if not items:
        console.print("[dim]nothing posted[/]")
        return
    console.print()
    t = Table(box=None, pad_edge=False, header_style="dim")
    t.add_column("Type", style="dim", width=11)
    t.add_column("Item")
    t.add_column("Posted / due", style="dim")
    for it in items:
        kind = (it.kind or it.icon or "").title() or "—"
        style = "bold" if kind.lower().startswith("assign") else ""
        t.add_row(kind, Text(it.title, style=style), it.dates)
    console.print(t)


def render_detail(a: Assignment) -> None:
    console.print(Text(a.title, style="bold"))
    console.print(Text(f"{a.course}", style="dim"))
    console.print()
    if a.due:
        console.print(Text("due    ", style="dim"), due_text(a.due, a.due_text))
    elif a.due_text:
        console.print(Text("due    ", style="dim"), Text(a.due_text, style="dim"))
    if a.points:
        console.print(Text("points ", style="dim"), a.points)
    if a.states:
        console.print(Text("state  ", style="dim"), " · ".join(a.states))
    console.print(Text("link   ", style="dim"), Text(a.url, style="dim"))
    if a.attachments:
        console.print()
        console.print(Text("attached", style="dim"))
        for att in a.attachments:
            console.print(f"  · {att}")
    if a.links:
        console.print()
        console.print(Text("links", style="dim"))
        for link in a.links:
            console.print(f"  · [dim]{link}[/]")


def render_attachments(course: Any, att: Any, show_text: bool = False) -> None:
    console.print(Text(att.name, style="bold"), end="")
    if course is not None:
        console.print(Text(f"  ·  {course.name}", style="dim"))
    else:
        console.print()
    if not att.files:
        console.print("[dim]no drive files found[/]")
        return
    console.print()
    for f in att.files:
        console.print(f"  · [dim]{f.kind:6}[/] {f.file_id}")
    if att.error:
        console.print(f"[red]read failed:[/] {att.error}")
    if show_text and att.text:
        console.print()
        console.print(att.text)


def render_weeks(weeks: list[Any]) -> None:
    for w in weeks:
        head = Text(w.title or "schedule", style="bold")
        if w.week_of:
            head.append(f"  ·  week of {w.week_of.strftime('%b %-d')}", style="dim")
        if w.term:
            head.append(f"  ·  {w.term}", style="dim")
        console.print(head)
        if not w.assignments:
            console.print("[dim]  nothing parsed[/]\n")
            continue
        t = Table(box=None, pad_edge=False, header_style="dim")
        t.add_column("Due", no_wrap=True)
        t.add_column("Assignment")
        t.add_column("Listed under", style="dim", no_wrap=True)
        for a in sorted(w.assignments, key=lambda x: (x.due is None, x.due or date.max)):
            if a.needs_review:
                t.add_row(Text("needs review", style="yellow"), a.title,
                          a.day.strftime("%a %-m/%-d") if a.day else "")
            else:
                t.add_row(due_text(a.due, a.due_text),
                          a.title,
                          a.day.strftime("%a %-m/%-d") if a.day else "")
        console.print(t)
        flagged = [a for a in w.assignments if a.needs_review]
        if flagged:
            console.print(f"[yellow]{len(flagged)} entr{'y' if len(flagged)==1 else 'ies'} "
                          f"need a human look[/]")
        console.print()


def render_sync(course: Any, result: dict[str, Any], *, applied: bool) -> None:
    mode = "applied" if applied else "dry run — pass --apply to write"
    console.print(Text(course.name, style="bold"), end="")
    console.print(Text(f"  ·  {mode}", style="dim"))
    console.print()

    def block(label: str, rows: list[dict], style: str, extra: str = ""):
        if not rows:
            return
        console.print(Text(f"{label} ({len(rows)})", style=style))
        for r in rows:
            due = r.get("due") or "no date"
            line = f"  · {due}  {r['title']}"
            if extra and r.get(extra):
                line += f"  [dim]({r[extra]})[/]"
            console.print(line)
        console.print()

    block("create", result.get("created", []), "green")
    block("update", result.get("updated", []), "cyan", extra="was")
    if result.get("unchanged"):
        console.print(Text(f"unchanged: {result['unchanged']}", style="dim"))
        console.print()
    block("needs review (not written)", result.get("skipped", []), "yellow", extra="reason")
    block("failed", result.get("failed", []), "red", extra="error")
    if not (result.get("created") or result.get("updated") or result.get("skipped")):
        console.print("[dim]nothing to sync[/]")


def render_crosscheck(result: Any) -> None:
    console.print(Text("outstanding work: Skyward vs Classroom", style="bold"))
    console.print()

    def section(label: str, items: list[Any], style: str, why: str = "") -> None:
        console.print(Text(f"{label} ({len(items)})", style=style))
        if why:
            console.print(Text(f"  {why}", style="dim"))
        if not items:
            console.print("[dim]  none[/]")
        for i in items:
            due = i.due.strftime("%a %b %-d") if i.due else "no date"
            console.print(f"  · [dim]{due:12}[/] {i.title[:56]} [dim]{i.course[:26]}[/]")
        console.print()

    section("in both (do these first)", [b[1] for b in result.both], "bold red")
    section("only in Classroom", result.only_classroom, "yellow",
            "how these usually look: turned in on paper, or Classroom-only materials")
    section("only in Skyward", result.only_skyward, "cyan",
            "graded in the gradebook but never posted to Classroom")


def to_json(obj: Any) -> str:
    def default(o: Any) -> Any:
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        raise TypeError(f"{type(o).__name__} is not JSON serialisable")
    return json.dumps(obj, indent=2, default=default, ensure_ascii=False)

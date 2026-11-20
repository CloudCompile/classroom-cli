"""classroom — assignments, missing work and class materials from Classroom."""

from __future__ import annotations

import re
import sys
from typing import Optional

import typer

import os

from . import cdp, identity, render, scrape
from . import profile as profile_mod

app = typer.Typer(
    help="Google Classroom assignments, to-do and classwork from the terminal.",
    no_args_is_help=True,
    add_completion=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _open(headed: bool = False) -> tuple[profile_mod.Browser, cdp.Tab]:
    """Attach to the classroom profile and verify the account before reading."""
    try:
        browser = profile_mod.Browser(port=int(os.environ.get("CLASSROOM_CDP_PORT", "9351")))
        browser.json("/json/version")
    except Exception:
        if headed:
            browser = profile_mod.launch(headed=True)
        else:
            try:
                browser = profile_mod.launch(headed=False)
            except profile_mod.ClassroomError as e:
                render.err_console.print(
                    f"[red]{e}[/]\n[dim]If you haven't signed in yet, run:[/]\n"
                    f"  [bold]classroom login[/]")
                raise typer.Exit(3)
    try:
        tab = cdp.work_tab(browser, cdp.WORK_TAB_URL, settle=3)
        ident = identity.verify(tab)          # refuses the wrong account
    except identity.NotSignedIn as e:
        render.err_console.print(f"[red]not signed in:[/] {e}\n[dim]run[/] [bold]classroom login[/]")
        raise typer.Exit(2)
    except identity.WrongAccount as e:
        render.err_console.print(f"[red]refusing to continue:[/] {e}")
        raise typer.Exit(4)
    except profile_mod.ClassroomError as e:
        render.err_console.print(f"[red]classroom error:[/] {e}")
        raise typer.Exit(3)
    if not render.quiet:
        render.err_console.print(f"[dim]reading as {ident.email}[/]")
    return browser, tab


@app.command()
def login(
    url: str = typer.Option("https://accounts.google.com/AccountChooser?continue=https://classroom.google.com/",
                            "--url", help="Page to open for signing in"),
):
    """Open the classroom Chrome so a human can sign in (once)."""
    browser = profile_mod.launch(headed=True, url=url)
    render.console.print(f"[green]Chrome is open on {profile_mod.DISPLAY}[/]")
    render.console.print(f"profile: [dim]{profile_mod.profile_dir()}[/]")
    render.console.print("Sign in as the school account only, then run: [bold]classroom whoami[/]")
    render.console.print(f"[dim]leave this running: pid {browser.proc.pid if browser.proc else '?'}[/]")


@app.command()
def whoami(json_out: bool = typer.Option(False, "--json")):
    """Show which Google account the classroom profile is using."""
    browser, tab = _open()
    ident = identity.verify(tab)
    allowed = profile_mod.expected_account()
    if json_out:
        typer.echo(render.to_json({"email": ident.email, "allowed": allowed,
                                   "profile": profile_mod.profile_dir()}))
        return
    render.console.print(f"[bold]{ident.email}[/]")
    render.console.print(f"[dim]profile: {profile_mod.profile_dir()}[/]")
    if allowed:
        render.console.print(f"[dim]pinned to: {allowed}[/]")


@app.command("courses")
def courses_cmd(json_out: bool = typer.Option(False, "--json")):
    """List your classes."""
    browser, tab = _open()
    items = scrape.courses(tab)
    if json_out:
        typer.echo(render.to_json({"courses": items}))
        return
    render.render_courses(items)


@app.command()
def todo(
    json_out: bool = typer.Option(False, "--json"),
    due: bool = typer.Option(False, "--due", help="Include due dates"),
    detail: bool = typer.Option(False, "--detail", help="Open each item for points/attachments (slow)"),
):
    """Work Classroom still has you on (its To-do tab)."""
    browser, tab = _open()
    items = scrape.todo(tab)
    if detail:
        for a in items:
            scrape.detail(tab, a)
    if json_out:
        typer.echo(render.to_json({"todo": items}))
        return
    render.render_assignments(items, title="to-do", show_due=due)


@app.command()
def missing(
    json_out: bool = typer.Option(False, "--json"),
    detail: bool = typer.Option(False, "--detail", help="Open each item for points/attachments (slow)"),
):
    """Work marked not turned in."""
    browser, tab = _open()
    items = scrape.missing(tab)
    if detail:
        for a in items:
            scrape.detail(tab, a)
    if json_out:
        typer.echo(render.to_json({"missing": items}))
        return
    render.render_assignments(items, title="missing", show_due=True)


@app.command()
def done(
    json_out: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """Work you've turned in."""
    browser, tab = _open()
    items = scrape.done(tab)[:limit]
    if json_out:
        typer.echo(render.to_json({"done": items}))
        return
    render.render_assignments(items, title="done", show_due=True)


@app.command()
def classwork(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Materials and posted work for one class."""
    browser, tab = _open()
    all_courses = scrape.courses(tab)
    if not course:
        render.err_console.print("[red]--course is required[/] [dim]one of:[/]")
        for c in all_courses:
            render.err_console.print(f"  · {c.name}")
        raise typer.Exit(1)
    matches = [c for c in all_courses if course.strip().lower() in c.name.lower()]
    if not matches:
        render.err_console.print(f"[red]no course matching {course!r}[/]")
        raise typer.Exit(1)
    target = matches[0]
    items = scrape.classwork(tab, target)
    if json_out:
        typer.echo(render.to_json({"course": target, "items": items}))
        return
    render.render_classwork(target, items)


@app.command()
def show(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="Assignment title fragment"),
    pick: int = typer.Option(1, "--pick", help="Which match to open (1 = first)"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Full detail for one assignment: points, due date, attachments, state."""
    browser, tab = _open()
    pool = scrape.todo(tab) + scrape.missing(tab)
    if course:
        pool = [a for a in pool if course.strip().lower() in a.course.lower()]
    if title:
        pool = [a for a in pool if title.strip().lower() in a.title.lower()]
    if not pool:
        render.err_console.print("[red]no matching assignment[/]")
        raise typer.Exit(1)
    if pick < 1 or pick > len(pool):
        render.err_console.print(f"[red]--pick {pick} is out of range (1..{len(pool)})[/]")
        raise typer.Exit(1)
    item = scrape.detail(tab, pool[pick - 1])
    if json_out:
        typer.echo(render.to_json({"assignment": item}))
        return
    render.render_detail(item)


@app.command()
def attachments(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    match: Optional[str] = typer.Option(None, "--match", "-m", help="Classwork item title fragment"),
    read: bool = typer.Option(False, "--read", "-r", help="Also extract the file text"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Find the Drive files attached to a classwork item (optionally read them)."""
    from . import attachments as at

    browser, tab = _open()
    target, hit = _pick_course_item(tab, course, match)
    try:
        files = at.open_classwork_item(tab, target, hit)
    except profile_mod.ClassroomError as e:
        render.err_console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    att = at.Attachment(name=hit, files=files)
    if read:
        for f in files:
            try:
                _, att.text = at.read_drive_file(tab, f.file_id, f.kind)
                break                     # first readable file wins
            except profile_mod.ClassroomError as e:
                att.error = str(e)
    if json_out:
        typer.echo(render.to_json({"course": target, "attachment": att}))
        return
    render.render_attachments(target, att, show_text=read)


@app.command()
def read(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    match: Optional[str] = typer.Option(None, "--match", "-m", help="Classwork item title fragment"),
    path: Optional[str] = typer.Option(None, "--file", "-f", help="A Drive file id or URL to read directly"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Print the text of an attachment (for humans and agents)."""
    from . import attachments as at

    browser, tab = _open()
    if path:
        files = at.parse_drive_links([path])
        if not files:
            render.err_console.print("[red]--file must be a Drive URL or a file id[/]")
            raise typer.Exit(1)
        target, hit = None, files[0].file_id
    else:
        target, hit = _pick_course_item(tab, course, match)
        files = at.open_classwork_item(tab, target, hit)
        if not files:
            render.err_console.print("[red]that item has no Drive file[/]")
            raise typer.Exit(1)
    try:
        title, body = at.read_drive_file(tab, files[0].file_id, files[0].kind)
    except profile_mod.ClassroomError as e:
        render.err_console.print(f"[red]{e}[/]")
        raise typer.Exit(3)
    if json_out:
        typer.echo(render.to_json({"course": target, "title": title, "text": body}))
        return
    if title:
        render.console.print(render.Text(title, style="bold"))
        render.console.print()
    render.console.print(body)


def _pick_course_item(tab, course: Optional[str], match: Optional[str]):
    """Resolve --course/--match against the real classwork lists."""
    all_courses = scrape.courses(tab)
    if not course:
        render.err_console.print("[red]--course is required[/] [dim]one of:[/]")
        for c in all_courses:
            render.err_console.print(f"  · {c.name}")
        raise typer.Exit(1)
    matches = [c for c in all_courses if course.strip().lower() in c.name.lower()]
    if not matches:
        render.err_console.print(f"[red]no course matching {course!r}[/]")
        raise typer.Exit(1)
    if not match:
        items = scrape.classwork(tab, matches[0])
        render.err_console.print("[red]--match is required[/] [dim]classwork items:[/]")
        for it in items[:25]:
            render.err_console.print(f"  · {it.title}")
        raise typer.Exit(1)
    return matches[0], match


@app.command()
def parse(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    match: Optional[str] = typer.Option("Week", "--match", "-m",
                                        help="Classwork item title fragment (default: Week)"),
    path: Optional[str] = typer.Option(None, "--file", "-f", help="A Drive file id/URL, or a local .txt"),
    weeks: int = typer.Option(1, "--weeks", "-n", help="How many matching items to parse"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Turn a teacher's weekly schedule into structured assignments.

    Defaults to dry-run style output: every entry is either dated or flagged
    `needs_review`, never silently guessed.
    """
    from . import attachments as at, schedule as sched

    if path and not path.startswith("http") and not re.fullmatch(r"[\w-]{20,}", path):
        texts = {path.rsplit("/", 1)[-1]: open(path, encoding="utf-8").read()}
    else:
        browser, tab = _open()
        texts = {}
        if path:
            files = at.parse_drive_links([path])
            if not files:
                render.err_console.print("[red]--file must be a Drive URL or a file id[/]")
                raise typer.Exit(1)
            title, body = at.read_drive_file(tab, files[0].file_id)
            texts[title or files[0].file_id] = body
        else:
            target, hit = _pick_course_item(tab, course, match)
            cid = target.href.rstrip("/").split("/")[-1]
            # _pick_course_item leaves the tab on /h; go back to classwork first
            tab.navigate(f"https://classroom.google.com/w/{cid}/t/all", wait=2.5)
            rows = at.classwork_rows(tab)
            hits = [i for i, r in enumerate(rows) if hit.strip().lower() in r.lower()]
            for i in hits[:max(1, weeks)]:
                tab.navigate(f"https://classroom.google.com/w/{cid}/t/all", wait=2.5)
                tab.js("""(() => { const r=[...document.querySelectorAll('[jsname="tdoU3e"]')][%d];
                          if (r) r.click(); return true; })()""" % i)
                import time
                time.sleep(2.0)
                files = at.parse_drive_links(
                    [l for l in (tab.js(at.JS_DRIVE_LINKS) or []) if "/d/" in l])
                if not files:
                    continue
                title, body = at.read_drive_file(tab, files[0].file_id)
                texts[title or hit] = body
    parsed = sched.parse_schedules(texts)
    if json_out:
        typer.echo(render.to_json({"weeks": parsed}))
        return
    render.render_weeks(parsed)


@app.command()
def watch(
    once: bool = typer.Option(True, "--once/--loop", help="One pass, or poll forever"),
    interval: float = typer.Option(300.0, "--interval", help="Seconds between polls with --loop"),
    baseline: bool = typer.Option(False, "--baseline", help="Accept current posts as known (no output)"),
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Only this course"),
    exec_cmd: Optional[str] = typer.Option(None, "--exec", help="Shell command; new items piped as TSV on stdin"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Report (and optionally trigger on) newly posted materials and assignments."""
    from . import watch as watch_mod

    browser, tab = _open()
    courses = scrape.courses(tab)
    if course:
        courses = [c for c in courses if course.strip().lower() in c.name.lower()]
        if not courses:
            render.err_console.print(f"[red]no course matching {course!r}[/]")
            raise typer.Exit(1)

    def handle(posts):
        text = watch_mod.format_new(posts)
        if exec_cmd:
            import subprocess
            proc = subprocess.run(exec_cmd, shell=True, input=text,
                                  text=True, capture_output=True)
            if proc.stdout.strip():
                render.console.print(proc.stdout.strip())
            if proc.returncode:
                render.err_console.print(f"[red]trigger exited {proc.returncode}[/]")
        elif not json_out:
            for p in posts:
                render.console.print(f"[bold]{p.kind}[/] · {p.course} · {p.title}")

    if baseline:
        res = watch_mod.watch_once(tab, courses, baseline=True)
        render.console.print(f"[dim]baseline recorded: {res.scanned} posts across {len(courses)} courses[/]")
        return

    if once:
        res = watch_mod.watch_once(tab, courses)
        if json_out:
            typer.echo(render.to_json(res))
            return
        if res.baseline:
            render.console.print(
                f"[dim]first run: recorded {res.scanned} existing posts as a baseline. "
                f"Run again later to see what's new.[/]")
            return
        if not res.new:
            render.console.print(f"[dim]nothing new ({res.scanned} posts seen)[/]")
            return
        handle(res.new)
        return

    render.console.print(f"[dim]watching {len(courses)} courses every {int(interval)}s[/]")
    watch_mod.watch_loop(tab, lambda: courses, interval=interval, on_new=handle)


@app.command()
def sync(
    course: Optional[str] = typer.Option(None, "--course", "-c", help="Course name fragment"),
    match: Optional[str] = typer.Option("Week", "--match", "-m", help="Weekly schedule item fragment"),
    weeks: int = typer.Option(1, "--weeks", "-n", help="How many schedules to read"),
    apply: bool = typer.Option(False, "--apply", help="Actually write to MyStudyLife"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Read a teacher's weekly schedule and put it into MyStudyLife.

    Dry run unless --apply. Entries the parser couldn't date are reported,
    never guessed.
    """
    from . import attachments as at, schedule as sched, sync_bridge

    if not sync_bridge.available():
        render.err_console.print("[red]msl isn't available[/] [dim](expected ~/msl)[/]")
        raise typer.Exit(3)
    browser, tab = _open()
    target, hit = _pick_course_item(tab, course, match)
    cid = target.href.rstrip("/").split("/")[-1]
    tab.navigate(f"https://classroom.google.com/w/{cid}/t/all", wait=2.5)
    rows = at.classwork_rows(tab)
    hits = [i for i, r in enumerate(rows) if hit.strip().lower() in r.lower()]
    texts: dict[str, str] = {}
    for i in hits[:max(1, weeks)]:
        tab.navigate(f"https://classroom.google.com/w/{cid}/t/all", wait=2.5)
        tab.js("""(() => { const r=[...document.querySelectorAll('[jsname="tdoU3e"]')][%d];
                  if (r) r.click(); return true; })()""" % i)
        import time
        time.sleep(2.0)
        files = at.parse_drive_links(
            [l for l in (tab.js(at.JS_DRIVE_LINKS) or []) if "/d/" in l])
        if not files:
            continue
        title, body = at.read_drive_file(tab, files[0].file_id)
        texts[title or hit] = body
    if not texts:
        render.err_console.print("[red]no schedule documents found[/]")
        raise typer.Exit(1)
    weeks_parsed = sched.parse_schedules(texts)
    cands = sync_bridge.candidates(weeks_parsed, target.name)
    try:
        result = sync_bridge.plan_and_apply(cands, dry_run=not apply)
    except sync_bridge.BridgeError as e:
        render.err_console.print(f"[red]{e}[/]")
        raise typer.Exit(3)
    if json_out:
        typer.echo(render.to_json({"course": target, "result": result}))
        return
    render.render_sync(target, result, applied=apply)
    skipped = result.get("skipped") or []
    if skipped:
        render.console.print(
            f"[yellow]{len(skipped)} entr{'y' if len(skipped)==1 else 'ies'} had no due date "
            f"and were NOT imported.[/] [dim]Check the document: in-class work still needs doing.[/]")


@app.command("crosscheck")
def crosscheck_cmd(
    json_out: bool = typer.Option(False, "--json"),
):
    """Compare Classroom's missing work with Skyward's gradebook."""
    from . import crosscheck as cc

    # Skyward side
    try:
        import sys
        from pathlib import Path
        sp = Path.home() / "skyward"
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
        from skyward import client as sky_client, parse as sky_parse  # type: ignore
    except Exception as e:                       # noqa: BLE001
        render.err_console.print(f"[red]skyward isn't available:[/] {e}")
        raise typer.Exit(3)
    try:
        sc = sky_client.SkywardClient()
        sc.login(sky_client.resolve_credentials())
        classes = sky_parse.parse_gradebook(sc.gradebook())
    except Exception as e:                       # noqa: BLE001
        render.err_console.print(f"[red]skyward:[/] {e}")
        raise typer.Exit(3)

    browser, tab = _open()
    missing = scrape.missing(tab)

    sky_items = [
        cc.Item(source="skyward", title=a.name, course=c.name, due=None,
                detail=a.term)
        for c in classes for a in c.assignments if not a.grade
    ]
    cl_items = [
        cc.Item(source="classroom", title=a.title, course=a.course, due=a.due,
                url=a.url)
        for a in missing
    ]
    result = cc.cross_check(sky_items, cl_items)
    if json_out:
        typer.echo(render.to_json(result.to_dict()))
        return
    render.render_crosscheck(result)


@app.command()
def logout(reset: bool = typer.Option(False, "--reset", help="Delete the profile (signs out)")):
    """Sign out of the classroom profile by deleting it."""
    if not reset:
        render.err_console.print(
            "[dim]This deletes the Chrome profile, which is the only complete way to\n"
            "sign out. Re-run with --reset to do it.[/]")
        raise typer.Exit(1)
    profile_mod.reset_profile()
    render.console.print("[green]classroom profile deleted[/]")


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)

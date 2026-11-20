"""Open Classroom attachments and read their text.

Two hard-won facts:

1. A material's Drive file is often **not** a link in the DOM — it lives in a
   side panel behind a click. The reliable way to find it is to click the
   classwork row and read the `docs.google.com|drive.google.com` links that
   appear.
2. `docs.google.com/document/<id>/export?format=txt` and `/preview` both lead
   to an empty model for view-only files, but
   **`drive.google.com/file/<id>/view` renders the document as real text**
   (that is how a teacher's weekly schedule PDF/Doc becomes readable here).

So: find Drive file ids, then let Drive render them, then take the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .cdp import Tab
from .profile import ClassroomError
from .scrape import BASE, Course, _settle

DRIVE_VIEW = "https://drive.google.com/file/d/{}/view"

JS_DRIVE_LINKS = r"""(() => {
  const out = new Set();
  for (const a of document.querySelectorAll('a[href*="docs.google.com"], a[href*="drive.google.com"]')) {
    out.add(a.getAttribute('href'));
  }
  for (const el of document.querySelectorAll('[data-drive-id], [data-id]')) {
    const id = el.getAttribute('data-drive-id') || el.getAttribute('data-id');
    if (id && /^[\w-]{20,}$/.test(id)) out.add('drive-id:' + id);
  }
  return [...out];
})()"""

JS_DRIVE_TEXT = r"""(() => {
  const text = el => (el.innerText || '').replace(/\u00a0/g, ' ');
  // Drive renders the page inside a viewer; pick the richest text block.
  const blocks = [...document.querySelectorAll('div,span')]
    .map(el => ({ el, n: text(el).length }))
    .filter(x => x.n > 200)
    .sort((a, b) => b.n - a.n);
  const body = blocks.length ? text(blocks[0].el) : text(document.body);
  return {
    url: location.href,
    title: (document.title || '').replace(/\s*-\s*Google (Drive|Docs)$/, '').trim(),
    text: body,
    signed_out: /accounts\.google\.com|myaccount\.google\.com|google\.com\/account/.test(location.href),
  };
})()"""

# Chrome UI noise that appears above the rendered document.
_CHROME_NOISE = re.compile(
    r"^(Move|File|View|Tools|Help|More|Open with Google Docs|Share|Page|/\s*$|"
    r"Printing not yet available|Full screen|Zoom out|Zoom in|Page zoom control|"
    r"Hide file header|Page \d+ of \d+|Skip to main content)\s*$")


@dataclass
class DriveFile:
    file_id: str
    kind: str = ""            # doc / sheet / slide / file / folder
    url: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"file_id": self.file_id, "kind": self.kind, "url": self.url, "name": self.name}


@dataclass
class Attachment:
    """An attachment found on an assignment or classwork row."""

    name: str
    files: list[DriveFile] = field(default_factory=list)
    text: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "files": [f.to_dict() for f in self.files],
                "text": self.text, "error": self.error}


_ID_PATTERNS = [
    (re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})"), "doc"),
    (re.compile(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})"), "sheet"),
    (re.compile(r"docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})"), "slide"),
    (re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]{20,})"), "file"),
    (re.compile(r"drive\.google\.com/drive/folders/([A-Za-z0-9_-]{20,})"), "folder"),
    (re.compile(r"drive\.google\.com/open\?id=([A-Za-z0-9_-]{20,})"), "file"),
]


def parse_drive_links(links: list[str]) -> list[DriveFile]:
    out: list[DriveFile] = []
    seen: set[str] = set()
    for link in links or []:
        if link.startswith("drive-id:"):
            fid = link.split(":", 1)[1]
            if fid not in seen:
                seen.add(fid)
                out.append(DriveFile(file_id=fid, kind="file", url=DRIVE_VIEW.format(fid)))
            continue
        for pattern, kind in _ID_PATTERNS:
            m = pattern.search(link)
            if m and m.group(1) not in seen:
                seen.add(m.group(1))
                out.append(DriveFile(file_id=m.group(1), kind=kind, url=link))
    return out


JS_ROWS = """[...document.querySelectorAll('[jsname="tdoU3e"]')]
                 .map(r => (r.innerText || '').replace(/\\s+/g, ' ').trim())"""


def classwork_rows(tab: Tab) -> list[str]:
    """The text of each classwork row, in page order.

    The list renders after the page's shell, so waiting for the URL alone
    yields zero rows (which fails silently — that bug produced an empty
    parse). Wait for rows, then read.
    """
    tab.wait_for("""document.querySelectorAll('[jsname="tdoU3e"]').length > 0
                    || /No work|Nothing posted/.test(document.body.innerText)""",
                 timeout=20)
    return tab.js(JS_ROWS) or []


def _links_after_click(tab: Tab) -> list[str]:
    return tab.js(JS_DRIVE_LINKS) or []


def open_classwork_item(tab: Tab, course: Course, match: str) -> list[DriveFile]:
    """Click the classwork row matching `match` and return its Drive files."""
    cid = course.href.rstrip("/").split("/")[-1]
    tab.navigate(f"{BASE}/w/{cid}/t/all", wait=2.5)
    _settle(tab, "Classwork")
    rows = classwork_rows(tab)
    hits = [i for i, r in enumerate(rows) if match.strip().lower() in r.lower()]
    if not hits:
        raise ClassroomError(f"no classwork item matching {match!r} in {course.name}")
    before = set(_links_after_click(tab))
    idx = hits[0]
    tab.js("""(() => {
      const rows = [...document.querySelectorAll('[jsname="tdoU3e"]')];
      const row = rows[%d];
      if (row) row.click();
      return true;
    })()""" % idx)
    # the panel loads asynchronously; wait for new drive links or a timeout
    import time
    deadline = time.time() + 15
    files: list[DriveFile] = []
    while time.time() < deadline:
        links = _links_after_click(tab)
        files = parse_drive_links([ln for ln in links if ln not in before])
        # keep re-reading; the panel can add the doc id a beat later
        if files:
            break
        time.sleep(0.6)
    if not files:
        files = parse_drive_links(_links_after_click(tab))
    return files


def read_drive_file(tab: Tab, file_id: str, kind: str = "file") -> tuple[str, str]:
    """Render a Drive file and return (title, text).

    `/view` works for Docs and PDFs alike because Drive's own viewer draws
    the page; other Google types fall back to their native viewers.
    """
    url = DRIVE_VIEW.format(file_id)
    tab.navigate(url, wait=4)
    raw = tab.js(JS_DRIVE_TEXT) or {}
    if raw.get("signed_out"):
        raise ClassroomError("Drive redirected to the sign-in page while reading the file")
    text = clean_viewer_text(raw.get("text") or "")
    if not text:
        raise ClassroomError(
            f"could not read file {file_id}: Drive rendered no text "
            "(it may be image-only or not shared with this account)")
    return (raw.get("title") or ""), text


def clean_viewer_text(text: str) -> str:
    """Drop the Drive viewer chrome and keep the document body."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    kept: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            kept.append("")
            continue
        if _CHROME_NOISE.match(s):
            continue
        if s.startswith("Geometry Honors Schedule") and kept and kept[-1].strip() == "":
            pass
        kept.append(s)
    # collapse leading blank lines and runs of >2 blanks
    out: list[str] = []
    blank = 0
    for ln in kept:
        if ln == "":
            blank += 1
            if blank <= 1 and out:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    return "\n".join(out).strip()


def cid_for(course: Course) -> str:
    return course.href.rstrip("/").split("/")[-1]

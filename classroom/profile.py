"""A Chrome profile that holds exactly one Google account: the school one.

Classroom has an official API, but the school account can't create an OAuth
client, so this drives a browser instead. That means we inherit browser
rules, and Google's account model is the main hazard:

* Sessions live in a profile, and account slots (`/u/0`, `/u/1`, ...) get
  renumbered whenever an account is added or removed.
* A profile with the personal account in it can silently serve the personal
  Classroom, and nothing in the URL says which account answered.

So: a dedicated profile (`~/.config/classroom-chrome-profile`), and every
session starts with an identity check that refuses to continue unless the
signed-in account is the expected school account.

The profile is launched *headed* on the desktop display the first time (a
human signs in, including any 2FA), and can then be driven headless.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass

CHROME = os.environ.get("CLASSROOM_CHROME", "/usr/bin/google-chrome")
DISPLAY = os.environ.get("CLASSROOM_DISPLAY", ":1")
CLASSROOM_URL = "https://classroom.google.com/"
ACCOUNTS_URL = "https://myaccount.google.com/"
SIGNIN_URL = ("https://accounts.google.com/AccountChooser"
              "?continue=https://classroom.google.com/")


class ClassroomError(Exception):
    """Base error."""


class NotSignedIn(ClassroomError):
    """The profile has no Google session, or it expired."""


class WrongAccount(ClassroomError):
    """Signed in as an account that isn't allowed to be used."""


def profile_dir() -> str:
    return os.environ.get("CLASSROOM_PROFILE") or os.path.expanduser(
        "~/.config/classroom-chrome-profile")


def config_path() -> str:
    base = os.environ.get("CLASSROOM_CONFIG_DIR") or os.path.expanduser("~/.config/classroom")
    return os.path.join(base, "config")


def load_config() -> dict[str, str]:
    path = os.environ.get("CLASSROOM_CONFIG") or config_path()
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip().lower()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def expected_account() -> str:
    """Which Google account this profile is allowed to be.

    Configured in ~/.config/classroom/config as `account=...`; anything else
    is refused. This is the guard against the personal-account mix-up.
    """
    return (os.environ.get("CLASSROOM_ACCOUNT")
            or load_config().get("account") or "").strip().lower()


def _port_free(port: int) -> bool:
    """True only if something is actually listening.

    A bind() test reports busy while a previous Chrome's sockets sit in
    TIME_WAIT, which made a just-restarted profile look occupied. Connect
    instead: refused means free.
    """
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return False
        except OSError:
            return True


@dataclass
class Browser:
    port: int
    proc: subprocess.Popen | None = None

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def json(self, path: str, method: str = "GET") -> object:
        req = urllib.request.Request(self.endpoint + path, method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode() or "{}"
        return json.loads(body)

    def tabs(self) -> list[dict]:
        return self.json("/json/list")  # type: ignore[return-value]

    def classroom_tab(self) -> dict | None:
        """The tab the classroom CLI drives: never the user's visible one."""
        for t in self.tabs():
            if t.get("type") == "page" and "classroom-cli" in (t.get("url") or ""):
                return t
        return None

    def tab(self, match: str | None = "classroom.google.com") -> dict | None:
        """A page tab whose URL contains `match` (or the first page tab)."""
        pages = [t for t in self.tabs() if t.get("type") == "page"]
        if match is None:
            return pages[0] if pages else None
        live = [t for t in pages if "__blank" not in (t.get("url") or "")]
        for t in live or pages:
            if match in (t.get("url") or ""):
                return t
        return None

    def open_tab(self, url: str) -> dict:
        # Chrome >=111 requires PUT for /json/new
        url_q = urllib.parse.quote(url, safe="")
        return self.json(f"/json/new?{url_q}", method="PUT")  # type: ignore[return-value]

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def launch(headed: bool = False, port: int | None = None,
           url: str = SIGNIN_URL, wait: float = 8.0) -> Browser:
    """Start Chrome on the classroom profile. Headed = visible on DISPLAY."""
    port = port or int(os.environ.get("CLASSROOM_CDP_PORT", "9351"))
    if not _port_free(port):
        raise ClassroomError(
            f"port {port} is already in use; is a classroom Chrome already running? "
            "close it or set CLASSROOM_CDP_PORT")
    os.makedirs(profile_dir(), exist_ok=True)
    args = [
        CHROME,
        f"--user-data-dir={profile_dir()}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--window-size=1400,900",
    ]
    env = dict(os.environ)
    if headed:
        args.append("--window-position=0,0")
        env["DISPLAY"] = DISPLAY
    else:
        args.extend(["--headless=new", "--ozone-platform=headless"])
    args.append(url)
    log = open(os.path.join("/tmp", "opencode-classroom-chrome.log"), "ab")
    proc = subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)
    browser = Browser(port=port, proc=proc)

    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            browser.json("/json/version")
            return browser
        except Exception:
            time.sleep(0.3)
    raise ClassroomError("Chrome did not open a debugging port; check your display")


def reset_profile() -> None:
    """Delete the profile so the next run starts signed out."""
    path = profile_dir()
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


import urllib.parse  # noqa: E402  (used by Browser.open_tab)

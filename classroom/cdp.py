"""Minimal Chrome DevTools Protocol driver.

Only what classroom needs: open a tab, evaluate JavaScript (awaiting
promises), and wait for the document to settle. Deliberately small — no
Playwright dependency, since the box already drives Chrome this way.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .profile import Browser, ClassroomError


class Tab:
    def __init__(self, browser: Browser, target: dict[str, Any]):
        self.browser = browser
        self.target = target
        self.ws_url = target["webSocketDebuggerUrl"]
        self._ws = None
        self._id = 0

    # ── connection ───────────────────────────────────────────────────────

    def connect(self) -> "Tab":
        try:
            import websocket  # websocket-client
        except ImportError:  # pragma: no cover - env issue, not logic
            raise ClassroomError("classroom needs websocket-client: pip3 install websocket-client")
        self._ws = websocket.create_connection(self.ws_url, timeout=60,
                                               suppress_origin=True)
        return self

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None

    def __enter__(self) -> "Tab":
        return self.connect()

    def __exit__(self, *a) -> None:
        self.close()

    # ── protocol ─────────────────────────────────────────────────────────

    def wait_event(self, method: str, timeout: float = 45.0) -> dict[str, Any] | None:
        """Wait for a CDP event (e.g. Page.loadEventFired), skipping others."""
        if self._ws is None:
            self.connect()
        deadline = time.time() + timeout
        self._ws.settimeout(1.0)
        try:
            while time.time() < deadline:
                try:
                    raw = self._ws.recv()
                except Exception:          # socket timeout: keep waiting
                    continue
                if not raw:
                    continue
                msg = json.loads(raw)
                if msg.get("method") == method:
                    return msg.get("params", {})
        finally:
            self._ws.settimeout(60)
        return None

    def send(self, method: str, **params: Any) -> dict[str, Any]:
        if self._ws is None:
            self.connect()
        self._id += 1
        msg_id = self._id
        self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            raw = self._ws.recv()
            if not raw:
                raise ClassroomError("devtools connection closed")
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise ClassroomError(f"{method} failed: {msg['error'].get('message')}")
                return msg.get("result", {})

    def js(self, expression: str, timeout: float = 40.0) -> Any:
        """Evaluate an expression (promises awaited) and return its JSON value."""
        result = self.send("Runtime.evaluate", expression=expression,
                           awaitPromise=True, returnByValue=True,
                           userGesture=True, timeout=int(timeout * 1000))
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text", "")
            raise ClassroomError(f"page script error: {text.splitlines()[0]}")
        return result.get("result", {}).get("value")

    def url(self) -> str:
        return self.js("location.href")

    def navigate(self, url: str, wait: float = 2.0) -> None:
        """Navigate and wait for the page's own load event.

        Waiting on the event instead of polling `readyState` avoids racing a
        server-side redirect (Google's account pages redirect more than once).
        """
        self.send("Page.enable")
        self.send("Page.navigate", url=url)
        self.wait_event("Page.loadEventFired", timeout=45)
        self.wait_ready(wait)

    def wait_ready(self, extra: float = 1.0, timeout: float = 45.0) -> None:
        """Wait for a real document to finish loading, then settle a moment.

        Chrome reports a fresh target's initial `about:blank`, so checking
        readyState alone returns instantly and the caller then races the
        real navigation (Runtime.evaluate: "Inspected target navigated").
        Require a non-blank URL *and* two consecutive complete reads.
        """
        deadline = time.time() + timeout
        stable = 0
        while time.time() < deadline:
            try:
                url = self.js("location.href") or ""
                state = self.js("document.readyState")
                if url and not url.startswith("about:") and state == "complete":
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
            except ClassroomError:
                stable = 0
            time.sleep(0.35)
        time.sleep(extra)

    def text(self, selector: str = "body") -> str:
        return self.js(
            f"""(() => {{ const el = document.querySelector({json.dumps(selector)});
                 return el ? el.innerText : ''; }})()""") or ""

    def wait_for(self, predicate_js: str, timeout: float = 30.0,
                 interval: float = 0.5) -> bool:
        """Poll a JS snippet that returns truthy until it does (or timeout)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js(f"Boolean({predicate_js})"):
                    return True
            except ClassroomError:
                pass
            time.sleep(interval)
        return False


WORK_TAB_URL = "https://classroom.google.com/h?classroom-cli=1"
# Remembers the automation tab's target id for this process, so repeated
# navigations reuse one tab instead of leaking a new one per call (this
# leaked 50+ tabs during development).
_WORK_TAB_ID: str | None = None


def _find_work_tab(browser: Browser) -> dict | None:
    global _WORK_TAB_ID
    tabs = [t for t in browser.tabs() if t.get("type") == "page"]
    if _WORK_TAB_ID:
        for t in tabs:
            if t.get("id") == _WORK_TAB_ID:
                return t
        _WORK_TAB_ID = None
    for t in tabs:
        if "classroom-cli" in (t.get("url") or ""):
            _WORK_TAB_ID = t.get("id")
            return t
    return None


def work_tab(browser: Browser, url: str = WORK_TAB_URL, settle: float = 3.0) -> Tab:
    """The one tab automation uses; the human's tab is never touched."""
    global _WORK_TAB_ID
    target = _find_work_tab(browser)
    if target is None:
        target = browser.open_tab(WORK_TAB_URL)
        time.sleep(1.0)
        # Chrome may swap the target during first navigation; re-resolve
        target = _find_work_tab(browser) or target
        _WORK_TAB_ID = target.get("id")
    tab = Tab(browser, target).connect()
    tab.navigate(url, wait=settle)
    return tab

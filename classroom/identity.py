"""Who is signed into the classroom profile?

The whole point: Classroom content is per-account, and a profile that also
holds the personal account can quietly answer as the wrong person. Before
reading anything, ask the browser who it is and compare with the configured
school account.

Signed out, Google redirects `myaccount.google.com` to an informational
page (`google.com/account/about`) — so "is it signed in" is a URL test, not
a DOM scrape, which keeps it stable across Google's redesigns.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cdp import Tab
from .profile import ACCOUNTS_URL, ClassroomError, NotSignedIn, WrongAccount, expected_account

# Reads the signed-in address from the account chooser (the avatar menu), which
# works across account pages; falls back to the page's own email display.
_PROBE_JS = r"""
(() => {
  const out = new Set();
  const add = s => {
    if (!s) return;
    for (const m of String(s).matchAll(/[^@\s()<>]+@[^@\s()<>]+\.[a-z]{2,}/gi)) {
      out.add(m[0].toLowerCase());
    }
  };
  // Read-only: the avatar's aria-label is
  //   "Google Account: Name  \n(email@example.com)"
  // Clicking it would trigger a client-side navigation mid-evaluation.
  for (const el of document.querySelectorAll('[aria-label], [data-email], [title]')) {
    add(el.getAttribute('aria-label'));
    add(el.getAttribute('data-email'));
    add(el.getAttribute('title'));
  }
  for (const el of document.querySelectorAll('span, div, a')) {
    if (el.children.length === 0) add(el.textContent);
  }
  return [...out];
})()
"""


@dataclass
class Identity:
    email: str
    signed_in: bool

    def __bool__(self) -> bool:
        return bool(self.email)


def detect(tab: Tab, settle: float = 2.5) -> Identity:
    """Return the signed-in account, or raise if signed out / ambiguous."""
    if "accounts.google.com" in (tab.url() or ""):
        raise NotSignedIn("the classroom profile is not signed in")
    tab.navigate(ACCOUNTS_URL, wait=settle)
    url = tab.url() or ""
    if "myaccount.google.com" not in url and "accounts.google.com" not in url:
        raise NotSignedIn(f"signed out (Google redirected to {url.split('/')[2]})")
    if "accounts.google.com" in url:
        raise NotSignedIn("the classroom profile is not signed in")

    candidates = tab.js(_PROBE_JS) or []
    emails = sorted({c.lower() for c in candidates if isinstance(c, str) and "@" in c})
    if not emails:
        raise ClassroomError(
            "signed in, but could not read the account address from the page; "
            "run `classroom whoami` and paste the email into ~/.config/classroom/config")
    return Identity(email=emails[0], signed_in=True)


def verify(tab: Tab, allowed: str | None = None) -> Identity:
    """Detect the identity and enforce that it's the school account."""
    allowed = (allowed or expected_account() or "").strip().lower()
    ident = detect(tab)
    if allowed and ident.email != allowed:
        raise WrongAccount(
            f"the classroom profile is signed in as {ident.email}, but only "
            f"{allowed} is allowed. Sign out of the other account, or change "
            f"`account=` in ~/.config/classroom/config")
    if not allowed:
        raise ClassroomError(
            f"signed in as {ident.email} but no account is configured; add\n"
            f"    account={ident.email}\n"
            f"to ~/.config/classroom/config to lock this profile to it")
    return ident

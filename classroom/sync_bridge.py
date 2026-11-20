"""Bridge: classroom's parsers -> msl's sync planner.

Lives here (not in msl) so msl stays a self-contained MyStudyLife client, and
classroom owns the conversion from its own document formats. msl is optional:
if it isn't importable, `available()` is False and the CLI explains how to
install it.
"""

from __future__ import annotations

from typing import Any

try:                                    # msl is a sibling checkout
    import sys
    from pathlib import Path

    _sibling = Path.home() / "msl"
    if _sibling.is_dir() and str(_sibling) not in sys.path:
        sys.path.insert(0, str(_sibling))
    from msl import sync as msl_sync       # type: ignore
    from msl.client import MSLClient, MSLError  # type: ignore
    AVAILABLE = True
except Exception:                        # noqa: BLE001 - optional dependency
    msl_sync = None                      # type: ignore
    MSLClient = None                     # type: ignore
    MSLError = Exception                 # type: ignore
    AVAILABLE = False


class BridgeError(Exception):
    pass


def available() -> bool:
    return AVAILABLE


def candidates(weeks: list[Any], course: str) -> list[Any]:
    """Flatten parsed classroom weeks into msl sync candidates."""
    if not AVAILABLE:
        raise BridgeError("the msl package isn't importable (expected ~/msl/msl)")
    out: list[Any] = []
    for week in weeks:
        out.extend(msl_sync.candidates_from_week(week, course))
    # a week can repeat a title (e.g. a project carried over); keep the
    # earliest due date for a given key so a re-listing doesn't fight itself
    best: dict[str, Any] = {}
    for c in out:
        cur = best.get(c.key)
        if cur is None or (c.due and (cur.due is None or c.due < cur.due)):
            best[c.key] = c
    return list(best.values())


def connect_msl():
    if not AVAILABLE:
        raise BridgeError("the msl package isn't importable (expected ~/msl/msl)")
    client = MSLClient()
    try:
        client.ensure_session()
    except MSLError as e:
        raise BridgeError(f"msl login failed: {e}")
    return client


def plan_and_apply(cands: list[Any], *, dry_run: bool = True) -> dict[str, Any]:
    """Compare against MSL and (optionally) write."""
    client = connect_msl()
    existing = client.tasks()
    plan = msl_sync.build_plan(cands, existing)
    result = msl_sync.apply_plan(client, plan, dry_run=dry_run)
    result["skipped"] = [c.to_dict() for c in plan.skipped]
    result["unchanged"] = len(plan.unchanged)
    return result

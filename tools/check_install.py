"""Report the Harvey that Python actually loads, not the one on disk.

An editable install, a stale virtualenv or a second checkout all produce the
same symptom: a fix is in the repository and the running code does not have
it. This prints the imported module's own path and probes for the behaviour
rather than trusting a file listing.

    HARVEY_HOME=~/.harvey-massena python tools/check_install.py
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys


def line(label: str, value: object) -> None:
    print(f"  {label:<34} {value}")


async def main() -> int:
    import harvey
    import harvey.agents.scout as scout_module
    import harvey.state as state_module
    from harvey import paths
    from harvey.state import StateManager

    print("\n  Harvey — what is actually loaded")
    print("  " + "=" * 56)
    line("python", sys.executable)
    line("harvey package", harvey.__file__)
    try:
        sha = subprocess.run(
            ["git", "-C", str(paths.PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        sha = "(unknown)"
    line("checkout commit", sha or "(not a git checkout)")

    ok = True

    marker = getattr(state_module, "DO_NOT_PROSPECT", None)
    line("DO_NOT_PROSPECT", marker or "MISSING")
    ok &= marker is not None

    source = inspect.getsource(scout_module.Scout.run)
    try:
        ordered = source.index('"company_discovery"') < source.index('"profile_search"')
    except ValueError:
        ordered = False
    line("discovery before profile search", "yes" if ordered else "NO")
    ok &= ordered

    backfill = inspect.getsource(StateManager.companies_needing_prospects)
    honours = "DO_NOT_PROSPECT" in backfill
    line("backfill honours the marker", "yes" if honours else "NO")
    ok &= honours

    print("\n  This profile")
    print("  " + "=" * 56)
    for key, value in paths.describe().items():
        line(key.replace("_", " "), value)

    state = StateManager()
    await state.init_db()
    paused = await state.get_setting("sending_paused")
    line("sending", f"PAUSED ({paused})" if paused else "ACTIVE — not a dry run")
    ok &= bool(paused)

    if marker:
        excluded = await state.cohort(require=[marker], limit=5000)
        remaining = await state.companies_needing_prospects(limit=5000)
        leaking = [c for c in remaining if c["id"] in set(excluded)]
        line("companies marked do-not-prospect", len(excluded))
        line("still prospectable", len(remaining))
        line("marked but still prospectable", f"{len(leaking)} (must be 0)")
        ok &= not leaking

    print("\n  " + ("ALL CHECKS PASS" if ok else "SOMETHING IS STALE — see the NO lines above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Canonical filesystem locations.

Harvey started out repo-centric: config, knowledge and state all lived next to
the package, so the ``harvey`` command only behaved correctly when run from
inside the checkout. That is fine for a clone you ``cd`` into, and wrong for a
tool installed once and used from every directory on the machine.

So there are now two roots:

``PROJECT_ROOT``
    Where the package itself lives. Still the home of the files that SHIP with
    Harvey — ``prompts/`` and ``skills/`` — and still a valid place to keep a
    per-checkout config or database.

``home()``
    A per-user directory, ``$HARVEY_HOME`` or ``~/.harvey``, holding the
    config, ``.env``, database and logs of a normal install. This is what makes
    ``harvey status`` answer the same thing from ``/`` as from the checkout.

``resolve()`` on PROJECT_ROOT matters — when the package is reached through a
symlink (some editable installs), a bare ``Path(__file__).parent`` would point
into site-packages and Harvey would silently read/write the wrong files.

Lookups prefer, in order: the current directory (a checkout you are standing
in), then the portable home, then PROJECT_ROOT. An existing database is never
orphaned: if a checkout already holds ``data/harvey.db``, that file keeps being
used, so upgrading an old install does not appear to lose every prospect.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV_HOME = "HARVEY_HOME"
ENV_DB = "HARVEY_DB"

DEFAULT_HOME_NAME = ".harvey"


def home() -> Path:
    """The per-user directory holding config, env, database and logs."""
    raw = os.environ.get(ENV_HOME, "").strip()
    base = Path(raw).expanduser() if raw else Path.home() / DEFAULT_HOME_NAME
    return base.resolve()


def ensure_home() -> Path:
    root = home()
    for path in (root, root / "data", root / "logs"):
        path.mkdir(parents=True, exist_ok=True)
    return root


def _checkout_db() -> Path:
    return PROJECT_ROOT / "data" / "harvey.db"


def data_dir() -> Path:
    """Where the database and other local state live.

    A checkout that already carries a database keeps it — moving an existing
    install's data out from under it would look exactly like data loss.
    """
    if _checkout_db().exists():
        return PROJECT_ROOT / "data"
    return home() / "data"


def state_db() -> Path:
    """The database path, honouring an explicit ``HARVEY_DB`` override."""
    override = os.environ.get(ENV_DB, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return data_dir() / "harvey.db"


def _first_existing(*candidates: Path) -> Path | None:
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def env_file() -> Path:
    """The ``.env`` to load. Falls back to the home copy for a global install."""
    found = _first_existing(
        Path.cwd() / ".env",
        home() / ".env",
        PROJECT_ROOT / ".env",
    )
    return found or home() / ".env"


def env_files() -> list[Path]:
    """Every ``.env`` worth loading, least specific first.

    The home copy holds the credentials shared by every directory; a checkout's
    own ``.env`` then overrides it for that project.
    """
    ordered = [home() / ".env", PROJECT_ROOT / ".env", Path.cwd() / ".env"]
    seen: list[Path] = []
    for path in ordered:
        if path.is_file() and path not in seen:
            seen.append(path)
    return seen


def config_candidates() -> list[Path]:
    """Config files to try, most specific first.

    ``harvey.local.yaml`` wins over ``harvey.yaml`` at each level: it is
    gitignored, so a fork can carry a real product configuration while the
    tracked template stays untrained.
    """
    cwd = Path.cwd()
    return [
        cwd / "harvey.local.yaml",
        home() / "harvey.local.yaml",
        PROJECT_ROOT / "harvey.local.yaml",
        cwd / "harvey.yaml",
        cwd.parent / "harvey.yaml",
        home() / "harvey.yaml",
        PROJECT_ROOT / "harvey.yaml",
    ]


def config_write_path() -> Path:
    """Where a newly written config belongs.

    An existing config is rewritten in place; otherwise a config written from
    inside the checkout stays there, and one written from anywhere else goes to
    the portable home.
    """
    existing = _first_existing(*config_candidates())
    if existing is not None:
        return existing
    if Path.cwd() == PROJECT_ROOT:
        return PROJECT_ROOT / "harvey.yaml"
    return home() / "harvey.yaml"


def knowledge_dir(kind: str) -> Path:
    """``prompts`` or ``skills``.

    A checkout's copy wins so editing the repo's markdown takes effect at once;
    a per-user override in the home comes next; the shipped copy is the floor.
    """
    for base in (Path.cwd(), home()):
        candidate = base / kind
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT / kind


def log_file() -> Path:
    return home() / "logs" / "harvey.log"


def describe() -> dict[str, str]:
    """Resolved locations, for ``harvey status`` and debugging."""
    return {
        "project_root": str(PROJECT_ROOT),
        "home": str(home()),
        "data_dir": str(data_dir()),
        "database": str(state_db()),
        "env_file": str(env_file()),
        "config": str(_first_existing(*config_candidates()) or "(none found)"),
        "prompts": str(knowledge_dir("prompts")),
        "skills": str(knowledge_dir("skills")),
    }

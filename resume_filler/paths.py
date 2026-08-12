"""Where configuration lives.

Everything used to be resolved against the working directory, which is fine
when the tool is run from its own checkout and useless once it is a standalone
executable someone runs from anywhere. Config would silently be looked for in
whatever folder they happened to be in.

Resolution is therefore: the current directory first, so an existing checkout
keeps behaving exactly as before, then a per-user directory that does not move.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "resume-filler"


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than a checkout."""
    return getattr(sys, "frozen", False)


def user_config_dir() -> Path:
    """The per-user directory for config, following each platform's convention."""
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_NAME


def find_config_file(name: str) -> Path | None:
    """Locate a config file, preferring one beside the user in the current folder.

    Returns None when there is none anywhere, which callers treat as "not
    configured yet" rather than an error.
    """
    local = Path.cwd() / name
    if local.is_file():
        return local
    stored = user_config_dir() / name
    return stored if stored.is_file() else None


def default_config_dir() -> Path:
    """Where ``init`` should write.

    A checkout keeps its config beside the code, which is what someone working
    on the tool expects. A standalone executable has no meaningful "beside the
    code", so it uses the per-user directory and works from anywhere.
    """
    return user_config_dir() if is_frozen() else Path.cwd()


def resolve_data_path(value: str | Path, config_dir: Path | None = None) -> Path:
    """Turn a configured path into an absolute one.

    A relative path in .env means "next to the config that named it", not
    "wherever this happens to be run from". Without that, a database or output
    directory would scatter itself across every folder the executable is
    invoked from.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    base = config_dir or default_config_dir()
    return base / path

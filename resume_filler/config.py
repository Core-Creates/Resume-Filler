"""Configuration loaded from the environment.

Credentials are never stored in source. They come from a .env file that is
gitignored, or from real environment variables in a CI or container context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .field_map import DEFAULT_CONFIDENCE_THRESHOLD
from .paths import default_config_dir, find_config_file, resolve_data_path


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Runtime settings for a single run."""

    resume_path: Path = field(default_factory=lambda: Path("resume.pdf"))
    cover_letter_path: Path | None = None
    browser: str = "chrome"
    headless: bool = False
    page_timeout: float = 15.0
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    profile_path: Path = field(default_factory=lambda: Path("profile.json"))
    session_dir: Path | None = None
    """Where the browser keeps cookies, so a login survives between runs."""
    database_path: Path = field(default_factory=lambda: Path("applications.db"))
    output_dir: Path = field(default_factory=lambda: Path("runs"))

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        """Build settings from a .env file plus the process environment.

        The .env is looked for in the current directory first, so a checkout
        behaves as it always did, then in the per-user directory, so a
        standalone executable finds its config wherever it is run from.
        """
        found = Path(env_file).expanduser() if env_file else find_config_file(".env")
        if found and found.is_file():
            load_dotenv(found, override=False)
            config_dir = found.parent
        else:
            load_dotenv(override=False)
            config_dir = default_config_dir()

        cover_letter = os.getenv("COVER_LETTER_PATH", "").strip()
        profile_default = find_config_file("profile.json") or (config_dir / "profile.json")
        return cls(
            resume_path=Path(os.getenv("RESUME_PATH", "resume.pdf")).expanduser(),
            cover_letter_path=Path(cover_letter).expanduser() if cover_letter else None,
            browser=os.getenv("BROWSER", "chrome").strip().lower(),
            headless=_env_bool("HEADLESS", False),
            page_timeout=_env_float("PAGE_TIMEOUT", 15.0),
            confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD),
            profile_path=Path(profile_raw).expanduser()
            if (profile_raw := os.getenv("PROFILE_PATH", "").strip())
            else profile_default,
            session_dir=Path(session_raw).expanduser()
            if (session_raw := os.getenv("SESSION_DIR", "").strip())
            else None,
            # Relative paths belong beside the config that named them, not in
            # whatever directory the executable happened to be launched from.
            database_path=resolve_data_path(
                os.getenv("DATABASE_PATH", "applications.db"), config_dir
            ),
            output_dir=resolve_data_path(os.getenv("OUTPUT_DIR", "runs"), config_dir),
        )

    def validate_for_browsing(self) -> list[str]:
        """Return a list of problems that would stop a browser run."""
        problems: list[str] = []
        if not self.resume_path.is_file():
            problems.append(f"Resume not found at {self.resume_path}")
        if self.cover_letter_path and not self.cover_letter_path.is_file():
            problems.append(f"Cover letter not found at {self.cover_letter_path}")
        if self.browser not in {"chrome", "edge", "firefox"}:
            problems.append(f"Unsupported browser {self.browser!r}. Use chrome, edge or firefox.")
        if not 0.0 < self.confidence_threshold <= 1.0:
            problems.append("CONFIDENCE_THRESHOLD must be between 0 and 1.")
        return problems

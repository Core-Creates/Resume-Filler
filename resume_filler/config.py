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

    username: str = ""
    password: str = ""
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
        """Build settings from a .env file plus the process environment."""
        if env_file:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        cover_letter = os.getenv("COVER_LETTER_PATH", "").strip()
        return cls(
            username=os.getenv("JOB_SITE_USERNAME", ""),
            password=os.getenv("JOB_SITE_PASSWORD", ""),
            resume_path=Path(os.getenv("RESUME_PATH", "resume.pdf")).expanduser(),
            cover_letter_path=Path(cover_letter).expanduser() if cover_letter else None,
            browser=os.getenv("BROWSER", "chrome").strip().lower(),
            headless=_env_bool("HEADLESS", False),
            page_timeout=_env_float("PAGE_TIMEOUT", 15.0),
            confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD),
            profile_path=Path(os.getenv("PROFILE_PATH", "profile.json")).expanduser(),
            session_dir=Path(session_raw).expanduser()
            if (session_raw := os.getenv("SESSION_DIR", "").strip())
            else None,
            database_path=Path(os.getenv("DATABASE_PATH", "applications.db")).expanduser(),
            output_dir=Path(os.getenv("OUTPUT_DIR", "runs")).expanduser(),
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

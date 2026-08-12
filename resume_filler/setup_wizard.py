"""First run setup.

The tool was unusable until you had read the README, copied two example files
and understood what RESUME_PATH meant. That is a wall in front of the first
useful thing it does, so this removes it: find the resume, confirm it parses,
write the config, and say what to run next.

Nothing here overwrites an existing file without being told to, and every
prompt has a safe default so pressing Enter throughout is a reasonable outcome.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from .models import ResumeData

logger = logging.getLogger(__name__)

# Where people keep resumes. Ordered so the most likely wins ties.
SEARCH_DIRECTORIES = (
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "OneDrive" / "Documents",
)

RESUME_HINTS = ("resume", "cv", "curriculum")
MAX_CANDIDATES = 12


def find_resumes(directories: tuple[Path, ...] = SEARCH_DIRECTORIES) -> list[Path]:
    """Look for resume-shaped PDFs, most recently modified first."""
    found: dict[Path, float] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            for path in directory.glob("*.pdf"):
                if any(hint in path.name.lower() for hint in RESUME_HINTS):
                    found[path.resolve()] = path.stat().st_mtime
        except OSError:
            logger.debug("Could not read %s", directory, exc_info=True)
    return [p for p, _ in sorted(found.items(), key=lambda item: -item[1])][:MAX_CANDIDATES]


def render_env(resume_path: Path, session_dir: Path) -> str:
    """The .env file, written for the choices just made."""
    return f"""# Written by "resume-filler init". Safe to edit by hand.
# The tool never asks for or stores your password; sign in with the login
# command instead, which keeps a browser session in SESSION_DIR.

RESUME_PATH={resume_path}

# Where a browser login is kept. Treat it as a password.
SESSION_DIR={session_dir}

# Answers your resume does not contain.
PROFILE_PATH=profile.json

# Browser to drive: chrome, edge or firefox.
BROWSER=chrome

# Keep this false so you can watch what happens.
HEADLESS=false

# Seconds to wait for elements before giving up.
PAGE_TIMEOUT=15

# Minimum match confidence, 0 to 1, before a field is filled.
CONFIDENCE_THRESHOLD=0.55

# Where the application tracker and run reports are written.
DATABASE_PATH=applications.db
OUTPUT_DIR=runs
"""


def suggested_profile(resume: ResumeData | None) -> dict[str, str]:
    """Pre-fill what the resume already told us, so there is less to type."""
    values = {
        "address_line1": "",
        "city": "",
        "state": "",
        "postal_code": "",
        "country": "",
        "work_authorization": "",
        "how_did_you_hear": "",
    }
    if resume:
        values["city"] = resume.city
        values["state"] = resume.state
        values["postal_code"] = resume.postal_code
        values["country"] = resume.country
    return values


def render_profile(values: dict[str, str]) -> str:
    """A profile file that keeps the unanswered keys visible as reminders."""
    body = {
        "_comment": (
            "Answers your resume does not contain. Keys match the MAPPED TO column "
            "in a fill plan, so a reported gap names the key that would fix it. "
            "Blank values are ignored, so leave anything you would rather answer "
            "by hand."
        ),
        **values,
    }
    return json.dumps(body, indent=2) + "\n"


class Prompter:
    """Asks questions, or takes the default when nobody is there to answer.

    Keeping this separate is what lets the wizard be tested without a terminal,
    and lets it run unattended in a script without hanging on input.
    """

    def __init__(self, interactive: bool | None = None) -> None:
        if interactive is None:
            interactive = sys.stdin is not None and sys.stdin.isatty()
        self.interactive = interactive

    def ask(self, question: str, default: str = "") -> str:
        if not self.interactive:
            return default
        shown = f" [{default}]" if default else ""
        try:
            answer = input(f"{question}{shown}: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        if not self.interactive:
            return default
        suffix = "Y/n" if default else "y/N"
        try:
            answer = input(f"{question} [{suffix}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return default
        if not answer:
            return default
        return answer.startswith("y")

    def choose(self, question: str, options: list[str], default: int = 0) -> int:
        if not options:
            return -1
        if not self.interactive or len(options) == 1:
            return default
        print(f"\n{question}")
        for index, option in enumerate(options, start=1):
            print(f"  {index}. {option}")
        raw = self.ask("Choose a number", str(default + 1))
        try:
            chosen = int(raw) - 1
        except ValueError:
            return default
        return chosen if 0 <= chosen < len(options) else default

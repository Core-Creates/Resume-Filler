"""Answers a resume cannot supply.

Every application form asks for things no resume contains: a street address, a
work authorisation declaration, a salary expectation, where you heard about the
role. Those came back as gaps on every real form tested, and a street address in
particular is required nearly everywhere, so the form could never be completed
from the resume alone.

The profile is a small JSON file keyed by the same canonical names the fill plan
prints in its "MAPPED TO" column, so reading a plan tells you exactly which key
to add.

Values here may answer questions the engine otherwise refuses to touch. That is
deliberate and is not a hole in the policy. The policy exists because the tool
must not invent an answer to a legal declaration or a negotiation; a value the
applicant wrote into their own profile is their answer, already considered. The
plan labels those so it stays visible that they came from here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "profile.json"

# Keys accepted in the file. Anything else is reported rather than ignored
# silently, because a typo would otherwise look like the profile had no effect.
KNOWN_KEYS = frozenset(
    {
        "address_line1",
        "city",
        "state",
        "postal_code",
        "country",
        "portfolio_url",
        "linkedin_url",
        "github_url",
        "current_company",
        "current_title",
        "years_experience",
        "work_authorization",
        "sponsorship",
        "desired_salary",
        "how_did_you_hear",
        "school",
        "degree",
        "major",
        "graduation_year",
    }
)


class Profile:
    """Supplementary answers, keyed by canonical field name."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = {}
        for key, value in (values or {}).items():
            text = "" if value is None else str(value).strip()
            if text:
                self.values[key.strip()] = text

    def __bool__(self) -> bool:
        return bool(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def get(self, canonical: str) -> str:
        return self.values.get(canonical, "")

    def unknown_keys(self) -> list[str]:
        return sorted(set(self.values) - KNOWN_KEYS)


def load_profile(path: str | Path | None = None) -> Profile:
    """Read the profile file, returning an empty profile when there is none.

    A missing file is normal and not an error. A malformed one is an error worth
    raising, because silently continuing would leave the applicant wondering why
    their answers never appeared.
    """
    file_path = Path(path).expanduser() if path else Path(DEFAULT_PROFILE_NAME)
    if not file_path.is_file():
        logger.debug("No profile file at %s", file_path)
        return Profile()

    try:
        raw: Any = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file_path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"{file_path} must contain a JSON object of field names to values.")

    profile = Profile(raw)
    logger.info("Loaded %d profile value(s) from %s", len(profile), file_path)
    unknown = profile.unknown_keys()
    if unknown:
        logger.warning(
            "Profile keys not recognised and therefore unused: %s. "
            "Keys must match the names shown in the plan's MAPPED TO column.",
            ", ".join(unknown),
        )
    return profile

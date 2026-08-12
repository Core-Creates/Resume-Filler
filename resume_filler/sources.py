"""Where job postings come from.

The original code scraped LinkedIn's search results by CSS class. That approach
is brittle by construction, since those class names are generated and rotate,
and driving LinkedIn with a bot conflicts with their user agreement.

This module prefers documented, public job board APIs. Greenhouse and Lever both
publish read only JSON endpoints for a company's open roles, which are stable,
fast, and do not require a login. A plain list of URLs is also supported for
everything else.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import requests

from .models import JobPosting

logger = logging.getLogger(__name__)

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{company}"
REQUEST_TIMEOUT = 20
USER_AGENT = "Resume-Filler/2.0 (+https://github.com/Core-Creates/Resume-Filler)"


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def from_urls_file(path: str | Path) -> list[JobPosting]:
    """Read one application URL per line. Blank lines and # comments are ignored."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise FileNotFoundError(f"URL list not found: {file_path}")

    postings: list[JobPosting] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        postings.append(JobPosting(url=url, source="url-list"))
    logger.info("Loaded %d posting(s) from %s", len(postings), file_path)
    return postings


def from_csv(path: str | Path) -> list[JobPosting]:
    """Read postings from a CSV with at least a ``url`` column.

    Optional columns: ``title``, ``company``, ``location``.
    """
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise FileNotFoundError(f"CSV not found: {file_path}")

    postings: list[JobPosting] = []
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "url" not in {f.lower() for f in reader.fieldnames}:
            raise ValueError(f"{file_path} must have a 'url' column")
        for row in reader:
            lowered = {(k or "").lower(): (v or "").strip() for k, v in row.items()}
            if not lowered.get("url"):
                continue
            postings.append(
                JobPosting(
                    url=lowered["url"],
                    title=lowered.get("title", ""),
                    company=lowered.get("company", ""),
                    location=lowered.get("location", ""),
                    source="csv",
                )
            )
    logger.info("Loaded %d posting(s) from %s", len(postings), file_path)
    return postings


def from_greenhouse(board_token: str) -> list[JobPosting]:
    """Fetch open roles from a company's public Greenhouse board."""
    url = GREENHOUSE_API.format(board=board_token)
    with _session() as session:
        response = session.get(url, params={"content": "true"}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

    postings = [
        JobPosting(
            title=job.get("title", ""),
            company=board_token,
            location=(job.get("location") or {}).get("name", ""),
            url=job.get("absolute_url", ""),
            source="greenhouse",
            description=job.get("content", "") or "",
        )
        for job in payload.get("jobs", [])
        if job.get("absolute_url")
    ]
    logger.info("Greenhouse board %r returned %d posting(s)", board_token, len(postings))
    return postings


def from_lever(company: str) -> list[JobPosting]:
    """Fetch open roles from a company's public Lever board."""
    url = LEVER_API.format(company=company)
    with _session() as session:
        response = session.get(url, params={"mode": "json"}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()

    postings = [
        JobPosting(
            title=job.get("text", ""),
            company=company,
            location=(job.get("categories") or {}).get("location", ""),
            url=job.get("applyUrl") or job.get("hostedUrl", ""),
            source="lever",
            description=job.get("descriptionPlain", "") or "",
        )
        for job in payload
        if job.get("applyUrl") or job.get("hostedUrl")
    ]
    logger.info("Lever board %r returned %d posting(s)", company, len(postings))
    return postings


def filter_postings(
    postings: list[JobPosting],
    *,
    keywords: list[str] | None = None,
    location: str = "",
) -> list[JobPosting]:
    """Narrow a posting list by title keywords and location substring."""
    result = postings
    if keywords:
        lowered = [k.strip().lower() for k in keywords if k.strip()]
        result = [
            p for p in result if any(k in f"{p.title} {p.description}".lower() for k in lowered)
        ]
    if location:
        needle = location.strip().lower()
        result = [p for p in result if needle in p.location.lower()]
    return result

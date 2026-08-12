"""Human readable and machine readable run output."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .models import ApplicationResult, FieldMatch, FillStatus, ResumeData

logger = logging.getLogger(__name__)

STATUS_LABELS: dict[FillStatus, str] = {
    FillStatus.FILLED: "[FILL]",
    FillStatus.SKIPPED_NO_MATCH: "[----]",
    FillStatus.SKIPPED_LOW_CONFIDENCE: "[LOW ]",
    FillStatus.SKIPPED_NO_VALUE: "[GAP ]",
    FillStatus.SKIPPED_BY_POLICY: "[YOU ]",
    FillStatus.FAILED: "[FAIL]",
}


def _truncate(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "."


def render_resume_summary(resume: ResumeData) -> str:
    """A short block confirming what was parsed, so mistakes are caught early."""
    education = resume.latest_education
    lines = [
        "Parsed resume",
        "-" * 60,
        f"  Name      : {resume.full_name or '(not found)'}",
        f"  Email     : {resume.email or '(not found)'}",
        f"  Phone     : {resume.phone or '(not found)'}",
        f"  Location  : {', '.join(p for p in (resume.city, resume.state) if p) or '(not found)'}",
        f"  LinkedIn  : {resume.linkedin_url or '(not found)'}",
        f"  GitHub    : {resume.github_url or '(not found)'}",
        f"  Current   : {resume.current_title or '(not found)'}"
        f"{' at ' + resume.current_company if resume.current_company else ''}",
        f"  Education : {education.degree + ' ' + education.school if education else '(not found)'}",
        f"  Positions : {len(resume.positions)}",
        f"  Skills    : {len(resume.skills)}",
    ]
    return "\n".join(lines)


def render_plan(matches: list[FieldMatch]) -> str:
    """A table of every control on the form and what the engine decided."""
    if not matches:
        return "No fillable controls were found on this page."

    header = f"  {'':6} {'FIELD':<34} {'MAPPED TO':<20} {'CONF':>5}  VALUE"
    lines = [header, "  " + "-" * 92]

    for match in matches:
        label = STATUS_LABELS.get(match.status, "[?   ]")
        required = "*" if match.form_field.required else " "
        if match.status is FillStatus.FILLED:
            value = match.value
        elif match.status is FillStatus.SKIPPED_NO_MATCH:
            value = ""
        else:
            value = match.reason
        lines.append(
            f"  {label} {required}{_truncate(match.form_field.describe(), 33):<33} "
            f"{_truncate(match.canonical or '-', 20):<20} "
            f"{match.confidence:>5.2f}  {_truncate(value, 40)}"
        )

    lines.append("")
    lines.append(
        "  [FILL] ready   [YOU ] your call   [GAP ] missing from resume   "
        "[----] unrecognised   * required"
    )
    return "\n".join(lines)


def render_result(result: ApplicationResult) -> str:
    posting = result.posting
    title = posting.title or posting.url
    heading = f"{title}" + (f" at {posting.company}" if posting.company else "")
    return "\n".join(
        [
            "",
            "=" * 96,
            heading,
            posting.url,
            "=" * 96,
            render_plan(result.matches),
            "",
            f"  Status: {result.status.value}. {result.message}",
        ]
    )


def write_json_report(
    results: list[ApplicationResult],
    output_dir: str | Path,
    resume: ResumeData | None = None,
) -> Path:
    """Persist the full run for later inspection or diffing."""
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"run-{stamp}.json"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resume": {key: value for key, value in asdict(resume).items() if key != "raw_text"}
        if resume
        else None,
        "applications": [
            {
                "posting": asdict(result.posting),
                "status": result.status.value,
                "message": result.message,
                "filled": result.filled_count,
                "needs_review": result.review_count,
                "fields": [
                    {
                        "field": match.form_field.describe(),
                        "name": match.form_field.name,
                        "required": match.form_field.required,
                        "canonical": match.canonical,
                        "confidence": match.confidence,
                        "status": match.status.value,
                        "value": match.value,
                        "reason": match.reason,
                    }
                    for match in result.matches
                ],
            }
            for result in results
        ],
    }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote run report to %s", path)
    return path

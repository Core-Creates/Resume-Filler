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
    FillStatus.NOT_APPLICABLE: "[ n/a]",
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
        f"  Education : "
        f"{education.degree_display + ' ' + education.school if education else '(not found)'}",
        f"  Positions : {len(resume.positions)}",
        f"  Skills    : {len(resume.skills)}",
    ]
    return "\n".join(lines)


def _collapse_empty_group_rows(matches: list[FieldMatch]) -> tuple[list[FieldMatch], list[str]]:
    """Fold repeating rows that received nothing into a one-line note.

    Workday renders ten blank work-history rows regardless of how many jobs you
    have. Listing every field of every unused row buries the real output under
    forty lines of noise, so an entirely empty row is summarised instead.
    """
    empty_rows: dict[str, list[int]] = {}
    for match in matches:
        field = match.form_field
        if not field.is_grouped:
            continue
        key = (field.group, field.group_index)
        rows = [m for m in matches if (m.form_field.group, m.form_field.group_index) == key]
        if all(m.status is not FillStatus.FILLED for m in rows):
            empty_rows.setdefault(field.group, [])
            if field.group_index not in empty_rows[field.group]:
                empty_rows[field.group].append(field.group_index)

    if not empty_rows:
        return matches, []

    hidden = {(g, i) for g, rows in empty_rows.items() for i in rows}
    kept = [
        m
        for m in matches
        if not (
            m.form_field.is_grouped and (m.form_field.group, m.form_field.group_index) in hidden
        )
    ]
    notes = [
        f"  {len(rows)} unused {group} row(s) on the form had no matching resume entry."
        for group, rows in sorted(empty_rows.items())
    ]
    return kept, notes


def render_summary(matches: list[FieldMatch]) -> str:
    """One line telling you whether this form is nearly done or barely started.

    Scanning a hundred rows to work that out is the tool making you do the work
    it was supposed to save.
    """
    ready = sum(1 for m in matches if m.status is FillStatus.FILLED)
    yours = sum(1 for m in matches if m.status is FillStatus.SKIPPED_BY_POLICY)
    gaps = sum(1 for m in matches if m.status is FillStatus.SKIPPED_NO_VALUE)
    failed = sum(1 for m in matches if m.status is FillStatus.FAILED)
    unknown = sum(1 for m in matches if m.status is FillStatus.SKIPPED_NO_MATCH)

    parts = [f"{ready} ready"]
    if yours:
        parts.append(f"{yours} your call")
    if gaps:
        parts.append(f"{gaps} missing")
    if failed:
        parts.append(f"{failed} failed")
    if unknown:
        parts.append(f"{unknown} unrecognised")
    return "  " + "   ".join(parts)


def render_next_steps(matches: list[FieldMatch]) -> str:
    """The short list of what actually needs the applicant.

    Everything else in the plan is either done or nothing they can act on.
    Unused repeating rows are dropped first: a blank Workday work-history row is
    not thirty-six things the applicant forgot.
    """
    matches, _ = _collapse_empty_group_rows(matches)
    yours = [m for m in matches if m.status is FillStatus.SKIPPED_BY_POLICY]
    gaps = [m for m in matches if m.status is FillStatus.SKIPPED_NO_VALUE]
    failed = [m for m in matches if m.status is FillStatus.FAILED]

    if not (yours or gaps or failed):
        return "  Nothing left for you. Every recognised field is ready."

    lines: list[str] = []
    if yours:
        lines.append(f"  You need to answer these {len(yours)} yourself:")
        lines.extend(f"    - {_truncate(m.form_field.describe(), 70)}" for m in _unique(yours))
        lines.append("")
    if gaps:
        keys = sorted({m.canonical for m in gaps if m.canonical})
        lines.append(f"  Missing from your resume ({len(gaps)}):")
        lines.extend(f"    - {_truncate(m.form_field.describe(), 70)}" for m in _unique(gaps))
        if keys:
            lines.append("")
            lines.append(f"    Add to profile.json to fill these next time: {', '.join(keys)}")
        lines.append("")
    if failed:
        lines.append(f"  Could not be filled ({len(failed)}):")
        lines.extend(
            f"    - {_truncate(m.form_field.describe(), 40)}: {_truncate(m.reason, 60)}"
            for m in _unique(failed)
        )
    return "\n".join(lines).rstrip()


def _unique(matches: list[FieldMatch]) -> list[FieldMatch]:
    """Collapse repeated labels, which repeating sections produce by the dozen."""
    seen: set[str] = set()
    result: list[FieldMatch] = []
    for match in matches:
        key = match.form_field.describe()
        if key not in seen:
            seen.add(key)
            result.append(match)
    return result


def render_plan(matches: list[FieldMatch], *, verbose: bool = False) -> str:
    """A table of every control on the form and what the engine decided.

    Unrecognised controls are hidden unless ``verbose``. On a real page they are
    cookie banners, nav widgets and search boxes, and they were roughly two
    fifths of the output while being the rows the applicant can do least about.
    """
    if not matches:
        return "No fillable controls were found on this page."

    # Collapse before counting. An unused repeating row is not a pile of
    # missing answers, so including it would inflate the summary and contradict
    # the note that says those rows were skipped.
    matches, group_notes = _collapse_empty_group_rows(matches)
    counted = matches

    hidden = 0
    if not verbose:
        shown = [m for m in matches if m.status is not FillStatus.SKIPPED_NO_MATCH]
        hidden = len(matches) - len(shown)
        matches = shown

    lines = [render_summary(counted), ""]
    header = f"  {'':6} {'FIELD':<34} {'MAPPED TO':<20} {'CONF':>5}  VALUE"
    lines += [header, "  " + "-" * 92]

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
    lines.extend(group_notes)
    if hidden:
        lines.append(
            f"  {hidden} unrecognised control(s) hidden "
            "(cookie banners, nav, site search). Use --verbose to see them."
        )
    if group_notes or hidden:
        lines.append("")
    lines.append(
        "  [FILL] ready   [YOU ] your call   [GAP ] missing from resume   "
        "[ n/a] not applicable   * required"
    )
    return "\n".join(lines)


# Below this many controls, a page is almost certainly not the application form.
SPARSE_SCAN_THRESHOLD = 6

_WIZARD_MARKERS = (
    "items to complete",
    "application tasks",
    "save and close",
    "continue",
    "next step",
    "step 1 of",
)


def diagnose_sparse_scan(html: str, field_count: int) -> str:
    """Explain a nearly empty scan instead of leaving the user guessing.

    A saved page can legitimately contain no form. Modern application portals
    are single-page apps that render one step at a time, so saving the landing
    step captures a task list and nothing else. Reporting "4 controls, none
    matched" with no explanation looks like the tool is broken when it is
    working correctly.
    """
    if field_count >= SPARSE_SCAN_THRESHOLD:
        return ""

    lowered = html.lower()
    notes = [
        f"  Only {field_count} fillable control(s) were found, "
        "which is too few to be an application form."
    ]

    hits = [marker for marker in _WIZARD_MARKERS if marker in lowered]
    if hits:
        notes.append(
            "  This looks like one step of a multi-step application "
            f"(the page mentions {', '.join(repr(h) for h in hits[:3])})."
        )
        notes.append(
            "  Open the step that actually contains the fields, save that page, "
            "and inspect it instead."
        )
    if lowered.count("<script") > 20:
        notes.append(
            "  The page is a JavaScript application. Saving it before the form "
            "renders captures only the shell."
        )
    notes.append(
        "  To let a browser render and step through it instead, use: inspect --url <the live URL>"
    )
    return "\n".join(notes)


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

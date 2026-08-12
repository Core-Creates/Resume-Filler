"""Executes a fill plan against a live application form.

Submission is opt in and gated twice. ``submit=True`` must be passed explicitly,
and even then the form is only submitted when every required field was filled
confidently. A required field the engine could not answer aborts submission and
leaves the page open for the human to finish.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .extractors import fields_from_driver
from .field_map import DEFAULT_CONFIDENCE_THRESHOLD, plan_fill
from .models import (
    ApplicationResult,
    ApplicationStatus,
    FieldMatch,
    FillStatus,
    JobPosting,
    ResumeData,
)

logger = logging.getLogger(__name__)

APPLY_BUTTON_XPATHS = (
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'easy apply')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'quick apply')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply now')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
    "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'apply')]",
)

SUBMIT_BUTTON_XPATHS = (
    "//button[@type='submit']",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit application')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit')]",
    "//input[@type='submit']",
)


def _apply_one(driver: Any, match: FieldMatch, timeout: float) -> None:
    """Write a single value into its element. Raises on failure."""
    from selenium.webdriver.support.ui import Select

    element = match.form_field.handle
    if element is None:
        raise ValueError("Field match has no live element handle")

    if match.form_field.is_file_input:
        path = Path(match.value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Upload target does not exist: {path}")
        element.send_keys(str(path))
        return

    if match.form_field.tag == "select":
        select = Select(element)
        wanted = match.value.strip().lower()
        for option in select.options:
            if option.text.strip().lower() == wanted:
                select.select_by_visible_text(option.text)
                return
        for option in select.options:
            if wanted and wanted in option.text.strip().lower():
                select.select_by_visible_text(option.text)
                return
        raise ValueError(f"No option in the dropdown matched {match.value!r}")

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    element.clear()
    element.send_keys(match.value)


def fill_form(
    driver: Any,
    resume: ResumeData,
    *,
    resume_path: str = "",
    cover_letter_path: str = "",
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    timeout: float = 15.0,
    dry_run: bool = True,
) -> list[FieldMatch]:
    """Plan and optionally execute a fill of the form on the current page.

    With ``dry_run=True`` nothing is typed. The returned plan shows exactly what
    would have been entered, which is the recommended way to validate the engine
    against a new site before letting it touch anything.
    """
    from selenium.common.exceptions import WebDriverException

    fields = fields_from_driver(driver)
    logger.info("Found %d fillable controls on %s", len(fields), driver.current_url)

    matches = plan_fill(fields, resume, resume_path=resume_path, threshold=threshold)

    # The cover letter is a second file input and is not part of the resume
    # schema, so patch it in when the caller supplied one.
    if cover_letter_path:
        for match in matches:
            if match.canonical == "cover_letter" and not match.value:
                match.value = cover_letter_path
                match.status = FillStatus.FILLED

    if dry_run:
        for match in matches:
            if match.status is FillStatus.FILLED:
                match.reason = "Dry run. Value not entered."
        return matches

    for match in matches:
        if match.status is not FillStatus.FILLED:
            continue
        try:
            _apply_one(driver, match, timeout)
            logger.debug("Filled %s with %r", match.form_field.describe(), match.value)
        except (WebDriverException, ValueError, FileNotFoundError) as exc:
            match.status = FillStatus.FAILED
            match.reason = f"{type(exc).__name__}: {exc}"
            logger.warning("Could not fill %s: %s", match.form_field.describe(), exc)

    return matches


def _click_first(driver: Any, xpaths: tuple[str, ...], timeout: float) -> bool:
    """Click the first clickable element matching any XPath. Returns success."""
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    for xpath in xpaths:
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.click()
            return True
        except (TimeoutException, WebDriverException):
            continue
    return False


def apply_to_job(
    driver: Any,
    posting: JobPosting,
    resume: ResumeData,
    *,
    resume_path: str = "",
    cover_letter_path: str = "",
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    timeout: float = 15.0,
    submit: bool = False,
) -> ApplicationResult:
    """Open a posting, fill the application, and submit only if explicitly told to.

    Returns a result describing what was filled and what still needs a human.
    """
    from selenium.common.exceptions import WebDriverException

    try:
        driver.get(posting.url)
    except WebDriverException as exc:
        return ApplicationResult(
            posting=posting,
            status=ApplicationStatus.FAILED,
            message=f"Could not open the posting: {exc}",
        )

    opened = _click_first(driver, APPLY_BUTTON_XPATHS, timeout)
    if not opened:
        logger.info("No apply button found on %s, treating the page as the form", posting.url)

    matches = fill_form(
        driver,
        resume,
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
        threshold=threshold,
        timeout=timeout,
        dry_run=not submit,
    )

    result = ApplicationResult(posting=posting, status=ApplicationStatus.PREPARED, matches=matches)

    if not submit:
        result.message = (
            f"Dry run. {result.filled_count} fields ready, {result.review_count} need review."
        )
        return result

    gaps = result.required_gaps
    if gaps:
        result.status = ApplicationStatus.NEEDS_REVIEW
        names = ", ".join(gap.form_field.describe() for gap in gaps[:5])
        result.message = (
            f"Not submitted. {len(gaps)} required field(s) could not be filled: {names}. "
            "The form is filled as far as possible. Finish it in the open browser."
        )
        logger.warning("Skipping submit for %s: %s", posting.url, result.message)
        return result

    if _click_first(driver, SUBMIT_BUTTON_XPATHS, timeout):
        result.status = ApplicationStatus.SUBMITTED
        result.message = f"Submitted with {result.filled_count} fields filled."
    else:
        result.status = ApplicationStatus.NEEDS_REVIEW
        result.message = "Form filled but no submit button was found. Finish it manually."

    return result

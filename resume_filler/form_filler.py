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

from .extractors import fields_from_driver, switch_to_frame_path
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

# Advancing a multi-step wizard. "Submit" is deliberately absent: advancing must
# never be able to send the application.
NEXT_BUTTON_XPATHS = (
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save and continue')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
    "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]",
    "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next step')]",
)

# Where a scripted dropdown renders its options once opened.
COMBOBOX_OPTION_SELECTOR = (
    "[role='option'], [role='listbox'] li, .select__option, .Select-option, .ant-select-item-option"
)

MAX_WIZARD_STEPS = 8


def _fill_combobox(driver: Any, match: FieldMatch, timeout: float) -> None:
    """Drive a scripted dropdown by opening it and clicking the option.

    Typing into one of these leaves the widget's internal state unset, so the
    value looks correct on screen and submits as empty.
    """
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    element = match.form_field.handle
    wanted = match.value.strip().lower()

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    element.click()

    options = driver.find_elements(By.CSS_SELECTOR, COMBOBOX_OPTION_SELECTOR)
    for want_exact in (True, False):
        for option in options:
            try:
                text = option.text.strip().lower()
            except WebDriverException:
                continue
            if not text:
                continue
            if (text == wanted) if want_exact else (wanted in text):
                option.click()
                return

    # Some widgets only render options after the user types a filter.
    try:
        element.send_keys(match.value)
        element.send_keys(Keys.ENTER)
    except WebDriverException as exc:
        raise ValueError(f"Could not select {match.value!r} in the dropdown") from exc

    committed = (element.get_attribute("value") or element.text or "").strip().lower()
    if wanted and wanted not in committed:
        raise ValueError(f"Dropdown did not commit {match.value!r}")


def _apply_one(driver: Any, match: FieldMatch, timeout: float) -> None:
    """Write a single value into its element. Raises on failure."""
    from selenium.webdriver.support.ui import Select

    element = match.form_field.handle
    if element is None:
        raise ValueError("Field match has no live element handle")

    if match.form_field.is_combobox:
        _fill_combobox(driver, match, timeout)
        return

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

    # Fill frame by frame. Controls are grouped by browsing context so the driver
    # switches once per frame rather than once per field, and the sort keeps the
    # top document first.
    current_path: tuple[int, ...] | None = None
    try:
        for match in sorted(matches, key=lambda m: m.form_field.frame_path):
            if match.status is not FillStatus.FILLED:
                continue
            try:
                if match.form_field.frame_path != current_path:
                    switch_to_frame_path(driver, match.form_field.frame_path)
                    current_path = match.form_field.frame_path
                _apply_one(driver, match, timeout)
                logger.debug("Filled %s with %r", match.form_field.describe(), match.value)
            except (WebDriverException, ValueError, FileNotFoundError) as exc:
                match.status = FillStatus.FAILED
                match.reason = f"{type(exc).__name__}: {exc}"
                logger.warning("Could not fill %s: %s", match.form_field.describe(), exc)
    finally:
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            logger.debug("Could not return to the top document", exc_info=True)

    return matches


def _step_signature(driver: Any, matches: list[FieldMatch]) -> tuple[str, ...]:
    """Identify a wizard step so a loop that stops progressing can be detected."""
    try:
        url = driver.current_url
    except Exception:  # noqa: BLE001 - a signature is best effort
        url = ""
    return (url, *sorted(m.form_field.describe() for m in matches))


def fill_wizard(
    driver: Any,
    resume: ResumeData,
    *,
    resume_path: str = "",
    cover_letter_path: str = "",
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    timeout: float = 15.0,
    dry_run: bool = True,
    max_steps: int = MAX_WIZARD_STEPS,
) -> list[FieldMatch]:
    """Fill a form that may span several pages, as Workday and similar ATS do.

    Returns the combined plan across every step reached.

    In dry run the wizard deliberately stops after the first step. Advancing
    requires clicking Continue, which is a real interaction with the employer's
    form, and on a page whose required fields were never filled it would fail
    validation anyway. Preview shows step one; run with --submit to go further.
    """
    all_matches: list[FieldMatch] = []
    seen: set[tuple[str, ...]] = set()

    for step in range(1, max_steps + 1):
        matches = fill_form(
            driver,
            resume,
            resume_path=resume_path,
            cover_letter_path=cover_letter_path,
            threshold=threshold,
            timeout=timeout,
            dry_run=dry_run,
        )
        all_matches.extend(matches)

        signature = _step_signature(driver, matches)
        if signature in seen:
            logger.info("Step %d looks identical to an earlier one, stopping.", step)
            break
        seen.add(signature)

        if dry_run:
            if _has_next_button(driver):
                logger.info(
                    "This form has more steps. Dry run stops at step 1; "
                    "run with --submit to continue through the wizard."
                )
            break

        if not _click_first(driver, NEXT_BUTTON_XPATHS, timeout):
            logger.debug("No Continue button at step %d, treating it as the last.", step)
            break
        logger.info("Advanced to wizard step %d", step + 1)

    return all_matches


def _has_next_button(driver: Any) -> bool:
    """Whether a Continue control is present, without clicking it."""
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By

    for xpath in NEXT_BUTTON_XPATHS:
        try:
            if driver.find_elements(By.XPATH, xpath):
                return True
        except WebDriverException:
            continue
    return False


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
    max_steps: int = MAX_WIZARD_STEPS,
) -> ApplicationResult:
    """Open a posting, fill the application, and submit only if explicitly told to.

    Handles both single page forms and multi-step wizards. A single page form
    simply has no Continue button, so the wizard loop exits after one pass.

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

    matches = fill_wizard(
        driver,
        resume,
        resume_path=resume_path,
        cover_letter_path=cover_letter_path,
        threshold=threshold,
        timeout=timeout,
        dry_run=not submit,
        max_steps=max_steps,
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

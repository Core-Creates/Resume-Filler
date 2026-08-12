"""Executes a fill plan against a live application form.

Three modes, see ``RunMode``. PREVIEW types nothing, FILL completes the form and
stops, SUBMIT also sends it and still refuses when a required field could not be
filled.

Known limit: an employer typeahead backed by its own list, such as Greenhouse's
School and Degree, is not always selectable. The value is looked up, then typed
so the list can filter, and the match is clicked if it appears. When it does not
the field is reported as needing the applicant rather than left holding text the
widget never committed, which would submit as empty. In FILL mode the form is
open in front of them, so finishing those two fields by hand costs seconds.
"""

from __future__ import annotations

import logging
import re
import time
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
    RunMode,
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

# How long a typeahead needs to fetch and render its options.
TYPEAHEAD_SETTLE_SECONDS = 1.5


def _dismiss_open_menus(driver: Any) -> None:
    """Close anything currently overlaying the page.

    Selecting from one scripted dropdown can leave its option list open, and it
    then covers the next control so the click lands on the menu instead. On a
    real Greenhouse form this failed School and Degree, the two dropdowns
    following Country.
    """
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except WebDriverException:
        logger.debug("Could not send Escape to close an open menu", exc_info=True)


def _click_element(driver: Any, element: Any) -> None:
    """Click, and cope with something sitting on top of the target.

    A normal click is tried first because it exercises the page the way a person
    would. When an overlay intercepts it, the menu is dismissed and it is tried
    again, and only then does it fall back to a scripted click, which ignores
    hit testing entirely.
    """
    from selenium.common.exceptions import ElementClickInterceptedException

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
        return
    except ElementClickInterceptedException:
        logger.debug("Click intercepted, dismissing overlays and retrying")

    _dismiss_open_menus(driver)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
        return
    except ElementClickInterceptedException:
        logger.debug("Still intercepted, falling back to a scripted click")

    driver.execute_script("arguments[0].click();", element)


def _fill_combobox(driver: Any, match: FieldMatch, timeout: float) -> None:
    """Drive a scripted dropdown by opening it and clicking the option.

    Typing into one of these leaves the widget's internal state unset, so the
    value looks correct on screen and submits as empty.
    """
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.keys import Keys

    element = match.form_field.handle
    wanted = match.value.strip().lower()

    _click_element(driver, element)

    if _select_matching_option(driver, wanted):
        _dismiss_open_menus(driver)
        return

    # A typeahead has no options at all until something is typed. Greenhouse
    # uses one for School and Degree, which query as you type, so opening it and
    # looking for a match finds an empty list every time.
    try:
        element.send_keys(match.value)
    except WebDriverException as exc:
        raise ValueError(f"Could not type into the dropdown for {match.value!r}") from exc

    time.sleep(TYPEAHEAD_SETTLE_SECONDS)
    if _select_matching_option(driver, wanted):
        _dismiss_open_menus(driver)
        return

    # Last resort: accept whatever the widget has highlighted.
    try:
        element.send_keys(Keys.ENTER)
    except WebDriverException as exc:
        raise ValueError(f"Could not select {match.value!r} in the dropdown") from exc

    committed = (element.get_attribute("value") or element.text or "").strip().lower()
    if wanted and not _values_agree(wanted, committed):
        raise ValueError(
            f"Dropdown did not offer {match.value!r}. "
            "It may not be in this employer's list; choose it yourself."
        )


def _values_agree(wanted: str, committed: str) -> bool:
    """Whether the widget settled on what was asked for.

    Compared loosely, because a dropdown routinely displays a shortened or
    reordered form of the option: "UT San Antonio" for "The University of Texas
    at San Antonio". Demanding an exact substring reports a correct selection as
    a failure.
    """
    if not committed:
        return False
    if wanted in committed or committed in wanted:
        return True
    wanted_words = {w for w in re.findall(r"[a-z0-9]+", wanted) if len(w) > 2}
    committed_words = {w for w in re.findall(r"[a-z0-9]+", committed) if len(w) > 2}
    if not wanted_words:
        return False
    overlap = len(wanted_words & committed_words) / len(wanted_words)
    return overlap >= 0.5


def _select_matching_option(driver: Any, wanted: str) -> bool:
    """Click the visible option matching ``wanted``. Returns whether it did."""
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By

    options = driver.find_elements(By.CSS_SELECTOR, COMBOBOX_OPTION_SELECTOR)
    for want_exact in (True, False):
        for option in options:
            try:
                text = option.text.strip().lower()
            except WebDriverException:
                continue
            if not text:
                continue
            if (text == wanted) if want_exact else (wanted in text or text in wanted):
                _click_element(driver, option)
                return True
    return False


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
    mode: RunMode = RunMode.PREVIEW,
    max_steps: int = MAX_WIZARD_STEPS,
) -> ApplicationResult:
    """Open a posting, fill the application, and go only as far as ``mode`` allows.

    PREVIEW types nothing. FILL completes the form and stops, leaving it for the
    applicant to check and send. SUBMIT also clicks Submit, and still refuses
    when a required field could not be filled.

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
        dry_run=not mode.types_anything,
        max_steps=max_steps,
    )

    result = ApplicationResult(posting=posting, status=ApplicationStatus.PREPARED, matches=matches)

    if mode is RunMode.PREVIEW:
        result.message = (
            f"Preview only, nothing typed. {result.filled_count} fields ready, "
            f"{result.review_count} need you."
        )
        return result

    if mode is RunMode.FILL:
        result.message = (
            f"Filled {result.filled_count} field(s) and stopped. "
            f"{result.review_count} still need you. Nothing was submitted."
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

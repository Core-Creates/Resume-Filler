"""Adapters that turn a page into browser-agnostic ``FormField`` objects.

Two adapters exist and they must agree with each other:

* ``fields_from_html`` parses a static HTML string with BeautifulSoup. Tests use
  it against saved fixtures of real application forms, so the matching engine
  can be exercised without launching a browser.
* ``fields_from_driver`` walks a live Selenium page.

Keeping label resolution in one place per adapter is the point. Label discovery
is the single most error prone part of form automation, because forms in the
wild associate labels five different ways.
"""

from __future__ import annotations

import logging
from typing import Any

from bs4 import BeautifulSoup, Tag

from .models import FormField

logger = logging.getLogger(__name__)

# Controls that never hold candidate data.
IGNORED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}


def _clean(text: Any) -> str:
    """Collapse whitespace. Accepts anything, because BeautifulSoup attribute
    lookups return a str, a list of str for multi-valued attributes, or None."""
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(str(item) for item in text)
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# Static HTML adapter
# --------------------------------------------------------------------------


def _legend_for_html(element: Tag) -> str:
    """Text of the <legend> for the nearest enclosing <fieldset>."""
    fieldset = element.find_parent("fieldset")
    if fieldset:
        legend = fieldset.find("legend")
        if legend:
            return _clean(legend.get_text(" "))
    return ""


def _label_for_html(element: Tag, soup: BeautifulSoup, field_type: str = "") -> str:
    """Resolve the visible label for a control, trying each association in turn."""
    element_id = element.get("id")
    if element_id:
        explicit = soup.find("label", attrs={"for": element_id})
        if explicit:
            return _clean(explicit.get_text(" "))

    labelled_by = element.get("aria-labelledby")
    if labelled_by:
        parts = []
        for token in str(labelled_by).split():
            target = soup.find(id=token)
            if target:
                parts.append(_clean(target.get_text(" ")))
        if parts:
            return _clean(" ".join(parts))

    # For a radio or checkbox the wrapping <label> holds the option text ("Yes"),
    # while the <legend> holds the actual question. The question is what
    # identifies the field, so it takes priority for these controls only.
    if field_type in {"radio", "checkbox"}:
        legend = _legend_for_html(element)
        if legend:
            return legend

    # A control nested inside its own <label>.
    ancestor = element.find_parent("label")
    if ancestor:
        return _clean(ancestor.get_text(" "))

    legend = _legend_for_html(element)
    if legend:
        return legend

    # Fall back to the nearest preceding label-like sibling.
    previous = element.find_previous(["label", "legend"])
    if previous:
        return _clean(previous.get_text(" "))

    return ""


def fields_from_html(html: str) -> list[FormField]:
    """Extract every fillable control from an HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    fields: list[FormField] = []

    for element in soup.find_all(["input", "select", "textarea"]):
        tag = element.name
        field_type = str(element.get("type", "text")).lower() if tag == "input" else tag
        if tag == "input" and field_type in IGNORED_INPUT_TYPES:
            continue

        options: list[str] = []
        if tag == "select":
            options = [_clean(opt.get_text(" ")) for opt in element.find_all("option")]
        elif field_type in {"radio", "checkbox"}:
            # The wrapping label is this option's own text, for example "Yes".
            own_label = element.find_parent("label")
            if own_label:
                options = [_clean(own_label.get_text(" "))]

        fields.append(
            FormField(
                tag=tag,
                field_type=field_type,
                name=_clean(element.get("name")),
                element_id=_clean(element.get("id")),
                label=_label_for_html(element, soup, field_type),
                aria_label=_clean(element.get("aria-label")),
                placeholder=_clean(element.get("placeholder")),
                autocomplete=_clean(element.get("autocomplete")),
                required=element.has_attr("required")
                or str(element.get("aria-required", "")).lower() == "true",
                options=options,
                handle=element,
            )
        )
    return fields


# --------------------------------------------------------------------------
# Selenium adapter
# --------------------------------------------------------------------------

_LABEL_SCRIPT = """
const el = arguments[0];
const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
if (el.id) {
  const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  if (explicit) return clean(explicit.innerText);
}
const labelledBy = el.getAttribute('aria-labelledby');
if (labelledBy) {
  const parts = labelledBy.split(/\\s+/)
    .map((id) => document.getElementById(id))
    .filter(Boolean)
    .map((n) => clean(n.innerText));
  if (parts.length) return clean(parts.join(' '));
}
const legendText = () => {
  const fieldset = el.closest('fieldset');
  if (!fieldset) return '';
  const legend = fieldset.querySelector('legend');
  return legend ? clean(legend.innerText) : '';
};
// For radios and checkboxes the wrapping label is the option text ("Yes") while
// the legend is the question. The question identifies the field.
const type = (el.getAttribute('type') || '').toLowerCase();
if (type === 'radio' || type === 'checkbox') {
  const legend = legendText();
  if (legend) return legend;
}
const ancestor = el.closest('label');
if (ancestor) return clean(ancestor.innerText);
return legendText();
"""


def fields_from_driver(driver: Any) -> list[FormField]:
    """Extract every visible fillable control from a live Selenium page.

    Invisible controls are skipped because a human applicant cannot fill them
    either, and attempting to do so is a reliable way to raise
    ``ElementNotInteractableException``.
    """
    from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
    from selenium.webdriver.common.by import By

    fields: list[FormField] = []
    elements = driver.find_elements(By.CSS_SELECTOR, "input, select, textarea")

    for element in elements:
        try:
            tag = element.tag_name.lower()
            field_type = (element.get_attribute("type") or "text").lower()
            if tag == "input" and field_type in IGNORED_INPUT_TYPES:
                continue
            # File inputs are routinely hidden behind a styled button, so they
            # are the one control we accept while not displayed.
            if not element.is_displayed() and field_type != "file":
                continue

            try:
                label = driver.execute_script(_LABEL_SCRIPT, element) or ""
            except WebDriverException:
                logger.debug("Label lookup script failed for one element", exc_info=True)
                label = ""

            options: list[str] = []
            if tag == "select":
                options = [
                    _clean(option.text) for option in element.find_elements(By.TAG_NAME, "option")
                ]

            fields.append(
                FormField(
                    tag=tag,
                    field_type=field_type if tag == "input" else tag,
                    name=_clean(element.get_attribute("name")),
                    element_id=_clean(element.get_attribute("id")),
                    label=_clean(label),
                    aria_label=_clean(element.get_attribute("aria-label")),
                    placeholder=_clean(element.get_attribute("placeholder")),
                    autocomplete=_clean(element.get_attribute("autocomplete")),
                    required=bool(element.get_attribute("required"))
                    or (element.get_attribute("aria-required") or "").lower() == "true",
                    options=options,
                    handle=element,
                )
            )
        except StaleElementReferenceException:
            logger.debug("Skipped a stale element while scanning the form")
            continue

    return fields

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
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from .models import FormField

logger = logging.getLogger(__name__)

# Controls that never hold candidate data.
IGNORED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}

# Native controls plus scripted dropdowns. React, Ant Design and Select2 render
# a combobox as a div, so restricting the scan to input/select/textarea misses
# them entirely and the engine reports the field as unrecognised.
#
# Shared by both adapters so the static and live scans cannot disagree about
# what counts as a control.
CONTROL_SELECTOR = (
    "input, select, textarea, [role='combobox'], [role='listbox'], "
    "[aria-haspopup='listbox'], [aria-autocomplete='list']"
)

MAX_FRAME_DEPTH = 3


def _is_decorative(aria_hidden: str, element_type: str) -> bool:
    """Whether a control exists for the widget's plumbing, not for the applicant.

    react-select, which Greenhouse and many other ATS use, renders a hidden
    ``required`` input next to each dropdown purely so native form validation
    fires. A real Greenhouse page carries nine of them. Collecting those means
    nine unmappable required fields, which would block every submission.

    Anything marked aria-hidden is by definition not presented to the user, so
    it is not something a human applicant could fill either. File inputs are the
    exception: they are routinely hidden behind a styled button.
    """
    return aria_hidden.lower() == "true" and element_type != "file"


# Repeating sections announce themselves in the identifier. Workday writes
# "workExperience-2--jobTitle"; Rails-style forms write "education[1][school]"
# or "education_1_school". All three carry the same information: which group,
# and which row within it.
_GROUP_PATTERNS = (
    re.compile(r"^(?P<group>[A-Za-z][A-Za-z]*)-(?P<index>\d+)--(?P<field>.+)$"),
    re.compile(r"^(?P<group>[A-Za-z_]+)\[(?P<index>\d+)\]\[?(?P<field>[A-Za-z_]+)\]?$"),
    re.compile(r"^(?P<group>[A-Za-z]+(?:[A-Z][a-z]+)*)_(?P<index>\d+)_(?P<field>.+)$"),
)


def parse_group(identifier: str) -> tuple[str, int]:
    """Extract the repeating-section name and row index from an identifier.

    Returns ``("", -1)`` when the identifier does not belong to a group.
    """
    text = (identifier or "").strip()
    if not text:
        return "", -1
    for pattern in _GROUP_PATTERNS:
        match = pattern.match(text)
        if match:
            try:
                return match.group("group"), int(match.group("index"))
            except (ValueError, IndexError):
                continue
    return "", -1


DATE_PART_LABELS = {"month", "year", "day"}


def normalize_fields(fields: list[FormField]) -> list[FormField]:
    """Post-process a raw scan. Shared by both adapters so they cannot diverge.

    Two jobs:

    * Attach repeating-group identity, renumbering row indices to a dense
      zero-based sequence. Workday's markup starts at arbitrary numbers and
      skips values, so the raw index is not a usable offset into a resume.
    * Give split date inputs a distinguishing label. Workday renders "From" as
      two spinbuttons whose only difference is an aria-label of "Month" or
      "Year", so both otherwise arrive labelled "From" and are indistinguishable.
    """
    for field in fields:
        group, index = parse_group(field.element_id)
        if not group:
            group, index = parse_group(field.name)
        field.group, field.group_index = group, index

        aria = field.aria_label.strip().lower()
        if aria in DATE_PART_LABELS and field.label and aria not in field.label.lower():
            field.label = f"{field.label} {field.aria_label.strip()}"

    # Renumber each group's rows to 0, 1, 2 ... in the order they appear.
    for group_name in {f.group for f in fields if f.group}:
        members = [f for f in fields if f.group == group_name]
        ordering = sorted({f.group_index for f in members})
        remap = {original: position for position, original in enumerate(ordering)}
        for field in members:
            field.group_index = remap[field.group_index]

    return fields


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

    return _container_label(element)


# Many ATS render a label as a styled div rather than a <label>. Lever writes
# <div class="application-label"> for every custom question.
_LABEL_LIKE_SELECTOR = "label, legend, [class*='label']"


def _container_label(element: Tag, max_levels: int = 4) -> str:
    """Find the label text inside the control's own container.

    Searching the whole document backwards for the nearest <label> is what a
    naive fallback does, and on a real Lever page it assigns the same
    "Portfolio URL" label to ten unrelated custom questions, because their real
    labels are divs it skips straight past. Wrong labels are worse than missing
    ones: they can route a value into the wrong field.

    Bounding the search to a few ancestors keeps a label from leaking across
    sections of the form.
    """
    node: Tag | None = element
    for _ in range(max_levels):
        parent = node.parent if node is not None else None
        if parent is None or not isinstance(parent, Tag):
            return ""
        for candidate in parent.select(_LABEL_LIKE_SELECTOR):
            # A label bound to a different control is that control's, not ours.
            bound_to = candidate.get("for")
            if bound_to and bound_to != element.get("id"):
                continue
            text = _clean(candidate.get_text(" "))
            if text:
                return text
        node = parent
    return ""


def _is_combobox_tag(element: Tag) -> bool:
    """Whether an element is a scripted dropdown rather than a plain field.

    react-select, the widget behind Greenhouse's dropdowns, renders a real
    ``<input>`` and advertises itself with ``aria-autocomplete="list"`` rather
    than ``role="combobox"``. Missing that variant means the field is driven by
    typing, which leaves the selection uncommitted and submits as empty.
    """
    role = str(element.get("role", "")).lower()
    if role in {"combobox", "listbox"}:
        return True
    if str(element.get("aria-autocomplete", "")).lower() == "list":
        return True
    if element.name in {"input", "select", "textarea"}:
        return False
    return str(element.get("aria-haspopup", "")).lower() == "listbox"


def _combobox_options(element: Tag) -> list[str]:
    """Option text for a scripted dropdown, which lives in a sibling listbox."""
    listbox: Tag | None = None
    if str(element.get("role", "")).lower() == "listbox":
        listbox = element
    elif element.parent is not None:
        listbox = element.parent.select_one("[role='listbox']")
    if listbox is None:
        return []
    return [_clean(option.get_text(" ")) for option in listbox.select("[role='option']")]


def _resolve_local_frame(base_dir: Path, src: str) -> Path | None:
    """Map an iframe src to a file on disk, or None if it is not local.

    When a browser saves a page it writes each iframe's document into the
    companion "_files" folder and rewrites the src to point at it, so a saved
    page really does carry its frames with it.
    """
    text = src.strip()
    if not text or text.lower().startswith(("http://", "https://", "//", "data:", "about:")):
        return None
    candidate = base_dir / unquote(text.split("?", 1)[0].split("#", 1)[0])
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _collect_from_document(
    html: str,
    base_dir: Path | None,
    path: tuple[int, ...],
    max_depth: int,
    out: list[FormField],
) -> None:
    """Scan one document, then descend into any locally saved iframes."""
    soup = BeautifulSoup(html, "html.parser")
    out.extend(_fields_from_soup(soup, path))

    if base_dir is None or len(path) >= max_depth:
        return

    # Enumerate every frame, including ones that cannot be resolved, so the
    # indices stay aligned with the positions Selenium would switch to.
    for index, frame in enumerate(soup.find_all(["iframe", "frame"])):
        src = frame.get("src")
        if not src:
            continue
        child = _resolve_local_frame(base_dir, str(src))
        if child is None:
            continue
        try:
            child_html = child.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("Could not read saved frame %s", child)
            continue
        _collect_from_document(child_html, child.parent, (*path, index), max_depth, out)


def fields_from_html(
    html: str,
    *,
    base_path: str | Path | None = None,
    max_depth: int = MAX_FRAME_DEPTH,
) -> list[FormField]:
    """Extract every fillable control from an HTML document.

    Scripted dropdowns are included alongside native controls, because an ATS
    that renders its selects as divs would otherwise look like a form with
    missing fields.

    Pass ``base_path`` (the saved .html file, or its directory) to follow
    iframes into their saved companion documents. iCIMS puts the entire
    application inside an iframe, so without this a saved iCIMS page yields
    only the site search box while the real 61-control form sits one file away.
    """
    fields: list[FormField] = []
    base_dir: Path | None = None
    if base_path is not None:
        given = Path(base_path).expanduser()
        base_dir = given.parent if given.is_file() else given

    _collect_from_document(html, base_dir, (), max_depth, fields)
    return normalize_fields(fields)


def _fields_from_soup(soup: BeautifulSoup, path: tuple[int, ...] = ()) -> list[FormField]:
    """Collect the controls in a single parsed document."""
    fields: list[FormField] = []

    # The same selector the Selenium adapter uses, so the two adapters cannot
    # drift apart on which elements count as controls.
    for element in soup.select(CONTROL_SELECTOR):
        tag = element.name
        combobox = _is_combobox_tag(element)
        if tag not in {"input", "select", "textarea"} and not combobox:
            continue
        # A <ul role="listbox"> is the option container, not a control.
        if tag in {"ul", "ol"} and combobox:
            continue

        field_type = str(element.get("type", "text")).lower() if tag == "input" else tag
        if tag == "input" and field_type in IGNORED_INPUT_TYPES:
            continue
        if _is_decorative(_clean(element.get("aria-hidden")), field_type):
            continue

        options: list[str] = []
        if tag == "select":
            options = [_clean(opt.get_text(" ")) for opt in element.find_all("option")]
        elif combobox:
            options = _combobox_options(element)
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
                accept=_clean(element.get("accept")),
                required=element.has_attr("required")
                or str(element.get("aria-required", "")).lower() == "true",
                options=options,
                widget="combobox" if combobox else "native",
                frame_path=path,
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
// Resolve against the element's own root. For a control inside a shadow tree
// that root is the shadow root, and querying `document` would find nothing,
// because label associations do not cross the shadow boundary.
const root = el.getRootNode ? el.getRootNode() : document;
const byId = (id) => (root.getElementById
  ? root.getElementById(id)
  : root.querySelector(`#${CSS.escape(id)}`));
if (el.id) {
  const explicit = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
  if (explicit) return clean(explicit.innerText);
}
const labelledBy = el.getAttribute('aria-labelledby');
if (labelledBy) {
  const parts = labelledBy.split(/\\s+/)
    .map(byId)
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
const legend = legendText();
if (legend) return legend;
// Container search, mirroring _container_label in the static adapter. Many ATS
// render a label as a styled div; Lever writes <div class="application-label">
// for every custom question. Without this the live scan returns 14 unlabelled
// required controls on a real Lever page while the static scan labels them all.
let node = el;
for (let depth = 0; depth < 4; depth++) {
  const parent = node.parentElement;
  if (!parent) break;
  const candidates = parent.querySelectorAll('label, legend, [class*="label"]');
  for (const candidate of candidates) {
    const boundTo = candidate.getAttribute('for');
    if (boundTo && boundTo !== el.id) continue;
    const text = clean(candidate.innerText || candidate.textContent);
    if (text) return text;
  }
  node = parent;
}
return '';
"""


def switch_to_frame_path(driver: Any, path: tuple[int, ...]) -> None:
    """Move the driver into the browsing context identified by ``path``.

    Always re-walks from the top document, because there is no reliable way to
    ask Selenium where it currently is.
    """
    driver.switch_to.default_content()
    for index in path:
        driver.switch_to.frame(index)


def _is_combobox(element: Any, tag: str) -> bool:
    """Live-page counterpart of ``_is_combobox_tag``. Keep the two in step."""
    role = (element.get_attribute("role") or "").lower()
    if role in {"combobox", "listbox"}:
        return True
    if (element.get_attribute("aria-autocomplete") or "").lower() == "list":
        return True
    if tag in {"input", "select", "textarea"}:
        return False
    return (element.get_attribute("aria-haspopup") or "").lower() == "listbox"


# Walks open shadow roots, which querySelectorAll cannot cross. Some ATS build
# their controls as web components, and those fields are invisible to an ordinary
# scan.
#
# Closed roots cannot be traversed and cannot even be counted: `host.shadowRoot`
# is null for them, so a closed component is indistinguishable from an empty
# one. Nothing here works around that, and no browser API allows it. A form
# built entirely from closed components will simply report no fields, which the
# caller surfaces rather than silently treating as an empty form.
_SHADOW_SCAN_SCRIPT = """
const selector = arguments[0];
const maxDepth = arguments[1];
const found = [];
let tooDeep = 0;

const walk = (root, depth) => {
  if (depth > maxDepth) { tooDeep += 1; return; }
  let matches = [];
  try { matches = root.querySelectorAll(selector); } catch (e) { return; }
  for (const el of matches) found.push(el);
  let hosts = [];
  try { hosts = root.querySelectorAll('*'); } catch (e) { return; }
  for (const host of hosts) {
    if (host.shadowRoot) walk(host.shadowRoot, depth + 1);
  }
};

walk(document, 0);
return [found, tooDeep];
"""

SHADOW_MAX_DEPTH = 5


def _elements_in_context(driver: Any, selector: str) -> list[Any]:
    """Find controls in this context, including inside open shadow roots.

    Falls back to a plain query when the script cannot run, so a page that
    blocks script execution still gets an ordinary scan rather than nothing.
    """
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By

    try:
        found, too_deep = driver.execute_script(_SHADOW_SCAN_SCRIPT, selector, SHADOW_MAX_DEPTH)
    except (WebDriverException, ValueError, TypeError):
        logger.debug("Shadow-aware scan failed, falling back to a flat query", exc_info=True)
        return list(driver.find_elements(By.CSS_SELECTOR, selector))

    if too_deep:
        logger.info(
            "Stopped descending at %d shadow root(s) beyond depth %d", too_deep, SHADOW_MAX_DEPTH
        )
    return list(found or [])


def _scan_context(driver: Any, path: tuple[int, ...]) -> list[FormField]:
    """Collect controls in the browsing context the driver is currently in."""
    from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
    from selenium.webdriver.common.by import By

    fields: list[FormField] = []
    for element in _elements_in_context(driver, CONTROL_SELECTOR):
        try:
            tag = element.tag_name.lower()
            field_type = (element.get_attribute("type") or "text").lower()
            if tag == "input" and field_type in IGNORED_INPUT_TYPES:
                continue
            if _is_decorative(element.get_attribute("aria-hidden") or "", field_type):
                continue
            # File inputs are routinely hidden behind a styled button, so they
            # are the one control we accept while not displayed.
            if not element.is_displayed() and field_type != "file":
                continue

            combobox = _is_combobox(element, tag)
            if tag not in {"input", "select", "textarea"} and not combobox:
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
                    accept=_clean(element.get_attribute("accept")),
                    required=bool(element.get_attribute("required"))
                    or (element.get_attribute("aria-required") or "").lower() == "true",
                    options=options,
                    frame_path=path,
                    widget="combobox" if combobox else "native",
                    handle=element,
                )
            )
        except StaleElementReferenceException:
            logger.debug("Skipped a stale element while scanning the form")
            continue

    return fields


def fields_from_driver(driver: Any, max_depth: int = MAX_FRAME_DEPTH) -> list[FormField]:
    """Extract every visible fillable control, descending into nested iframes.

    Invisible controls are skipped because a human applicant cannot fill them
    either, and attempting to do so is a reliable way to raise
    ``ElementNotInteractableException``.

    The driver is left in the top document when this returns.
    """
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By

    fields: list[FormField] = []

    def scan(path: tuple[int, ...]) -> None:
        fields.extend(_scan_context(driver, path))
        if len(path) >= max_depth:
            return
        frame_count = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
        for index in range(frame_count):
            try:
                switch_to_frame_path(driver, path)
                driver.switch_to.frame(index)
            except WebDriverException:
                logger.debug("Could not enter frame %d at path %s", index, path)
                continue
            try:
                scan((*path, index))
            except WebDriverException:
                # A cross-origin frame denies access. That is expected, not fatal.
                logger.debug("Skipped inaccessible frame %d at path %s", index, path)

    try:
        switch_to_frame_path(driver, ())
        scan(())
    finally:
        try:
            driver.switch_to.default_content()
        except WebDriverException:
            logger.debug("Could not return to the top document", exc_info=True)

    normalize_fields(fields)
    groups = {f.group for f in fields if f.group}
    logger.info(
        "Scanned %d control(s) across %d browsing context(s)%s",
        len(fields),
        len({field.frame_path for field in fields}) or 1,
        f", repeating groups: {sorted(groups)}" if groups else "",
    )
    return fields

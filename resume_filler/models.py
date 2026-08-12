"""Core data structures shared across the application.

These types are deliberately free of Selenium and BeautifulSoup imports so the
matching logic can be unit tested without a browser or a network connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FillStatus(str, Enum):
    """Outcome of attempting to fill a single form field."""

    FILLED = "filled"
    SKIPPED_NO_MATCH = "skipped_no_match"
    SKIPPED_LOW_CONFIDENCE = "skipped_low_confidence"
    SKIPPED_NO_VALUE = "skipped_no_value"
    SKIPPED_BY_POLICY = "skipped_by_policy"
    NOT_APPLICABLE = "not_applicable"
    """Considered, and correctly left alone. Distinct from a gap.

    "I currently work here" on a role that ended in 2022 is answered by leaving
    it unchecked. Reporting that as missing data is wrong twice over: it tells
    the applicant to go find something that does not exist, and if the control
    is required it blocks submission over a question already answered.
    """

    FAILED = "failed"


class RunMode(str, Enum):
    """How far a run is allowed to go.

    There used to be only two: type nothing, or type everything and submit.
    Neither is what a careful applicant wants, so all the filling logic never
    saved anyone a keystroke. FILL is the middle: the form is completed and then
    handed over for the human to check and send.
    """

    PREVIEW = "preview"
    """Read the form and report. Nothing is typed."""

    FILL = "fill"
    """Complete the form and stop. Never submits."""

    SUBMIT = "submit"
    """Complete the form and send it, if nothing required is missing."""

    @property
    def types_anything(self) -> bool:
        return self is not RunMode.PREVIEW


class ApplicationStatus(str, Enum):
    """Outcome of attempting a full job application."""

    PREPARED = "prepared"
    SUBMITTED = "submitted"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Position:
    """A single role from the candidate's work history."""

    company: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""

    @property
    def is_current(self) -> bool:
        return self.end_date.strip().lower() in {"present", "current", ""}


@dataclass
class Education:
    """A single education entry."""

    school: str = ""
    degree: str = ""
    major: str = ""
    graduation_year: str = ""
    in_progress: bool = False
    """True when the resume marks the degree unfinished.

    Dropping this turns "Bachelor of Business Administration (In Progress)" into
    a completed degree, which would overstate the applicant's credentials on
    every form the tool fills. Never discard it.
    """

    @property
    def degree_display(self) -> str:
        """The degree as it may safely be written onto an application."""
        if self.degree and self.in_progress:
            return f"{self.degree} (In Progress)"
        return self.degree


@dataclass
class ResumeData:
    """Structured candidate information extracted from a resume.

    Every attribute defaults to empty so a partial parse never raises when the
    filler asks for a value that the resume did not contain.
    """

    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    address_line1: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    raw_text: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    @property
    def current_position(self) -> Position | None:
        for position in self.positions:
            if position.is_current:
                return position
        return self.positions[0] if self.positions else None

    @property
    def current_company(self) -> str:
        position = self.current_position
        return position.company if position else ""

    @property
    def current_title(self) -> str:
        position = self.current_position
        return position.title if position else ""

    @property
    def latest_education(self) -> Education | None:
        return self.education[0] if self.education else None


@dataclass
class FormField:
    """A browser-agnostic description of one interactive form control.

    Both the Selenium adapter and the static HTML adapter produce these, which
    is what lets the matching engine be tested against saved fixtures.
    """

    tag: str
    field_type: str = "text"
    name: str = ""
    element_id: str = ""
    label: str = ""
    aria_label: str = ""
    placeholder: str = ""
    autocomplete: str = ""
    accept: str = ""
    """The file types a file input will take.

    Decisive in both directions. An input accepting only images cannot be a
    resume upload however its label reads, and an unlabelled input accepting
    ".pdf,.doc,.docx" almost certainly is one.
    """

    required: bool = False
    options: list[str] = field(default_factory=list)
    group: str = ""
    """Name of the repeating section this control belongs to, if any.

    Workday's work history is a repeating group: workExperience-1--jobTitle,
    workExperience-2--jobTitle, and so on. Without this the engine treats the
    second "Job Title" as a duplicate of the first and fills only one row.
    """

    group_index: int = -1
    """Zero-based position of this control's row within its group."""

    frame_path: tuple[int, ...] = ()
    """Indices of the nested iframes this control lives in, from the top document.

    Empty means the top document. Greenhouse and Lever boards embedded on a
    company careers site sit inside an iframe, so without this the engine finds
    no fields at all on those pages.
    """
    widget: str = "native"
    """How the control must be driven: ``native`` for real HTML inputs, or
    ``combobox`` for a scripted dropdown built out of divs, which has to be
    opened and clicked rather than typed into."""
    handle: Any = None
    """Opaque back-reference to the live element, unused during matching.

    Untyped on purpose: it holds a Selenium WebElement in production and a
    BeautifulSoup Tag in tests, and the matching engine must not depend on
    either. Only ``form_filler`` ever touches it.
    """

    @property
    def is_file_input(self) -> bool:
        return self.field_type == "file"

    @property
    def is_choice_input(self) -> bool:
        return self.tag == "select" or self.field_type in {"radio", "checkbox"}

    @property
    def is_combobox(self) -> bool:
        """A scripted dropdown. Typing into one leaves the value uncommitted."""
        return self.widget == "combobox"

    @property
    def is_grouped(self) -> bool:
        """Whether this control belongs to a repeating section."""
        return bool(self.group) and self.group_index >= 0

    def describe(self) -> str:
        """Human readable identifier used in logs and review output."""
        for candidate in (
            self.label,
            self.aria_label,
            self.name,
            self.element_id,
            self.placeholder,
        ):
            if candidate:
                return candidate
        return f"<{self.tag} type={self.field_type}>"


@dataclass
class FieldMatch:
    """The engine's decision about one form field."""

    form_field: FormField
    canonical: str = ""
    confidence: float = 0.0
    value: str = ""
    status: FillStatus = FillStatus.SKIPPED_NO_MATCH
    reason: str = ""

    @property
    def needs_review(self) -> bool:
        """True for anything a human still has to deal with.

        SKIPPED_NO_VALUE belongs here: a required field the resume could not
        supply is exactly the case that must block an automatic submission.
        NOT_APPLICABLE does not, because that question has been answered.
        """
        return self.status not in {FillStatus.FILLED, FillStatus.NOT_APPLICABLE}


@dataclass
class JobPosting:
    """A scraped job listing."""

    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    source: str = ""
    description: str = ""


@dataclass
class ApplicationResult:
    """The outcome of preparing or submitting one application."""

    posting: JobPosting
    status: ApplicationStatus
    matches: list[FieldMatch] = field(default_factory=list)
    message: str = ""

    @property
    def filled_count(self) -> int:
        return sum(1 for match in self.matches if match.status is FillStatus.FILLED)

    @property
    def review_count(self) -> int:
        return sum(1 for match in self.matches if match.needs_review)

    @property
    def required_gaps(self) -> list[FieldMatch]:
        """Required fields the engine could not confidently fill."""
        return [m for m in self.matches if m.form_field.required and m.needs_review]

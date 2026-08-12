"""The field mapping engine.

This module answers one question: given an arbitrary form control scraped from
an application page, which piece of candidate data belongs in it, and how sure
are we? It contains no browser code at all, which is what makes it testable
against saved HTML fixtures.

The scoring model is deliberately simple and inspectable:

1. If the control carries a standard HTML ``autocomplete`` token, trust it
   completely. Browsers and ATS vendors both honour these tokens, so an exact
   match short circuits everything else with full confidence.
2. Otherwise, score each descriptive attribute (label, aria-label, name, id,
   placeholder) against a list of regular expressions per canonical field.
   Attributes that a human would find most authoritative carry more weight.
3. Disqualify a candidate outright if a negative pattern fires. This is what
   stops "Confirm Email" from being treated as "Email" and what stops
   "Company Name" from being treated as "Name".
4. Resolve the whole form at once with a greedy one to one assignment so two
   controls can never claim the same piece of data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .models import FieldMatch, FillStatus, FormField, ResumeData
from .profile import Profile

# How much to trust each descriptive attribute. A visible <label> is what the
# human applicant reads, so it outranks internal identifiers.
ATTRIBUTE_WEIGHTS: dict[str, float] = {
    "label": 1.00,
    "aria_label": 0.95,
    "name": 0.90,
    "element_id": 0.85,
    "placeholder": 0.80,
}

DEFAULT_CONFIDENCE_THRESHOLD = 0.55

# Standard autocomplete tokens mapped to our canonical field names.
AUTOCOMPLETE_TOKENS: dict[str, str] = {
    "given-name": "first_name",
    "additional-name": "middle_name",
    "family-name": "last_name",
    "name": "full_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "street-address": "address_line1",
    "address-line1": "address_line1",
    "address-level2": "city",
    "address-level1": "state",
    "postal-code": "postal_code",
    "country": "country",
    "country-name": "country",
    "organization": "current_company",
    "organization-title": "current_title",
    "url": "portfolio_url",
}


class FillPolicy(str, Enum):
    """Whether the engine is allowed to answer a field on the user's behalf."""

    AUTO = "auto"
    """Factual data straight off the resume. Safe to fill."""

    REVIEW_ONLY = "review_only"
    """Recognised, but never auto answered. Demographics, salary expectations,
    work authorisation and free text essays are personal or strategic choices
    that the applicant must make themselves."""


@dataclass(frozen=True)
class CanonicalField:
    """One piece of candidate data the engine knows how to recognise."""

    name: str
    patterns: tuple[tuple[str, float], ...]
    negatives: tuple[str, ...] = ()
    policy: FillPolicy = FillPolicy.AUTO
    allow_multiple: bool = False
    note: str = ""

    def best_pattern_score(self, text: str) -> float:
        """Highest strength among patterns matching ``text``, or zero."""
        if not text:
            return 0.0
        for negative in self.negatives:
            if re.search(negative, text):
                return 0.0
        best = 0.0
        for pattern, strength in self.patterns:
            if re.search(pattern, text) and strength > best:
                best = strength
        return best


# Applied to every canonical field. "email address" must not read as a street
# address, and a file input's own filename must not read as a person's name.
_ADDRESS_NEGATIVES = (r"\bemail\b", r"\be-mail\b", r"\bip\b", r"\bweb\b")
_NAME_NEGATIVES = (
    r"\bcompany\b",
    r"\bemployer\b",
    r"\bschool\b",
    r"\buniversity\b",
    r"\bfile\b",
    r"\buser\s*name\b",
    r"\breference\b",
    r"\bmanager\b",
)

CANONICAL_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        name="first_name",
        patterns=(
            (r"\bfirst\s*name\b", 1.0),
            (r"\bgiven\s*name\b", 1.0),
            (r"\bforename\b", 0.95),
            (r"\bfname\b", 0.95),
            (r"^first$", 0.85),
        ),
        negatives=_NAME_NEGATIVES,
    ),
    CanonicalField(
        name="last_name",
        patterns=(
            (r"\blast\s*name\b", 1.0),
            (r"\bfamily\s*name\b", 1.0),
            (r"\bsurname\b", 0.95),
            (r"\blname\b", 0.95),
            (r"^last$", 0.85),
        ),
        negatives=_NAME_NEGATIVES,
    ),
    CanonicalField(
        name="full_name",
        patterns=(
            (r"\bfull\s*name\b", 1.0),
            (r"\blegal\s*name\b", 0.95),
            (r"\bcandidate\s*name\b", 0.95),
            (r"\byour\s*name\b", 0.90),
            (r"^name$", 0.80),
            (r"^applicant$", 0.75),
        ),
        negatives=_NAME_NEGATIVES
        + (r"\bfirst\b", r"\blast\b", r"\bmiddle\b", r"\bpreferred\b", r"\bmaiden\b"),
    ),
    CanonicalField(
        name="email",
        patterns=(
            (r"\be-?mail\b", 1.0),
            (r"^email\s*address$", 1.0),
        ),
        negatives=(r"\bconfirm\b", r"\bverify\b", r"\bre-?enter\b", r"\brepeat\b", r"\bagain\b"),
    ),
    CanonicalField(
        name="confirm_email",
        patterns=(
            (r"(confirm|verify|re-?enter|repeat)\w*\s*e-?mail", 1.0),
            (r"e-?mail\s*(confirmation|again|verification)", 1.0),
        ),
    ),
    CanonicalField(
        name="phone",
        patterns=(
            (r"\bphone\b", 1.0),
            (r"\bmobile\b", 0.95),
            (r"\bcell\b", 0.95),
            (r"\btelephone\b", 1.0),
            (r"^tel$", 0.90),
            (r"\bcontact\s*number\b", 0.90),
        ),
        negatives=(r"\bcountry\s*code\b", r"\bextension\b", r"\bext\b", r"\btype\b"),
    ),
    CanonicalField(
        name="address_line1",
        patterns=(
            (r"\baddress\s*line\s*1\b", 1.0),
            (r"\bstreet\s*address\b", 1.0),
            (r"\bstreet\b", 0.90),
            (r"^address$", 0.85),
        ),
        negatives=_ADDRESS_NEGATIVES + (r"\bline\s*2\b", r"\bapt\b", r"\bsuite\b"),
    ),
    CanonicalField(
        name="city",
        patterns=(
            (r"\bcity\b", 1.0),
            (r"\btown\b", 0.90),
            (r"\blocality\b", 0.85),
        ),
    ),
    CanonicalField(
        name="state",
        patterns=(
            (r"\bstate\b", 1.0),
            (r"\bprovince\b", 1.0),
            (r"\bregion\b", 0.85),
        ),
        # "Search by country/region or code" is a phone dialling-code picker.
        # Matching it on "region" typed the applicant's state into it.
        negatives=(
            r"\bunited\s*states\b",
            r"\bstatement\b",
            r"\bcountry\b",
            r"\bcode\b",
            r"\bphone\b",
            r"\bdial\b",
        ),
    ),
    CanonicalField(
        name="postal_code",
        patterns=(
            (r"\bzip\b", 1.0),
            (r"\bpostal\s*code\b", 1.0),
            (r"\bpost\s*code\b", 1.0),
        ),
    ),
    CanonicalField(
        name="country",
        patterns=((r"\bcountry\b", 1.0),),
        negatives=(r"\bcode\b",),
    ),
    CanonicalField(
        name="linkedin_url",
        patterns=(
            (r"\blinked\s*in\b", 1.0),
            (r"\blinkedin\s*(profile|url)\b", 1.0),
        ),
    ),
    CanonicalField(
        name="github_url",
        patterns=(
            (r"\bgit\s*hub\b", 1.0),
            (r"\bgitlab\b", 0.80),
        ),
    ),
    CanonicalField(
        name="portfolio_url",
        patterns=(
            (r"\bportfolio\b", 1.0),
            (r"\bpersonal\s*(site|website)\b", 1.0),
            (r"\bwebsite\b", 0.85),
            (r"\bblog\b", 0.80),
        ),
        negatives=(r"\bcompany\b", r"\bemployer\b"),
    ),
    CanonicalField(
        name="resume_file",
        patterns=(
            (r"\bresume\b", 1.0),
            (r"\bcurriculum\s*vitae\b", 1.0),
            (r"\bcv\b", 0.90),
            (r"\bupload\b", 0.70),
            (r"\battach\b", 0.70),
        ),
        # Never the avatar control. SmartRecruiters labels it "Upload profile
        # image", which the generic "upload" pattern otherwise matches.
        negatives=(
            r"\bcover\s*letter\b",
            r"\btranscript\b",
            r"\bportfolio\b",
            r"\bphoto\b",
            r"\bimage\b",
            r"\bpicture\b",
            r"\bavatar\b",
            r"\bheadshot\b",
            r"\blogo\b",
        ),
    ),
    CanonicalField(
        name="cover_letter",
        patterns=(
            (r"\bcover\s*letter\b", 1.0),
            (r"\bletter\s*of\s*interest\b", 0.95),
        ),
    ),
    CanonicalField(
        name="current_company",
        patterns=(
            (r"\bcurrent\s*(employer|company)\b", 1.0),
            (r"\bmost\s*recent\s*(employer|company)\b", 1.0),
            (r"\bemployer\b", 0.90),
            (r"\bcompany\b", 0.85),
            (r"\borganization\b", 0.80),
        ),
        negatives=(r"\bapply\b", r"\bposition\b", r"\bwhy\b"),
    ),
    CanonicalField(
        name="current_title",
        patterns=(
            (r"\bcurrent\s*(title|role|position)\b", 1.0),
            (r"\bjob\s*title\b", 1.0),
            (r"\bmost\s*recent\s*title\b", 1.0),
            (r"\boccupation\b", 0.90),
            (r"^title$", 0.80),
        ),
        negatives=(r"\bmr\b", r"\bms\b", r"\bsalutation\b", r"\bprefix\b"),
    ),
    CanonicalField(
        name="years_experience",
        patterns=(
            (r"\byears?\s*(of\s*)?experience\b", 1.0),
            (r"\byears?\s*in\s*(the\s*)?(field|industry|role)\b", 0.90),
        ),
    ),
    CanonicalField(
        name="school",
        patterns=(
            (r"\bschool\b", 1.0),
            (r"\buniversity\b", 1.0),
            (r"\bcollege\b", 0.95),
            (r"\binstitution\b", 0.90),
        ),
        negatives=(r"\bhigh\s*school\s*only\b",),
    ),
    CanonicalField(
        name="degree",
        patterns=(
            (r"\bdegree\b", 1.0),
            (r"\bqualification\b", 0.85),
            (r"\beducation\s*level\b", 0.90),
        ),
    ),
    CanonicalField(
        name="major",
        patterns=(
            (r"\bmajor\b", 1.0),
            (r"\bfield\s*of\s*study\b", 1.0),
            (r"\bdiscipline\b", 0.90),
            (r"\bconcentration\b", 0.85),
        ),
    ),
    CanonicalField(
        name="graduation_year",
        patterns=(
            (r"\bgraduation\s*(year|date)\b", 1.0),
            (r"\bgrad\s*year\b", 1.0),
            (r"\byear\s*of\s*graduation\b", 1.0),
        ),
    ),
    # Everything below is recognised so it can be reported as a gap, but is
    # never answered automatically.
    CanonicalField(
        name="desired_salary",
        patterns=(
            (r"\b(desired|expected|requested)\s*(salary|compensation|pay|rate)\b", 1.0),
            (r"\bsalary\s*(expectation|requirement)s?\b", 1.0),
            (r"\bcompensation\b", 0.80),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        note="Negotiation decision. Answer this yourself.",
    ),
    CanonicalField(
        name="work_authorization",
        patterns=(
            (r"\b(legally\s*)?authoriz\w*\s*to\s*work\b", 1.0),
            (r"\bwork\s*authoriz\w*\b", 1.0),
            (r"\bright\s*to\s*work\b", 1.0),
            (r"\bvisa\s*status\b", 0.95),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Legal declaration. Answer this yourself.",
    ),
    CanonicalField(
        name="sponsorship",
        patterns=(
            (r"\bsponsorship\b", 1.0),
            (r"\brequire\s*sponsor\w*\b", 1.0),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Legal declaration. Answer this yourself.",
    ),
    CanonicalField(
        name="demographic",
        patterns=(
            (r"\bgender\b", 1.0),
            (r"\brace\b", 1.0),
            (r"\bethnicity\b", 1.0),
            (r"\bveteran\b", 1.0),
            (r"\bdisability\b", 1.0),
            (r"\bhispanic\b", 1.0),
            (r"\bpronouns\b", 0.90),
            (r"\bsexual\s*orientation\b", 1.0),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Voluntary self identification. Left blank by design.",
    ),
    CanonicalField(
        name="how_did_you_hear",
        patterns=(
            (r"\bhow\s*did\s*you\s*hear\b", 1.0),
            (r"\breferral\s*source\b", 0.95),
            (r"\bsource\b", 0.70),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        note="Site specific dropdown. Answer this yourself.",
    ),
    CanonicalField(
        name="free_text_question",
        patterns=(
            (r"\bwhy\s*(do\s*you\s*want|are\s*you\s*interested)\b", 1.0),
            (r"\btell\s*(us|me)\s*about\b", 0.95),
            (r"\badditional\s*information\b", 0.85),
            # Ashby's prompts open with these and matched nothing before.
            (r"\bdescribe\s+(a|an|your|the)\b", 1.0),
            (r"\bwhat.?s\s+something\b", 1.0),
            (r"\bwalk\s+(us|me)\s+through\b", 0.95),
            (r"\bgive\s+(us|me)\s+an\s+example\b", 0.95),
            (r"\bhow\s+(would|did)\s+you\b", 0.90),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Custom essay question. Answer this yourself.",
    ),
    # Self-identification questions are often rendered as a bare list of radios
    # with no group label at all, so the only text available is the option
    # itself: "Man", "Woman", "Under 30". Matching the question text alone left
    # every one of those merely unrecognised, which loses the reason it must not
    # be answered automatically. Restricted to choice controls, where these
    # phrasings are unambiguous.
    CanonicalField(
        name="demographic_option",
        patterns=(
            (r"^(man|woman)$", 1.0),
            (r"\bnon.?binary\b", 1.0),
            (r"\bgender\s*identity\b", 1.0),
            (r"\btransgender\b", 1.0),
            (r"^under\s*\d+$", 1.0),
            # Normalisation strips punctuation, so "30-39" arrives as "30 39".
            (r"^\d{2}\s*-?\s*\d{2}$", 0.95),
            (r"^\d+\s*or\s*(older|over|above)$", 1.0),
            (r"\bprefer\s*not\s*to\s*(answer|say|disclose)\b", 1.0),
            (r"\bdecline\s*to\s*(self.?identify|answer|state)\b", 1.0),
            (r"\bhispanic\s*or\s*latino\b", 1.0),
            (r"\bblack\s*or\s*african\s*american\b", 1.0),
            (r"\bamerican\s*indian\b", 1.0),
            (r"\bnative\s*hawaiian\b", 1.0),
            (r"\btwo\s*or\s*more\s*races\b", 1.0),
            (r"\bprotected\s*veteran\b", 1.0),
            (r"\byes,?\s*i\s*have\s*a\s*disability\b", 1.0),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Voluntary self identification. Left blank by design.",
    ),
)

# Fields that only exist inside a repeating section. Workday's work history is
# ten identical rows, so "Job Title" in row three means the third job, not a
# duplicate of the first. These are matched only against grouped controls, which
# keeps them from competing with the flat-form canonicals above.
ENTRY_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField(
        name="entry_title",
        patterns=(
            (r"\bjob\s*title\b", 1.0),
            (r"\bposition\s*title\b", 1.0),
            (r"^title$", 0.90),
            (r"\brole\b", 0.85),
        ),
        negatives=(r"\bsalutation\b", r"\bprefix\b"),
    ),
    CanonicalField(
        name="entry_company",
        patterns=(
            (r"\bcompany\s*name\b", 1.0),
            (r"\bemployer\b", 1.0),
            (r"^company$", 0.95),
            (r"\borganization\b", 0.85),
        ),
    ),
    CanonicalField(
        name="entry_location",
        patterns=(
            (r"^location$", 1.0),
            (r"\bcity\b", 0.85),
        ),
    ),
    CanonicalField(
        name="entry_description",
        patterns=(
            (r"\brole\s*description\b", 1.0),
            (r"\bdescription\b", 0.90),
            (r"\bresponsibilities\b", 0.90),
        ),
    ),
    CanonicalField(
        name="entry_start_month",
        patterns=((r"\bfrom\s*month\b", 1.0), (r"\bstart\s*date\s*month\b", 1.0)),
    ),
    CanonicalField(
        name="entry_start_year",
        patterns=((r"\bfrom\s*year\b", 1.0), (r"\bstart\s*date\s*year\b", 1.0)),
    ),
    CanonicalField(
        name="entry_end_month",
        patterns=((r"\bto\s*month\b", 1.0), (r"\bend\s*date\s*month\b", 1.0)),
    ),
    CanonicalField(
        name="entry_end_year",
        patterns=((r"\bto\s*year\b", 1.0), (r"\bend\s*date\s*year\b", 1.0)),
    ),
    CanonicalField(
        name="entry_currently_here",
        patterns=(
            (r"\bcurrently\s*work\s*here\b", 1.0),
            (r"\bi\s*currently\s*work\b", 1.0),
            (r"\bpresent\b", 0.80),
        ),
    ),
    CanonicalField(
        name="entry_school",
        patterns=(
            (r"\bschool\s*name\b", 1.0),
            (r"\bschool\b", 0.95),
            (r"\buniversity\b", 0.95),
            (r"\bcollege\b", 0.90),
            (r"\binstitution\b", 0.90),
        ),
    ),
    CanonicalField(
        name="entry_degree",
        patterns=((r"\bdegree\b", 1.0), (r"\bqualification\b", 0.85)),
    ),
    CanonicalField(
        name="entry_field_of_study",
        patterns=(
            (r"\bfield\s*of\s*study\b", 1.0),
            (r"\bmajor\b", 0.95),
            (r"\bdiscipline\b", 0.90),
        ),
    ),
    CanonicalField(
        name="entry_gpa",
        patterns=((r"\bgrade\s*average\b", 1.0), (r"\bgpa\b", 1.0)),
        policy=FillPolicy.REVIEW_ONLY,
        note="Verify against your transcript before entering.",
    ),
)

# Group names mapped to the resume collection their rows draw from.
GROUP_SOURCES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("experience", "employment", "work", "job", "position"), "positions"),
    (("education", "school", "degree", "academic"), "education"),
)


def group_source(group_name: str) -> str:
    """Which resume collection a repeating section should be filled from."""
    lowered = normalize(group_name)
    for keywords, source in GROUP_SOURCES:
        if any(keyword in lowered for keyword in keywords):
            return source
    return ""


_BY_NAME: dict[str, CanonicalField] = {f.name: f for f in (*CANONICAL_FIELDS, *ENTRY_FIELDS)}


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    Also splits camelCase and snake_case identifiers so that a control named
    ``firstName`` or ``first_name`` normalises to ``first name``.
    """
    if not text:
        return ""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    lowered = re.sub(r"[^a-z0-9]+", " ", spaced.lower())
    return re.sub(r"\s+", " ", lowered).strip()


def score_field(form_field: FormField, canonical: CanonicalField) -> float:
    """Confidence in the range 0.0 to 1.0 that ``form_field`` holds ``canonical``."""
    autocomplete = normalize(form_field.autocomplete).replace(" ", "-")
    if autocomplete and AUTOCOMPLETE_TOKENS.get(autocomplete) == canonical.name:
        return 1.0

    # A file input that takes documents is a resume upload even with no label
    # at all, which is exactly how SmartRecruiters ships it.
    if (
        canonical.name == "resume_file"
        and form_field.is_file_input
        and accept_kind(form_field.accept) == "document"
    ):
        return max(DOCUMENT_UPLOAD_CONFIDENCE, _best_attribute_score(form_field, canonical))

    # A textarea with a long or interrogative label is an essay prompt whatever
    # its exact wording. Employers phrase these however they like, so a pattern
    # list will always trail behind; the shape of the control is the reliable
    # signal, and the consequence of catching one is only that the applicant is
    # told to answer it themselves.
    if (
        canonical.name == "free_text_question"
        and form_field.tag == "textarea"
        and _looks_like_a_prompt(form_field.label or form_field.aria_label)
    ):
        return max(ESSAY_PROMPT_CONFIDENCE, _best_attribute_score(form_field, canonical))

    return _best_attribute_score(form_field, canonical)


ESSAY_PROMPT_CONFIDENCE = 0.70
MIN_PROMPT_LENGTH = 40


def _looks_like_a_prompt(label: str) -> bool:
    text = label.strip()
    return bool(text) and (text.endswith("?") or len(text) >= MIN_PROMPT_LENGTH)


def _best_attribute_score(form_field: FormField, canonical: CanonicalField) -> float:
    """Highest weighted pattern match across the control's descriptive attributes."""
    best = 0.0
    for attribute, weight in ATTRIBUTE_WEIGHTS.items():
        text = normalize(getattr(form_field, attribute, ""))
        strength = canonical.best_pattern_score(text)
        if strength:
            best = max(best, strength * weight)
    return round(best, 4)


IMAGE_EXTENSIONS = frozenset(
    [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".jfif",
        ".svg",
        ".heic",
        ".heif",
        ".avif",
    ]
)
DOCUMENT_EXTENSIONS = frozenset(
    [
        ".pdf",
        ".doc",
        ".docx",
        ".dot",
        ".dotx",
        ".rtf",
        ".txt",
        ".odt",
        ".pages",
        ".resume",
        ".rsm",
        ".rmr",
        ".wpd",
        ".abw",
    ]
)
DOCUMENT_UPLOAD_CONFIDENCE = 0.75


def accept_kind(accept: str) -> str:
    """Classify a file input's accept list as "image", "document" or unknown.

    SmartRecruiters labels its avatar control "Upload profile image" and its
    resume control not at all, so the label is the weaker signal here and the
    accept list is the strong one.
    """
    text = accept.lower()
    if not text.strip():
        return ""
    if "image/" in text:
        return "image"
    if "application/pdf" in text or "application/msword" in text:
        return "document"

    tokens = {token.strip() for token in text.split(",") if token.strip()}
    extensions = {token if token.startswith(".") else f".{token}" for token in tokens}
    if extensions & DOCUMENT_EXTENSIONS:
        return "document"
    if extensions and extensions <= IMAGE_EXTENSIONS:
        return "image"
    return ""


def _is_type_compatible(form_field: FormField, canonical_name: str) -> bool:
    """Reject pairings that cannot possibly work, such as text into a file input."""
    if canonical_name == "demographic_option":
        # Only meaningful for a bare option in a choice group. A free text field
        # containing the word "woman" is not a self-identification control.
        return form_field.is_choice_input
    if form_field.is_file_input:
        # An input that only takes images is never the resume upload, whatever
        # its label says. Without this the engine uploads the resume PDF into
        # the profile photo field.
        if accept_kind(form_field.accept) == "image":
            return False
        return canonical_name in {"resume_file", "cover_letter"}
    if canonical_name in {"resume_file", "cover_letter"}:
        return True
    if form_field.field_type == "email":
        return canonical_name in {"email", "confirm_email"}
    return True


def match_form(
    fields: list[FormField],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[FieldMatch]:
    """Assign canonical fields to form controls.

    The one to one constraint applies within a scope, not across the whole
    document. Ungrouped controls share one scope; each row of a repeating
    section is its own. Without that, Workday's ten work-history rows look like
    nine duplicates of the first and only one row ever gets filled.

    Within a scope, candidate pairings are sorted by confidence and consumed
    highest first, so a strong "First Name" match claims that control before a
    weaker generic "Name" match can. Fields flagged ``allow_multiple`` may be
    assigned more than once, because forms routinely ask several demographic
    questions.
    """
    scopes: dict[tuple[str, int], list[int]] = {}
    for index, form_field in enumerate(fields):
        key = (form_field.group, form_field.group_index) if form_field.is_grouped else ("", -1)
        scopes.setdefault(key, []).append(index)

    matches_by_index: dict[int, FieldMatch] = {}
    for (group_name, _row), member_indexes in scopes.items():
        vocabulary = ENTRY_FIELDS if group_name else CANONICAL_FIELDS
        for index, match in _match_scope(fields, member_indexes, vocabulary, threshold).items():
            matches_by_index[index] = match

    return [matches_by_index[index] for index in range(len(fields))]


DUPLICATE_MIN_CONFIDENCE = 0.80


def _descriptor_signature(form_field: FormField) -> str:
    """What a human would read as this control's identity.

    Two controls sharing a signature are the same question asked twice, not two
    different questions that happen to look alike.
    """
    for attribute in ("label", "aria_label", "name", "element_id"):
        text = normalize(getattr(form_field, attribute, ""))
        if text:
            return text
    return ""


def _assign_duplicate_controls(
    fields: list[FormField],
    member_indexes: list[int],
    assignment: dict[int, tuple[str, float]],
) -> None:
    """Let an identical control take the same value as the one that claimed it.

    One to one assignment exists so two different controls cannot claim the same
    data. It is wrong in exactly one case: a form that asks the same question
    twice. iCIMS renders First Name, Last Name and Email once to register an
    account and again for the profile, both marked required, so the second set
    was left blank and blocked submission.

    Restricted to confident matches on non-file controls. Uploading the same
    document to two file inputs is a different decision, since the second is
    usually a cover letter rather than a duplicate.
    """
    claimed_by_signature: dict[str, tuple[str, float]] = {}
    for index, (canonical_name, confidence) in assignment.items():
        if confidence < DUPLICATE_MIN_CONFIDENCE or fields[index].is_file_input:
            continue
        signature = _descriptor_signature(fields[index])
        if signature:
            claimed_by_signature.setdefault(signature, (canonical_name, confidence))

    for index in member_indexes:
        if index in assignment or fields[index].is_file_input:
            continue
        signature = _descriptor_signature(fields[index])
        duplicate = claimed_by_signature.get(signature) if signature else None
        if duplicate:
            assignment[index] = duplicate


def _match_scope(
    fields: list[FormField],
    member_indexes: list[int],
    vocabulary: tuple[CanonicalField, ...],
    threshold: float,
) -> dict[int, FieldMatch]:
    """Run the greedy assignment across one independent scope."""
    candidates: list[tuple[float, int, str]] = []
    for index in member_indexes:
        form_field = fields[index]
        for canonical in vocabulary:
            if not _is_type_compatible(form_field, canonical.name):
                continue
            confidence = score_field(form_field, canonical)
            if confidence >= threshold:
                candidates.append((confidence, index, canonical.name))

    # Sort by confidence descending, then by field order for determinism.
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    claimed_fields: set[int] = set()
    claimed_canonicals: set[str] = set()
    assignment: dict[int, tuple[str, float]] = {}

    for confidence, index, canonical_name in candidates:
        if index in claimed_fields:
            continue
        canonical = _BY_NAME[canonical_name]
        if canonical_name in claimed_canonicals and not canonical.allow_multiple:
            continue
        assignment[index] = (canonical_name, confidence)
        claimed_fields.add(index)
        claimed_canonicals.add(canonical_name)

    _assign_duplicate_controls(fields, member_indexes, assignment)

    matches: dict[int, FieldMatch] = {}
    for index in member_indexes:
        form_field = fields[index]
        if index not in assignment:
            matches[index] = FieldMatch(
                form_field=form_field,
                status=FillStatus.SKIPPED_NO_MATCH,
                reason="No canonical field scored above the confidence threshold.",
            )
            continue
        canonical_name, confidence = assignment[index]
        canonical = _BY_NAME[canonical_name]
        matches[index] = FieldMatch(
            form_field=form_field,
            canonical=canonical_name,
            confidence=confidence,
            status=FillStatus.SKIPPED_BY_POLICY
            if canonical.policy is FillPolicy.REVIEW_ONLY
            else FillStatus.SKIPPED_NO_VALUE,
            reason=canonical.note if canonical.policy is FillPolicy.REVIEW_ONLY else "",
        )
    return matches


def resolve_value(canonical_name: str, resume: ResumeData, *, resume_path: str = "") -> str:
    """Look up the value for a canonical field from parsed resume data.

    Returns an empty string when the resume did not supply the value, which the
    caller reports as a gap rather than filling with a guess.
    """
    education = resume.latest_education
    lookup: dict[str, str] = {
        "first_name": resume.first_name,
        "last_name": resume.last_name,
        "full_name": resume.full_name,
        "email": resume.email,
        "confirm_email": resume.email,
        "phone": resume.phone,
        "address_line1": resume.address_line1,
        "city": resume.city,
        "state": resume.state,
        "postal_code": resume.postal_code,
        "country": resume.country,
        "linkedin_url": resume.linkedin_url,
        "github_url": resume.github_url,
        "portfolio_url": resume.portfolio_url,
        "current_company": resume.current_company,
        "current_title": resume.current_title,
        "resume_file": resume_path,
        "school": education.school if education else "",
        "degree": education.degree_display if education else "",
        "major": education.major if education else "",
        "graduation_year": education.graduation_year if education else "",
    }
    return lookup.get(canonical_name, "")


_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}  # fmt: skip


def _split_date(value: str) -> tuple[str, str]:
    """Split a resume date such as "Jul 2021" into ("07", "2021").

    Workday renders each date as separate month and year inputs, so a single
    string cannot be typed into either one.
    """
    text = value.strip()
    if not text or text.lower() in {"present", "current", "now"}:
        return "", ""
    year = ""
    year_match = re.search(r"(19|20)\d{2}", text)
    if year_match:
        year = year_match.group(0)
    month = ""
    month_match = re.search(r"[A-Za-z]{3}", text)
    if month_match:
        month = _MONTHS.get(month_match.group(0).lower(), "")
    if not month:
        numeric = re.match(r"\s*(\d{1,2})\s*/", text)
        if numeric:
            month = f"{int(numeric.group(1)):02d}"
    return month, year


def resolve_entry_value(
    canonical_name: str,
    resume: ResumeData,
    group_name: str,
    row: int,
) -> str:
    """Look up a value for one row of a repeating section.

    Row 0 is the most recent entry, matching how both resumes and application
    forms order history. A row beyond what the resume supplies returns empty and
    is reported as a gap.
    """
    source = group_source(group_name)

    if source == "positions":
        if row >= len(resume.positions):
            return ""
        position = resume.positions[row]
        start_month, start_year = _split_date(position.start_date)
        end_month, end_year = _split_date(position.end_date)
        return {
            "entry_title": position.title,
            "entry_company": position.company,
            "entry_description": position.description,
            "entry_start_month": start_month,
            "entry_start_year": start_year,
            "entry_end_month": end_month,
            "entry_end_year": end_year,
            "entry_currently_here": "yes" if position.is_current else "",
            "entry_location": position.location,
        }.get(canonical_name, "")

    if source == "education":
        if row >= len(resume.education):
            return ""
        entry = resume.education[row]
        return {
            "entry_school": entry.school,
            "entry_degree": entry.degree_display,
            "entry_field_of_study": entry.major,
            "entry_end_year": entry.graduation_year,
        }.get(canonical_name, "")

    return ""


def plan_fill(
    fields: list[FormField],
    resume: ResumeData,
    *,
    resume_path: str = "",
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    profile: Profile | None = None,
) -> list[FieldMatch]:
    """Full planning pass: match every control, then attach the value to use.

    The result is a complete, inspectable plan. Nothing has touched a browser
    at this point, so the plan can be printed for review or diffed in tests.
    """
    matches = match_form(fields, threshold=threshold)
    for match in matches:
        if not match.canonical:
            continue
        field = match.form_field

        # A value the applicant wrote into their own profile is their answer,
        # already considered, so it may settle a question the engine would
        # otherwise refuse to touch. The plan says where it came from.
        supplied = profile.get(match.canonical) if profile else ""
        if supplied:
            match.value = supplied
            match.status = FillStatus.FILLED
            match.reason = "From your profile."
            continue

        if match.status is FillStatus.SKIPPED_BY_POLICY:
            continue

        if field.is_grouped:
            value = resolve_entry_value(match.canonical, resume, field.group, field.group_index)
        else:
            value = resolve_value(match.canonical, resume, resume_path=resume_path)
        if value:
            match.value = value
            match.status = FillStatus.FILLED
        else:
            match.status = FillStatus.SKIPPED_NO_VALUE
            match.reason = (
                f'Not in your resume. Add "{match.canonical}" to your profile '
                "file to fill this automatically."
            )
    return matches

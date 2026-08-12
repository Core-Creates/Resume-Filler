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
        negatives=(r"\bunited\s*states\b", r"\bstatement\b"),
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
        negatives=(r"\bcover\s*letter\b", r"\btranscript\b", r"\bportfolio\b"),
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
            (r"\btell\s*us\s*about\b", 0.95),
            (r"\badditional\s*information\b", 0.85),
        ),
        policy=FillPolicy.REVIEW_ONLY,
        allow_multiple=True,
        note="Custom essay question. Answer this yourself.",
    ),
)

_BY_NAME: dict[str, CanonicalField] = {f.name: f for f in CANONICAL_FIELDS}


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

    best = 0.0
    for attribute, weight in ATTRIBUTE_WEIGHTS.items():
        text = normalize(getattr(form_field, attribute, ""))
        strength = canonical.best_pattern_score(text)
        if strength:
            best = max(best, strength * weight)
    return round(best, 4)


def _is_type_compatible(form_field: FormField, canonical_name: str) -> bool:
    """Reject pairings that cannot possibly work, such as text into a file input."""
    if canonical_name in {"resume_file", "cover_letter"}:
        return True
    if form_field.is_file_input:
        return canonical_name in {"resume_file", "cover_letter"}
    if form_field.field_type == "email":
        return canonical_name in {"email", "confirm_email"}
    return True


def match_form(
    fields: list[FormField],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[FieldMatch]:
    """Assign canonical fields to form controls with a greedy one to one pass.

    Candidate pairings are sorted by confidence and consumed highest first, so
    a strong "First Name" match claims that control before a weaker generic
    "Name" match can. Fields flagged ``allow_multiple`` may be assigned to more
    than one control because forms routinely ask several demographic questions.
    """
    candidates: list[tuple[float, int, str]] = []
    for index, form_field in enumerate(fields):
        for canonical in CANONICAL_FIELDS:
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

    matches: list[FieldMatch] = []
    for index, form_field in enumerate(fields):
        if index not in assignment:
            matches.append(
                FieldMatch(
                    form_field=form_field,
                    status=FillStatus.SKIPPED_NO_MATCH,
                    reason="No canonical field scored above the confidence threshold.",
                )
            )
            continue
        canonical_name, confidence = assignment[index]
        canonical = _BY_NAME[canonical_name]
        matches.append(
            FieldMatch(
                form_field=form_field,
                canonical=canonical_name,
                confidence=confidence,
                status=FillStatus.SKIPPED_BY_POLICY
                if canonical.policy is FillPolicy.REVIEW_ONLY
                else FillStatus.SKIPPED_NO_VALUE,
                reason=canonical.note if canonical.policy is FillPolicy.REVIEW_ONLY else "",
            )
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
        "degree": education.degree if education else "",
        "major": education.major if education else "",
        "graduation_year": education.graduation_year if education else "",
    }
    return lookup.get(canonical_name, "")


def plan_fill(
    fields: list[FormField],
    resume: ResumeData,
    *,
    resume_path: str = "",
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> list[FieldMatch]:
    """Full planning pass: match every control, then attach the value to use.

    The result is a complete, inspectable plan. Nothing has touched a browser
    at this point, so the plan can be printed for review or diffed in tests.
    """
    matches = match_form(fields, threshold=threshold)
    for match in matches:
        if not match.canonical or match.status is FillStatus.SKIPPED_BY_POLICY:
            continue
        value = resolve_value(match.canonical, resume, resume_path=resume_path)
        if value:
            match.value = value
            match.status = FillStatus.FILLED
        else:
            match.status = FillStatus.SKIPPED_NO_VALUE
            match.reason = f"Resume did not supply a value for '{match.canonical}'."
    return matches

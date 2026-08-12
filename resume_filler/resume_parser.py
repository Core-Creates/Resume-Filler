"""Resume parsing.

The original implementation searched the whole document with one regex per
field, which meant the name pattern matched the first pair of capitalised words
anywhere in the first five lines. On a typical resume that is the job title, not
the person.

This version works in two stages. It first splits the document into the standard
resume sections by locating heading lines, then parses each section with rules
appropriate to that section. Contact details are only sought in the header
block, and work history is only sought in the experience block.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import Education, Position, ResumeData

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?:\s*(?:x|ext\.?)\s*\d{1,6})?"
)
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?", re.I)
GITHUB_RE = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9\-_.]+/?", re.I)
# The lookbehind stops the domain half of an email address being read as a URL.
URL_RE = re.compile(r"(?<![@\w])(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+\.[A-Za-z]{2,}(?:/[^\s,;]*)?")
# Horizontal whitespace only. Allowing \s here let a city match run backwards
# across newlines and swallow the name and job title above it.
LOCATION_RE = re.compile(
    r"([A-Z][A-Za-z.\-]+(?:[ \t][A-Z][A-Za-z.\-]+)*),[ \t]*([A-Z]{2})\b(?:[ \t]+(\d{5}(?:-\d{4})?))?"
)

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE})\s*(?:-|–|—|to|through)\s*(?P<end>{_DATE}|Present|Current|Now)",
    re.I,
)
# Non-capturing on purpose. With a capturing group, findall returns just the
# century ("20") instead of the full year.
GRAD_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

# The optional "of Science" tail keeps "Master of Science" whole rather than
# truncating it to "Master".
DEGREE_RE = re.compile(
    r"\b(?:Ph\.?D|Doctorate|M\.?B\.?A|M\.?S\.?c?|M\.?A|Master(?:'s)?|B\.?S\.?c?|B\.?A|B\.?Eng"
    r"|Bachelor(?:'s)?|Associate(?:'s)?|A\.?A\.?S?)\b\.?"
    r"(?:\s+of\s+[A-Z][A-Za-z]+)?",
    re.I,
)
SCHOOL_RE = re.compile(r"\b(University|College|Institute|Academy|School)\b", re.I)
# Only "in", never "of". Matching "of" turned "Master of Science in Computer
# Science" into a major of "Science in Computer Science".
MAJOR_RE = re.compile(r"\bin\s+([A-Z][A-Za-z&]+(?:\s+(?:and\s+)?[A-Z][A-Za-z&]+)*)")

# Heading text mapped to the canonical section key it opens.
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "experience": (
        "work experience",
        "professional experience",
        "employment history",
        "experience",
        "employment",
        "work history",
        "career history",
        "relevant experience",
    ),
    "education": ("education", "academic background", "academic history"),
    "skills": ("skills", "technical skills", "core competencies", "technologies", "expertise"),
    "summary": ("summary", "profile", "objective", "about me", "professional summary"),
    "projects": ("projects", "selected projects", "personal projects"),
    "certifications": ("certifications", "licenses", "certificates"),
}

# Words that mean a header line is a job title or a document label, not a name.
_NOT_A_NAME = re.compile(
    r"\b(resume|curriculum\s*vitae|^cv$|engineer|developer|manager|analyst|scientist|designer"
    r"|consultant|director|architect|administrator|specialist|intern|student|profile|summary"
    r"|objective|contact|phone|email|address|linkedin|github|portfolio)\b",
    re.I,
)


def _normalize_heading(line: str) -> str:
    return re.sub(r"[^a-z\s]", "", line.lower()).strip()


def _is_heading(line: str) -> str | None:
    """Return the canonical section key if ``line`` is a section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 45:
        return None
    normalized = _normalize_heading(stripped)
    if not normalized:
        return None
    # Headings are short, and are typically all caps, title case, or underlined.
    looks_like_heading = (
        stripped.isupper() or stripped.istitle() or stripped.endswith(":") or len(normalized) <= 30
    )
    if not looks_like_heading:
        return None
    for key, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return key
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Split resume text into a header block plus one block per known section."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        heading = _is_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            continue
        if line.strip():
            sections.setdefault(current, []).append(line.rstrip())
    return sections


def extract_name(header_lines: list[str]) -> tuple[str, str]:
    """Find the candidate's name in the header block.

    Looks for the first short line of two to four alphabetic words that is not a
    job title, a contact detail, or a document label. Handles all caps names by
    title casing them.
    """
    for line in header_lines[:10]:
        candidate = line.strip()
        if not candidate or len(candidate) > 60:
            continue
        if "@" in candidate or "http" in candidate.lower() or any(c.isdigit() for c in candidate):
            continue
        if _NOT_A_NAME.search(candidate):
            continue
        # Drop credential suffixes such as "Jane Doe, PhD".
        candidate = re.split(r"[,|]", candidate)[0].strip()
        tokens = [t for t in re.split(r"\s+", candidate) if t]
        if not 2 <= len(tokens) <= 4:
            continue
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z'\-.]*", token) for token in tokens):
            continue
        if candidate.isupper():
            tokens = [token.title() for token in tokens]
        elif not all(token[0].isupper() for token in tokens):
            continue
        return tokens[0], tokens[-1]
    return "", ""


def _extract_contact(text: str, data: ResumeData) -> None:
    email = EMAIL_RE.search(text)
    if email:
        data.email = email.group(0)

    for candidate in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", candidate.group(0))
        # Reject year ranges and zip codes that happen to match.
        if 10 <= len(digits) <= 15:
            data.phone = " ".join(candidate.group(0).split())
            break

    linkedin = LINKEDIN_RE.search(text)
    if linkedin:
        data.linkedin_url = _with_scheme(linkedin.group(0))

    github = GITHUB_RE.search(text)
    if github:
        data.github_url = _with_scheme(github.group(0))

    location = LOCATION_RE.search(text)
    if location:
        data.city, data.state = location.group(1), location.group(2)
        if location.group(3):
            data.postal_code = location.group(3)

    for url in URL_RE.finditer(text):
        raw = url.group(0)
        lowered = raw.lower()
        if any(host in lowered for host in ("linkedin.com", "github.com")):
            continue
        if "@" in raw or lowered.endswith((".pdf", ".docx")):
            continue
        if not re.search(r"\.(com|dev|io|net|org|me|ai|co|xyz|app)\b", lowered):
            continue
        data.portfolio_url = _with_scheme(raw)
        break


def _with_scheme(url: str) -> str:
    return url if url.lower().startswith("http") else f"https://{url}"


US_STATES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
        "PR",
        "VI",
        "GU",
        "AS",
        "MP",
    ]
)


def infer_country(data: ResumeData) -> str:
    """Derive the country when the resume states a location but not a country.

    Resumes almost never write the country out, yet Country is a required field
    on real Greenhouse pages, so leaving it blank blocks every submission. A
    two-letter US state code is unambiguous enough to act on. Anything else is
    left empty and reported as a gap rather than guessed.
    """
    if data.country:
        return data.country
    if data.state.upper() in US_STATES:
        return "United States"
    return ""


def extract_positions(lines: list[str]) -> list[Position]:
    """Parse the experience section into individual roles.

    A role is anchored on a line containing a date range. The company and title
    are taken from that line and the line above it, which covers the two layouts
    that account for most resumes: "Title, Company    Dates" on one line, and
    "Company" then "Title    Dates" on consecutive lines.
    """
    positions: list[Position] = []
    for index, line in enumerate(lines):
        match = DATE_RANGE_RE.search(line)
        if not match:
            continue

        remainder = (line[: match.start()] + " " + line[match.end() :]).strip(" ,|•-\t")
        previous = lines[index - 1].strip(" ,|•-\t") if index > 0 else ""

        parts = [p.strip() for p in re.split(r"\s*[,|]\s*|\s{2,}|\s+at\s+", remainder) if p.strip()]
        title, company = "", ""
        if len(parts) >= 2:
            title, company = parts[0], parts[1]
        elif len(parts) == 1:
            title = parts[0]
            company = previous
        elif previous:
            title = previous

        end = match.group("end")
        positions.append(
            Position(
                company=company,
                title=title,
                start_date=match.group("start"),
                end_date="Present" if end.lower() in {"present", "current", "now"} else end,
            )
        )
    return positions


def _school_from_line(line: str) -> str:
    """Pull just the institution name out of a line, dropping the year and city."""
    for segment in re.split(r"\s*[,|]\s*|\s{2,}", line):
        if SCHOOL_RE.search(segment):
            return segment.strip(" .-•")
    return ""


def extract_education(lines: list[str]) -> list[Education]:
    """Parse the education section into degree entries.

    Entries are accumulated across lines rather than built per line, because the
    common layout puts the degree on one line and the institution on the next.
    A new entry starts whenever a degree appears and the current entry already
    has one.
    """
    entries: list[Education] = []
    current: Education | None = None

    for line in lines:
        degree_match = DEGREE_RE.search(line)
        school = _school_from_line(line)
        if not degree_match and not school:
            continue

        if current is None or (degree_match and current.degree):
            current = Education()
            entries.append(current)

        if degree_match and not current.degree:
            current.degree = degree_match.group(0).strip(". ")
        if school and not current.school:
            current.school = school
        if not current.major:
            major_match = MAJOR_RE.search(line)
            if major_match:
                current.major = major_match.group(1).strip()
        if not current.graduation_year:
            years = GRAD_YEAR_RE.findall(line)
            if years:
                current.graduation_year = max(years)

    return [entry for entry in entries if entry.school or entry.degree]


def extract_skills(lines: list[str]) -> list[str]:
    """Flatten the skills section into a de-duplicated list."""
    skills: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = re.sub(r"^[\s•\-*]+", "", line)
        cleaned = re.sub(r"^[A-Za-z /]{3,25}:\s*", "", cleaned)
        for token in re.split(r"\s*[,;|]\s*|\s{3,}", cleaned):
            skill = token.strip(" .")
            if 1 < len(skill) <= 40 and skill.lower() not in seen:
                seen.add(skill.lower())
                skills.append(skill)
    return skills


def parse_resume_text(text: str) -> ResumeData:
    """Parse already extracted resume text. Kept separate so tests skip the PDF."""
    data = ResumeData(raw_text=text)
    sections = split_sections(text)
    header = sections.get("header", [])

    data.first_name, data.last_name = extract_name(header)

    # Contact details live in the header, but fall back to the whole document
    # for resumes that place them in a footer.
    header_text = "\n".join(header)
    _extract_contact(header_text, data)
    if not data.email or not data.phone:
        _extract_contact(text, data)

    data.country = infer_country(data)

    if sections.get("summary"):
        data.summary = " ".join(sections["summary"]).strip()
    data.skills = extract_skills(sections.get("skills", []))
    data.positions = extract_positions(sections.get("experience", []))
    data.education = extract_education(sections.get("education", []))

    return data


def parse_resume(resume_path: str | Path) -> ResumeData:
    """Extract structured data from a PDF resume.

    Raises ``FileNotFoundError`` if the path does not exist and ``ValueError`` if
    the PDF yields no extractable text, which usually means it is a scanned
    image and needs OCR.
    """
    path = Path(resume_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Resume not found: {path}")

    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            content = page.extract_text()
            if content:
                pages.append(content)

    text = "\n".join(pages)
    if not text.strip():
        raise ValueError(
            f"No extractable text in {path.name}. If this is a scanned resume, "
            "run it through OCR first."
        )

    data = parse_resume_text(text)
    logger.info(
        "Parsed resume: name=%r email=%r phone=%r positions=%d education=%d skills=%d",
        data.full_name,
        data.email,
        data.phone,
        len(data.positions),
        len(data.education),
        len(data.skills),
    )
    return data

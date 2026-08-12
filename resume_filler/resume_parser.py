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
    # Up to three words after "of", so "Bachelor of Business Administration"
    # survives instead of truncating to "Bachelor of Business". The connector
    # exclusions matter because re.I makes [A-Z] match lowercase too, so without
    # them "Master of Science in Computer Science" swallows the "in ..." clause,
    # which belongs to the major rather than the degree.
    r"(?:\s+of\s+(?!in\b|at\b|from\b|with\b)[A-Za-z]+"
    r"(?:\s+(?!in\b|at\b|from\b|with\b)[A-Za-z]+){0,2})?",
    re.I,
)
SCHOOL_RE = re.compile(r"\b(University|College|Institute|Academy|School)\b", re.I)
# An unfinished degree must never be reported as complete.
IN_PROGRESS_RE = re.compile(
    r"\((?:in\s*progress|ongoing|current)\)|\b(?:in\s*progress|expected|anticipated"
    r"|pursuing|candidate\s+for)\b",
    re.I,
)
# Only "in", never "of". Matching "of" turned "Master of Science in Computer
# Science" into a major of "Science in Computer Science".
MAJOR_RE = re.compile(r"\bin\s+([A-Z][A-Za-z&]+(?:\s+(?:and\s+)?[A-Z][A-Za-z&]+)*)")

# Section headings are matched on keywords rather than an exact alias list.
# A real resume used "Career Experience" and "Technical Proficiencies", neither
# of which an exact list anticipated, and the whole document collapsed into the
# header block: zero positions, zero skills. Substring rules degrade gracefully
# against wording nobody predicted.
#
# Order matters. "Academic Experience" should open education, not experience,
# so education is tested first.
SECTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("education", ("education", "academic")),
    ("certifications", ("certification", "certificate", "license", "credential")),
    ("projects", ("project",)),
    (
        "skills",
        (
            "skill",
            "proficien",
            "competenc",
            "technolog",
            "expertise",
            "toolkit",
            "qualification",
        ),
    ),
    ("experience", ("experience", "employment", "work history", "career", "background")),
    ("summary", ("summary", "profile", "objective", "about me", "overview")),
)

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
    # A bullet or a sentence-ending period means prose, not a heading.
    if stripped[0] in "•-*●▪" or stripped.endswith((".", ",", ";")):
        return None
    normalized = _normalize_heading(stripped)
    if not normalized or len(normalized.split()) > 5:
        return None
    # Headings are short, and are typically all caps, title case, or underlined.
    looks_like_heading = (
        stripped.isupper() or stripped.istitle() or stripped.endswith(":") or len(normalized) <= 30
    )
    if not looks_like_heading:
        return None
    for key, keywords in SECTION_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
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


def _strip_contact_details(line: str) -> str:
    """Remove email, phone, URLs and separators, leaving any prose behind.

    Real resumes routinely put everything on one line:
    "Corrina Ray Alcoser  corrinaalcoser@example.com | (210) 555-0100".
    Rejecting any line containing an "@" or a digit misses the name entirely.
    """
    cleaned = EMAIL_RE.sub(" ", line)
    cleaned = re.sub(r"https?://\S+|www\.\S+", " ", cleaned)
    cleaned = PHONE_RE.sub(" ", cleaned)
    # Commas are deliberately preserved: the caller splits on them to drop
    # credential suffixes, so "Jane Doe, PhD" must keep its comma.
    cleaned = re.sub(r"[|•·●]+", " ", cleaned)
    return " ".join(cleaned.split()).strip()


def extract_name(header_lines: list[str]) -> tuple[str, str]:
    """Find the candidate's name in the header block.

    Looks for the first short line of two to four alphabetic words that is not a
    job title, a contact detail, or a document label. Handles all caps names by
    title casing them.
    """
    for line in header_lines[:10]:
        candidate = _strip_contact_details(line)
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


_JOB_TITLE_WORDS = re.compile(
    r"\b(engineer|developer|manager|analyst|scientist|designer|consultant|director"
    r"|architect|administrator|specialist|intern|lead|principal|staff|senior|junior"
    r"|associate|officer|technician|coordinator|supervisor|president|founder|head"
    r"|programmer|researcher|instructor|professor|assistant|clerk|advisor"
    r"|support|operator|representative|trainer|writer|editor|strategist|tester)\b",
    re.I,
)


def _is_plausible_line(line: str) -> bool:
    """Short, non-bullet, non-sentence. Shared precondition for both checks."""
    text = line.strip()
    if not text or len(text) > 70:
        return False
    return not (text[0] in "•-*●▪" or text.endswith((".", ":", ";")))


def _is_role_title(line: str) -> bool:
    """Strict: does this name an actual role?

    Used to judge text sitting on the date line itself, where the alternative is
    an employer name. "USAF A1VDC" and "AT&T Corporate Office" are short and
    capitalised, so a loose test calls them titles and swaps the two fields.
    Only an explicit role word is trusted here.
    """
    return _is_plausible_line(line) and bool(_JOB_TITLE_WORDS.search(line))


def _could_be_title(line: str) -> bool:
    """Loose: could this line be the role title?

    Used only for the line directly adjacent to a date range, where a resume
    almost always puts the title, so the bar is lower. A keyword list alone is
    endless whack-a-mole; "Tier 3 IT Support (Insight Global Contractor)" is
    plainly a title and contains no obvious role noun.
    """
    if not _is_plausible_line(line):
        return False
    if _JOB_TITLE_WORDS.search(line):
        return True

    words = line.split()
    if not 1 < len(words) <= 9:
        return False
    # Prose starts with a verb and runs lowercase; a title is mostly capitalised.
    capitalised = sum(1 for word in words if word[:1].isupper() or word[:1].isdigit())
    return capitalised >= len(words) * 0.6


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
        following = lines[index + 1].strip(" ,|•-\t") if index + 1 < len(lines) else ""

        parts = [p.strip() for p in re.split(r"\s*[,|]\s*|\s{2,}|\s+at\s+", remainder) if p.strip()]
        title, company, location = "", "", ""
        if len(parts) >= 2:
            # One resume can mix both layouts:
            #   "Employer, City, State      Dates"  with the title on the next line
            #   "Title, Employer            Dates"  with everything on one line
            # Deciding by whether the first part reads as a role handles both,
            # rather than assuming a fixed column order.
            if not _is_role_title(parts[0]) and _could_be_title(following):
                company, title = parts[0], following
                # Whatever trails the employer on that line is its location:
                # "ManTech, San Antonio, Texas" leaves "San Antonio, Texas".
                location = ", ".join(parts[1:])
            else:
                title, company = parts[0], parts[1]
                location = ", ".join(parts[2:])
        elif len(parts) == 1:
            # "ManTech, San Antonio, Texas   Jul 2021 to May 2022" with the job
            # title on the line below is a common layout, and reading the title
            # from the line above gets the previous role's bullet instead.
            company = parts[0]
            if _could_be_title(following):
                title = following
            elif _could_be_title(previous):
                title = previous
            else:
                title, company = company, previous
        elif previous:
            title = previous

        end = match.group("end")
        positions.append(
            Position(
                company=company,
                title=title,
                location=location,
                start_date=match.group("start"),
                end_date="Present" if end.lower() in {"present", "current", "now"} else end,
                description=_description_after(lines, index, title),
            )
        )
    return positions


# A page footer sits in the middle of the extracted text and is not prose.
PAGE_FOOTER_RE = re.compile(r"^\s*page\s*\d+\s*(?:[|/]\s*\d+)?\s*$", re.I)


def _description_after(lines: list[str], date_index: int, title: str) -> str:
    """Collect the prose and bullets belonging to one role.

    Runs from just after the date line to the next date line. Workday and most
    ATS ask for a role description, and the text is sitting right there in the
    resume; discarding it leaves a required field empty for no reason.
    """
    collected: list[str] = []
    for line in lines[date_index + 1 :]:
        if DATE_RANGE_RE.search(line):
            break
        text = line.strip()
        if not text or PAGE_FOOTER_RE.match(text):
            continue
        if text == title.strip():
            continue
        collected.append(text)
    return "\n".join(collected).strip()


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

        if IN_PROGRESS_RE.search(line):
            current.in_progress = True
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

    # De-duplicate across the whole list, not just against the previous entry.
    # A real resume repeated the same school and degree further down the
    # section, which then filled two Workday education rows with identical text.
    unique: list[Education] = []
    for entry in entries:
        if not (entry.school or entry.degree):
            continue
        if any((e.school, e.degree) == (entry.school, entry.degree) for e in unique):
            continue
        unique.append(entry)
    return unique


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


# English prose runs roughly one space every six characters. Far below that and
# the PDF's character spacing has defeated the default word-splitting tolerance,
# yielding "SanAntonio,TX" and "Jul2021". Every downstream regex then fails.
MIN_SPACE_RATIO = 0.10
NARROW_WORD_TOLERANCE = 1.5


def _space_ratio(text: str) -> float:
    if not text:
        return 0.0
    return text.count(" ") / len(text)


def _extract_pdf_text(path: Path) -> str:
    """Pull text from a PDF, retrying with tighter word splitting if needed.

    pdfplumber's default x_tolerance of 3 merges adjacent words in some PDFs,
    notably ones exported from Word. The retry is only paid for when the first
    pass looks broken, and the result is kept only if it is genuinely better.
    """
    import pdfplumber

    def _read(**kwargs: object) -> str:
        pages: list[str] = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages:
                content = page.extract_text(**kwargs)
                if content:
                    pages.append(content)
        return "\n".join(pages)

    text = _read()
    if _space_ratio(text) >= MIN_SPACE_RATIO:
        return text

    logger.info(
        "Word spacing in %s looks collapsed (%.3f spaces per character); "
        "re-extracting with a tighter tolerance",
        path.name,
        _space_ratio(text),
    )
    retried = _read(x_tolerance=NARROW_WORD_TOLERANCE)
    return retried if _space_ratio(retried) > _space_ratio(text) else text


def parse_resume(resume_path: str | Path) -> ResumeData:
    """Extract structured data from a PDF resume.

    Raises ``FileNotFoundError`` if the path does not exist and ``ValueError`` if
    the PDF yields no extractable text, which usually means it is a scanned
    image and needs OCR.
    """
    path = Path(resume_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Resume not found: {path}")

    text = _extract_pdf_text(path)
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

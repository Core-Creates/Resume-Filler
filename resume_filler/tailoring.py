"""Per-job tailoring: what a posting asks for versus what your resume says.

Two capabilities:

* ``keyword_gap`` compares the skills and phrases a job description emphasises
  against the ones your resume actually contains, so you can see what is missing
  before you apply rather than after the rejection.
* ``draft_cover_letter`` produces a targeted first draft grounded strictly in
  facts already present in your resume.

The generator never invents experience. It only recombines what the resume
already states, because a cover letter claiming skills you do not have is worse
than no cover letter. Every draft is written to disk for you to edit; nothing is
attached to an application without your review.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .models import JobPosting, ResumeData

logger = logging.getLogger(__name__)

# Words too common to carry signal in a job description. Grouped by why they are
# excluded, and kept as separate string constants so a formatter cannot collapse
# the whole list onto one unreadable line.
_GRAMMAR_WORDS = """
    a an and are as at be been but by for from has have if in into is it its of on or
    our that the their there they this to was were will with you your we us can may
    must should would could able about across after all also any before being both
    during each else ever every here how just like made make many more most much need
    only other over same some such than then these those through under until upon very
    what when where which while who whom why
"""

_POSTING_BOILERPLATE = """
    work working works job role position candidate applicant company team teams
    experience experienced years year strong excellent good great new using use used
    help helping including include ability responsibilities requirements qualifications
    preferred required plus opportunity opportunities benefits salary apply application
    employer equal
"""

# Generic action verbs and seniority labels. Without these the top gaps come back
# as "design" and "own", which tells the applicant nothing actionable.
_GENERIC_VERBS = """
    design designing own owning build building scale scaling deliver delivering
    drive driving lead leading support supporting manage managing ensure ensuring
    develop developing implement implementing maintain maintaining collaborate
    partner partnering senior junior staff principal level mid entry
    seek seeking seeks hiring hire looking want wants deep deeply central extensive
    expertise proven demonstrated familiarity solid hands
"""

STOP_WORDS = frozenset((_GRAMMAR_WORDS + _POSTING_BOILERPLATE + _GENERIC_VERBS).split())

# A capital letter that is not sentence-initial marks a proper noun, which is how
# technologies announce themselves in prose: "Kubernetes", "GraphQL", "Terraform".
# Without this the top gaps come back as "design" and "own", which is noise.
_SENTENCE_START = re.compile(r"(?:^|[.!?:;>\n•\-]\s*)$")
_CAPITALIZED_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,29}")
PROPER_NOUN_WEIGHT = 3

# Multi-word technical phrases worth matching as a unit.
KNOWN_PHRASES = (
    "machine learning",
    "deep learning",
    "natural language processing",
    "computer vision",
    "data engineering",
    "data science",
    "distributed systems",
    "microservices",
    "infrastructure as code",
    "continuous integration",
    "continuous delivery",
    "test driven development",
    "incident response",
    "threat modeling",
    "penetration testing",
    "reverse engineering",
    "cloud security",
    "site reliability",
    "technical writing",
    "project management",
    "product management",
    "code review",
    "unit testing",
    "version control",
    "agile",
    "scrum",
)

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,29}")
HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class KeywordGap:
    """What a posting emphasises, and how much of it your resume covers."""

    posting: JobPosting
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    top_terms: list[tuple[str, int]] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        """Fraction of the posting's emphasised terms your resume contains."""
        total = len(self.matched) + len(self.missing)
        return len(self.matched) / total if total else 0.0

    @property
    def coverage_percent(self) -> int:
        return round(self.coverage * 100)


def strip_html(text: str) -> str:
    """Job board APIs return descriptions as HTML fragments."""
    if "<" not in text:
        return text
    from html import unescape

    return unescape(HTML_TAG_RE.sub(" ", text))


def _normalize_token(token: str) -> str:
    """Strip trailing punctuation while preserving it inside and in front.

    Sentence-final words otherwise tokenize as "engineer." and never match
    "engineer" in the resume. Leading dots are kept so ".net" survives, and
    internal dots so "node.js" does.
    """
    return token.rstrip(".-")


def _tokenize(text: str) -> list[str]:
    lowered = strip_html(text).lower()
    tokens = []
    for raw in TOKEN_RE.findall(lowered):
        token = _normalize_token(raw)
        if len(token) > 1 and token not in STOP_WORDS:
            tokens.append(token)
    for phrase in KNOWN_PHRASES:
        if phrase in lowered:
            tokens.append(phrase)
    return tokens


def _resume_corpus(resume: ResumeData) -> str:
    parts = [
        resume.raw_text,
        resume.summary,
        " ".join(resume.skills),
        " ".join(f"{p.title} {p.company} {p.description}" for p in resume.positions),
        " ".join(f"{e.degree} {e.major} {e.school}" for e in resume.education),
    ]
    return " ".join(part for part in parts if part).lower()


def proper_nouns(text: str) -> set[str]:
    """Lowercased terms that appear as proper nouns in ``text``.

    Two signals count: an internal capital (GraphQL, PostgreSQL, JavaScript) or a
    leading capital somewhere other than the start of a sentence or list item.
    """
    plain = strip_html(text)
    found: set[str] = set()
    for match in _CAPITALIZED_RE.finditer(plain):
        word = _normalize_token(match.group(0))
        if not word or not word[0].isupper():
            continue
        if any(char.isupper() for char in word[1:]):
            found.add(word.lower())
            continue
        prefix = plain[max(0, match.start() - 3) : match.start()]
        if not _SENTENCE_START.search(prefix):
            found.add(word.lower())
    return found


def keyword_gap(posting: JobPosting, resume: ResumeData, *, top_n: int = 25) -> KeywordGap:
    """Compare the posting's emphasised terms against the resume's content.

    Terms are ranked by salience, which is repetition weighted by whether the
    term reads as a proper noun. A posting naming "Kubernetes" three times cares
    about it more than one passing mention, and "Kubernetes" matters more than
    "design" even at equal frequency.
    """
    description = posting.description or ""
    if not description.strip():
        logger.info("Posting %s has no description text to analyse", posting.url)
        return KeywordGap(posting=posting)

    counts = Counter(_tokenize(description))
    proper = proper_nouns(description)

    def salience(item: tuple[str, int]) -> tuple[int, int]:
        term, count = item
        weight = PROPER_NOUN_WEIGHT if term in proper else 1
        return (count * weight, count)

    ranked = sorted(counts.items(), key=lambda item: (-salience(item)[0], item[0]))[:top_n]
    corpus = _resume_corpus(resume)

    matched: list[str] = []
    missing: list[str] = []
    for term, _count in ranked:
        # Word-boundary match so "go" does not match "goal" and "r" does not
        # match every word containing the letter.
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", corpus):
            matched.append(term)
        else:
            missing.append(term)

    return KeywordGap(posting=posting, matched=matched, missing=missing, top_terms=ranked)


def render_keyword_gap(gap: KeywordGap) -> str:
    """A readable report of coverage, matches, and gaps."""
    if not gap.top_terms:
        return "  No job description text was available for this posting."

    lines = [
        f"  Coverage: {gap.coverage_percent}% "
        f"({len(gap.matched)} of {len(gap.matched) + len(gap.missing)} emphasised terms)",
        "",
    ]
    if gap.matched:
        lines.append("  Present in your resume:")
        lines.append("    " + ", ".join(gap.matched))
        lines.append("")
    if gap.missing:
        lines.append("  Emphasised by the posting, absent from your resume:")
        lines.append("    " + ", ".join(gap.missing))
        lines.append("")
        lines.append("  Add the ones you genuinely have. Do not add the ones you do not.")
    return "\n".join(lines)


def draft_cover_letter(
    posting: JobPosting,
    resume: ResumeData,
    gap: KeywordGap | None = None,
    *,
    today: date | None = None,
) -> str:
    """Produce a targeted first draft, grounded only in resume facts.

    The output is intentionally a draft with obvious seams. It is meant to be
    edited, and a letter that reads as finished is a letter people send without
    reading.
    """
    gap = gap or keyword_gap(posting, resume)
    stamp = (today or date.today()).strftime("%B %d, %Y")

    company = posting.company or "your team"
    title = posting.title or "the open role"

    contact_bits = [resume.full_name, resume.email, resume.phone]
    header = "\n".join(bit for bit in contact_bits if bit)

    opening = (
        f"I am writing to apply for the {title} position at {company}. "
        f"I am currently {resume.current_title} at {resume.current_company}."
        if resume.current_title and resume.current_company
        else f"I am writing to apply for the {title} position at {company}."
    )

    # Ground the middle paragraph in overlap that actually exists.
    overlap = [term for term in gap.matched if term not in STOP_WORDS][:6]
    if overlap:
        strengths = ", ".join(overlap[:-1]) + (f" and {overlap[-1]}" if len(overlap) > 1 else "")
        relevance = f"Your posting emphasises {strengths}. My background covers these directly."
    else:
        relevance = (
            "[DRAFT NOTE: no clear overlap was detected between this posting and "
            "your resume. Write this paragraph yourself, or reconsider the fit.]"
        )

    history_lines = []
    for position in resume.positions[:2]:
        if position.title and position.company:
            history_lines.append(
                f"At {position.company} I worked as {position.title} "
                f"({position.start_date} to {position.end_date})."
            )
    history = " ".join(history_lines) or "[DRAFT NOTE: add one concrete accomplishment here.]"

    missing_note = ""
    if gap.missing:
        missing_note = (
            "\n\n[DRAFT NOTE: this posting also emphasises "
            + ", ".join(gap.missing[:6])
            + ", which your resume does not mention. Address any you genuinely have, "
            "and delete this note before sending.]"
        )

    body = "\n\n".join(
        [
            header,
            stamp,
            f"Dear {company} Hiring Team,",
            opening,
            relevance,
            history,
            "[DRAFT NOTE: replace this line with the specific reason you want to work "
            f"at {company}. Generic enthusiasm is the fastest way to be filtered out.]",
            "Thank you for your consideration. I would welcome the chance to discuss "
            "how I can contribute.",
            "Sincerely,",
            resume.full_name or "[Your name]",
        ]
    )
    return body + missing_note + "\n"


def write_cover_letter(
    posting: JobPosting,
    resume: ResumeData,
    output_dir: str | Path,
    gap: KeywordGap | None = None,
) -> Path:
    """Write a cover letter draft to disk and return its path."""
    directory = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    slug_source = " ".join(part for part in (posting.company, posting.title) if part)
    slug = re.sub(r"[^a-z0-9]+", "-", slug_source.lower()).strip("-")[:60] or "posting"
    path = directory / f"cover-letter-{slug}.txt"

    path.write_text(draft_cover_letter(posting, resume, gap), encoding="utf-8")
    logger.info("Wrote cover letter draft to %s", path)
    return path

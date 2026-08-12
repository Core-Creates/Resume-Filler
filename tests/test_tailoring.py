"""Tests for per-job tailoring: keyword gap analysis and cover letter drafting."""

from __future__ import annotations

from datetime import date

import pytest

from resume_filler.models import JobPosting, Position, ResumeData
from resume_filler.tailoring import (
    draft_cover_letter,
    keyword_gap,
    render_keyword_gap,
    strip_html,
    write_cover_letter,
)

DESCRIPTION = """
<div><p>We are hiring a Senior Backend Engineer.</p>
<ul>
<li>Strong Python and Kubernetes experience required</li>
<li>You will design distributed systems and own Kubernetes deployments</li>
<li>Familiarity with Terraform, Kafka, and Rust is a plus</li>
<li>Experience with GraphQL and Elixir preferred</li>
<li>Kubernetes at scale is central to this role</li>
</ul></div>
"""


@pytest.fixture
def posting() -> JobPosting:
    return JobPosting(
        title="Senior Backend Engineer",
        company="Northwind",
        url="https://example.com/jobs/1",
        description=DESCRIPTION,
    )


@pytest.fixture
def candidate() -> ResumeData:
    return ResumeData(
        first_name="Jane",
        last_name="Rivera",
        email="jane.rivera@example.com",
        phone="(512) 555-0184",
        skills=["Python", "Kubernetes", "Terraform", "Kafka", "PostgreSQL"],
        positions=[
            Position(
                company="Northwind Systems",
                title="Staff Software Engineer",
                start_date="Mar 2021",
                end_date="Present",
            )
        ],
        raw_text="Python Kubernetes Terraform Kafka PostgreSQL distributed systems",
    )


class TestStripHtml:
    def test_removes_tags_and_unescapes(self) -> None:
        assert "hiring" in strip_html("<p>We are hiring</p>")
        assert "<" not in strip_html(DESCRIPTION)

    def test_plain_text_passes_through_untouched(self) -> None:
        assert strip_html("plain text") == "plain text"


class TestKeywordGap:
    def test_identifies_terms_the_resume_has(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        assert "kubernetes" in gap.matched
        assert "python" in gap.matched

    def test_identifies_terms_the_resume_lacks(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        assert "graphql" in gap.missing
        assert "elixir" in gap.missing

    def test_ranks_by_emphasis_in_the_posting(self, posting, candidate) -> None:
        """Kubernetes appears three times, so it should outrank a single mention."""
        gap = keyword_gap(posting, candidate)
        terms = [term for term, _ in gap.top_terms]
        assert terms.index("kubernetes") < terms.index("graphql")

    def test_multi_word_phrases_are_detected(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        assert "distributed systems" in gap.matched

    def test_stop_words_are_excluded(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        terms = {term for term, _ in gap.top_terms}
        assert not terms & {"the", "and", "with", "experience", "required"}

    def test_generic_verbs_do_not_crowd_out_technologies(self, posting, candidate) -> None:
        """Filler like 'design' and 'own' used to outrank Elixir and GraphQL."""
        gap = keyword_gap(posting, candidate)
        terms = [term for term, _ in gap.top_terms]
        assert not {"design", "own", "scale"} & set(terms)
        assert terms.index("elixir") < 10
        assert terms.index("graphql") < 10

    def test_proper_nouns_outrank_plain_words(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        terms = [term for term, _ in gap.top_terms]
        assert terms.index("elixir") < terms.index("deployments")

    def test_trailing_punctuation_is_stripped(self, candidate) -> None:
        """'engineer.' at the end of a sentence must match 'engineer' in a resume."""
        posting = JobPosting(description="We want a Backend Engineer. Engineer skills matter.")
        gap = keyword_gap(posting, candidate)
        terms = [term for term, _ in gap.top_terms]
        assert "engineer" in terms
        assert not any(term.endswith(".") for term in terms)

    def test_dotted_technology_names_survive(self, candidate) -> None:
        posting = JobPosting(description="We use Node.js heavily. Node.js is core.")
        terms = [term for term, _ in keyword_gap(posting, candidate).top_terms]
        assert "node.js" in terms

    def test_coverage_is_a_sane_fraction(self, posting, candidate) -> None:
        gap = keyword_gap(posting, candidate)
        assert 0.0 < gap.coverage <= 1.0
        assert gap.coverage_percent == round(gap.coverage * 100)

    def test_word_boundaries_prevent_false_matches(self) -> None:
        """'go' must not match 'goal'; a substring check would report it present."""
        posting = JobPosting(description="We use Go extensively. Go is required.")
        resume = ResumeData(raw_text="I had a goal of going to Google.")
        gap = keyword_gap(posting, resume)
        assert "go" in gap.missing

    def test_empty_description_is_handled(self, candidate) -> None:
        gap = keyword_gap(JobPosting(url="https://example.com/j"), candidate)
        assert gap.top_terms == []
        assert gap.coverage == 0.0
        assert "No job description" in render_keyword_gap(gap)

    def test_report_names_the_missing_terms(self, posting, candidate) -> None:
        report = render_keyword_gap(keyword_gap(posting, candidate))
        assert "graphql" in report
        assert "Coverage:" in report


class TestCoverLetter:
    def test_includes_the_role_and_company(self, posting, candidate) -> None:
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert "Senior Backend Engineer" in letter
        assert "Northwind" in letter

    def test_includes_contact_details_and_date(self, posting, candidate) -> None:
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert "Jane Rivera" in letter
        assert "jane.rivera@example.com" in letter
        assert "August 12, 2026" in letter

    def test_grounds_claims_in_real_overlap(self, posting, candidate) -> None:
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert "kubernetes" in letter.lower()

    def test_never_claims_skills_the_resume_lacks(self, posting, candidate) -> None:
        """A letter asserting unheld skills is worse than no letter. Missing terms
        may only appear inside a bracketed note to the author."""
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        body = "\n".join(line for line in letter.splitlines() if "[DRAFT NOTE" not in line)
        assert "elixir" not in body.lower()
        assert "graphql" not in body.lower()

    def test_flags_gaps_to_the_author(self, posting, candidate) -> None:
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert "DRAFT NOTE" in letter
        note = letter[letter.index("[DRAFT NOTE: this posting also emphasises") :]
        # The technologies the resume lacks must reach the author's attention.
        assert {"elixir", "graphql"} & set(note.lower().split(", "))  # noqa: SIM118

    def test_marks_itself_as_needing_edits(self, posting, candidate) -> None:
        """A draft that reads as finished gets sent unread."""
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert letter.count("[DRAFT NOTE") >= 2

    def test_no_overlap_says_so_rather_than_inventing(self, candidate) -> None:
        posting = JobPosting(
            title="Pastry Chef",
            company="Bakery",
            description="Croissant lamination and viennoiserie required.",
        )
        letter = draft_cover_letter(posting, candidate, today=date(2026, 8, 12))
        assert "no clear overlap" in letter.lower()

    def test_handles_a_sparse_resume(self, posting) -> None:
        letter = draft_cover_letter(
            posting, ResumeData(first_name="Jane", last_name="Rivera"), today=date(2026, 8, 12)
        )
        assert "Jane Rivera" in letter
        assert "DRAFT NOTE" in letter

    def test_writes_a_slugged_file(self, posting, candidate, tmp_path) -> None:
        path = write_cover_letter(posting, candidate, tmp_path)
        assert path.exists()
        assert path.name == "cover-letter-northwind-senior-backend-engineer.txt"
        assert "Northwind" in path.read_text(encoding="utf-8")

    def test_untitled_posting_still_produces_a_file(self, candidate, tmp_path) -> None:
        path = write_cover_letter(JobPosting(url="https://example.com/j"), candidate, tmp_path)
        assert path.exists()

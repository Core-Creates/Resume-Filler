"""Tests for reading postings out of saved pages, and for unfinished degrees.

Both came out of using the tool on a real application: a Workday ISSM posting
and the resume being sent to it.
"""

from __future__ import annotations

import pytest

from resume_filler import sources
from resume_filler.field_map import resolve_entry_value, resolve_value
from resume_filler.models import Education, ResumeData
from resume_filler.resume_parser import parse_resume_text

WORKDAY_SAVED_PAGE = """<!doctype html>
<html><head>
<title>Information Systems Security Manager (ISSM) I</title>
<meta name="description" content="Clearance Level Must Currently Possess: Top Secret
Job Qualifications: Skills: Cybersecurity, Information Security Experience: 5 + years of
related experience Job Description: The ISSM serves as principal advisor on all matters
involving the security of information systems. Perform risk assessments and make
recommendations to DoD agency customers. Responsibilities include the Risk Management
Framework authorization process.">
</head><body>
<h1>Information Systems Security Manager (ISSM) I</h1>
<!-- The rendered body of an application step is mostly form chrome. -->
<form>
  <label>From</label><input aria-label="Month"><input aria-label="Year">
  <span>mm</span><span>yyyy</span>
  <button>Add</button><button>Delete</button>
  <p>Use the left and right arrows to navigate the spin buttons.</p>
  <p>Suggested skills. Add a title, a location and a description.</p>
</form>
</body></html>
"""


class TestSavedPostings:
    @pytest.fixture
    def saved_page(self, tmp_path):
        path = tmp_path / "issm.html"
        path.write_text(WORKDAY_SAVED_PAGE, encoding="utf-8")
        return path

    def test_reads_a_posting_from_a_saved_page(self, saved_page) -> None:
        postings = sources.from_html_file(saved_page)
        assert len(postings) == 1
        assert "Information Systems Security Manager" in postings[0].title

    def test_prefers_the_real_description_over_form_chrome(self, saved_page) -> None:
        """The body is longer, so choosing by length returns "mm", "yyyy" and
        "spin buttons" as the posting's most emphasised terms."""
        description = sources.from_html_file(saved_page)[0].description
        assert "principal advisor" in description
        assert "spin buttons" not in description

    def test_description_survives_keyword_analysis(self, saved_page) -> None:
        from resume_filler.tailoring import keyword_gap

        posting = sources.from_html_file(saved_page)[0]
        resume = ResumeData(raw_text="security information systems risk")
        gap = keyword_gap(posting, resume)
        terms = {term for term, _ in gap.top_terms}
        assert "clearance" in terms or "dod" in terms
        assert not terms & {"mm", "yyyy", "arrows", "spin"}

    def test_body_text_is_used_when_there_is_no_useful_meta(self, tmp_path) -> None:
        path = tmp_path / "plain.html"
        path.write_text(
            "<html><head><title>Engineer</title></head><body>"
            "<h1>Engineer</h1><p>Responsibilities: build things. "
            "Qualifications: Python experience required.</p></body></html>",
            encoding="utf-8",
        )
        description = sources.from_html_file(path)[0].description
        assert "build things" in description

    def test_missing_file_raises_clearly(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError, match="Saved posting not found"):
            sources.from_html_file(tmp_path / "nope.html")


class TestSparseScanDiagnostic:
    """A saved page can legitimately contain no form.

    Modern portals are single-page apps that render one step at a time, so
    saving the landing step captures a task list and nothing else. Reporting
    "4 controls, none matched" with no explanation looks like the tool is
    broken when it is working correctly.
    """

    WIZARD_SHELL = """<html><body>
      <h1>Application Tasks</h1>
      <p>Items to Complete</p>
      <ul><li>Instructions</li><li>Contact Information</li><li>Education</li></ul>
      <label>* I Agree</label><input type="checkbox">
      <button>Save and Close</button><button>Continue</button>
    </body></html>"""

    def test_a_full_form_gets_no_diagnostic(self, greenhouse_html: str) -> None:
        from resume_filler.reporting import diagnose_sparse_scan

        assert diagnose_sparse_scan(greenhouse_html, 25) == ""

    def test_a_sparse_page_is_explained(self) -> None:
        from resume_filler.reporting import diagnose_sparse_scan

        hint = diagnose_sparse_scan(self.WIZARD_SHELL, 1)
        assert "too few to be an application form" in hint

    def test_a_wizard_shell_is_identified_as_such(self) -> None:
        from resume_filler.reporting import diagnose_sparse_scan

        hint = diagnose_sparse_scan(self.WIZARD_SHELL, 1)
        assert "one step of a multi-step application" in hint
        assert "application tasks" in hint

    def test_the_diagnostic_says_what_to_do_next(self) -> None:
        from resume_filler.reporting import diagnose_sparse_scan

        hint = diagnose_sparse_scan(self.WIZARD_SHELL, 1)
        assert "save that page" in hint
        assert "inspect --url" in hint

    def test_a_javascript_shell_is_called_out(self) -> None:
        from resume_filler.reporting import diagnose_sparse_scan

        html = "<html><body>" + ("<script src='a.js'></script>" * 30) + "</body></html>"
        assert "JavaScript application" in diagnose_sparse_scan(html, 2)


class TestUnfinishedDegrees:
    """An unfinished degree must never be written onto a form as complete."""

    @pytest.mark.parametrize(
        "line",
        [
            "Bachelor of Business Administration, Information Systems (In Progress)",
            "Bachelor of Science in Computer Science, expected 2027",
            "Master of Science, Cybersecurity (ongoing)",
            "Bachelor of Arts - anticipated graduation 2026",
        ],
    )
    def test_unfinished_markers_are_detected(self, line: str) -> None:
        text = f"Ada Lovelace\n\nEducation\n{line}\nExample University, 2027\n"
        data = parse_resume_text(text)
        assert data.education, "the entry should still be captured"
        assert data.education[0].in_progress is True

    def test_a_completed_degree_is_not_flagged(self) -> None:
        text = "Ada Lovelace\n\nEducation\nBachelor of Science in Mathematics\nExample University, 2015\n"
        data = parse_resume_text(text)
        assert data.education[0].in_progress is False

    def test_form_value_carries_the_qualifier(self) -> None:
        """This is the whole point: the value typed into a Degree field."""
        resume = ResumeData(
            education=[
                Education(
                    school="Example University",
                    degree="Bachelor of Business Administration",
                    in_progress=True,
                )
            ]
        )
        assert (
            resolve_value("degree", resume) == "Bachelor of Business Administration (In Progress)"
        )

    def test_repeating_education_rows_carry_it_too(self) -> None:
        resume = ResumeData(education=[Education(degree="Bachelor of Science", in_progress=True)])
        value = resolve_entry_value("entry_degree", resume, "education", 0)
        assert value == "Bachelor of Science (In Progress)"

    def test_completed_degrees_are_written_unchanged(self) -> None:
        resume = ResumeData(education=[Education(degree="Master of Science")])
        assert resolve_value("degree", resume) == "Master of Science"

    def test_summary_shows_the_qualifier(self) -> None:
        from resume_filler.reporting import render_resume_summary

        resume = ResumeData(
            education=[Education(school="Example University", degree="BBA", in_progress=True)]
        )
        assert "(In Progress)" in render_resume_summary(resume)

"""Tests for the setup wizard, the plan summary, and the next-steps block.

The tool did the hard work and then made the applicant do the scanning: a
hundred-row table with no total, no shortlist of what still needed them, and
forty percent of it cookie banners.
"""

from __future__ import annotations

import json

import pytest

from resume_filler.models import FieldMatch, FillStatus, FormField, ResumeData
from resume_filler.reporting import (
    render_next_steps,
    render_plan,
    render_summary,
)
from resume_filler.resume_parser import parse_resume_text
from resume_filler.setup_wizard import (
    Prompter,
    find_resumes,
    render_env,
    render_profile,
    suggested_profile,
)


def match(label: str, status: FillStatus, canonical: str = "", value: str = "") -> FieldMatch:
    return FieldMatch(
        form_field=FormField(tag="input", label=label),
        canonical=canonical,
        confidence=1.0,
        value=value,
        status=status,
        reason="" if status is FillStatus.FILLED else "because",
    )


@pytest.fixture
def mixed_plan() -> list[FieldMatch]:
    return [
        match("First Name", FillStatus.FILLED, "first_name", "Jane"),
        match("Email", FillStatus.FILLED, "email", "jane@example.com"),
        match("Gender", FillStatus.SKIPPED_BY_POLICY, "demographic"),
        match("Street Address", FillStatus.SKIPPED_NO_VALUE, "address_line1"),
        match("Cookie banner", FillStatus.SKIPPED_NO_MATCH),
        match("Nav search", FillStatus.SKIPPED_NO_MATCH),
    ]


class TestSummary:
    def test_it_counts_each_outcome(self, mixed_plan) -> None:
        summary = render_summary(mixed_plan)
        assert "2 ready" in summary
        assert "1 your call" in summary
        assert "1 missing" in summary
        assert "2 unrecognised" in summary

    def test_zero_categories_are_left_out(self) -> None:
        """A tidy line, not a row of zeroes."""
        summary = render_summary([match("First Name", FillStatus.FILLED, "first_name", "Jane")])
        assert summary.strip() == "1 ready"

    def test_it_leads_the_plan(self, mixed_plan) -> None:
        assert render_plan(mixed_plan).splitlines()[0].strip().startswith("2 ready")


class TestQuietOutput:
    def test_unrecognised_rows_are_hidden_by_default(self, mixed_plan) -> None:
        output = render_plan(mixed_plan)
        assert "Cookie banner" not in output
        assert "First Name" in output

    def test_the_hidden_count_is_reported(self, mixed_plan) -> None:
        """Hiding without saying so would look like the scan missed them."""
        assert "2 unrecognised control(s) hidden" in render_plan(mixed_plan)

    def test_verbose_shows_everything(self, mixed_plan) -> None:
        output = render_plan(mixed_plan, verbose=True)
        assert "Cookie banner" in output
        assert "hidden" not in output

    def test_a_plan_with_nothing_hidden_says_nothing(self) -> None:
        plan = [match("First Name", FillStatus.FILLED, "first_name", "Jane")]
        assert "hidden" not in render_plan(plan)


class TestNextSteps:
    def test_it_lists_what_needs_the_applicant(self, mixed_plan) -> None:
        steps = render_next_steps(mixed_plan)
        assert "Gender" in steps
        assert "Street Address" in steps

    def test_it_leaves_out_what_is_done(self, mixed_plan) -> None:
        steps = render_next_steps(mixed_plan)
        assert "First Name" not in steps
        assert "Cookie banner" not in steps

    def test_it_names_the_profile_keys_that_would_help(self, mixed_plan) -> None:
        assert "address_line1" in render_next_steps(mixed_plan)

    def test_a_finished_form_says_so(self) -> None:
        plan = [match("First Name", FillStatus.FILLED, "first_name", "Jane")]
        assert "Nothing left for you" in render_next_steps(plan)

    def test_repeated_labels_are_listed_once(self) -> None:
        """A repeating section produces the same gap by the dozen."""
        plan = [match("Location", FillStatus.SKIPPED_NO_VALUE, "entry_location") for _ in range(6)]
        assert render_next_steps(plan).count("- Location") == 1

    def test_failures_are_called_out_separately(self) -> None:
        plan = [match("Resume", FillStatus.FAILED, "resume_file")]
        assert "Could not be filled" in render_next_steps(plan)


class TestSetupWizard:
    def test_it_finds_resume_shaped_pdfs(self, tmp_path) -> None:
        (tmp_path / "My Resume.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "cv_2026.pdf").write_bytes(b"%PDF-1.4")
        (tmp_path / "taxes.pdf").write_bytes(b"%PDF-1.4")
        found = {p.name for p in find_resumes((tmp_path,))}
        assert found == {"My Resume.pdf", "cv_2026.pdf"}

    def test_a_missing_directory_is_skipped(self, tmp_path) -> None:
        assert find_resumes((tmp_path / "absent",)) == []

    def test_the_env_file_names_the_resume(self, tmp_path) -> None:
        content = render_env(tmp_path / "cv.pdf", tmp_path / "session")
        assert f"RESUME_PATH={tmp_path / 'cv.pdf'}" in content
        assert "SESSION_DIR=" in content

    def test_the_env_file_defines_no_credential_setting(self) -> None:
        """The tool has no use for one, and a key invites filling it in.

        The prose is allowed to mention passwords, since it says the tool never
        asks for one; what must not exist is an assignable key.
        """
        from pathlib import Path as P

        content = render_env(P("cv.pdf"), P("s"))
        assignments = [
            line.split("=", 1)[0]
            for line in content.splitlines()
            if "=" in line and not line.strip().startswith("#")
        ]
        assert not any("PASSWORD" in key.upper() for key in assignments)
        assert not any("USERNAME" in key.upper() for key in assignments)

    def test_the_profile_is_seeded_from_the_resume(self) -> None:
        resume = ResumeData(city="San Antonio", state="TX", postal_code="78240")
        values = suggested_profile(resume)
        assert values["city"] == "San Antonio"
        assert values["postal_code"] == "78240"
        assert values["address_line1"] == "", "a street address is never on a resume"

    def test_the_profile_file_is_valid_json(self) -> None:
        parsed = json.loads(render_profile(suggested_profile(None)))
        assert "address_line1" in parsed
        assert "_comment" in parsed


class TestPrompterWithoutATerminal:
    """Running unattended must take defaults rather than hang on input."""

    def test_ask_returns_the_default(self) -> None:
        assert Prompter(interactive=False).ask("Street", "1 Example St") == "1 Example St"

    def test_confirm_returns_the_default(self) -> None:
        assert Prompter(interactive=False).confirm("Replace?", False) is False
        assert Prompter(interactive=False).confirm("Replace?", True) is True

    def test_choose_returns_the_default_index(self) -> None:
        assert Prompter(interactive=False).choose("Pick", ["a", "b"], default=1) == 1

    def test_choose_handles_an_empty_list(self) -> None:
        assert Prompter(interactive=False).choose("Pick", []) == -1


class TestLinkedInStyleLayout:
    """A third resume layout, found because init went looking for resumes.

    LinkedIn exports "Title—Company" on one line and "Location | Type | Dates"
    below, so reading title and company off the date line produced
    "San Antonio" working at "Texas".
    """

    RESUME = """Alex Morgan Reyes
alex.reyes@example.com | (210) 555-0100
San Antonio, TX

Experience
Chief Information Security Officer—The AI Cowboys
San Antonio, Texas | Full-time | Nov 2025 - Present (9 months)
Lead security operations and strategy for the organisation.

Cyber Security Specialist—The AI Cowboys
United States (Hybrid) | Contract | Oct 2025 - Dec 2025 (3 months)
Delivered assessments and reporting.
"""

    def test_the_role_comes_off_the_line_above(self) -> None:
        data = parse_resume_text(self.RESUME)
        assert data.positions[0].title == "Chief Information Security Officer"
        assert data.positions[0].company == "The AI Cowboys"

    def test_the_location_is_not_mistaken_for_the_employer(self) -> None:
        data = parse_resume_text(self.RESUME)
        assert data.current_company != "Texas"
        assert data.positions[0].location.startswith("San Antonio")

    def test_the_employment_type_is_not_the_location(self) -> None:
        data = parse_resume_text(self.RESUME)
        assert "Full-time" not in data.positions[0].location

    def test_every_role_is_found(self) -> None:
        assert len(parse_resume_text(self.RESUME).positions) == 2

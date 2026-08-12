"""Tests for repeating field groups.

Found by running the engine against a real Workday application page. Workday's
work history is ten identical rows, and a matcher that assigns each canonical
field once per document reads rows two onward as duplicates of the first. On a
live page with six jobs in the resume, exactly one row was filled and 44
controls came back unrecognised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_filler.extractors import fields_from_html, normalize_fields, parse_group
from resume_filler.field_map import _split_date, group_source, match_form, plan_fill
from resume_filler.models import Education, FillStatus, FormField, Position, ResumeData
from resume_filler.reporting import render_plan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workday_html() -> str:
    return (FIXTURES / "workday_experience.html").read_text(encoding="utf-8")


@pytest.fixture
def candidate() -> ResumeData:
    return ResumeData(
        first_name="Alex",
        last_name="Reyes",
        email="alex@example.com",
        positions=[
            Position(
                title="Senior Software Developer",
                company="Northwind Systems",
                location="San Antonio, Texas",
                start_date="Jul 2021",
                end_date="May 2022",
                description="Built the ingest pipeline.",
            ),
            Position(
                title="Cloud DevOps Engineer",
                company="Contoso Cloud",
                location="Austin, Texas",
                start_date="Jan 2021",
                end_date="May 2021",
                description="Ran the migration.",
            ),
        ],
        education=[
            Education(
                school="The University of Texas at San Antonio",
                degree="Bachelor of Business Administration",
                major="Information Systems",
                graduation_year="2025",
            )
        ],
    )


class TestGroupParsing:
    @pytest.mark.parametrize(
        ("identifier", "expected"),
        [
            ("workExperience-3--jobTitle", ("workExperience", 3)),
            ("education-1--schoolName", ("education", 1)),
            ("education[2][school]", ("education", 2)),
            ("workExperience_4_title", ("workExperience", 4)),
        ],
    )
    def test_recognises_group_identifiers(self, identifier, expected) -> None:
        assert parse_group(identifier) == expected

    @pytest.mark.parametrize("identifier", ["first_name", "email", "", "question_12345"])
    def test_flat_identifiers_are_not_groups(self, identifier) -> None:
        assert parse_group(identifier) == ("", -1)

    def test_row_indices_are_renumbered_densely(self) -> None:
        """Workday's raw indices are arbitrary and non-contiguous, so they are
        not usable as an offset into a resume."""
        fields = [
            FormField(tag="input", element_id="workExperience-7--jobTitle"),
            FormField(tag="input", element_id="workExperience-3--jobTitle"),
            FormField(tag="input", element_id="workExperience-9--jobTitle"),
        ]
        normalize_fields(fields)
        by_id = {f.element_id: f.group_index for f in fields}
        assert by_id["workExperience-3--jobTitle"] == 0
        assert by_id["workExperience-7--jobTitle"] == 1
        assert by_id["workExperience-9--jobTitle"] == 2

    def test_group_source_maps_to_a_resume_collection(self) -> None:
        assert group_source("workExperience") == "positions"
        assert group_source("education") == "education"
        assert group_source("somethingElse") == ""


class TestSplitDates:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Jul 2021", ("07", "2021")),
            ("January 2019", ("01", "2019")),
            ("3/2018", ("03", "2018")),
            ("2015", ("", "2015")),
            ("Present", ("", "")),
            ("", ("", "")),
        ],
    )
    def test_splits_resume_dates_into_month_and_year(self, value, expected) -> None:
        """Workday renders each date as two inputs, so one string cannot be
        typed into either."""
        assert _split_date(value) == expected


class TestRepeatingRows:
    def test_every_row_is_matched_not_just_the_first(self, workday_html: str) -> None:
        matches = match_form(fields_from_html(workday_html))
        titles = [m for m in matches if m.canonical == "entry_title"]
        assert len(titles) == 3, "each row must get its own title mapping"

    def test_each_row_draws_from_its_own_resume_entry(self, workday_html, candidate) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        by_row = {
            (m.form_field.group_index, m.canonical): m.value
            for m in matches
            if m.form_field.group == "workExperience"
        }
        assert by_row[(0, "entry_title")] == "Senior Software Developer"
        assert by_row[(0, "entry_company")] == "Northwind Systems"
        assert by_row[(1, "entry_title")] == "Cloud DevOps Engineer"
        assert by_row[(1, "entry_company")] == "Contoso Cloud"

    def test_rows_beyond_the_resume_are_reported_not_invented(
        self, workday_html, candidate
    ) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        third = [
            m
            for m in matches
            if m.form_field.group == "workExperience" and m.form_field.group_index == 2
        ]
        assert third, "the blank template row should still be seen"
        assert all(m.status is not FillStatus.FILLED for m in third)
        assert all(not m.value for m in third)

    def test_dates_land_in_the_right_month_and_year_inputs(self, workday_html, candidate) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        by_row = {
            (m.form_field.group_index, m.canonical): m.value
            for m in matches
            if m.form_field.group == "workExperience"
        }
        assert by_row[(0, "entry_start_month")] == "07"
        assert by_row[(0, "entry_start_year")] == "2021"
        assert by_row[(0, "entry_end_month")] == "05"
        assert by_row[(0, "entry_end_year")] == "2022"

    def test_split_date_inputs_get_distinguishing_labels(self, workday_html: str) -> None:
        """Both spinbuttons are labelled "From"; only aria-label differs."""
        fields = {f.element_id: f for f in fields_from_html(workday_html)}
        assert "Month" in fields["workExperience-3--startDateMonth"].label
        assert "Year" in fields["workExperience-3--startDateYear"].label

    def test_location_and_description_are_filled(self, workday_html, candidate) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        by_row = {
            (m.form_field.group_index, m.canonical): m.value
            for m in matches
            if m.form_field.group == "workExperience"
        }
        assert by_row[(0, "entry_location")] == "San Antonio, Texas"
        assert by_row[(0, "entry_description")] == "Built the ingest pipeline."

    def test_education_rows_use_the_education_collection(self, workday_html, candidate) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        by_canonical = {m.canonical: m.value for m in matches if m.form_field.group == "education"}
        assert by_canonical["entry_school"] == "The University of Texas at San Antonio"
        assert by_canonical["entry_degree"] == "Bachelor of Business Administration"
        assert by_canonical["entry_field_of_study"] == "Information Systems"

    def test_gpa_is_never_auto_filled(self, workday_html, candidate) -> None:
        matches = plan_fill(fields_from_html(workday_html), candidate)
        gpa = next(m for m in matches if m.canonical == "entry_gpa")
        assert gpa.status is FillStatus.SKIPPED_BY_POLICY

    def test_flat_fields_on_the_same_page_still_work(self, workday_html, candidate) -> None:
        """Grouped and ungrouped controls coexist; neither may break the other."""
        candidate.linkedin_url = "https://linkedin.com/in/alex"
        matches = plan_fill(fields_from_html(workday_html), candidate)
        linkedin = next(m for m in matches if m.canonical == "linkedin_url")
        assert linkedin.value == "https://linkedin.com/in/alex"

    def test_entry_fields_never_claim_flat_controls(self, workday_html, candidate) -> None:
        for match in plan_fill(fields_from_html(workday_html), candidate):
            if match.canonical.startswith("entry_"):
                assert match.form_field.is_grouped


class TestGroupReporting:
    def test_unused_rows_are_summarised_rather_than_listed(self, workday_html, candidate) -> None:
        """A live Workday page has ten blank rows; listing every field of every
        unused one buries the real output."""
        output = render_plan(plan_fill(fields_from_html(workday_html), candidate))
        assert "unused workExperience row(s)" in output
        assert output.count("Job Title") == 2, "the blank third row should be folded away"

    def test_filled_rows_are_never_collapsed(self, workday_html, candidate) -> None:
        output = render_plan(plan_fill(fields_from_html(workday_html), candidate))
        assert "Senior Software Developer" in output
        assert "Cloud DevOps Engineer" in output

    def test_a_form_with_no_groups_is_unaffected(self, greenhouse_html: str, resume) -> None:
        output = render_plan(plan_fill(fields_from_html(greenhouse_html), resume))
        assert "unused" not in output

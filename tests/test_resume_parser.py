"""Tests for resume parsing."""

from __future__ import annotations

import pytest

from resume_filler.resume_parser import (
    extract_name,
    parse_resume,
    parse_resume_text,
    split_sections,
)


class TestNameExtraction:
    def test_finds_the_name_not_the_job_title(self, sample_resume_text: str) -> None:
        """The original code returned 'Senior Software' here, taking the title."""
        data = parse_resume_text(sample_resume_text)
        assert data.first_name == "Jane"
        assert data.last_name == "Rivera"

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (["Maria Gonzalez"], ("Maria", "Gonzalez")),
            (["MARIA GONZALEZ"], ("Maria", "Gonzalez")),
            (["Maria Elena Gonzalez"], ("Maria", "Gonzalez")),
            (["Maria Gonzalez, PhD"], ("Maria", "Gonzalez")),
            (["Jean-Luc Picard"], ("Jean-Luc", "Picard")),
        ],
    )
    def test_name_layouts(self, header: list[str], expected: tuple[str, str]) -> None:
        assert extract_name(header) == expected

    @pytest.mark.parametrize(
        "header",
        [
            ["Software Engineer"],
            ["Curriculum Vitae"],
            ["jane@example.com"],
            ["(512) 555-0184"],
            ["Professional Summary"],
        ],
    )
    def test_rejects_non_names(self, header: list[str]) -> None:
        assert extract_name(header) == ("", "")

    def test_skips_title_line_to_find_name_below(self) -> None:
        assert extract_name(["Senior Data Scientist", "Ada Lovelace"]) == ("Ada", "Lovelace")


class TestContactExtraction:
    def test_extracts_contact_details(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert data.email == "jane.rivera@example.com"
        assert "555-0184" in data.phone
        assert data.city == "Austin"
        assert data.state == "TX"
        assert data.postal_code == "78701"

    def test_extracts_profile_urls_and_adds_scheme(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert data.linkedin_url == "https://linkedin.com/in/janeqrivera"
        assert data.github_url == "https://github.com/janeqrivera"
        assert data.portfolio_url == "https://janerivera.dev"

    def test_portfolio_never_captures_linkedin_or_github(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert "linkedin.com" not in data.portfolio_url
        assert "github.com" not in data.portfolio_url

    def test_year_range_is_not_mistaken_for_a_phone_number(self) -> None:
        data = parse_resume_text("Ada Lovelace\nWorked 2015 2018 2021\n")
        assert data.phone == ""


class TestRealWorldResumeLayout:
    """Regressions found by running the parser against two real resumes.

    The synthetic fixture used conventional headings and a single consistent
    layout. A real resume used "Career Experience" and "Technical
    Proficiencies", put the name on the same line as the contact details, and
    mixed two different company/title arrangements in one document. The parser
    returned zero positions and zero skills.
    """

    @pytest.fixture
    def modern(self):
        from pathlib import Path

        path = Path(__file__).parent / "fixtures" / "modern_resume.txt"
        return parse_resume_text(path.read_text(encoding="utf-8"))

    def test_name_shares_a_line_with_contact_details(self, modern) -> None:
        """Rejecting any line containing '@' or a digit missed the name entirely."""
        assert modern.first_name == "Alex"
        assert modern.last_name == "Reyes"

    def test_unconventional_section_headings_are_found(self, modern) -> None:
        """'Career Experience' and 'Technical Proficiencies' are in no alias list."""
        assert len(modern.positions) == 3
        assert modern.skills

    def test_company_first_layout_with_title_below(self, modern) -> None:
        """ "Employer, City, State  Dates" with the role on the following line."""
        first = modern.positions[0]
        assert first.title == "Senior Software Developer"
        assert first.company == "Northwind Systems"

    def test_title_first_layout_on_the_same_line(self, modern) -> None:
        """The same resume also uses "Title, Employer  Dates"."""
        third = modern.positions[2]
        assert third.title == "Security Analyst/Consultant"
        assert third.company == "Digital Defense Inc."

    def test_title_without_an_obvious_role_noun(self, modern) -> None:
        """'Tier 3 IT Support (Fabrikam Contractor)' matched no keyword list."""
        second = modern.positions[1]
        assert second.title.startswith("Tier 3 IT Support")
        assert second.company == "Contoso Cloud Group"

    def test_employer_names_are_never_read_as_titles(self, modern) -> None:
        """A loose title test called 'Contoso Cloud Group' a role and swapped
        the two fields."""
        for position in modern.positions:
            assert "Group" not in position.title
            assert "Systems" not in position.title or "Engineer" in position.title

    def test_labelled_skill_lines_are_flattened(self, modern) -> None:
        """Skills arrive as 'Platforms: Windows, Linux, and Unix servers'."""
        lowered = {s.lower() for s in modern.skills}
        assert "python" in lowered
        assert "react" in lowered
        assert not any(s.lower().startswith("platforms:") for s in modern.skills)

    def test_multi_word_degree_survives(self, modern) -> None:
        assert modern.education[0].degree == "Bachelor of Business Administration"

    def test_degree_does_not_swallow_the_major(self, modern) -> None:
        """re.I makes [A-Z] match 'in', so the degree ate the 'in ...' clause."""
        assert "in Information" not in modern.education[0].degree
        assert modern.education[0].major == "Information Systems"

    def test_bullets_are_not_mistaken_for_headings(self, modern) -> None:
        assert all(not p.title.startswith("●") for p in modern.positions)


class TestPdfTextExtraction:
    def test_collapsed_word_spacing_is_detected(self) -> None:
        """One real PDF extracted as 'SanAntonio,TX' and 'Jul2021', which
        defeats every downstream regex."""
        from resume_filler.resume_parser import MIN_SPACE_RATIO, _space_ratio

        assert _space_ratio("SanAntonio,TX,78240Jul2021toMay2022") < MIN_SPACE_RATIO
        assert _space_ratio("San Antonio, TX 78240 Jul 2021 to May 2022") >= MIN_SPACE_RATIO

    def test_empty_text_does_not_divide_by_zero(self) -> None:
        from resume_filler.resume_parser import _space_ratio

        assert _space_ratio("") == 0.0


class TestCountryInference:
    def test_us_state_implies_the_country(self, sample_resume_text: str) -> None:
        """Country is required on real Greenhouse pages but resumes never state
        it, so a blank country blocked every submission."""
        assert parse_resume_text(sample_resume_text).country == "United States"

    def test_unknown_state_leaves_country_blank(self) -> None:
        """Reported as a gap rather than guessed."""
        from resume_filler.models import ResumeData
        from resume_filler.resume_parser import infer_country

        assert infer_country(ResumeData(state="ZZ")) == ""
        assert infer_country(ResumeData()) == ""

    def test_an_explicit_country_is_never_overwritten(self) -> None:
        from resume_filler.models import ResumeData
        from resume_filler.resume_parser import infer_country

        assert infer_country(ResumeData(state="TX", country="Canada")) == "Canada"


class TestSections:
    def test_splits_known_headings(self, sample_resume_text: str) -> None:
        sections = split_sections(sample_resume_text)
        assert "experience" in sections
        assert "education" in sections
        assert "skills" in sections
        assert sections["header"], "header block should hold the contact lines"

    def test_contact_details_stay_out_of_experience(self, sample_resume_text: str) -> None:
        sections = split_sections(sample_resume_text)
        assert not any("@example.com" in line for line in sections["experience"])


class TestWorkHistory:
    def test_parses_every_role(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert len(data.positions) == 3

    def test_parses_company_and_title(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        first = data.positions[0]
        assert first.title == "Staff Software Engineer"
        assert first.company == "Northwind Systems"
        assert first.end_date == "Present"

    def test_current_position_drives_the_derived_properties(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert data.current_company == "Northwind Systems"
        assert data.current_title == "Staff Software Engineer"

    def test_handles_en_dash_date_ranges(self) -> None:
        text = "Ada Lovelace\n\nEXPERIENCE\nEngineer, Acme Corp   Jan 2020 – Dec 2022\n"
        data = parse_resume_text(text)
        assert len(data.positions) == 1
        assert data.positions[0].company == "Acme Corp"


class TestEducation:
    def test_parses_degree_and_school(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert data.education
        latest = data.education[0]
        assert "Texas" in latest.school
        assert latest.graduation_year == "2015"

    def test_degree_keeps_its_full_name(self, sample_resume_text: str) -> None:
        """'Master of Science' must not be truncated to 'Master'."""
        data = parse_resume_text(sample_resume_text)
        assert data.education[0].degree == "Master of Science"

    def test_school_excludes_the_degree_and_the_year(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        school = data.education[0].school
        assert school == "The University of Texas at Austin"
        assert "Master" not in school
        assert "2015" not in school

    def test_major_comes_from_in_not_of(self, sample_resume_text: str) -> None:
        """'Master of Science in Computer Science' has a major of Computer Science."""
        data = parse_resume_text(sample_resume_text)
        assert data.education[0].major == "Computer Science"

    def test_parses_each_degree_as_a_separate_entry(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert len(data.education) == 2
        assert data.education[1].degree.startswith("B.S")
        assert data.education[1].major == "Computer Engineering"
        assert data.education[1].graduation_year == "2013"

    def test_graduation_year_is_the_full_year(self, sample_resume_text: str) -> None:
        """A capturing group in the year regex once returned '20' instead of '2015'."""
        for entry in parse_resume_text(sample_resume_text).education:
            assert len(entry.graduation_year) == 4

    def test_skills_are_flattened_and_deduplicated(self, sample_resume_text: str) -> None:
        data = parse_resume_text(sample_resume_text)
        assert "Python" in data.skills
        assert "Kubernetes" in data.skills
        assert len(data.skills) == len({s.lower() for s in data.skills})


class TestFileHandling:
    def test_missing_file_raises_a_clear_error(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError, match="Resume not found"):
            parse_resume(tmp_path / "nope.pdf")

    def test_partial_resume_does_not_raise(self) -> None:
        """A resume with nothing but a name must still produce a usable object."""
        data = parse_resume_text("Ada Lovelace\n")
        assert data.full_name == "Ada Lovelace"
        assert data.email == ""
        assert data.positions == []
        assert data.current_company == ""

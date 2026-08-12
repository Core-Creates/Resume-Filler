"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_filler.models import Education, Position, ResumeData

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES


@pytest.fixture
def greenhouse_html() -> str:
    return (FIXTURES / "greenhouse_form.html").read_text(encoding="utf-8")


@pytest.fixture
def tricky_html() -> str:
    return (FIXTURES / "tricky_form.html").read_text(encoding="utf-8")


@pytest.fixture
def sample_resume_text() -> str:
    return (FIXTURES / "sample_resume.txt").read_text(encoding="utf-8")


@pytest.fixture
def resume() -> ResumeData:
    """A fully populated resume so value resolution is never the reason a test fails."""
    return ResumeData(
        first_name="Jane",
        last_name="Rivera",
        email="jane.rivera@example.com",
        phone="(512) 555-0184",
        address_line1="900 Congress Ave",
        city="Austin",
        state="TX",
        postal_code="78701",
        country="United States",
        linkedin_url="https://linkedin.com/in/janeqrivera",
        github_url="https://github.com/janeqrivera",
        portfolio_url="https://janerivera.dev",
        positions=[
            Position(
                company="Northwind Systems",
                title="Staff Software Engineer",
                start_date="Mar 2021",
                end_date="Present",
            )
        ],
        education=[
            Education(
                school="The University of Texas at Austin",
                degree="Master of Science",
                major="Computer Science",
                graduation_year="2015",
            )
        ],
    )

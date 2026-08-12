"""Tests for the three run modes and for readable driver failures.

The tool used to have only two settings: type nothing, or type everything and
submit. Neither is what a careful applicant wants, so every bit of the filling
logic went unused and the form was still completed by hand.
"""

from __future__ import annotations

import pytest

from resume_filler import browser, form_filler
from resume_filler.cli import build_parser
from resume_filler.extractors import fields_from_html
from resume_filler.models import ApplicationStatus, FillStatus, JobPosting, RunMode
from tests.test_form_filler import StubDriver, StubElement


@pytest.fixture
def patched_fields(monkeypatch, greenhouse_html: str):
    fields = fields_from_html(greenhouse_html)
    for field in fields:
        field.handle = StubElement()
    monkeypatch.setattr(form_filler, "fields_from_driver", lambda driver: fields)
    return fields


@pytest.fixture
def clicks(monkeypatch):
    recorded: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        form_filler, "_click_first", lambda d, xpaths, timeout: recorded.append(xpaths) or True
    )
    return recorded


class TestPreviewMode:
    def test_it_types_nothing(self, patched_fields, resume, clicks) -> None:
        form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume, mode=RunMode.PREVIEW
        )
        assert all(not f.handle.sent for f in patched_fields)

    def test_it_is_the_default(self, patched_fields, resume, clicks) -> None:
        result = form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume
        )
        assert result.status is ApplicationStatus.PREPARED
        assert "nothing typed" in result.message


class TestFillMode:
    def test_it_actually_types(self, patched_fields, resume, clicks) -> None:
        """The whole point: the form is completed rather than described."""
        form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume, mode=RunMode.FILL
        )
        filled = [f for f in patched_fields if f.handle.sent]
        assert filled, "fill mode must enter values"

    def test_it_never_clicks_submit(self, patched_fields, resume, clicks) -> None:
        form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume, mode=RunMode.FILL
        )
        assert form_filler.SUBMIT_BUTTON_XPATHS not in clicks

    def test_it_says_nothing_was_submitted(self, patched_fields, resume, clicks) -> None:
        result = form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume, mode=RunMode.FILL
        )
        assert result.status is ApplicationStatus.PREPARED
        assert "Nothing was submitted" in result.message

    def test_a_missing_required_field_does_not_stop_it(self, patched_fields, clicks) -> None:
        """Refusing to fill because something is missing would defeat the point;
        the applicant is going to look at it before sending."""
        from resume_filler.models import ResumeData

        incomplete = ResumeData(first_name="Jane", last_name="Rivera")
        result = form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), incomplete, mode=RunMode.FILL
        )
        assert result.status is ApplicationStatus.PREPARED
        assert any(f.handle.sent for f in patched_fields)

    def test_policy_fields_are_still_left_alone(self, patched_fields, resume, clicks) -> None:
        result = form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), resume, mode=RunMode.FILL
        )
        for match in result.matches:
            if match.status is FillStatus.SKIPPED_BY_POLICY:
                assert not match.form_field.handle.sent


class TestSubmitMode:
    def test_it_submits_when_nothing_required_is_missing(
        self, patched_fields, resume, tmp_path, clicks
    ) -> None:
        resume_file = tmp_path / "cv.pdf"
        resume_file.write_bytes(b"%PDF-1.4")
        result = form_filler.apply_to_job(
            StubDriver(),
            JobPosting(url="https://example.com/j"),
            resume,
            resume_path=str(resume_file),
            mode=RunMode.SUBMIT,
        )
        assert result.status is ApplicationStatus.SUBMITTED
        assert form_filler.SUBMIT_BUTTON_XPATHS in clicks

    def test_it_still_refuses_on_a_missing_required_field(self, patched_fields, clicks) -> None:
        from resume_filler.models import ResumeData

        incomplete = ResumeData(first_name="Jane", last_name="Rivera")
        result = form_filler.apply_to_job(
            StubDriver(), JobPosting(url="https://example.com/j"), incomplete, mode=RunMode.SUBMIT
        )
        assert result.status is ApplicationStatus.NEEDS_REVIEW
        assert form_filler.SUBMIT_BUTTON_XPATHS not in clicks


class TestRunModeFlags:
    def test_preview_is_the_default(self) -> None:
        args = build_parser().parse_args(["apply", "--url", "https://example.com/j"])
        assert not args.fill
        assert not args.submit

    def test_fill_and_submit_cannot_both_be_given(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["apply", "--url", "u", "--fill", "--submit"])

    def test_a_single_url_needs_no_file(self) -> None:
        """Writing a one-line text file to apply to one job was silly."""
        args = build_parser().parse_args(["apply", "--url", "https://example.com/j", "--fill"])
        assert args.url == "https://example.com/j"
        assert args.fill is True

    def test_types_anything_is_true_for_both_acting_modes(self) -> None:
        assert not RunMode.PREVIEW.types_anything
        assert RunMode.FILL.types_anything
        assert RunMode.SUBMIT.types_anything


class TestDriverFailureMessages:
    def test_a_session_already_open_is_explained(self) -> None:
        message = browser.explain_driver_failure(
            Exception("user data directory is already in use"), "chrome"
        )
        assert "Close every chrome window" in message

    def test_a_missing_browser_suggests_the_others(self) -> None:
        message = browser.explain_driver_failure(
            Exception("Unable to locate the chrome binary"), "chrome"
        )
        assert "--browser" in message
        assert "edge" in message and "firefox" in message

    def test_an_unreachable_address_is_explained(self) -> None:
        message = browser.explain_driver_failure(Exception("net::ERR_NAME_NOT_RESOLVED"), "chrome")
        assert "could not be reached" in message

    def test_an_unrecognised_failure_is_not_dressed_up(self) -> None:
        """Inventing an explanation for something unknown would mislead."""
        assert browser.explain_driver_failure(Exception("something odd"), "chrome") == ""

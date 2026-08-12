"""Tests for fill execution and the submission guardrails.

The guardrails are the most important behaviour in the project, because the
version this replaces clicked Submit on an empty form. These tests use a stub
driver and monkeypatched helpers so no browser is ever launched.
"""

from __future__ import annotations

import pytest

from resume_filler import form_filler
from resume_filler.extractors import fields_from_html
from resume_filler.models import ApplicationStatus, FillStatus, JobPosting, RunMode


class StubElement:
    """Records what was sent to it so tests can assert on the interaction."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.cleared = False

    def clear(self) -> None:
        self.cleared = True

    def send_keys(self, value: str) -> None:
        self.sent.append(value)


class StubSwitchTo:
    """Models driver.switch_to, which the frame-aware filler always uses."""

    def __init__(self, driver: StubDriver) -> None:
        self._driver = driver

    def default_content(self) -> None:
        self._driver.context = ()

    def frame(self, index: int) -> None:
        self._driver.context = (*self._driver.context, index)


class StubDriver:
    def __init__(self, url: str = "https://example.com/apply") -> None:
        self.current_url = url
        self.visited: list[str] = []
        self.scripts: list[str] = []
        self.context: tuple[int, ...] = ()
        self.switch_to = StubSwitchTo(self)

    def get(self, url: str) -> None:
        self.visited.append(url)
        self.current_url = url

    def execute_script(self, script: str, *args: object) -> str:
        self.scripts.append(script)
        return ""

    def find_elements(self, by: object, selector: str) -> list[object]:
        # A single page form: no Continue button anywhere.
        return []


@pytest.fixture
def stub_driver() -> StubDriver:
    return StubDriver()


@pytest.fixture
def patched_fields(monkeypatch, greenhouse_html: str):
    """Make fill_form see the fixture form, with a stub element behind each control."""
    fields = fields_from_html(greenhouse_html)
    for field in fields:
        field.handle = StubElement()
    monkeypatch.setattr(form_filler, "fields_from_driver", lambda driver: fields)
    return fields


class TestDryRun:
    def test_dry_run_types_nothing(self, stub_driver, patched_fields, resume) -> None:
        matches = form_filler.fill_form(
            stub_driver, resume, resume_path="/tmp/cv.pdf", dry_run=True
        )
        assert any(m.status is FillStatus.FILLED for m in matches)
        assert all(not field.handle.sent for field in patched_fields)

    def test_dry_run_explains_itself(self, stub_driver, patched_fields, resume) -> None:
        matches = form_filler.fill_form(stub_driver, resume, dry_run=True)
        filled = [m for m in matches if m.status is FillStatus.FILLED]
        assert filled and all("Dry run" in m.reason for m in filled)


class TestLiveFill:
    def test_writes_values_into_elements(self, stub_driver, patched_fields, resume) -> None:
        matches = form_filler.fill_form(
            stub_driver, resume, resume_path="/tmp/cv.pdf", dry_run=False
        )
        by_canonical = {m.canonical: m for m in matches}
        assert by_canonical["first_name"].form_field.handle.sent == ["Jane"]
        assert by_canonical["email"].form_field.handle.sent == ["jane.rivera@example.com"]

    def test_never_writes_into_policy_protected_fields(
        self, stub_driver, patched_fields, resume
    ) -> None:
        matches = form_filler.fill_form(stub_driver, resume, dry_run=False)
        for match in matches:
            if match.canonical in {"demographic", "work_authorization", "sponsorship"}:
                assert not match.form_field.handle.sent

    def test_missing_upload_file_is_recorded_as_a_failure_not_a_crash(
        self, stub_driver, patched_fields, resume
    ) -> None:
        matches = form_filler.fill_form(
            stub_driver, resume, resume_path="/nonexistent/path/cv.pdf", dry_run=False
        )
        upload = next(m for m in matches if m.canonical == "resume_file")
        assert upload.status is FillStatus.FAILED
        assert "does not exist" in upload.reason


class TestSubmissionGuardrails:
    def test_default_run_never_submits(
        self, stub_driver, patched_fields, resume, monkeypatch
    ) -> None:
        clicked: list[str] = []
        monkeypatch.setattr(
            form_filler,
            "_click_first",
            lambda driver, xpaths, timeout: clicked.append(xpaths[0]) or True,
        )
        result = form_filler.apply_to_job(
            stub_driver, JobPosting(url="https://example.com/j"), resume
        )
        assert result.status is ApplicationStatus.PREPARED
        assert form_filler.SUBMIT_BUTTON_XPATHS[0] not in clicked

    def test_submit_is_refused_when_a_required_field_is_unfilled(
        self, stub_driver, patched_fields, monkeypatch
    ) -> None:
        from resume_filler.models import ResumeData

        # No email on the resume, and the fixture marks email required.
        incomplete = ResumeData(first_name="Jane", last_name="Rivera")
        submitted: list[str] = []
        monkeypatch.setattr(
            form_filler,
            "_click_first",
            lambda driver, xpaths, timeout: submitted.append(xpaths[0]) or True,
        )
        result = form_filler.apply_to_job(
            stub_driver, JobPosting(url="https://example.com/j"), incomplete, mode=RunMode.SUBMIT
        )
        assert result.status is ApplicationStatus.NEEDS_REVIEW
        assert "required field" in result.message
        assert form_filler.SUBMIT_BUTTON_XPATHS[0] not in submitted

    def test_submit_proceeds_when_all_required_fields_are_filled(
        self, stub_driver, patched_fields, resume, tmp_path, monkeypatch
    ) -> None:
        resume_file = tmp_path / "cv.pdf"
        resume_file.write_bytes(b"%PDF-1.4 stub")
        clicked: list[tuple[str, ...]] = []

        def fake_click(driver, xpaths, timeout):
            clicked.append(xpaths)
            return True

        monkeypatch.setattr(form_filler, "_click_first", fake_click)
        result = form_filler.apply_to_job(
            stub_driver,
            JobPosting(url="https://example.com/j"),
            resume,
            resume_path=str(resume_file),
            mode=RunMode.SUBMIT,
        )
        assert result.status is ApplicationStatus.SUBMITTED
        assert form_filler.SUBMIT_BUTTON_XPATHS in clicked

    def test_unreachable_posting_fails_cleanly(self, resume, monkeypatch) -> None:
        from selenium.common.exceptions import WebDriverException

        class BrokenDriver(StubDriver):
            def get(self, url: str) -> None:
                raise WebDriverException("net::ERR_NAME_NOT_RESOLVED")

        result = form_filler.apply_to_job(
            BrokenDriver(), JobPosting(url="https://nope.invalid/j"), resume
        )
        assert result.status is ApplicationStatus.FAILED
        assert "Could not open" in result.message

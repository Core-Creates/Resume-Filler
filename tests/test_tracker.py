"""Tests for the application tracker and run reporting."""

from __future__ import annotations

import csv
import json

from resume_filler.models import (
    ApplicationResult,
    ApplicationStatus,
    FieldMatch,
    FillStatus,
    FormField,
    JobPosting,
)
from resume_filler.reporting import render_plan, write_json_report
from resume_filler.tracker import Tracker


def make_result(
    url: str, status: ApplicationStatus = ApplicationStatus.PREPARED
) -> ApplicationResult:
    return ApplicationResult(
        posting=JobPosting(url=url, title="Engineer", company="Acme", source="test"),
        status=status,
        matches=[
            FieldMatch(
                form_field=FormField(tag="input", label="First Name", required=True),
                canonical="first_name",
                confidence=1.0,
                value="Jane",
                status=FillStatus.FILLED,
            ),
            FieldMatch(
                form_field=FormField(tag="select", label="Gender"),
                canonical="demographic",
                confidence=1.0,
                status=FillStatus.SKIPPED_BY_POLICY,
                reason="Voluntary self identification.",
            ),
        ],
        message="two fields",
    )


class TestTracker:
    def test_records_and_reads_back(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        tracker.record(make_result("https://example.com/job/1"))
        records = tracker.all_records()
        assert len(records) == 1
        assert records[0]["url"] == "https://example.com/job/1"
        assert records[0]["fields_filled"] == 1
        assert records[0]["fields_review"] == 1

    def test_same_url_updates_rather_than_duplicates(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        tracker.record(make_result("https://example.com/job/1"))
        tracker.record(make_result("https://example.com/job/1", ApplicationStatus.SUBMITTED))
        records = tracker.all_records()
        assert len(records) == 1
        assert records[0]["status"] == "submitted"

    def test_already_applied_gates_reapplication(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        assert tracker.already_applied("https://example.com/job/1") is False
        tracker.record(make_result("https://example.com/job/1", ApplicationStatus.SUBMITTED))
        assert tracker.already_applied("https://example.com/job/1") is True

    def test_failed_applications_may_be_retried(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        tracker.record(make_result("https://example.com/job/2", ApplicationStatus.FAILED))
        assert tracker.already_applied("https://example.com/job/2") is False

    def test_export_csv_round_trips(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        tracker.record(make_result("https://example.com/job/1"))
        destination = tracker.export_csv(tmp_path / "out.csv")
        with destination.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["company"] == "Acme"
        assert rows[0]["url"] == "https://example.com/job/1"

    def test_summary_counts_by_status(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "apps.db")
        tracker.record(make_result("https://example.com/a", ApplicationStatus.SUBMITTED))
        tracker.record(make_result("https://example.com/b", ApplicationStatus.PREPARED))
        tracker.record(make_result("https://example.com/c", ApplicationStatus.PREPARED))
        assert tracker.summary() == {"submitted": 1, "prepared": 2}

    def test_creates_parent_directory(self, tmp_path) -> None:
        tracker = Tracker(tmp_path / "nested" / "dir" / "apps.db")
        assert tracker.path.parent.is_dir()


class TestReporting:
    def test_plan_renders_every_control(self) -> None:
        output = render_plan(make_result("https://example.com/j").matches)
        assert "First Name" in output
        assert "Gender" in output
        assert "[FILL]" in output
        assert "[YOU ]" in output

    def test_empty_form_is_reported_clearly(self) -> None:
        assert "No fillable controls" in render_plan([])

    def test_json_report_is_valid_and_complete(self, tmp_path, resume) -> None:
        path = write_json_report([make_result("https://example.com/j")], tmp_path, resume)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["applications"][0]["posting"]["url"] == "https://example.com/j"
        assert payload["applications"][0]["filled"] == 1
        assert len(payload["applications"][0]["fields"]) == 2

    def test_json_report_omits_raw_resume_text(self, tmp_path, resume) -> None:
        """The full resume body has no business in a run artifact."""
        resume.raw_text = "SENSITIVE FULL RESUME BODY"
        path = write_json_report([make_result("https://example.com/j")], tmp_path, resume)
        assert "SENSITIVE FULL RESUME BODY" not in path.read_text(encoding="utf-8")


class TestApplicationResult:
    def test_required_gaps_lists_unfilled_required_fields(self) -> None:
        result = ApplicationResult(
            posting=JobPosting(url="https://example.com/j"),
            status=ApplicationStatus.PREPARED,
            matches=[
                FieldMatch(
                    form_field=FormField(tag="input", label="Email", required=True),
                    canonical="email",
                    status=FillStatus.SKIPPED_NO_VALUE,
                ),
                FieldMatch(
                    form_field=FormField(tag="input", label="Phone", required=False),
                    canonical="phone",
                    status=FillStatus.SKIPPED_NO_VALUE,
                ),
            ],
        )
        gaps = result.required_gaps
        assert len(gaps) == 1
        assert gaps[0].canonical == "email"

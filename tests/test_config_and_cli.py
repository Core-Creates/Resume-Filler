"""Tests for configuration loading, source parsing, and CLI wiring."""

from __future__ import annotations

import pytest

from resume_filler import sources
from resume_filler.cli import build_parser
from resume_filler.config import Settings
from resume_filler.models import JobPosting


class TestSettings:
    def test_reads_from_environment(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("BROWSER", "firefox")
        monkeypatch.setenv("HEADLESS", "true")
        monkeypatch.setenv("CONFIDENCE_THRESHOLD", "0.8")
        settings = Settings.from_env(tmp_path / "missing.env")
        assert settings.browser == "firefox"
        assert settings.headless is True
        assert settings.confidence_threshold == 0.8

    def test_no_credential_settings_exist_at_all(self) -> None:
        """The tool must never hold a password.

        The original shipped one in source. The replacement read one from the
        environment and then never used it, which is worse than useless: it
        invited the user to store a plaintext password for no purpose. Sign in
        is handled by a real browser session instead.
        """
        settings = Settings()
        assert not hasattr(settings, "username")
        assert not hasattr(settings, "password")

    def test_invalid_threshold_is_reported(self, tmp_path) -> None:
        resume = tmp_path / "cv.pdf"
        resume.write_bytes(b"%PDF-1.4")
        settings = Settings(resume_path=resume, confidence_threshold=1.5)
        problems = settings.validate_for_browsing()
        assert any("CONFIDENCE_THRESHOLD" in problem for problem in problems)

    def test_missing_resume_is_reported(self, tmp_path) -> None:
        settings = Settings(resume_path=tmp_path / "nope.pdf")
        assert any("Resume not found" in problem for problem in settings.validate_for_browsing())

    def test_unsupported_browser_is_reported(self, tmp_path) -> None:
        resume = tmp_path / "cv.pdf"
        resume.write_bytes(b"%PDF-1.4")
        settings = Settings(resume_path=resume, browser="netscape")
        assert any("Unsupported browser" in p for p in settings.validate_for_browsing())

    def test_malformed_numeric_env_falls_back_to_default(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("PAGE_TIMEOUT", "not-a-number")
        settings = Settings.from_env(tmp_path / "missing.env")
        assert settings.page_timeout == 15.0


class TestSources:
    def test_reads_url_list_ignoring_comments(self, tmp_path) -> None:
        path = tmp_path / "urls.txt"
        path.write_text(
            "# my shortlist\nhttps://example.com/a\n\nhttps://example.com/b\n",
            encoding="utf-8",
        )
        postings = sources.from_urls_file(path)
        assert [p.url for p in postings] == ["https://example.com/a", "https://example.com/b"]

    def test_missing_url_file_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            sources.from_urls_file(tmp_path / "nope.txt")

    def test_reads_csv_with_optional_columns(self, tmp_path) -> None:
        path = tmp_path / "jobs.csv"
        path.write_text(
            "url,title,company,location\nhttps://example.com/a,Engineer,Acme,Austin TX\n",
            encoding="utf-8",
        )
        postings = sources.from_csv(path)
        assert postings[0].title == "Engineer"
        assert postings[0].company == "Acme"

    def test_csv_without_url_column_raises(self, tmp_path) -> None:
        path = tmp_path / "jobs.csv"
        path.write_text("title,company\nEngineer,Acme\n", encoding="utf-8")
        with pytest.raises(ValueError, match="url"):
            sources.from_csv(path)

    def test_filters_by_keyword_and_location(self) -> None:
        postings = [
            JobPosting(title="Senior Python Engineer", location="Austin, TX"),
            JobPosting(title="Marketing Manager", location="Austin, TX"),
            JobPosting(title="Python Engineer", location="Remote"),
        ]
        assert len(sources.filter_postings(postings, keywords=["python"])) == 2
        assert len(sources.filter_postings(postings, location="austin")) == 2
        assert len(sources.filter_postings(postings, keywords=["python"], location="remote")) == 1


class TestCLI:
    def test_apply_defaults_to_dry_run(self) -> None:
        args = build_parser().parse_args(["apply", "--urls", "jobs.txt"])
        assert args.submit is False

    def test_submit_must_be_explicit(self) -> None:
        args = build_parser().parse_args(["apply", "--urls", "jobs.txt", "--submit"])
        assert args.submit is True

    def test_apply_requires_a_posting_source(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["apply"])

    def test_posting_sources_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["apply", "--urls", "a.txt", "--csv", "b.csv"])

    def test_default_limit_is_bounded(self) -> None:
        args = build_parser().parse_args(["apply", "--urls", "jobs.txt"])
        assert args.limit == 10

    def test_inspect_accepts_saved_html(self) -> None:
        args = build_parser().parse_args(["inspect", "--html", "form.html"])
        assert args.html == "form.html"
        assert args.url is None

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

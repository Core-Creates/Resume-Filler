"""Command line interface.

Three subcommands:

* ``parse``   Show what the parser extracted from a resume. No browser.
* ``inspect`` Show the fill plan for a saved HTML file or a live URL.
* ``apply``   Work through a list of postings. Dry run unless --submit is given.

Nothing here submits anything by default. ``--submit`` is the only way to send
an application, and it still refuses when required fields could not be filled.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Settings
from .extractors import fields_from_html
from .field_map import plan_fill
from .logging_setup import configure_logging
from .models import ApplicationResult, ApplicationStatus, JobPosting
from .profile import load_profile
from .reporting import (
    diagnose_sparse_scan,
    render_plan,
    render_result,
    render_resume_summary,
    write_json_report,
)
from .resume_parser import parse_resume
from .tracker import Tracker

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resume-filler",
        description="Parse a resume and pre-fill online job applications for review.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    parser.add_argument("--env-file", help="Path to a .env file. Defaults to ./.env")
    parser.add_argument(
        "--profile",
        help="JSON file of answers your resume does not contain. Defaults to ./profile.json",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_cmd = subparsers.add_parser("parse", help="Parse a resume and print the result.")
    parse_cmd.add_argument("resume", nargs="?", help="Path to the resume PDF.")

    inspect_cmd = subparsers.add_parser(
        "inspect", help="Show the fill plan for one application form."
    )
    source = inspect_cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--html", help="Path to a saved HTML file. No browser needed.")
    source.add_argument("--url", help="Live URL to open in a browser.")
    inspect_cmd.add_argument("--resume", help="Path to the resume PDF.")

    apply_cmd = subparsers.add_parser("apply", help="Fill applications for a list of postings.")
    origin = apply_cmd.add_mutually_exclusive_group(required=True)
    origin.add_argument("--urls", help="Text file with one application URL per line.")
    origin.add_argument("--csv", help="CSV with a url column.")
    origin.add_argument("--greenhouse", help="Greenhouse board token, for example 'stripe'.")
    origin.add_argument("--lever", help="Lever company slug, for example 'netflix'.")
    apply_cmd.add_argument("--resume", help="Path to the resume PDF.")
    apply_cmd.add_argument("--keywords", nargs="*", help="Only keep postings matching these.")
    apply_cmd.add_argument("--location", default="", help="Only keep postings in this location.")
    apply_cmd.add_argument("--limit", type=int, default=10, help="Maximum postings to process.")
    apply_cmd.add_argument(
        "--submit",
        action="store_true",
        help="Actually submit. Without this flag the run is a dry run that types nothing.",
    )
    apply_cmd.add_argument(
        "--skip-seen", action="store_true", help="Skip postings already in the tracker."
    )
    apply_cmd.add_argument(
        "--tailor",
        action="store_true",
        help="Write a keyword gap report and a cover letter draft per posting.",
    )

    tailor_cmd = subparsers.add_parser(
        "tailor", help="Analyse postings against your resume without applying."
    )
    tailor_origin = tailor_cmd.add_mutually_exclusive_group(required=True)
    tailor_origin.add_argument("--greenhouse", help="Greenhouse board token.")
    tailor_origin.add_argument("--lever", help="Lever company slug.")
    tailor_origin.add_argument("--csv", help="CSV with url and description columns.")
    tailor_origin.add_argument(
        "--html", help="A job posting saved from the browser. Works for any ATS."
    )
    tailor_cmd.add_argument("--resume", help="Path to the resume PDF.")
    tailor_cmd.add_argument("--keywords", nargs="*", help="Only keep postings matching these.")
    tailor_cmd.add_argument("--location", default="", help="Only keep postings in this location.")
    tailor_cmd.add_argument("--limit", type=int, default=10, help="Maximum postings to analyse.")
    tailor_cmd.add_argument(
        "--cover-letters", action="store_true", help="Also write a cover letter draft for each."
    )

    export_cmd = subparsers.add_parser("export", help="Export the application tracker to CSV.")
    export_cmd.add_argument("destination", nargs="?", default="applications.csv")

    return parser


def _resolve_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env(args.env_file)
    resume_override = getattr(args, "resume", None) or getattr(args, "resume_positional", None)
    if getattr(args, "command", "") == "parse" and getattr(args, "resume", None):
        resume_override = args.resume
    if resume_override:
        settings.resume_path = Path(resume_override).expanduser()
    if getattr(args, "profile", None):
        settings.profile_path = Path(args.profile).expanduser()
    return settings


def command_parse(args: argparse.Namespace, settings: Settings) -> int:
    resume_path = Path(args.resume).expanduser() if args.resume else settings.resume_path
    resume = parse_resume(resume_path)
    print(render_resume_summary(resume))
    if resume.positions:
        print("\nWork history")
        print("-" * 60)
        for position in resume.positions:
            print(
                f"  {position.title or '(title?)'} at {position.company or '(company?)'}"
                f"  [{position.start_date} to {position.end_date}]"
            )
    if not resume.email or not resume.full_name:
        print(
            "\nWarning: name or email could not be found. Applications will have gaps.",
            file=sys.stderr,
        )
        return 1
    return 0


def command_inspect(args: argparse.Namespace, settings: Settings) -> int:
    resume_path = Path(args.resume).expanduser() if args.resume else settings.resume_path
    resume = parse_resume(resume_path)
    print(render_resume_summary(resume))

    if args.html:
        saved_page = Path(args.html).expanduser()
        html = saved_page.read_text(encoding="utf-8", errors="replace")
        # base_path lets the scan follow iframes into their saved companion
        # files, which is the whole form on an iCIMS page.
        fields = fields_from_html(html, base_path=saved_page)
        matches = plan_fill(
            fields,
            resume,
            resume_path=str(resume_path.resolve()),
            threshold=settings.confidence_threshold,
            profile=load_profile(settings.profile_path),
        )
        print(f"\nFill plan for {args.html}")
        print(render_plan(matches))
        hint = diagnose_sparse_scan(html, len(fields))
        if hint:
            print()
            print(hint)
        return 0

    from .browser import managed_driver
    from .form_filler import fill_form

    with managed_driver(settings.browser, settings.headless) as driver:
        driver.get(args.url)
        matches = fill_form(
            driver,
            resume,
            resume_path=str(resume_path.resolve()),
            threshold=settings.confidence_threshold,
            timeout=settings.page_timeout,
            dry_run=True,
        )
    print(f"\nFill plan for {args.url}")
    print(render_plan(matches))
    return 0


def _load_postings(args: argparse.Namespace) -> list[JobPosting]:
    from . import sources

    if getattr(args, "html", None):
        postings = sources.from_html_file(args.html)
        return sources.filter_postings(
            postings, keywords=getattr(args, "keywords", None), location=args.location
        )
    if args.urls:
        postings = sources.from_urls_file(args.urls)
    elif args.csv:
        postings = sources.from_csv(args.csv)
    elif args.greenhouse:
        postings = sources.from_greenhouse(args.greenhouse)
    else:
        postings = sources.from_lever(args.lever)

    return sources.filter_postings(postings, keywords=args.keywords, location=args.location)


def command_apply(args: argparse.Namespace, settings: Settings) -> int:
    problems = settings.validate_for_browsing()
    if problems:
        for problem in problems:
            print(f"Configuration error: {problem}", file=sys.stderr)
        return 2

    resume = parse_resume(settings.resume_path)
    print(render_resume_summary(resume))

    postings = _load_postings(args)
    tracker = Tracker(settings.database_path)

    if args.skip_seen:
        before = len(postings)
        postings = [p for p in postings if not tracker.already_applied(p.url)]
        skipped = before - len(postings)
        if skipped:
            print(f"\nSkipping {skipped} posting(s) already in the tracker.")

    postings = postings[: args.limit]
    if not postings:
        print("\nNo postings to process.")
        return 0

    mode = "SUBMIT" if args.submit else "DRY RUN"
    print(f"\nProcessing {len(postings)} posting(s) in {mode} mode.")
    if not args.submit:
        print("Nothing will be typed or submitted. Pass --submit to act on the plan.")

    from .browser import managed_driver
    from .form_filler import apply_to_job

    results: list[ApplicationResult] = []
    cover_letter = str(settings.cover_letter_path) if settings.cover_letter_path else ""

    try:
        with managed_driver(settings.browser, settings.headless) as driver:
            for posting in postings:
                result = apply_to_job(
                    driver,
                    posting,
                    resume,
                    resume_path=str(settings.resume_path.resolve()),
                    cover_letter_path=cover_letter,
                    threshold=settings.confidence_threshold,
                    timeout=settings.page_timeout,
                    submit=args.submit,
                )
                results.append(result)
                print(render_result(result))
                if args.tailor:
                    from .tailoring import keyword_gap, render_keyword_gap, write_cover_letter

                    gap = keyword_gap(posting, resume)
                    if gap.top_terms:
                        print("\n  Keyword gap")
                        print(render_keyword_gap(gap))
                        letter = write_cover_letter(posting, resume, settings.output_dir, gap)
                        print(f"  Cover letter draft: {letter}")
                tracker.record(result)
    except KeyboardInterrupt:
        print("\nInterrupted. Recording what completed so far.", file=sys.stderr)

    if results:
        report_path = write_json_report(results, settings.output_dir, resume)
        print(f"\nRun report: {report_path}")

    counts = tracker.summary()
    if counts:
        print("Tracker totals: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    failed = sum(1 for r in results if r.status is ApplicationStatus.FAILED)
    return 1 if failed else 0


def command_tailor(args: argparse.Namespace, settings: Settings) -> int:
    """Analyse postings against the resume. Opens no browser and applies to nothing."""
    from .tailoring import keyword_gap, render_keyword_gap, write_cover_letter

    resume_path = Path(args.resume).expanduser() if args.resume else settings.resume_path
    resume = parse_resume(resume_path)
    print(render_resume_summary(resume))

    args.urls = None  # tailor has no --urls source
    postings = _load_postings(args)[: args.limit]
    if not postings:
        print("\nNo postings matched.")
        return 0

    ranked = []
    for posting in postings:
        gap = keyword_gap(posting, resume)
        ranked.append((gap, posting))

    ranked.sort(key=lambda pair: pair[0].coverage, reverse=True)

    for gap, posting in ranked:
        heading = posting.title or posting.url
        if posting.company:
            heading += f" at {posting.company}"
        print("\n" + "=" * 96)
        print(heading)
        print(posting.url)
        print("=" * 96)
        print(render_keyword_gap(gap))
        if args.cover_letters:
            path = write_cover_letter(posting, resume, settings.output_dir, gap)
            print(f"  Cover letter draft: {path}")

    print("\nRanked best fit first by keyword coverage.")
    return 0


def command_export(args: argparse.Namespace, settings: Settings) -> int:
    tracker = Tracker(settings.database_path)
    path = tracker.export_csv(args.destination)
    print(f"Exported tracker to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    settings = _resolve_settings(args)

    handlers = {
        "parse": command_parse,
        "inspect": command_inspect,
        "apply": command_apply,
        "tailor": command_tailor,
        "export": command_export,
    }

    try:
        return handlers[args.command](args, settings)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

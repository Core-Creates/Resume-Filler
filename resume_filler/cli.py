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
from .models import ApplicationResult, ApplicationStatus, JobPosting, RunMode
from .profile import load_profile
from .reporting import (
    diagnose_sparse_scan,
    render_next_steps,
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
        "--browser", choices=("chrome", "edge", "firefox"), help="Which browser to drive."
    )
    parser.add_argument(
        "--profile",
        help="JSON file of answers your resume does not contain. Defaults to ./profile.json",
    )
    parser.add_argument(
        "--session",
        help=(
            "Directory where the browser keeps cookies, so a login survives between "
            "runs. Required for portals that put the application behind a sign in."
        ),
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
    origin.add_argument("--url", help="A single application URL.")
    origin.add_argument("--urls", help="Text file with one application URL per line.")
    origin.add_argument("--csv", help="CSV with a url column.")
    origin.add_argument("--greenhouse", help="Greenhouse board token, for example 'stripe'.")
    origin.add_argument("--lever", help="Lever company slug, for example 'netflix'.")
    apply_cmd.add_argument("--resume", help="Path to the resume PDF.")
    apply_cmd.add_argument("--keywords", nargs="*", help="Only keep postings matching these.")
    apply_cmd.add_argument("--location", default="", help="Only keep postings in this location.")
    apply_cmd.add_argument("--limit", type=int, default=10, help="Maximum postings to process.")
    depth = apply_cmd.add_mutually_exclusive_group()
    depth.add_argument(
        "--fill",
        action="store_true",
        help=(
            "Fill the form in a real browser and stop, leaving it open for you to "
            "check and submit yourself. Never submits."
        ),
    )
    depth.add_argument(
        "--submit",
        action="store_true",
        help="Fill and submit. Still refuses when a required field could not be filled.",
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

    init_cmd = subparsers.add_parser("init", help="Set up .env and profile.json. Start here.")
    init_cmd.add_argument(
        "--yes", action="store_true", help="Take every default without prompting."
    )

    login_cmd = subparsers.add_parser(
        "login", help="Open a browser so you can sign in once; the session is saved."
    )
    login_cmd.add_argument("url", help="The portal to open, for example your ATS sign-in page.")

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
    if getattr(args, "session", None):
        settings.session_dir = Path(args.session).expanduser()
    if getattr(args, "browser", None):
        settings.browser = args.browser
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
        print(render_plan(matches, verbose=args.verbose))
        print()
        print(render_next_steps(matches))
        hint = diagnose_sparse_scan(html, len(fields))
        if hint:
            print()
            print(hint)
        return 0

    from .browser import managed_driver
    from .form_filler import fill_form

    with managed_driver(settings.browser, settings.headless, settings.session_dir) as driver:
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
    print(render_plan(matches, verbose=args.verbose))
    print()
    print(render_next_steps(matches))
    return 0


def _load_postings(args: argparse.Namespace) -> list[JobPosting]:
    from . import sources

    if getattr(args, "html", None):
        postings = sources.from_html_file(args.html)
        return sources.filter_postings(
            postings, keywords=getattr(args, "keywords", None), location=args.location
        )
    if getattr(args, "url", None):
        postings = [JobPosting(url=args.url, source="url")]
    elif args.urls:
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

    if args.submit:
        mode = RunMode.SUBMIT
    elif args.fill:
        mode = RunMode.FILL
    else:
        mode = RunMode.PREVIEW

    print(f"\nProcessing {len(postings)} posting(s), mode: {mode.value}.")
    if mode is RunMode.PREVIEW:
        print("Nothing will be typed. Pass --fill to complete the form in a browser,")
        print("or --submit to fill and send it.")
    elif mode is RunMode.FILL:
        print("The form will be filled and left open for you to check and submit.")
        if settings.headless:
            print("HEADLESS is true, so you will not see it. Set HEADLESS=false in .env.")

    from .browser import managed_driver
    from .form_filler import apply_to_job

    results: list[ApplicationResult] = []
    cover_letter = str(settings.cover_letter_path) if settings.cover_letter_path else ""

    try:
        with managed_driver(settings.browser, settings.headless, settings.session_dir) as driver:
            for posting in postings:
                result = apply_to_job(
                    driver,
                    posting,
                    resume,
                    resume_path=str(settings.resume_path.resolve()),
                    cover_letter_path=cover_letter,
                    threshold=settings.confidence_threshold,
                    timeout=settings.page_timeout,
                    mode=mode,
                )
                results.append(result)
                print(render_result(result))
                print()
                print(render_next_steps(result.matches))
                if args.tailor:
                    from .tailoring import keyword_gap, render_keyword_gap, write_cover_letter

                    gap = keyword_gap(posting, resume)
                    if gap.top_terms:
                        print("\n  Keyword gap")
                        print(render_keyword_gap(gap))
                        letter = write_cover_letter(posting, resume, settings.output_dir, gap)
                        print(f"  Cover letter draft: {letter}")
                tracker.record(result)

                if mode is RunMode.FILL:
                    _hold_for_review(posting, last=posting is postings[-1])
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


def _hold_for_review(posting: JobPosting, last: bool) -> None:
    """Keep the filled form on screen until the applicant is done with it.

    Filling a form and then closing the window before anyone can look at it
    would be worse than not filling it, so this waits. With no terminal
    attached there is nobody to wait for, and it says so rather than blocking a
    script forever.
    """
    print()
    print("  The form is filled and waiting in the browser.")
    print("  Check it, fix anything you want, and submit it yourself.")

    if not (sys.stdin and sys.stdin.isatty()):
        print("  Not running interactively, so the browser will close now.")
        return

    prompt = "  Press Enter when you are done" + ("" if last else " to move to the next posting")
    try:
        input(f"{prompt}... ")
    except (EOFError, KeyboardInterrupt):
        print()


def command_init(args: argparse.Namespace, settings: Settings) -> int:
    """Set up .env and profile.json so the first real command just works."""
    from .paths import default_config_dir, is_frozen
    from .setup_wizard import (
        Prompter,
        find_resumes,
        render_env,
        render_profile,
        suggested_profile,
    )

    prompter = Prompter(interactive=None if not args.yes else False)
    root = default_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    env_path, profile_path = root / ".env", root / "profile.json"
    if is_frozen():
        print(f"Configuration will be kept in {root}")

    print("Resume-Filler setup")
    print("-" * 60)

    # 1. The resume.
    candidates = find_resumes()
    resume_path: Path | None = None
    if candidates:
        labels = [str(p) for p in candidates]
        index = prompter.choose("Which resume should be the default?", labels)
        resume_path = candidates[index] if index >= 0 else None
    if resume_path is None:
        typed = prompter.ask("\nPath to your resume PDF", "")
        resume_path = Path(typed).expanduser() if typed else None

    if resume_path and resume_path.is_file():
        print(f"\nUsing {resume_path}")
        try:
            resume = parse_resume(resume_path)
            print()
            print(render_resume_summary(resume))
            if not resume.email or not resume.full_name:
                print(
                    "\nWarning: the name or email did not come through. "
                    "Check the resume before relying on this."
                )
        except (FileNotFoundError, ValueError) as exc:
            print(f"\nCould not parse it: {exc}")
            resume = None
    else:
        print("\nNo resume selected. Set RESUME_PATH in .env when you have one.")
        resume_path, resume = Path("resume.pdf"), None

    # 2. The config file.
    session_dir = root / ".rf-session"
    if env_path.exists() and not prompter.confirm(f"\n{env_path.name} exists. Replace it?", False):
        print(f"Kept the existing {env_path.name}.")
    else:
        env_path.write_text(render_env(resume_path, session_dir), encoding="utf-8")
        print(f"\nWrote {env_path}")

    # 3. The profile.
    if profile_path.exists() and not prompter.confirm(
        f"{profile_path.name} exists. Replace it?", False
    ):
        print(f"Kept the existing {profile_path.name}.")
    else:
        values = suggested_profile(resume)
        print("\nA few answers no resume contains. Press Enter to skip any of them.")
        values["address_line1"] = prompter.ask("  Street address", values["address_line1"])
        values["city"] = prompter.ask("  City", values["city"])
        values["state"] = prompter.ask("  State", values["state"])
        values["postal_code"] = prompter.ask("  Postal code", values["postal_code"])
        values["country"] = prompter.ask("  Country", values["country"] or "United States")
        values["work_authorization"] = prompter.ask(
            "  Authorised to work without sponsorship? (Yes/No)", values["work_authorization"]
        )
        values["how_did_you_hear"] = prompter.ask(
            "  Usual answer to 'how did you hear about us'", values["how_did_you_hear"]
        )
        profile_path.write_text(render_profile(values), encoding="utf-8")
        print(f"\nWrote {profile_path}")

    print()
    print("-" * 60)
    print("Ready. Try one of these:")
    print()
    print("  resume-filler parse                     check what your resume gives us")
    print("  resume-filler inspect --html page.html  preview a saved application form")
    print("  resume-filler tailor --html page.html   see how well you match a posting")
    print()
    print("Nothing types or submits anything unless you pass --submit.")
    print("Both files are gitignored. Never commit them.")
    return 0


def command_login(args: argparse.Namespace, settings: Settings) -> int:
    """Open a browser, wait while the user signs in, and keep the session.

    Most application portals put the form behind a sign in, so a driver that
    starts with no cookies lands on a login page and finds nothing. There is no
    good way to automate the sign in itself, and no reason to: the applicant
    does it once by hand and every later run reuses it.
    """
    if not settings.session_dir:
        print(
            "Error: no session directory. Pass --session <dir> or set SESSION_DIR "
            "in .env, otherwise there is nowhere to keep the login.",
            file=sys.stderr,
        )
        return 2

    from .browser import managed_driver

    print(f"Opening {args.url}")
    print(f"Session will be kept in {settings.session_dir.resolve()}")
    print()
    print("Sign in in the browser window, then come back here and press Enter.")
    print("Close nothing yourself; this will shut the browser down for you.")

    try:
        with managed_driver(settings.browser, headless=False, profile_dir=settings.session_dir):
            input("\nPress Enter once you are signed in... ")
    except KeyboardInterrupt:
        print("\nCancelled. Nothing was saved.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - report rather than traceback
        message = str(exc)
        if "user data directory is already in use" in message.lower():
            print(
                "\nError: that session directory is in use by a running browser. "
                "Close every window of that browser and try again.",
                file=sys.stderr,
            )
            return 2
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    print(f"\nSession saved to {settings.session_dir.resolve()}")
    print("Pass the same --session to inspect or apply and they will be signed in.")
    print()
    print("Two things to know:")
    print(
        "  Treat that directory as a password. It holds live login cookies and is "
        "worth exactly as much as your account."
    )
    print(
        "  If the site issued a session-only cookie, the login ends when the browser "
        "closes. Tick 'remember me' at sign in, or do the login and the run together."
    )
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
        "init": command_init,
        "login": command_login,
        "export": command_export,
    }

    try:
        return handlers[args.command](args, settings)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a stack trace helps nobody here
        from .browser import explain_driver_failure

        explanation = explain_driver_failure(exc, settings.browser)
        if explanation:
            print(f"\n{explanation}", file=sys.stderr)
            logger.debug("Underlying error", exc_info=True)
            return 2
        raise
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

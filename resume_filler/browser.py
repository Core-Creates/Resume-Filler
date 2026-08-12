"""Selenium WebDriver construction.

Selenium 4.6 and later ships Selenium Manager, which resolves and downloads the
correct driver binary on its own. The external ``webdriver_manager`` dependency
the original code used is no longer needed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A plain automation banner is honest and harmless. Anti-bot evasion is
# deliberately not implemented here.
DEFAULT_WINDOW = (1440, 1000)


DEFAULT_DEBUG_PORT = 9222


def attach_to_browser(browser: str, port: int) -> Any:
    """Drive a browser that is already open, rather than starting a new one.

    This only works if that browser was started with a remote debugging port.
    A normally launched Chrome accepts no external control at all, by design,
    so there is no way to reach the window someone already has in front of them
    unless it was opened for it. ``resume-filler browser`` starts one correctly.

    Firefox is not supported here. Its automation protocol has no equivalent of
    attaching to a running instance.
    """
    from selenium import webdriver

    normalized = browser.strip().lower()
    if normalized == "chrome":
        options: Any = webdriver.ChromeOptions()
    elif normalized == "edge":
        options = webdriver.EdgeOptions()
    else:
        raise ValueError(
            f"Cannot attach to a running {browser}. Only chrome and edge support it; "
            "use --browser chrome, or drop --attach to start a fresh browser."
        )

    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    driver = (
        webdriver.Chrome(options=options)
        if normalized == "chrome"
        else webdriver.Edge(options=options)
    )
    logger.info("Attached to the %s already running on port %d", normalized, port)
    return driver


def build_driver(
    browser: str = "chrome",
    headless: bool = False,
    profile_dir: str | Path | None = None,
    attach_port: int | None = None,
) -> Any:
    """Create a WebDriver for the named browser.

    ``profile_dir`` gives the browser somewhere durable to keep cookies. Without
    it every run starts logged out, which is fine for public boards but useless
    for iCIMS, Workday and the other portals that put the application behind a
    sign in. With it, you log in once and later runs reuse the session.

    Raises ``ValueError`` for an unsupported browser name.
    """
    if attach_port:
        return attach_to_browser(browser, attach_port)

    from selenium import webdriver

    normalized = browser.strip().lower()
    profile_path = Path(profile_dir).expanduser().resolve() if profile_dir else None
    if profile_path:
        profile_path.mkdir(parents=True, exist_ok=True)

    # Each branch keeps its own options type. Selenium's per-browser Options
    # classes are unrelated to each other, so a shared variable would not type.
    driver: Any
    if normalized == "chrome":
        chrome_options = webdriver.ChromeOptions()
        if headless:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        if profile_path:
            chrome_options.add_argument(f"--user-data-dir={profile_path}")
        driver = webdriver.Chrome(options=chrome_options)
    elif normalized == "edge":
        edge_options = webdriver.EdgeOptions()
        if headless:
            edge_options.add_argument("--headless=new")
        edge_options.add_argument("--disable-gpu")
        if profile_path:
            edge_options.add_argument(f"--user-data-dir={profile_path}")
        driver = webdriver.Edge(options=edge_options)
    elif normalized == "firefox":
        firefox_options = webdriver.FirefoxOptions()
        if headless:
            firefox_options.add_argument("-headless")
        if profile_path:
            firefox_options.add_argument("-profile")
            firefox_options.add_argument(str(profile_path))
        driver = webdriver.Firefox(options=firefox_options)
    else:
        raise ValueError(f"Unsupported browser {browser!r}. Use chrome, edge or firefox.")

    driver.set_window_size(*DEFAULT_WINDOW)
    logger.info(
        "Started %s driver (headless=%s, session=%s)",
        normalized,
        headless,
        profile_path or "fresh",
    )
    return driver


def explain_driver_failure(exc: Exception, browser: str) -> str:
    """Turn a Selenium stack trace into a sentence and a next step.

    These three account for almost every failure to start: the browser is not
    installed, another window already owns the session directory, or the machine
    is offline while Selenium Manager tries to fetch a driver. A raw
    WebDriverException tells the applicant none of that.
    """
    message = str(exc).lower()

    if (
        "user data directory is already in use" in message
        or "profile appears to be in use" in message
    ):
        return (
            f"That session directory is already open in another {browser} window.\n"
            f"Close every {browser} window and run this again."
        )
    if "unable to locate" in message or "cannot find" in message or "no such file" in message:
        others = " or ".join(b for b in ("chrome", "edge", "firefox") if b != browser)
        return (
            f"{browser.title()} does not appear to be installed, or could not be found.\n"
            f"Install it, or use --browser with {others}."
        )
    if "err_name_not_resolved" in message or "err_internet_disconnected" in message:
        return "That address could not be reached. Check the URL and your connection."
    if "unable to obtain" in message or "could not start a new session" in message:
        return (
            f"Could not start {browser}. It may be mid-update, or a driver download\n"
            "may have failed. Try again, or use --browser with a different one."
        )
    return ""


@contextmanager
def managed_driver(
    browser: str = "chrome",
    headless: bool = False,
    profile_dir: str | Path | None = None,
    attach_port: int | None = None,
) -> Iterator[Any]:
    """Yield a driver and clean up, including on Ctrl-C.

    A browser we started is ours to close. One we merely attached to is not:
    quitting it would shut every tab the person had open, which is a rude way to
    end a run that was supposed to help.
    """
    driver = build_driver(browser, headless, profile_dir, attach_port)
    attached = bool(attach_port)
    try:
        yield driver
    finally:
        # Never "return" from this block. A return inside finally discards any
        # exception still propagating, so a run that failed would look like it
        # succeeded.
        if attached:
            logger.debug("Leaving the attached browser open, it was not ours to close")
        else:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001 - shutdown must never mask the real error
                logger.debug("Driver did not shut down cleanly", exc_info=True)

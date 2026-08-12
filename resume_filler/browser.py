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


def build_driver(
    browser: str = "chrome",
    headless: bool = False,
    profile_dir: str | Path | None = None,
) -> Any:
    """Create a WebDriver for the named browser.

    ``profile_dir`` gives the browser somewhere durable to keep cookies. Without
    it every run starts logged out, which is fine for public boards but useless
    for iCIMS, Workday and the other portals that put the application behind a
    sign in. With it, you log in once and later runs reuse the session.

    Raises ``ValueError`` for an unsupported browser name.
    """
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


@contextmanager
def managed_driver(
    browser: str = "chrome",
    headless: bool = False,
    profile_dir: str | Path | None = None,
) -> Iterator[Any]:
    """Yield a driver and guarantee it is closed, including on Ctrl-C."""
    driver = build_driver(browser, headless, profile_dir)
    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001 - shutdown must never mask the real error
            logger.debug("Driver did not shut down cleanly", exc_info=True)

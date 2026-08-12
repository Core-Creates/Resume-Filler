"""Tests for the persistent browser session.

Most application portals put the form behind a sign in, so a driver that starts
with no cookies lands on a login page and finds nothing. Three of the vendors
tested (iCIMS, Workday, Infor CloudSuite) are like this.

Driver construction is checked by capturing the options object rather than
launching a browser. That the cookies actually survive a restart was verified
separately against real Chrome.
"""

from __future__ import annotations

import pytest

from resume_filler import browser
from resume_filler.cli import build_parser, command_login
from resume_filler.config import Settings


class FakeOptions:
    def __init__(self) -> None:
        self.arguments: list[str] = []

    def add_argument(self, argument: str) -> None:
        self.arguments.append(argument)


class FakeDriver:
    def __init__(self, options: FakeOptions) -> None:
        self.options = options
        self.quit_called = False

    def set_window_size(self, width: int, height: int) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True


@pytest.fixture
def fake_webdriver(monkeypatch):
    """Stand in for selenium.webdriver so no browser is launched."""
    import types

    created: dict[str, FakeDriver] = {}

    def make(name):
        def factory(options=None, **_kwargs):
            driver = FakeDriver(options)
            created[name] = driver
            return driver

        return factory

    module = types.SimpleNamespace(
        ChromeOptions=FakeOptions,
        EdgeOptions=FakeOptions,
        FirefoxOptions=FakeOptions,
        Chrome=make("chrome"),
        Edge=make("edge"),
        Firefox=make("firefox"),
    )
    selenium = types.SimpleNamespace(webdriver=module)
    monkeypatch.setitem(__import__("sys").modules, "selenium", selenium)
    monkeypatch.setitem(__import__("sys").modules, "selenium.webdriver", module)
    return created


class TestSessionDirectory:
    def test_chrome_is_pointed_at_the_session(self, fake_webdriver, tmp_path) -> None:
        session = tmp_path / "session"
        driver = browser.build_driver("chrome", headless=True, profile_dir=session)
        assert any(
            arg.startswith("--user-data-dir=") and str(session.resolve()) in arg
            for arg in driver.options.arguments
        )

    def test_edge_is_pointed_at_the_session(self, fake_webdriver, tmp_path) -> None:
        driver = browser.build_driver("edge", profile_dir=tmp_path / "s")
        assert any(arg.startswith("--user-data-dir=") for arg in driver.options.arguments)

    def test_firefox_uses_its_own_profile_flag(self, fake_webdriver, tmp_path) -> None:
        """Firefox takes "-profile <dir>" as two arguments, not one."""
        driver = browser.build_driver("firefox", profile_dir=tmp_path / "s")
        assert "-profile" in driver.options.arguments

    def test_the_directory_is_created(self, fake_webdriver, tmp_path) -> None:
        session = tmp_path / "made" / "here"
        browser.build_driver("chrome", profile_dir=session)
        assert session.is_dir()

    def test_without_a_session_the_browser_starts_clean(self, fake_webdriver) -> None:
        """The default must stay a fresh session; a shared one is a surprise."""
        driver = browser.build_driver("chrome")
        assert not any("user-data-dir" in arg for arg in driver.options.arguments)

    def test_managed_driver_passes_it_through(self, fake_webdriver, tmp_path) -> None:
        session = tmp_path / "session"
        with browser.managed_driver("chrome", True, session) as driver:
            assert any("user-data-dir" in arg for arg in driver.options.arguments)
        assert driver.quit_called, "the browser must still be shut down"

    def test_an_unsupported_browser_still_raises(self, fake_webdriver, tmp_path) -> None:
        with pytest.raises(ValueError, match="Unsupported browser"):
            browser.build_driver("netscape", profile_dir=tmp_path)


class TestSessionSettings:
    def test_it_defaults_to_no_session(self) -> None:
        assert Settings().session_dir is None

    def test_it_reads_the_environment(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("SESSION_DIR", str(tmp_path / "s"))
        assert Settings.from_env(tmp_path / "missing.env").session_dir == tmp_path / "s"

    def test_a_blank_value_means_none(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("SESSION_DIR", "   ")
        assert Settings.from_env(tmp_path / "missing.env").session_dir is None


class TestLoginCommand:
    def test_it_is_registered(self) -> None:
        args = build_parser().parse_args(["login", "https://example.com/signin"])
        assert args.command == "login"
        assert args.url == "https://example.com/signin"

    def test_the_session_flag_is_global(self) -> None:
        args = build_parser().parse_args(["--session", "s", "login", "https://example.com"])
        assert args.session == "s"

    def test_it_refuses_without_somewhere_to_save(self, capsys) -> None:
        """Signing in with nowhere to keep the result would waste the user's time."""
        args = build_parser().parse_args(["login", "https://example.com"])
        assert command_login(args, Settings()) == 2
        assert "no session directory" in capsys.readouterr().err.lower()

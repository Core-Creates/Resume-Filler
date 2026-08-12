"""Tests for driving a browser that is already open.

A browser started the ordinary way accepts no external control, so there is no
way to reach the window someone already has in front of them. It has to have
been started with a remote debugging port, which is what the browser command is
for.

Verified against real Chrome as well: launched on port 9333, attached to, eight
fields filled in that window, and the browser still running afterwards.
"""

from __future__ import annotations

import pytest

from resume_filler import browser
from resume_filler.cli import build_parser
from resume_filler.config import Settings


class FakeOptions:
    def __init__(self) -> None:
        self.arguments: list[str] = []
        self.experimental: dict[str, object] = {}

    def add_argument(self, argument: str) -> None:
        self.arguments.append(argument)

    def add_experimental_option(self, name: str, value: object) -> None:
        self.experimental[name] = value


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
    import sys
    import types

    def factory(options=None, **_kwargs):
        return FakeDriver(options)

    module = types.SimpleNamespace(
        ChromeOptions=FakeOptions,
        EdgeOptions=FakeOptions,
        FirefoxOptions=FakeOptions,
        Chrome=factory,
        Edge=factory,
        Firefox=factory,
    )
    monkeypatch.setitem(sys.modules, "selenium", types.SimpleNamespace(webdriver=module))
    monkeypatch.setitem(sys.modules, "selenium.webdriver", module)


class TestAttaching:
    def test_it_points_at_the_debugging_port(self, fake_webdriver) -> None:
        driver = browser.attach_to_browser("chrome", 9222)
        assert driver.options.experimental["debuggerAddress"] == "127.0.0.1:9222"

    def test_edge_is_supported_too(self, fake_webdriver) -> None:
        driver = browser.attach_to_browser("edge", 9333)
        assert driver.options.experimental["debuggerAddress"] == "127.0.0.1:9333"

    def test_firefox_is_refused_with_a_reason(self, fake_webdriver) -> None:
        """Its automation protocol has no equivalent, so saying so beats failing
        obscurely later."""
        with pytest.raises(ValueError, match="Only chrome and edge"):
            browser.attach_to_browser("firefox", 9222)

    def test_build_driver_attaches_rather_than_launching(self, fake_webdriver) -> None:
        driver = browser.build_driver("chrome", headless=True, attach_port=9222)
        assert "debuggerAddress" in driver.options.experimental
        # None of the launch arguments belong on an attach.
        assert not any("headless" in arg for arg in driver.options.arguments)

    def test_without_a_port_it_still_launches_normally(self, fake_webdriver) -> None:
        driver = browser.build_driver("chrome", headless=True)
        assert "debuggerAddress" not in driver.options.experimental
        assert any("headless" in arg for arg in driver.options.arguments)


class TestNotClosingSomeoneElsesBrowser:
    def test_an_attached_browser_is_left_open(self, fake_webdriver) -> None:
        """Quitting would shut every tab they had open, which is a rude way to
        end a run that was supposed to help."""
        with browser.managed_driver("chrome", attach_port=9222) as driver:
            pass
        assert not driver.quit_called

    def test_a_browser_we_started_is_still_closed(self, fake_webdriver) -> None:
        with browser.managed_driver("chrome") as driver:
            pass
        assert driver.quit_called

    def test_it_is_left_open_even_when_the_run_fails(self, fake_webdriver) -> None:
        captured = {}
        with pytest.raises(RuntimeError), browser.managed_driver("chrome", attach_port=9222) as d:
            captured["driver"] = d
            raise RuntimeError("something went wrong mid-run")
        assert not captured["driver"].quit_called


class TestAttachFlags:
    def test_attach_takes_a_port(self) -> None:
        args = build_parser().parse_args(["--attach", "9333", "parse"])
        assert args.attach == 9333

    def test_the_port_is_required(self) -> None:
        """An optional value would swallow the subcommand: "--attach parse"
        would try to read "parse" as the port number."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--attach", "parse"])

    def test_no_attach_means_none(self) -> None:
        assert build_parser().parse_args(["parse"]).attach is None

    def test_the_browser_command_exists(self) -> None:
        args = build_parser().parse_args(["browser", "--port", "9333", "--isolated"])
        assert args.command == "browser"
        assert args.port == 9333
        assert args.isolated is True

    def test_settings_read_the_port_from_the_environment(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("ATTACH_PORT", "9333")
        assert Settings.from_env(tmp_path / "missing.env").attach_port == 9333

    def test_a_non_numeric_port_is_ignored(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("ATTACH_PORT", "not-a-port")
        assert Settings.from_env(tmp_path / "missing.env").attach_port is None

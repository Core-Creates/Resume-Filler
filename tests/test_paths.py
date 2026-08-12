"""Tests for config discovery.

Everything used to resolve against the working directory, which is correct for a
checkout and useless for a standalone executable someone runs from anywhere:
config would be looked for in whatever folder they happened to be in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_filler import paths
from resume_filler.config import Settings


class TestUserConfigDir:
    def test_windows_uses_appdata(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(paths.sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert paths.user_config_dir() == tmp_path / "resume-filler"

    def test_linux_follows_xdg(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(paths.sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert paths.user_config_dir() == tmp_path / "resume-filler"

    def test_macos_uses_application_support(self, monkeypatch) -> None:
        monkeypatch.setattr(paths.sys, "platform", "darwin")
        assert paths.user_config_dir().parts[-2:] == ("Application Support", "resume-filler")


class TestConfigDiscovery:
    def test_the_current_directory_wins(self, monkeypatch, tmp_path) -> None:
        """A checkout must keep behaving exactly as it did."""
        local = tmp_path / "here"
        local.mkdir()
        (local / ".env").write_text("RESUME_PATH=local.pdf", encoding="utf-8")
        monkeypatch.chdir(local)
        assert paths.find_config_file(".env") == local / ".env"

    def test_the_user_directory_is_the_fallback(self, monkeypatch, tmp_path) -> None:
        stored = tmp_path / "stored"
        stored.mkdir()
        (stored / ".env").write_text("RESUME_PATH=stored.pdf", encoding="utf-8")
        monkeypatch.setattr(paths, "user_config_dir", lambda: stored)
        empty = tmp_path / "elsewhere"
        empty.mkdir()
        monkeypatch.chdir(empty)
        assert paths.find_config_file(".env") == stored / ".env"

    def test_missing_everywhere_is_not_an_error(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(paths, "user_config_dir", lambda: tmp_path / "absent")
        monkeypatch.chdir(tmp_path)
        assert paths.find_config_file(".env") is None


class TestWhereInitWrites:
    def test_a_checkout_writes_beside_the_code(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(paths, "is_frozen", lambda: False)
        monkeypatch.chdir(tmp_path)
        assert paths.default_config_dir() == Path.cwd()

    def test_a_frozen_build_writes_to_the_user_directory(self, monkeypatch, tmp_path) -> None:
        """An executable has no meaningful "beside the code"."""
        monkeypatch.setattr(paths, "is_frozen", lambda: True)
        monkeypatch.setattr(paths, "user_config_dir", lambda: tmp_path / "cfg")
        assert paths.default_config_dir() == tmp_path / "cfg"


class TestRelativeDataPaths:
    def test_a_relative_path_sits_beside_its_config(self, tmp_path) -> None:
        """Otherwise a database scatters itself across every folder the
        executable is run from."""
        assert paths.resolve_data_path("applications.db", tmp_path) == tmp_path / "applications.db"

    def test_an_absolute_path_is_left_alone(self, tmp_path) -> None:
        target = tmp_path / "elsewhere" / "apps.db"
        assert paths.resolve_data_path(target, tmp_path) == target

    def test_settings_resolve_relative_paths(self, monkeypatch, tmp_path) -> None:
        config = tmp_path / "cfg"
        config.mkdir()
        (config / ".env").write_text("DATABASE_PATH=applications.db\n", encoding="utf-8")
        monkeypatch.delenv("DATABASE_PATH", raising=False)
        settings = Settings.from_env(config / ".env")
        assert settings.database_path == config / "applications.db"


class TestPackagingEntryPoint:
    def test_it_imports_absolutely(self) -> None:
        """A relative import works for python -m but fails as a PyInstaller
        entry script, which is how the first build broke."""
        source = (Path(__file__).parent.parent / "packaging_entry.py").read_text(encoding="utf-8")
        assert "from resume_filler.cli import main" in source
        # Only real import statements count. The docstring names the relative
        # form when explaining why it is not used.
        imports = [line.strip() for line in source.splitlines() if line.startswith("from ")]
        assert not any(line.startswith("from .") for line in imports)

    @pytest.mark.parametrize("collected", ["selenium", "pdfminer"])
    def test_the_spec_collects_what_would_silently_break(self, collected: str) -> None:
        """selenium carries selenium-manager, without which no browser starts;
        pdfminer carries the character maps text extraction needs."""
        spec = (Path(__file__).parent.parent / "resume-filler.spec").read_text(encoding="utf-8")
        assert collected in spec

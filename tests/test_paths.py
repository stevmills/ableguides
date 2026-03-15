"""Tests for ableguides.paths -- WSL/Windows path conversion."""

from __future__ import annotations

from pathlib import Path

import pytest

from ableguides.paths import is_windows_accessible, resolve_output_dir, to_windows_path


class TestToWindowsPath:
    def test_c_drive(self):
        assert to_windows_path(Path("/mnt/c/Users/me/Music")) == "C:\\Users\\me\\Music"

    def test_d_drive(self):
        assert to_windows_path(Path("/mnt/d/Projects")) == "D:\\Projects"

    def test_drive_root(self):
        assert to_windows_path(Path("/mnt/c")) == "C:\\"

    def test_uppercase_drive_normalized(self):
        assert to_windows_path(Path("/mnt/C/Users")) == "C:\\Users"

    def test_non_mnt_returns_none(self):
        assert to_windows_path(Path("/home/me/dev")) is None

    def test_mnt_without_drive_letter_returns_none(self):
        assert to_windows_path(Path("/mnt/longdrive/foo")) is None

    def test_root_returns_none(self):
        assert to_windows_path(Path("/")) is None

    def test_deeply_nested(self):
        result = to_windows_path(
            Path("/mnt/c/Users/testuser/Music/Ableton/GuidePacks")
        )
        assert result == "C:\\Users\\testuser\\Music\\Ableton\\GuidePacks"

    def test_spaces_in_path(self):
        result = to_windows_path(Path("/mnt/c/Users/testuser/My Music"))
        assert result == "C:\\Users\\testuser\\My Music"


class TestIsWindowsAccessible:
    def test_mnt_path_accessible(self):
        assert is_windows_accessible(Path("/mnt/c/Users")) is True

    def test_home_path_not_accessible(self):
        assert is_windows_accessible(Path("/home/testuser")) is False


class TestResolveOutputDir:
    def test_absolute_path_unchanged(self):
        result = resolve_output_dir("/mnt/c/Users/testuser/Music")
        assert result == Path("/mnt/c/Users/testuser/Music")

    def test_relative_resolved_against_cwd(self, tmp_path: Path):
        result = resolve_output_dir("output", cwd=tmp_path)
        assert result == tmp_path / "output"

    def test_tilde_expanded(self):
        result = resolve_output_dir("~/music")
        assert not str(result).startswith("~")
        assert result.is_absolute()

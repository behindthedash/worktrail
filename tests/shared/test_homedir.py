"""worktrail_home() resolution order.

Every case runs against a monkeypatched HOME + a scrubbed WORKTRAIL_HOME so
the real operator machine's ~/.worktrail / legacy ~/.go can never leak in
(same isolation posture as tests/conftest.py's machine-wide-config fixture).
"""

import os
import unittest
from unittest import mock

import pytest

from worktrail.shared import homedir
from worktrail.shared.homedir import worktrail_home


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Fake $HOME per test; no WORKTRAIL_HOME unless the test sets one."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("WORKTRAIL_HOME", raising=False)
    # The deprecation note is once-per-process; reset so each test observes
    # its own first-call behavior.
    monkeypatch.setattr(homedir, "_legacy_warned", False)
    return tmp_path


class TestEnvOverride:
    def test_env_var_wins_even_when_absent_on_disk(self, tmp_path, monkeypatch):
        target = tmp_path / "custom-state-dir"  # deliberately never created
        monkeypatch.setenv("WORKTRAIL_HOME", str(target))
        assert worktrail_home() == target

    def test_env_var_is_expanduserd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKTRAIL_HOME", "~/state")
        assert worktrail_home() == tmp_path / "state"

    def test_env_var_beats_existing_dirs(self, tmp_path, monkeypatch):
        (tmp_path / ".worktrail").mkdir()
        (tmp_path / ".go").mkdir()
        monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "elsewhere"))
        assert worktrail_home() == tmp_path / "elsewhere"


class TestDirScan:
    def test_worktrail_dir_when_it_exists(self, tmp_path, capsys):
        (tmp_path / ".worktrail").mkdir()
        assert worktrail_home() == tmp_path / ".worktrail"
        assert capsys.readouterr().err == ""

    def test_worktrail_dir_beats_legacy(self, tmp_path, capsys):
        (tmp_path / ".worktrail").mkdir()
        (tmp_path / ".go").mkdir()
        assert worktrail_home() == tmp_path / ".worktrail"
        assert capsys.readouterr().err == ""

    def test_legacy_fallback_with_one_deprecation_note(self, tmp_path, capsys):
        (tmp_path / ".go").mkdir()
        assert worktrail_home() == tmp_path / ".go"
        first = capsys.readouterr().err
        assert "legacy state dir" in first
        assert str(tmp_path / ".go") in first
        assert first.count("\n") == 1  # one line
        # Second call: same result, no second note (once per process).
        assert worktrail_home() == tmp_path / ".go"
        assert capsys.readouterr().err == ""

    def test_fresh_default_when_neither_exists(self, tmp_path, capsys):
        assert worktrail_home() == tmp_path / ".worktrail"
        # Never created eagerly -- write sites own their lazy mkdir.
        assert not (tmp_path / ".worktrail").exists()
        assert capsys.readouterr().err == ""


class EnvSettingTests(unittest.TestCase):
    def test_current_name_wins_over_legacy(self):
        with mock.patch.dict(
            os.environ,
            {"WORKTRAIL_CLUSTER_LOG": "/new/path", "GO_CLUSTER_LOG": "/old/path"},
        ):
            self.assertEqual(homedir.env_setting("WORKTRAIL_CLUSTER_LOG"), "/new/path")

    def test_legacy_synonym_accepted_when_current_unset(self):
        with mock.patch.dict(os.environ, {"GO_CLUSTER_LOG": "/old/path"}, clear=False):
            os.environ.pop("WORKTRAIL_CLUSTER_LOG", None)
            self.assertEqual(homedir.env_setting("WORKTRAIL_CLUSTER_LOG"), "/old/path")

    def test_neither_set_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORKTRAIL_CLUSTER_LOG", None)
            os.environ.pop("GO_CLUSTER_LOG", None)
            self.assertIsNone(homedir.env_setting("WORKTRAIL_CLUSTER_LOG"))

    def test_non_worktrail_name_rejected(self):
        with self.assertRaises(ValueError):
            homedir.env_setting("GO_CLUSTER_LOG")

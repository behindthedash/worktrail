from __future__ import annotations

import importlib.metadata as md
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import check_packaging_metadata as metadata


def _repo(
    tmp_path: Path, *, package_version: str = "1.2.3", plugin_version: str = "1.2.3"
) -> Path:
    (tmp_path / ".codex-plugin").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "worktrail"\nversion = "' + package_version + '"\n\n'
        '[project.scripts]\nworktrail-one = "worktrail.one:main"\n'
        'worktrail-two = "worktrail.two:main"  # supported comment\n',
        encoding="utf-8",
    )
    (tmp_path / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"version": plugin_version}), encoding="utf-8"
    )
    return tmp_path


def test_checkout_console_scripts_reads_the_checkout(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert metadata.checkout_console_scripts(repo) == {"worktrail-one", "worktrail-two"}


def test_check_accepts_fresh_installed_and_plugin_metadata(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with patch.object(
        metadata,
        "installed_distribution",
        return_value=("1.2.3", {"worktrail-one", "worktrail-two"}),
    ):
        assert metadata.check(repo) == []


def test_check_reports_stale_installed_entry_points(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with patch.object(
        metadata,
        "installed_distribution",
        return_value=("1.2.3", {"worktrail-one", "worktrail-old"}),
    ):
        errors = metadata.check(repo)
    assert errors == [
        "installed worktrail console-script metadata is stale: "
        "missing=['worktrail-two'] extra=['worktrail-old']"
    ]


def test_check_reports_plugin_version_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path, plugin_version="1.2.2")
    with patch.object(
        metadata,
        "installed_distribution",
        return_value=("1.2.3", {"worktrail-one", "worktrail-two"}),
    ):
        errors = metadata.check(repo)
    assert errors == [
        "Codex plugin manifest version differs from pyproject.toml: "
        "plugin='1.2.2' package='1.2.3'"
    ]


def test_check_reports_installed_version_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with patch.object(
        metadata,
        "installed_distribution",
        return_value=("1.2.2", {"worktrail-one", "worktrail-two"}),
    ):
        errors = metadata.check(repo)
    assert errors == [
        "installed worktrail version differs from pyproject.toml: "
        "installed='1.2.2' package='1.2.3'"
    ]


def test_main_fails_when_distribution_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    with patch.object(
        metadata,
        "installed_distribution",
        side_effect=md.PackageNotFoundError("worktrail"),
    ):
        assert metadata.main(["--repo", str(repo)]) == 1
    assert "PACKAGING METADATA: FAIL" in capsys.readouterr().err

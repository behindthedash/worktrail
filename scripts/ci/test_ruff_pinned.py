from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("ruff_pinned.py")

_spec = importlib.util.spec_from_file_location("ruff_pinned", SCRIPT)
ruff_pinned = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(ruff_pinned)


def _pyproject(tmp_path: Path, dev_entries: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\nname = "x"\nversion = "0"\n\n'
        f"[project.optional-dependencies]\ndev = [{dev_entries}]\n"
    )
    return path


class TestReadPin:
    def test_reads_an_exact_pin(self, tmp_path: Path) -> None:
        path = _pyproject(tmp_path, '"pytest>=7.0", "ruff==0.16.7"')
        assert ruff_pinned.read_pin(path) == "0.16.7"

    def test_a_range_is_not_a_pin(self, tmp_path: Path) -> None:
        """`ruff>=0.8` cannot make two machines agree on a version, which is
        the whole reason this script exists."""
        path = _pyproject(tmp_path, '"ruff>=0.8"')
        with pytest.raises(ruff_pinned.PinError, match="no exact"):
            ruff_pinned.read_pin(path)

    def test_missing_dev_group_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "x"\nversion = "0"\n')
        with pytest.raises(ruff_pinned.PinError, match="no exact"):
            ruff_pinned.read_pin(path)

    def test_unreadable_pyproject_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ruff_pinned.PinError, match="could not read"):
            ruff_pinned.read_pin(tmp_path / "nope.toml")

    def test_this_repo_pins_ruff_exactly(self) -> None:
        """The repo's own pin must stay an `==` pin, or every caller silently
        loses its guarantee."""
        pin = ruff_pinned.read_pin(SCRIPT.resolve().parents[2] / "pyproject.toml")
        assert pin.count(".") >= 1


class TestResolveRuff:
    def test_prefers_a_matching_ruff_on_path(self, monkeypatch) -> None:
        monkeypatch.setattr(ruff_pinned.shutil, "which", lambda name: f"/fake/{name}")
        monkeypatch.setattr(
            ruff_pinned,
            "_version_of",
            lambda argv: "9.9.9" if argv == ["/fake/ruff"] else None,
        )
        assert ruff_pinned.resolve_ruff("9.9.9") == ["/fake/ruff"]

    def test_falls_back_to_uvx_when_path_ruff_mismatches(self, monkeypatch) -> None:
        monkeypatch.setattr(ruff_pinned.shutil, "which", lambda name: f"/fake/{name}")

        def _version(argv):
            if argv == ["/fake/ruff"]:
                return "0.16.5"
            if argv == ["/fake/uvx", "ruff@9.9.9"]:
                return "9.9.9"
            return None

        monkeypatch.setattr(ruff_pinned, "_version_of", _version)
        assert ruff_pinned.resolve_ruff("9.9.9") == ["/fake/uvx", "ruff@9.9.9"]

    def test_refuses_rather_than_running_a_mismatched_ruff(self, monkeypatch) -> None:
        """Falling back to whatever is installed would restore the drift this
        script exists to remove."""
        monkeypatch.setattr(
            ruff_pinned.shutil,
            "which",
            lambda name: "/fake/ruff" if name == "ruff" else None,
        )
        monkeypatch.setattr(ruff_pinned, "_version_of", lambda argv: "0.16.5")
        with pytest.raises(ruff_pinned.PinError) as excinfo:
            ruff_pinned.resolve_ruff("9.9.9")
        message = str(excinfo.value)
        assert "0.16.5" in message and "9.9.9" in message
        assert 'pip install -e ".[dev]"' in message

    def test_no_ruff_at_all_names_both_remedies(self, monkeypatch) -> None:
        monkeypatch.setattr(ruff_pinned.shutil, "which", lambda name: None)
        with pytest.raises(ruff_pinned.PinError) as excinfo:
            ruff_pinned.resolve_ruff("9.9.9")
        message = str(excinfo.value)
        assert "not on PATH" in message
        assert "uvx" in message


class TestCli:
    def test_forwards_argv_and_runs_the_pinned_ruff(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        pin = ruff_pinned.read_pin(SCRIPT.resolve().parents[2] / "pyproject.toml")
        assert result.stdout.strip() == f"ruff {pin}", (
            "the wrapper must run the pinned version, not whatever is on PATH"
        )

    def test_pin_failure_exits_two_with_a_message(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            ruff_pinned,
            "read_pin",
            lambda *a, **k: (_ for _ in ()).throw(ruff_pinned.PinError("boom")),
        )
        assert ruff_pinned.main(["check", "."]) == 2
        assert "ruff_pinned: boom" in capsys.readouterr().err

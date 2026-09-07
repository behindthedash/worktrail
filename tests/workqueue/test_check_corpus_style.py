"""Tests for the work-queue corpus canonical-style scan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktrail.shared.brief_frontmatter import serialize_frontmatter
from worktrail.workqueue.check_corpus_style import main, scan_corpus


def _write(base: Path, subdir: str, name: str, content: str) -> Path:
    target = base / subdir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _canonical_brief(**fields: str) -> str:
    frontmatter = {"id": "b-1", "status": "queued", "focus": "Do the thing", **fields}
    return f"---\n{serialize_frontmatter(frontmatter)}---\n\n# Brief\n"


@pytest.fixture
def queue_base(tmp_path: Path) -> Path:
    base = tmp_path / "work-queue"
    (base / "queue").mkdir(parents=True)
    (base / "picked").mkdir(parents=True)
    return base


def test_canonical_brief_produces_no_finding(queue_base: Path) -> None:
    _write(queue_base, "queue", "clean.md", _canonical_brief())

    assert scan_corpus(queue_base) == []


def test_parseable_non_canonical_brief_is_style_mismatch(queue_base: Path) -> None:
    # Parses fine, but `focus` is a quoted flow scalar rather than the literal
    # block scalar `serialize_frontmatter` emits.
    path = _write(
        queue_base,
        "queue",
        "styled.md",
        '---\nid: b-1\nstatus: queued\nfocus: "Do the thing"\n---\n\n# Brief\n',
    )

    assert scan_corpus(queue_base) == [
        {"path": str(path), "classification": "style-mismatch"}
    ]


def test_brief_without_frontmatter_fence_is_malformed(queue_base: Path) -> None:
    path = _write(queue_base, "queue", "nofence.md", "# Brief\n\nno frontmatter here\n")

    assert scan_corpus(queue_base) == [
        {"path": str(path), "classification": "malformed"}
    ]


def test_brief_with_unparseable_yaml_is_malformed(queue_base: Path) -> None:
    path = _write(
        queue_base,
        "picked",
        "broken.md",
        "---\nid: b-1\n  status: [unclosed\n---\n\n# Brief\n",
    )

    assert scan_corpus(queue_base) == [
        {"path": str(path), "classification": "malformed"}
    ]


def test_cli_human_output_and_exit_code(
    queue_base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(queue_base, "queue", "clean.md", _canonical_brief())
    bad = _write(queue_base, "picked", "nofence.md", "# Brief\n")

    assert main(["--queue-dir", str(queue_base)]) == 1

    out = capsys.readouterr().out
    assert f"malformed: {bad}" in out
    assert "clean.md" not in out


def test_cli_human_output_clean_corpus_exits_zero(
    queue_base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(queue_base, "queue", "clean.md", _canonical_brief())

    assert main(["--queue-dir", str(queue_base)]) == 0
    assert "clean" in capsys.readouterr().out


def test_cli_json_output_and_exit_code(
    queue_base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _write(queue_base, "queue", "nofence.md", "# Brief\n")

    assert main(["--queue-dir", str(queue_base), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"findings": [{"path": str(bad), "classification": "malformed"}]}


def test_cli_json_output_clean_corpus_exits_zero(
    queue_base: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(queue_base, "queue", "clean.md", _canonical_brief())

    assert main(["--queue-dir", str(queue_base), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"findings": []}


def test_cli_defaults_to_work_queue_dir_env(
    queue_base: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = _write(queue_base, "queue", "nofence.md", "# Brief\n")
    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue_base))

    assert main(["--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["findings"] == [{"path": str(bad), "classification": "malformed"}]

"""Tests for the AC named-target precheck (`conductor/ac_targets.py`).

The extraction is narrow on purpose: an update-verb, a backticked path that
resolves to an existing file, and at least one other backticked token. Every
other shape -- additive phrasing, unbackticked prose, a path that does not
exist -- reports nothing.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from worktrail.conductor import ac_targets


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _change(tmp_path: Path, task_text: str) -> tuple[Path, Path]:
    """Build a change dir whose single task carries `task_text`."""
    repo = tmp_path / "repo"
    change = repo / "openspec" / "changes" / "sweep-docs"
    _write(
        change / "tasks.md",
        f"## 1. Docs\n\n- [ ] 1.1 {task_text}\n",
    )
    return change, repo


_AC = "Update the `canonical-checkout-drift-sweep.sh` entry in `scripts/README.md`."


def test_update_of_entry_absent_from_an_existing_file_is_reported(tmp_path: Path):
    change, repo = _change(tmp_path, _AC)
    _write(repo / "scripts" / "README.md", "# Scripts\n\n- `dev-install.sh`\n")

    findings = ac_targets.find_missing_ac_targets(change, repo)

    assert len(findings) == 1
    assert "1.1" in findings[0]
    assert "canonical-checkout-drift-sweep.sh" in findings[0]
    assert "scripts/README.md" in findings[0]


def test_update_of_entry_already_present_reports_nothing(tmp_path: Path):
    change, repo = _change(tmp_path, _AC)
    _write(
        repo / "scripts" / "README.md",
        "# Scripts\n\n- `canonical-checkout-drift-sweep.sh` -- sweeps for drift\n",
    )

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_additive_phrasing_reports_nothing(tmp_path: Path):
    change, repo = _change(
        tmp_path,
        "Add a `canonical-checkout-drift-sweep.sh` entry to `scripts/README.md`.",
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_unbackticked_prose_reports_nothing(tmp_path: Path):
    change, repo = _change(
        tmp_path,
        "Update the canonical-checkout-drift-sweep.sh entry in scripts/README.md.",
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_backticked_path_that_does_not_exist_reports_nothing(tmp_path: Path):
    change, repo = _change(tmp_path, _AC)

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_change_with_no_update_sentence_returns_empty(tmp_path: Path):
    change, repo = _change(tmp_path, "Write the new sweep script.")
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_criterion_on_a_wrapped_continuation_line_is_still_seen(tmp_path: Path):
    repo = tmp_path / "repo"
    change = repo / "openspec" / "changes" / "sweep-docs"
    _write(
        change / "tasks.md",
        """\
        ## 1. Docs

        - [ ] 1.1 Document the sweep script.
              Update the `canonical-checkout-drift-sweep.sh` entry in
              `scripts/README.md`.
        """,
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    findings = ac_targets.find_missing_ac_targets(change, repo)

    assert len(findings) == 1
    assert "canonical-checkout-drift-sweep.sh" in findings[0]


def test_token_named_after_the_path_is_new_content_not_a_needle(tmp_path: Path):
    change, repo = _change(
        tmp_path,
        "Extend `scripts/README.md` with an entry for `canonical-checkout-drift-sweep.sh`.",
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []

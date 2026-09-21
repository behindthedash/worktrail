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


def test_token_named_after_the_path_is_still_a_needle(tmp_path: Path):
    """The path is whichever token resolves to a file; order does not matter."""
    change, repo = _change(
        tmp_path,
        "Update `scripts/README.md` to name `canonical-checkout-drift-sweep.sh`.",
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    findings = ac_targets.find_missing_ac_targets(change, repo)

    assert len(findings) == 1
    assert "canonical-checkout-drift-sweep.sh" in findings[0]


def test_verb_in_a_prior_sentence_ending_in_a_bracket_does_not_carry_over(
    tmp_path: Path,
):
    """`.)` ends a sentence: its verb must not license the next one's tokens."""
    change, repo = _change(
        tmp_path,
        "Rename the old helper (it no longer matches.) "
        "In a new `tests/router/test_grace.py` using `FakeRun` from "
        "`tests/router/test_land_pr.py`, cover the budget.",
    )
    _write(repo / "tests" / "router" / "test_land_pr.py", "class FakeRun:\n    pass\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_verb_lookalike_words_do_not_license_a_sentence(tmp_path: Path):
    """`fixed`/`fixture`/`correctly` are not the nine update verbs."""
    change, repo = _change(
        tmp_path,
        "The `FakeRun` fixture in `tests/router/test_land_pr.py` is fixed "
        "and behaves correctly.",
    )
    _write(repo / "tests" / "router" / "test_land_pr.py", "pass\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []


def test_metadata_continuation_lines_are_not_scanned_as_prose(tmp_path: Path):
    """`files:`/`depends:`/`review:` carry paths, not acceptance criteria."""
    repo = tmp_path / "repo"
    change = repo / "openspec" / "changes" / "sweep-docs"
    _write(
        change / "tasks.md",
        """\
        ## 1. Docs

        - [ ] 1.1 Remove the stale note.
              files: scripts/README.md, scripts/sweep.sh
              depends: 1.0
              review: skip
        """,
    )
    _write(repo / "scripts" / "README.md", "# Scripts\n")

    assert ac_targets.find_missing_ac_targets(change, repo) == []

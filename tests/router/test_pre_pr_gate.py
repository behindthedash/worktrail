#!/usr/bin/env python3
"""Unit tests for pre_pr_gate.py (stdlib unittest, mirrors test_policy.py style)."""
from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router.policy import load_policy
from worktrail.router.pre_pr_gate import (
    CLARIFICATION_INTEGRITY_DRIFT_EXIT,
    SCOPE_COMPLETENESS_EXIT, SPEC_SYNC_DRIFT_EXIT,
    UNCONFIGURED_EXIT, is_docs_only, main, resolve_cmd,
    scope_review_failures, spec_sync_drift,
)


def _write_drifted_spec(specs_root: Path, spec_id: str = "000-fixture") -> None:
    """A spec whose one task is completed but whose parent Status header
    still reads 'Draft' — reproduces the historical drift check_spec_sync.py
    was written to catch."""
    spec_dir = specs_root / spec_id
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\n---\n\ndone\n", encoding="utf-8"
    )
    (spec_dir / "spec.md").write_text(
        "# Functional Specification: Fixture\n\n**Status**: Draft\n", encoding="utf-8"
    )


class TestPrePrGate(unittest.TestCase):
    def _repo(self, policy_yaml: str | None) -> str:
        d = tempfile.mkdtemp(prefix="prepr-")
        if policy_yaml is not None:
            spec = Path(d) / "docs" / "specs"
            spec.mkdir(parents=True)
            (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    # --- resolve_cmd -------------------------------------------------------
    def test_pre_pr_cmd_wins_over_integrate_smoke_cmd(self) -> None:
        repo = self._repo('pre_pr_cmd: "echo gate"\nintegrate_smoke_cmd: "echo smoke"\n')
        self.assertEqual(resolve_cmd(load_policy(Path(repo))), "echo gate")

    def test_falls_back_to_integrate_smoke_cmd(self) -> None:
        repo = self._repo('integrate_smoke_cmd: "echo smoke"\n')
        self.assertEqual(resolve_cmd(load_policy(Path(repo))), "echo smoke")

    def test_unconfigured_resolves_none(self) -> None:
        repo = self._repo("base_branch: dev\n")
        self.assertIsNone(resolve_cmd(load_policy(Path(repo))))

    # --- main() exit codes -------------------------------------------------
    def test_missing_policy_file_is_unconfigured(self) -> None:
        repo = self._repo(None)
        self.assertEqual(main(["--repo", repo]), UNCONFIGURED_EXIT)

    def test_unconfigured_policy_default_denies(self) -> None:
        repo = self._repo("base_branch: dev\n")
        self.assertEqual(main(["--repo", repo]), UNCONFIGURED_EXIT)

    def test_explicit_skip_passes(self) -> None:
        repo = self._repo("pre_pr_cmd: skip\n")
        self.assertEqual(main(["--repo", repo]), 0)

    def test_passing_command_returns_zero(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        self.assertEqual(main(["--repo", repo]), 0)

    def test_failing_command_propagates_exit_code(self) -> None:
        repo = self._repo('pre_pr_cmd: "exit 3"\n')
        self.assertEqual(main(["--repo", repo]), 3)

    def test_command_runs_in_repo_root(self) -> None:
        repo = self._repo('pre_pr_cmd: "test -f docs/specs/go-policy.yaml"\n')
        self.assertEqual(main(["--repo", repo]), 0)

    def test_print_cmd_does_not_execute(self) -> None:
        repo = self._repo('pre_pr_cmd: "exit 3"\n')
        self.assertEqual(main(["--repo", repo, "--print-cmd"]), 0)

    def test_nonexistent_repo_path_fails(self) -> None:
        self.assertEqual(main(["--repo", "/nonexistent/path/xyz"]), UNCONFIGURED_EXIT)

    # --- --risk / AUTOMERGE LABELS ------------------------------------------
    def test_risk_omitted_prints_no_labels_line(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo]), 0)
        self.assertNotIn("AUTOMERGE LABELS", out.getvalue())

    def test_eligible_risk_prints_risk_label_only(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate", return_value=(True, "checks ok")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "low"]), 0)
        self.assertIn("AUTOMERGE LABELS: go:risk-low  (eligible=True", out.getvalue())
        self.assertNotIn("go:no-automerge", out.getvalue())

    def test_ineligible_risk_prints_no_automerge_label(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "high"]), 0)
        self.assertIn("AUTOMERGE LABELS: go:risk-high go:no-automerge  (eligible=False", out.getvalue())

    def test_labels_only_returns_exact_resolved_labels_without_running_gate(self) -> None:
        repo = self._repo('pre_pr_cmd: "exit 3"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate", return_value=(True, "checks ok")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "low", "--labels-only"]), 0)
        self.assertEqual(out.getvalue().strip(), "go:risk-low")

    def test_labels_only_requires_risk(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        self.assertEqual(main(["--repo", repo, "--labels-only"]), UNCONFIGURED_EXIT)

    # --- required_checks_gate integration -----------------------------------
    def test_required_checks_gate_not_consulted_when_policy_already_ineligible(self) -> None:
        """Zero wasted `gh api` calls when policy alone already refuses --
        e.g. risk exceeds max_risk (as above, risk=high vs max_risk=medium)."""
        repo = self._repo('pre_pr_cmd: "true"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate") as gate, \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "high"]), 0)
        gate.assert_not_called()

    def test_required_checks_gate_overrides_policy_eligible(self) -> None:
        """Policy says eligible, but the base branch has no live required
        check -- the PR must still get go:no-automerge."""
        repo = self._repo('pre_pr_cmd: "true"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate",
                   return_value=(False, "zero required status checks")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "low"]), 0)
        self.assertIn("AUTOMERGE LABELS: go:risk-low go:no-automerge  (eligible=False", out.getvalue())
        self.assertIn("zero required status checks", out.getvalue())

    def test_failing_gate_never_prints_labels(self) -> None:
        repo = self._repo('pre_pr_cmd: "exit 3"\nautomerge:\n  enabled: true\n  max_risk: medium\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--repo", repo, "--risk", "low"]), 3)
        self.assertNotIn("AUTOMERGE LABELS", out.getvalue())

    def test_run_record_without_scope_review_is_blocked(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        run = Path(repo) / "run.yaml"
        run.write_text("scope_review:\n", encoding="utf-8")
        self.assertEqual(
            main(["--repo", repo, "--run", str(run)]), SCOPE_COMPLETENESS_EXIT
        )

    def test_complete_scope_review_allows_gate(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        run = Path(repo) / "run.yaml"
        run.write_text(
            'scope_review:\n  - "complete | smoke test | pytest -q"\n', encoding="utf-8"
        )
        self.assertEqual(main(["--repo", repo, "--run", str(run)]), 0)

    def test_blocked_scope_review_fails_gate(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        run = Path(repo) / "run.yaml"
        run.write_text(
            'scope_review:\n  - "blocked | smoke test | provider unavailable"\n', encoding="utf-8"
        )
        self.assertEqual(
            main(["--repo", repo, "--run", str(run)]), SCOPE_COMPLETENESS_EXIT
        )

    def test_out_of_scope_requires_explicit_boundary(self) -> None:
        run = Path(tempfile.mkdtemp()) / "run.yaml"
        run.write_text(
            'scope_review:\n  - "out-of-scope | smoke test | adjacent cleanup"\n',
            encoding="utf-8",
        )
        self.assertTrue(scope_review_failures(run))


class TestSpecSyncDriftGate(unittest.TestCase):
    """Every consuming repo inherits check_spec_sync.py via pre_pr_gate.py —
    see handoff brief 20260707-203826-generalize-spec-sync-guard-to-developer-kit."""

    def _repo(self, policy_yaml: str) -> str:
        d = tempfile.mkdtemp(prefix="prepr-specsync-")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    def test_no_docs_specs_dir_is_not_applicable(self) -> None:
        d = tempfile.mkdtemp(prefix="prepr-nospecs-")
        self.assertEqual(spec_sync_drift(Path(d)), [])

    def test_docs_specs_with_no_numbered_dirs_passes(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        self.assertEqual(spec_sync_drift(Path(repo)), [])

    def test_clean_spec_passes(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        specs_root = Path(repo) / "docs" / "specs"
        tasks_dir = specs_root / "000-fixture" / "tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "TASK-001.md").write_text(
            "---\nid: TASK-001\nstatus: completed\n---\n\ndone\n", encoding="utf-8"
        )
        (specs_root / "000-fixture" / "spec.md").write_text(
            "# Functional Specification: Fixture\n\n**Status**: Implemented\n",
            encoding="utf-8",
        )
        self.assertEqual(spec_sync_drift(Path(repo)), [])

    def test_drifted_spec_fails_gate_before_pre_pr_cmd_runs(self) -> None:
        # pre_pr_cmd would itself pass ("true") — the drift check must still
        # block, and must run before pre_pr_cmd (never reaching "true"/exit 0).
        repo = self._repo('pre_pr_cmd: "true"\n')
        _write_drifted_spec(Path(repo) / "docs" / "specs")
        drift = spec_sync_drift(Path(repo))
        self.assertEqual(len(drift), 1)
        self.assertIn("Draft", drift[0])
        self.assertEqual(main(["--repo", repo]), SPEC_SYNC_DRIFT_EXIT)

    def test_print_cmd_bypasses_drift_check(self) -> None:
        repo = self._repo('pre_pr_cmd: "true"\n')
        _write_drifted_spec(Path(repo) / "docs" / "specs")
        self.assertEqual(main(["--repo", repo, "--print-cmd"]), 0)

    def test_drift_blocks_even_when_docs_only_paths_would_bypass(self) -> None:
        # docs_only_paths exists to skip pre_pr_cmd on doc-only diffs — it
        # must never mask spec-sync drift, which is unrelated to that fast
        # path (see pre_pr_gate.py module docstring).
        repo = self._repo(
            'pre_pr_cmd: "true"\n'
            "docs_only_paths:\n"
            "  - docs/**\n"
        )
        _write_drifted_spec(Path(repo) / "docs" / "specs")
        self.assertEqual(main(["--repo", repo]), SPEC_SYNC_DRIFT_EXIT)


class TestDocsOnlySkip(unittest.TestCase):
    """Exercises the docs_only_paths fast path against a real throwaway git repo."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self, policy_yaml: str, base_branch: str = "main") -> str:
        d = tempfile.mkdtemp(prefix="prepr-docsonly-")
        self._git(d, "init", "-q", "-b", base_branch)
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        return d

    def _write(self, repo: str, relpath: str, content: str = "x\n") -> None:
        path = Path(repo) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo: str, message: str) -> None:
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", message)

    _POLICY = ('pre_pr_cmd: "exit 9"\n'
               'docs_only_paths:\n'
               '  - docs/**\n'
               "  - '**/*.md'\n"
               "  - .gitignore\n")

    def test_all_docs_diff_skips_pre_pr_cmd(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._commit(repo, "docs change")
        self.assertTrue(is_docs_only(Path(repo), load_policy(Path(repo))))
        # main() would run "exit 9" if the fast path didn't short-circuit it.
        self.assertEqual(main(["--repo", repo]), 0)

    def test_mixed_diff_falls_through_to_full_gate(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._write(repo, "src/app.py")
        self._commit(repo, "docs + source change")
        self.assertFalse(is_docs_only(Path(repo), load_policy(Path(repo))))
        self.assertEqual(main(["--repo", repo]), 9)

    def test_unconfigured_docs_only_paths_unaffected(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "exit 9"\n')
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._commit(repo, "docs change")
        self.assertFalse(is_docs_only(Path(repo), load_policy(Path(repo))))
        self.assertEqual(main(["--repo", repo]), 9)

    def test_unresolvable_base_ref_falls_through(self) -> None:
        repo = self._init_repo(self._POLICY, base_branch="onlybranch")
        self._write(repo, "docs/new-note.md")
        self._commit(repo, "docs change")
        self.assertFalse(is_docs_only(Path(repo), load_policy(Path(repo))))

    def test_empty_diff_is_not_docs_only(self) -> None:
        repo = self._init_repo(self._POLICY)
        self.assertFalse(is_docs_only(Path(repo), load_policy(Path(repo))))


class TestClarificationIntegrityGate(unittest.TestCase):
    """Exercises pre_pr_gate.py's wiring of check_clarification_integrity.py
    against a real throwaway git repo (mirrors TestDocsOnlySkip's pattern)."""

    INFERRED_AND_DECLARED_CLEAN_SPEC = (
        "# Functional Specification: Fixture\n\n"
        "## Clarifications\n\n"
        "- Q: Must workspace slugs be globally unique? -> A: Yes -- the locked "
        "schema has no representation for org-scoped slug namespaces [agent-resolved]\n\n"
        "## Open Questions\n\nOpen Questions: None outstanding\n"
    )

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="prepr-clarify-")
        self._git(d, "init", "-q", "-b", "main")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text('pre_pr_cmd: "true"\n', encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        return d

    def _write(self, repo: str, relpath: str, content: str) -> None:
        path = Path(repo) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo: str, message: str) -> None:
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", message)

    def test_newly_added_inferred_spec_fails_the_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/spec.md",
                    self.INFERRED_AND_DECLARED_CLEAN_SPEC)
        self._commit(repo, "add spec")
        self.assertEqual(main(["--repo", repo]), CLARIFICATION_INTEGRITY_DRIFT_EXIT)

    def test_pre_existing_inferred_spec_not_in_diff_does_not_fail(self) -> None:
        repo = self._init_repo()
        self._write(repo, "docs/specs/098-fixture/spec.md",
                    self.INFERRED_AND_DECLARED_CLEAN_SPEC)
        self._commit(repo, "pre-existing spec on main")
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/unrelated.md", "unrelated\n")
        self._commit(repo, "unrelated change")
        self.assertEqual(main(["--repo", repo]), 0)

    def test_clean_changed_spec_passes(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/099-fixture/spec.md",
            "# Functional Specification: Fixture\n\n"
            "## Clarifications\n\n- Q: x? -> A: y [agent-resolved]\n\n"
            "## Open Questions\n\nNone outstanding.\n",
        )
        self._commit(repo, "add spec")
        self.assertEqual(main(["--repo", repo]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

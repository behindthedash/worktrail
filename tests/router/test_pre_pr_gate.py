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

from worktrail.router.check_dod_verification import audit_all_specs
from worktrail.router.policy import load_policy
from worktrail.router.pre_pr_gate import (
    CLARIFICATION_INTEGRITY_DRIFT_EXIT, DOD_VERIFICATION_DRIFT_EXIT,
    REQ_AC_COVERAGE_DRIFT_EXIT, SCOPE_COMPLETENESS_EXIT, SPEC_SYNC_DRIFT_EXIT,
    UNCONFIGURED_EXIT, is_docs_only, is_promotion_pr, main, resolve_cmd,
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


class TestPromotionPrSkip(unittest.TestCase):
    """behindthedash/worktrail brief 20260819-212434: pre_pr_gate.py fails
    closed on a branch-promotion PR (e.g. a datalena stg->prd 'PRD Release'
    PR), forcing the full heavy pre_pr_cmd even though every commit on the
    branch already went through CI on the way onto `origin`. (The bug's
    original "zero local diff" symptom was itself a coincidence of diffing
    against policy.base_branch -- e.g. dev -- for a promotion branch that
    happens to be dev's own ancestor; a real promotion PR against its actual
    target branch routinely carries many real commits, so the fix keys off
    HEAD matching its own pushed `origin/<branch>`, not an empty diff.)"""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self, policy_yaml: str, base_branch: str = "prd") -> str:
        bare = tempfile.mkdtemp(prefix="prepr-promotion-origin-")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        d = tempfile.mkdtemp(prefix="prepr-promotion-")
        self._git(d, "init", "-q", "-b", base_branch)
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        self._git(d, "remote", "add", "origin", bare)
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        self._git(d, "push", "-q", "origin", base_branch)
        return d

    def _write(self, repo: str, relpath: str, content: str = "x\n") -> None:
        path = Path(repo) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _commit(self, repo: str, message: str) -> None:
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", message)

    _POLICY = ('pre_pr_cmd: "exit 9"\n'
               'promotion_pairs:\n'
               '  prd: stg\n')

    def test_clean_pushed_promotion_branch_skips_pre_pr_cmd(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "stg")
        self._write(repo, "src/app.py")
        self._commit(repo, "real stg content, already CI-vetted on its way onto origin")
        self._git(repo, "push", "-q", "-u", "origin", "stg")
        policy = load_policy(Path(repo))
        self.assertTrue(is_promotion_pr(Path(repo), policy, "prd"))
        # main() would run "exit 9" if the promotion fast path didn't short-circuit it.
        self.assertEqual(main(["--repo", repo, "--target-branch", "prd"]), 0)

    def test_non_canonical_head_branch_falls_through(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._git(repo, "push", "-q", "-u", "origin", "feature")
        policy = load_policy(Path(repo))
        self.assertFalse(is_promotion_pr(Path(repo), policy, "prd"))
        self.assertEqual(main(["--repo", repo, "--target-branch", "prd"]), 9)

    def test_uncommitted_local_change_falls_through(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "stg")
        self._git(repo, "push", "-q", "-u", "origin", "stg")
        self._write(repo, "src/app.py")  # uncommitted, dirty working tree
        policy = load_policy(Path(repo))
        self.assertFalse(is_promotion_pr(Path(repo), policy, "prd"))
        self.assertEqual(main(["--repo", repo, "--target-branch", "prd"]), 9)

    def test_unpushed_local_commit_falls_through(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "stg")
        self._git(repo, "push", "-q", "-u", "origin", "stg")
        self._write(repo, "src/app.py")
        self._commit(repo, "local-only, not yet CI-vetted")
        policy = load_policy(Path(repo))
        self.assertFalse(is_promotion_pr(Path(repo), policy, "prd"))
        self.assertEqual(main(["--repo", repo, "--target-branch", "prd"]), 9)

    def test_unconfigured_promotion_pairs_unaffected(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "exit 9"\n')
        self._git(repo, "checkout", "-q", "-b", "stg")
        self._git(repo, "push", "-q", "-u", "origin", "stg")
        policy = load_policy(Path(repo))
        self.assertFalse(is_promotion_pr(Path(repo), policy, "prd"))
        self.assertEqual(main(["--repo", repo, "--target-branch", "prd"]), 9)

    def test_missing_origin_remote_falls_through(self) -> None:
        d = tempfile.mkdtemp(prefix="prepr-promotion-noremote-")
        self._git(d, "init", "-q", "-b", "prd")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(self._POLICY, encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        self._git(d, "checkout", "-q", "-b", "stg")
        policy = load_policy(Path(d))
        self.assertFalse(is_promotion_pr(Path(d), policy, "prd"))
        self.assertEqual(main(["--repo", d, "--target-branch", "prd"]), 9)

    def test_target_branch_with_no_configured_pairing_is_not_promotion_pr(self) -> None:
        repo = self._init_repo(self._POLICY)
        self._git(repo, "checkout", "-q", "-b", "stg")
        self._git(repo, "push", "-q", "-u", "origin", "stg")
        policy = load_policy(Path(repo))
        # promotion_pairs only maps "prd"; a different target branch has no
        # configured canonical pairing, even with a clean pushed HEAD.
        self.assertFalse(is_promotion_pr(Path(repo), policy, "stg-preview"))


class TestDocsOnlyRiskCap(unittest.TestCase):
    """PR #86 regression: a docs-only diff under openspec/changes/ whose
    request prose incidentally matched the classifier's authz keyword
    heuristic (citing a spec folder named `038-authentication` as an example)
    came back go:risk-high/go:no-automerge and stalled an actually-safe
    spec-only PR all day. The diff is real ground truth that no code was
    touched, so resolve_pr_labels() must cap the classifier's risk verdict to
    low whenever is_docs_only() confirms the diff -- regardless of which
    keyword tier the classifier matched -- while leaving a genuinely
    code-touching diff's risk uncapped (an authz signal must still carry
    through to the label for a diff that actually touches auth-related code)."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    _POLICY = (
        'pre_pr_cmd: "true"\n'
        "automerge:\n"
        "  enabled: true\n"
        "  max_risk: low\n"
        "docs_only_paths:\n"
        '  - "openspec/**"\n'
        '  - "docs/**"\n'
    )

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="prepr-riskcap-")
        self._git(d, "init", "-q", "-b", "main")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(self._POLICY, encoding="utf-8")
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

    def test_docs_only_diff_caps_authz_flavored_high_risk_to_low(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "openspec/changes/dashboard-selfcheck/tasks.md",
            "- [ ] add authentication-ambiguity detector\n",
        )
        self._commit(repo, "spec(dashboard-selfcheck): add tasks")
        self.assertTrue(is_docs_only(Path(repo), load_policy(Path(repo))))

        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate",
                   return_value=(True, "checks ok")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["--repo", repo, "--risk", "high", "--labels-only"]), 0)
        self.assertEqual(out.getvalue().strip(), "go:risk-low")

    def test_docs_only_diff_caps_critical_risk_too(self) -> None:
        # The cap is unconditional on docs-only, not scoped to the authz
        # signal specifically -- any keyword tier is capped the same way.
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/notes.md", "mentions secrets rotation policy\n")
        self._commit(repo, "docs change")

        out = io.StringIO()
        with patch("worktrail.router.pre_pr_gate.required_checks_gate",
                   return_value=(True, "checks ok")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["--repo", repo, "--risk", "critical", "--labels-only"]), 0)
        self.assertEqual(out.getvalue().strip(), "go:risk-low")

    def test_non_docs_only_diff_with_high_risk_is_not_capped(self) -> None:
        # True-positive control: a diff that actually touches auth-related
        # code is not docs-only, so the authz-derived high risk must still
        # reach the PR label unchanged.
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "src/app/auth/permissions.py", "def check(): ...\n")
        self._commit(repo, "feat: tighten permission checks")
        self.assertFalse(is_docs_only(Path(repo), load_policy(Path(repo))))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["--repo", repo, "--risk", "high", "--labels-only"]), 0)
        self.assertEqual(out.getvalue().strip(), "go:risk-high go:no-automerge")

    def test_mixed_diff_with_one_non_docs_path_is_not_capped(self) -> None:
        # Mirrors is_docs_only's own mixed-diff behavior: one non-doc path in
        # the diff means the whole PR is not docs-only, so no cap applies.
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/notes.md")
        self._write(repo, "src/app.py")
        self._commit(repo, "docs + source change")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["--repo", repo, "--risk", "high", "--labels-only"]), 0)
        self.assertEqual(out.getvalue().strip(), "go:risk-high go:no-automerge")


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


class TestDodVerificationGate(unittest.TestCase):
    """Exercises pre_pr_gate.py's wiring of check_dod_verification.py against
    a real throwaway git repo (mirrors TestClarificationIntegrityGate's
    pattern)."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="prepr-dod-")
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

    def _task(self, dod_checks: str) -> str:
        return (
            "---\nid: TASK-001\ntitle: Fixture\nspec: 098-fixture\n"
            "status: completed\n"
            f"{dod_checks}"
            "---\n\nbody\n"
        )

    def test_newly_added_completed_task_with_failing_dod_check_fails_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            self._task("dod-checks:\n  - type: file_exists\n    path: nonexistent.txt\n"),
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo]), DOD_VERIFICATION_DRIFT_EXIT)

    def test_completed_task_with_passing_dod_checks_passes_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "README.md", "present\n")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            self._task("dod-checks:\n  - type: file_exists\n    path: README.md\n"),
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo]), 0)

    def test_completed_task_with_no_dod_checks_passes_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md", self._task("")
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo]), 0)


class TestDodVerificationDerivationGate(unittest.TestCase):
    """Exercises pre_pr_gate.py's wiring of check_dod_verification.py's
    derive_dod_checks fallback (task with no explicit `dod-checks:`) against
    a real throwaway git repo (mirrors TestDodVerificationGate's pattern)."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="prepr-dod-derive-")
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

    def test_completed_task_with_unchecked_ac_box_and_no_dod_checks_fails_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 098-fixture\n"
            "status: completed\n---\n\n"
            "## Acceptance Criteria\n\n- [ ] does the thing\n",
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo]), DOD_VERIFICATION_DRIFT_EXIT)

    def test_completed_task_with_checked_ac_boxes_and_tracked_files_passes_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "src/thing.py", "def thing(): return 1\n")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 098-fixture\n"
            "status: completed\nfiles:\n  - src/thing.py\n---\n\n"
            "## Acceptance Criteria\n\n- [x] does the thing\n",
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo]), 0)

    def test_all_flag_reports_pre_existing_backlog_drift_diff_scoped_gate_unaffected(
        self,
    ) -> None:
        # A completed task that predates this feature (committed on the base
        # branch itself, never touched in the current diff) has an unchecked
        # AC box -- audit_all_specs (--all) must surface it, but the ordinary
        # diff-scoped gate must not fail the current PR on account of it.
        repo = self._init_repo()
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Pre-existing\nspec: 098-fixture\n"
            "status: completed\n---\n\n"
            "## Acceptance Criteria\n\n- [ ] never verified\n",
        )
        self._commit(repo, "pre-existing completed task with drift")

        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "src/unrelated.py", "def unrelated(): return 1\n")
        self._commit(repo, "unrelated feature work")

        failures = audit_all_specs(Path(repo))
        self.assertEqual(len(failures), 1)
        self.assertIn("098-fixture/tasks/TASK-001.md", failures[0])

        self.assertEqual(main(["--repo", repo]), 0)


class TestReqAcCoverageGate(unittest.TestCase):
    """Exercises pre_pr_gate.py's wiring of check_req_coverage.py against a
    real throwaway git repo (mirrors TestClarificationIntegrityGate's and
    TestDodVerificationGate's pattern)."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self) -> str:
        d = tempfile.mkdtemp(prefix="prepr-reqcov-")
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

    _SPEC_WITH_REQ_001 = (
        "# Functional Specification: Fixture\n\n"
        "**Status**: Implemented\n\n"
        "## Requirements\n\n"
        "| REQ-001 | The system SHALL do the thing. |\n"
    )

    def test_newly_declared_uncovered_identifier_fails_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/spec.md", self._SPEC_WITH_REQ_001)
        self._commit(repo, "add spec")
        self.assertEqual(main(["--repo", repo]), REQ_AC_COVERAGE_DRIFT_EXIT)

    def test_newly_declared_identifier_with_task_coverage_passes_gate(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/spec.md", self._SPEC_WITH_REQ_001)
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 098-fixture\n"
            "status: completed\nreqs:\n  - REQ-001\n---\n\nbody\n",
        )
        self._commit(repo, "add spec and covering task")
        self.assertEqual(main(["--repo", repo]), 0)


class TestOrphanedTestsWarning(unittest.TestCase):
    """The orphaned-tests advisory is printed but must never gate the PR."""

    def _git_repo(self, policy_yaml: str, test_files: list[str]) -> str:
        d = tempfile.mkdtemp(prefix="prepr-drift-")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        for rel in test_files:
            p = Path(d) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# test\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        return d

    def _run(self, repo: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["--repo", repo])
        return code, err.getvalue()

    def test_warns_but_still_passes_when_tests_are_orphaned(self) -> None:
        repo = self._git_repo('pre_pr_cmd: "true"\n', ["tests/test_orphan.py"])
        code, err = self._run(repo)
        self.assertEqual(code, 0)
        self.assertIn("WARNING", err)
        self.assertIn("tests/test_orphan.py", err)

    def test_no_warning_when_the_gate_runs_the_tests(self) -> None:
        repo = self._git_repo('pre_pr_cmd: "pytest --version >/dev/null || true"\n',
                              ["tests/test_covered.py"])
        code, err = self._run(repo)
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", err)

    def test_no_warning_when_repo_has_no_test_files(self) -> None:
        repo = self._git_repo('pre_pr_cmd: "true"\n', [])
        code, err = self._run(repo)
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", err)

    def test_warning_does_not_rescue_a_failing_gate(self) -> None:
        repo = self._git_repo('pre_pr_cmd: "false"\n', ["tests/test_orphan.py"])
        code, err = self._run(repo)
        self.assertNotEqual(code, 0)
        self.assertIn("WARNING", err)

    def test_detector_failure_cannot_break_the_gate(self) -> None:
        repo = self._git_repo('pre_pr_cmd: "true"\n', ["tests/test_orphan.py"])
        with patch("worktrail.router.pre_pr_gate.orphaned_test_paths",
                   side_effect=RuntimeError("boom")):
            code, err = self._run(repo)
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", err)


class TestChecksOnly(unittest.TestCase):
    """`--checks-only` runs just the four deterministic drift checks and
    returns their combined exit code directly, without ever reaching
    `pre_pr_cmd`/`integrate_smoke_cmd` or the unconfigured default-deny
    (design D2)."""

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self, policy_yaml: str) -> str:
        d = tempfile.mkdtemp(prefix="prepr-checksonly-")
        self._git(d, "init", "-q", "-b", "main")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
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

    def test_drift_free_tree_passes_without_running_pre_pr_cmd(self) -> None:
        sentinel = Path(tempfile.mkdtemp()) / "sentinel"
        repo = self._init_repo(f'pre_pr_cmd: "touch {sentinel}"\n')
        self.assertEqual(main(["--repo", repo, "--checks-only"]), 0)
        self.assertFalse(sentinel.exists())

    def test_spec_sync_drift_returns_its_own_exit_code(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        _write_drifted_spec(Path(repo) / "docs" / "specs")
        self._commit(repo, "add drifted spec")
        self.assertEqual(main(["--repo", repo, "--checks-only"]), SPEC_SYNC_DRIFT_EXIT)

    def test_clarification_integrity_drift_returns_its_own_exit_code(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/spec.md",
                    TestClarificationIntegrityGate.INFERRED_AND_DECLARED_CLEAN_SPEC)
        self._commit(repo, "add spec")
        self.assertEqual(main(["--repo", repo, "--checks-only"]),
                          CLARIFICATION_INTEGRITY_DRIFT_EXIT)

    def test_dod_verification_drift_returns_its_own_exit_code(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(
            repo, "docs/specs/098-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 098-fixture\n"
            "status: completed\ndod-checks:\n  - type: file_exists\n"
            "    path: nonexistent.txt\n---\n\nbody\n",
        )
        self._commit(repo, "add task")
        self.assertEqual(main(["--repo", repo, "--checks-only"]),
                          DOD_VERIFICATION_DRIFT_EXIT)

    def test_req_ac_coverage_drift_returns_its_own_exit_code(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/specs/098-fixture/spec.md",
                    TestReqAcCoverageGate._SPEC_WITH_REQ_001)
        self._commit(repo, "add spec")
        self.assertEqual(main(["--repo", repo, "--checks-only"]),
                          REQ_AC_COVERAGE_DRIFT_EXIT)

    def test_unconfigured_pre_pr_cmd_does_not_hit_unconfigured_exit(self) -> None:
        # No pre_pr_cmd/integrate_smoke_cmd at all -- the ordinary gate would
        # default-deny at UNCONFIGURED_EXIT, but --checks-only never resolves
        # a command in the first place, so a drift-free tree still passes.
        repo = self._init_repo("base_branch: main\n")
        self.assertNotEqual(main(["--repo", repo, "--checks-only"]), UNCONFIGURED_EXIT)
        self.assertEqual(main(["--repo", repo, "--checks-only"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

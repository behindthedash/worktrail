#!/usr/bin/env python3
"""Unit tests for preflight.py (stdlib unittest, mirrors test_pre_pr_gate.py style)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.preflight import (
    check, duplicate_work_warning, main, marker_path, read_marker,
    tree_state, write_marker,
)
from worktrail.router.preflight import _pr_touched_files, _resolve_base_ref, _touched_files


class _GitRepoCase(unittest.TestCase):
    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self, policy_yaml: str = 'pre_pr_cmd: "true"\n') -> str:
        d = tempfile.mkdtemp(prefix="preflight-")
        self._git(d, "init", "-q", "-b", "main")
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

    def _add_worktree(self, repo: str, branch: str, start_point: str = "HEAD") -> str:
        import shutil
        wt_dir = tempfile.mkdtemp(prefix="preflight-wt-")
        shutil.rmtree(wt_dir)
        self._git(repo, "worktree", "add", "-b", branch, wt_dir, start_point)
        self.addCleanup(lambda: self._git(repo, "worktree", "remove", "--force", wt_dir))
        return wt_dir


class TestTreeState(_GitRepoCase):
    def test_clean_tree_is_stable(self) -> None:
        repo = self._init_repo()
        self.assertEqual(tree_state(Path(repo)), tree_state(Path(repo)))

    def test_dirty_tree_changes_state(self) -> None:
        repo = self._init_repo()
        before = tree_state(Path(repo))
        self._write(repo, "dirty.txt")
        after = tree_state(Path(repo))
        self.assertNotEqual(before, after)

    def test_new_commit_changes_state(self) -> None:
        repo = self._init_repo()
        before = tree_state(Path(repo))
        self._write(repo, "committed.txt")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "add file")
        after = tree_state(Path(repo))
        self.assertNotEqual(before, after)

    def test_non_git_dir_returns_none(self) -> None:
        d = tempfile.mkdtemp(prefix="notgit-")
        self.assertIsNone(tree_state(Path(d)))


class TestMarkerRoundtrip(_GitRepoCase):
    def test_write_then_read_marker(self) -> None:
        repo = self._init_repo()
        state = tree_state(Path(repo))
        path = write_marker(Path(repo), state, "pytest -q")
        self.assertTrue(path.exists())
        marker = read_marker(Path(repo))
        self.assertEqual(marker["state"], state)
        self.assertEqual(marker["cmd"], "pytest -q")

    def test_marker_lives_in_private_git_dir(self) -> None:
        repo = self._init_repo()
        path = marker_path(Path(repo))
        self.assertEqual(path.parent, Path(repo) / ".git")

    def test_no_marker_returns_none(self) -> None:
        repo = self._init_repo()
        self.assertIsNone(read_marker(Path(repo)))

    def test_malformed_marker_returns_none(self) -> None:
        repo = self._init_repo()
        marker_path(Path(repo)).write_text("not json", encoding="utf-8")
        self.assertIsNone(read_marker(Path(repo)))


class TestCheckVerdict(_GitRepoCase):
    def test_unconfigured_repo_allows(self) -> None:
        repo = self._init_repo("base_branch: main\n")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_explicit_skip_allows(self) -> None:
        repo = self._init_repo("pre_pr_cmd: skip\n")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_docs_only_diff_allows(self) -> None:
        repo = self._init_repo(
            'pre_pr_cmd: "exit 9"\ndocs_only_paths:\n  - docs/**\n'
        )
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "docs change")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_no_marker_and_configured_cmd_denies(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("worktrail-preflight run", verdict["reason"])

    def test_matching_marker_allows(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_stale_marker_denies(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        write_marker(Path(repo), "stale-state-from-before-a-commit", "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")

    def test_nonexistent_repo_denies(self) -> None:
        verdict = check(Path("/nonexistent/path/xyz"))
        self.assertEqual(verdict["decision"], "deny")


class TestCheckCli(_GitRepoCase):
    def test_check_cli_allow_exits_zero_and_prints_json(self) -> None:
        repo = self._init_repo("pre_pr_cmd: skip\n")
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--repo", repo])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "allow")

    def test_check_cli_deny_exits_nonzero(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--repo", repo])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "deny")


class TestRunCli(_GitRepoCase):
    def test_run_records_marker_on_pass(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self.assertIsNone(read_marker(Path(repo)))
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        marker = read_marker(Path(repo))
        self.assertIsNotNone(marker)
        self.assertEqual(marker["state"], tree_state(Path(repo)))
        # A subsequent check() now allows, proving the marker round-trips
        # through the exact same tree_state() the check path reads.
        self.assertEqual(check(Path(repo))["decision"], "allow")

    def test_run_does_not_record_marker_on_failure(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "exit 3"\n')
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 3)
        self.assertIsNone(read_marker(Path(repo)))


class TestOpenPrBranches(_GitRepoCase):
    def test_gh_missing_returns_empty(self) -> None:
        from worktrail.router.preflight import _open_pr_branches
        repo = self._init_repo()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("gh")):
            self.assertEqual(_open_pr_branches(Path(repo)), [])

    def test_gh_nonzero_exit_returns_empty(self) -> None:
        from worktrail.router.preflight import _open_pr_branches
        repo = self._init_repo()
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        with mock.patch("subprocess.run", return_value=result):
            self.assertEqual(_open_pr_branches(Path(repo)), [])

    def test_gh_success_parses_branch_names(self) -> None:
        from worktrail.router.preflight import _open_pr_branches
        repo = self._init_repo()
        payload = json.dumps([{"headRefName": "a-b-c"}, {"headRefName": "d-e-f"}])
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")
        with mock.patch("subprocess.run", return_value=result):
            self.assertEqual(_open_pr_branches(Path(repo)), ["a-b-c", "d-e-f"])


class TestPrTouchedFiles(_GitRepoCase):
    def test_gh_missing_returns_none(self) -> None:
        repo = self._init_repo()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("gh")):
            self.assertIsNone(_pr_touched_files(Path(repo), "some-branch"))

    def test_gh_nonzero_exit_returns_none(self) -> None:
        repo = self._init_repo()
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="err")
        with mock.patch("subprocess.run", return_value=result):
            self.assertIsNone(_pr_touched_files(Path(repo), "some-branch"))

    def test_gh_success_parses_file_names(self) -> None:
        repo = self._init_repo()
        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="a.py\nb.py\n", stderr="",
        )
        with mock.patch("subprocess.run", return_value=result):
            self.assertEqual(
                _pr_touched_files(Path(repo), "some-branch"), frozenset({"a.py", "b.py"}),
            )


class TestResolveBaseRef(_GitRepoCase):
    def test_finds_local_main_when_unconfigured(self) -> None:
        repo = self._init_repo()
        self.assertEqual(_resolve_base_ref(Path(repo)), "main")

    def test_prefers_configured_base_branch(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\nbase_branch: main\n')
        self.assertEqual(_resolve_base_ref(Path(repo)), "main")

    def test_returns_none_when_unresolvable(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\nbase_branch: nonexistent-branch\n')
        self.assertIsNone(_resolve_base_ref(Path(repo)))


class TestTouchedFiles(_GitRepoCase):
    def test_reports_diff_from_base(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "src/new_file.py")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "add file")
        self.assertEqual(
            _touched_files(Path(repo), "main", "feature"), frozenset({"src/new_file.py"}),
        )

    def test_empty_when_no_divergence(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "feature")
        self.assertEqual(_touched_files(Path(repo), "main", "feature"), frozenset())


class TestDuplicateWorkWarning(_GitRepoCase):
    def test_no_siblings_and_no_open_prs_returns_none(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "add-metrics-dashboard")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            self.assertIsNone(duplicate_work_warning(Path(repo)))

    def test_overlapping_sibling_worktree_warns(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        self._add_worktree(repo, "investigate-wire-plan-audit-into-verify")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            warning = duplicate_work_warning(Path(repo))
        assert warning is not None
        self.assertIn("investigate-wire-plan-audit-into-verify", warning)
        self.assertIn("worktree", warning)

    def test_dissimilar_sibling_worktree_does_not_warn(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "fix-typo-in-readme")
        self._add_worktree(repo, "add-metrics-dashboard")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            self.assertIsNone(duplicate_work_warning(Path(repo)))

    def test_own_worktree_never_self_matches(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            self.assertIsNone(duplicate_work_warning(Path(repo)))

    def test_matching_open_pr_warns(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["investigate/wire-plan-audit-into-verify"],
        ):
            warning = duplicate_work_warning(Path(repo))
        assert warning is not None
        self.assertIn("open PR", warning)

    def test_single_word_branch_never_warns(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "fix")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=["fixes"]):
            self.assertIsNone(duplicate_work_warning(Path(repo)))

    def test_low_overlap_does_not_warn(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "add-metrics-dashboard-panel")
        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["add-billing-invoice-export"],
        ):
            self.assertIsNone(duplicate_work_warning(Path(repo)))


class TestDuplicateWorkWarningFileOverlap(_GitRepoCase):
    def test_overlapping_touched_files_in_sibling_worktree_warns(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "improve-onboarding-flow")
        self._write(repo, "src/shared_module.py", "own change\n")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "touch shared module")

        wt_dir = self._add_worktree(repo, "polish-signup-experience", start_point="main")
        self._write(wt_dir, "src/shared_module.py", "other change\n")
        self._git(wt_dir, "add", ".")
        self._git(wt_dir, "commit", "-q", "-m", "touch shared module too")

        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            warning = duplicate_work_warning(Path(repo))
        assert warning is not None
        self.assertIn("shared_module.py", warning)
        self.assertIn("polish-signup-experience", warning)
        self.assertIn("worktree", warning)

    def test_overlapping_touched_files_in_open_pr_warns(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "improve-onboarding-flow")
        self._write(repo, "src/shared_module.py", "own change\n")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "touch shared module")

        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["polish-signup-experience"],
        ), mock.patch(
            "worktrail.router.preflight._pr_touched_files",
            return_value=frozenset({"src/shared_module.py", "docs/notes.md"}),
        ):
            warning = duplicate_work_warning(Path(repo))
        assert warning is not None
        self.assertIn("shared_module.py", warning)
        self.assertIn("open PR", warning)

    def test_dissimilar_touched_files_and_names_does_not_warn(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "improve-onboarding-flow")
        self._write(repo, "src/onboarding.py", "own change\n")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "touch onboarding")

        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["polish-signup-experience"],
        ), mock.patch(
            "worktrail.router.preflight._pr_touched_files",
            return_value=frozenset({"src/billing.py"}),
        ):
            self.assertIsNone(duplicate_work_warning(Path(repo)))

    def test_pr_diff_fetch_failure_is_fail_open(self) -> None:
        repo = self._init_repo()
        self._git(repo, "checkout", "-q", "-b", "improve-onboarding-flow")
        self._write(repo, "src/onboarding.py", "own change\n")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "touch onboarding")

        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["polish-signup-experience"],
        ), mock.patch(
            "worktrail.router.preflight._pr_touched_files",
            return_value=None,
        ):
            self.assertIsNone(duplicate_work_warning(Path(repo)))


class TestCheckWarningIntegration(_GitRepoCase):
    def test_warning_key_present_on_allow_when_duplicate_detected(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["investigate/wire-plan-audit-into-verify"],
        ):
            verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")
        self.assertIn("warning", verdict)

    def test_warning_key_present_on_deny_when_duplicate_detected(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["investigate/wire-plan-audit-into-verify"],
        ):
            verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("warning", verdict)

    def test_warning_key_absent_when_no_duplicate(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "add-metrics-dashboard")
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        with mock.patch("worktrail.router.preflight._open_pr_branches", return_value=[]):
            verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")
        self.assertNotIn("warning", verdict)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit tests for preflight.py (stdlib unittest, mirrors test_pre_pr_gate.py style)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from worktrail.addons.base import AddOnResult
from worktrail.router.policy import load_policy
from worktrail.router.preflight import (
    ADDON_REQUIRED_FAILURE_EXIT,
    check, dirty_tree_reason, duplicate_work_warning, is_running,
    is_unparseable_command, labels_in_command, main, marker_path, read_marker,
    read_running_lock, remove_running_lock, running_lock_path, tree_state,
    write_marker, write_running_lock,
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


class TestDirtyTreeReason(_GitRepoCase):
    def test_clean_tree_returns_none(self) -> None:
        repo = self._init_repo()
        self.assertIsNone(dirty_tree_reason(Path(repo)))

    def test_unstaged_change_to_tracked_file_returns_reason(self) -> None:
        repo = self._init_repo()
        self._write(repo, "docs/specs/go-policy.yaml", 'pre_pr_cmd: "false"\n')
        reason = dirty_tree_reason(Path(repo))
        self.assertIsNotNone(reason)
        self.assertIn("go-policy.yaml", reason)
        self.assertIn("git commit", reason)

    def test_staged_change_to_tracked_file_returns_reason(self) -> None:
        repo = self._init_repo()
        self._write(repo, "docs/specs/go-policy.yaml", 'pre_pr_cmd: "false"\n')
        self._git(repo, "add", ".")
        self.assertIsNotNone(dirty_tree_reason(Path(repo)))

    def test_untracked_file_is_ignored(self) -> None:
        """A brand-new file with no history yet -- e.g. a scratch note -- was
        never going to be part of a `git push` regardless, so it carries none
        of the risk this check exists to catch."""
        repo = self._init_repo()
        self._write(repo, "scratch-notes.txt")
        self.assertIsNone(dirty_tree_reason(Path(repo)))

    def test_committing_the_change_clears_it(self) -> None:
        repo = self._init_repo()
        self._write(repo, "docs/specs/go-policy.yaml", 'pre_pr_cmd: "false"\n')
        self.assertIsNotNone(dirty_tree_reason(Path(repo)))
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "update policy")
        self.assertIsNone(dirty_tree_reason(Path(repo)))

    def test_non_git_dir_returns_none(self) -> None:
        d = tempfile.mkdtemp(prefix="notgit-")
        self.assertIsNone(dirty_tree_reason(Path(d)))

    def test_many_files_truncates_sample_with_a_count(self) -> None:
        repo = self._init_repo()
        for i in range(7):
            self._write(repo, f"docs/specs/note-{i}.md")
        self._git(repo, "add", ".")
        reason = dirty_tree_reason(Path(repo))
        self.assertIsNotNone(reason)
        self.assertIn("+2 more", reason)


class TestCheckDirtyTree(_GitRepoCase):
    """`check()`'s dirty-tree guard takes priority over every other verdict
    path -- unconfigured repo, explicit skip, docs-only diff, and an
    otherwise-matching pass marker all still deny while the tree is dirty."""

    def test_dirty_tree_denies_even_with_no_pre_pr_cmd(self) -> None:
        repo = self._init_repo("base_branch: main\n")
        self._write(repo, "scratch.md")
        self._git(repo, "add", ".")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("uncommitted changes", verdict["reason"])

    def test_dirty_tree_denies_even_with_explicit_skip(self) -> None:
        repo = self._init_repo("pre_pr_cmd: skip\n")
        self._write(repo, "scratch.md")
        self._git(repo, "add", ".")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")

    def test_dirty_tree_denies_even_on_docs_only_diff(self) -> None:
        repo = self._init_repo(
            'pre_pr_cmd: "exit 9"\ndocs_only_paths:\n  - docs/**\n'
        )
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "docs change")
        # A further, uncommitted docs edit on top of the committed one.
        self._write(repo, "docs/new-note.md", "more\n")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")

    def test_dirty_tree_denies_even_with_matching_marker(self) -> None:
        """This is the exact incident shape: `run` recorded a pass marker
        against a tree that already included the uncommitted edit (tree_state
        folds working-tree status into its own key), so the marker matches --
        but `git push` would still silently drop the edit. The dirty-tree
        guard must catch this regardless of marker freshness."""
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._write(repo, "docs/notes.md")
        self._git(repo, "add", ".")
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("uncommitted changes", verdict["reason"])

    def test_clean_tree_with_matching_marker_still_allows(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_dirty_tree_deny_still_carries_warning_key(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self._git(repo, "checkout", "-q", "-b", "wire-plan-audit-into-verify")
        self._write(repo, "docs/notes.md")
        self._git(repo, "add", ".")
        with mock.patch(
            "worktrail.router.preflight._open_pr_branches",
            return_value=["investigate/wire-plan-audit-into-verify"],
        ):
            verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("warning", verdict)


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

    def test_labels_round_trip(self) -> None:
        repo = self._init_repo()
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "pytest -q", ["go:risk-high", "go:no-automerge"])
        marker = read_marker(Path(repo))
        self.assertEqual(marker["labels"], ["go:risk-high", "go:no-automerge"])

    def test_omitted_labels_default_to_empty_list(self) -> None:
        repo = self._init_repo()
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "pytest -q")
        marker = read_marker(Path(repo))
        self.assertEqual(marker["labels"], [])


class TestRunningLock(_GitRepoCase):
    def test_no_lock_is_not_running(self) -> None:
        repo = self._init_repo()
        self.assertIsNone(read_running_lock(Path(repo)))
        self.assertFalse(is_running(Path(repo)))

    def test_lock_lives_in_private_git_dir(self) -> None:
        repo = self._init_repo()
        path = running_lock_path(Path(repo))
        self.assertEqual(path.parent, Path(repo) / ".git")

    def test_own_pid_lock_is_running(self) -> None:
        repo = self._init_repo()
        write_running_lock(Path(repo))
        self.assertTrue(is_running(Path(repo)))
        remove_running_lock(Path(repo))
        self.assertFalse(is_running(Path(repo)))

    def test_stale_lock_with_dead_pid_self_heals(self) -> None:
        # A lock whose pid has already exited (crash before the `finally`
        # cleanup ran, e.g. kill -9) must not wedge every future check --
        # is_running() removes it and reports not-running.
        repo = self._init_repo()
        proc = subprocess.Popen(["true"])
        dead_pid = proc.pid
        proc.wait()
        running_lock_path(Path(repo)).write_text(
            json.dumps({"pid": dead_pid, "started_at": 0}), encoding="utf-8",
        )
        self.assertFalse(is_running(Path(repo)))
        self.assertIsNone(read_running_lock(Path(repo)))

    def test_malformed_lock_is_not_running(self) -> None:
        repo = self._init_repo()
        running_lock_path(Path(repo)).write_text("not json", encoding="utf-8")
        self.assertFalse(is_running(Path(repo)))


class TestLabelsInCommand(unittest.TestCase):
    def test_space_separated_label(self) -> None:
        self.assertEqual(
            labels_in_command("gh pr create --title x --label go:risk-high"),
            {"go:risk-high"},
        )

    def test_equals_separated_label(self) -> None:
        self.assertEqual(
            labels_in_command("gh pr create --title x --label=go:risk-low"),
            {"go:risk-low"},
        )

    def test_multiple_labels(self) -> None:
        self.assertEqual(
            labels_in_command(
                "gh pr create --label go:risk-high --label go:no-automerge --title x"
            ),
            {"go:risk-high", "go:no-automerge"},
        )

    def test_quoted_title_does_not_confuse_parsing(self) -> None:
        self.assertEqual(
            labels_in_command(
                "gh pr create --title 'fix: something --label fake' --label go:risk-low"
            ),
            {"go:risk-low"},
        )

    def test_no_labels_returns_empty_set(self) -> None:
        self.assertEqual(labels_in_command("gh pr create --title x"), set())

    def test_malformed_quoting_fails_closed_to_empty_set(self) -> None:
        self.assertEqual(labels_in_command("gh pr create --title 'unterminated"), set())


class TestIsUnparseableCommand(unittest.TestCase):
    def test_apostrophe_in_heredoc_prose_is_unparseable(self) -> None:
        # A real-world case: `don't`/`isn't` inside an unquoted `--body $(cat
        # <<'EOF' ...)` heredoc trips shlex's own quote-tracking, even though
        # the shell parses it fine.
        command = (
            "gh pr create --title x --label go:risk-low --body $(cat <<'EOF'\n"
            "This isn't going to break anything.\n"
            "EOF\n"
            ")"
        )
        self.assertTrue(is_unparseable_command(command))

    def test_well_formed_command_is_parseable(self) -> None:
        self.assertFalse(
            is_unparseable_command("gh pr create --title x --label go:risk-low")
        )


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

    def test_matching_marker_with_no_labels_omits_required_labels_key(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")
        self.assertNotIn("required_labels", verdict)

    def test_matching_marker_with_labels_surfaces_required_labels(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high", "go:no-automerge"])
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")
        self.assertEqual(verdict["required_labels"], ["go:risk-high", "go:no-automerge"])

    def test_gh_pr_create_missing_required_label_denies(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high", "go:no-automerge"])
        verdict = check(
            Path(repo),
            command="gh pr create --title x --label go:risk-high",
        )
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("go:no-automerge", verdict["reason"])

    def test_gh_pr_create_with_all_required_labels_allows(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high", "go:no-automerge"])
        verdict = check(
            Path(repo),
            command="gh pr create --title x --label go:risk-high --label go:no-automerge",
        )
        self.assertEqual(verdict["decision"], "allow")

    def test_gh_pr_ready_ignores_required_labels(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high", "go:no-automerge"])
        verdict = check(Path(repo), command="gh pr ready 42")
        self.assertEqual(verdict["decision"], "allow")

    def test_no_command_argument_does_not_enforce_labels(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high", "go:no-automerge"])
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_unparseable_command_denies_with_distinct_message(self) -> None:
        """A `--label go:risk-high` IS present, but the apostrophe in the
        heredoc body breaks shlex parsing -- the deny reason must name the
        real cause (unparseable command) instead of claiming the label is
        missing, so the actual fix (--body-file) is discoverable."""
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-high"])
        command = (
            "gh pr create --title x --label go:risk-high --body $(cat <<'EOF'\n"
            "This isn't going to break anything.\n"
            "EOF\n"
            ")"
        )
        verdict = check(Path(repo), command=command)
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("could not be parsed", verdict["reason"])
        self.assertIn("--body-file", verdict["reason"])
        self.assertNotIn("does not pass them", verdict["reason"])


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

    def test_check_cli_command_flag_enforces_missing_label(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true", ["go:risk-critical", "go:no-automerge"])
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([
                "check", "--repo", repo,
                "--command", "gh pr create --title x --label go:risk-critical",
            ])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "deny")
        self.assertIn("go:no-automerge", payload["reason"])


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

    def test_run_without_risk_records_empty_labels(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        self.assertEqual(read_marker(Path(repo))["labels"], [])

    def test_run_with_protected_gate_records_risk_and_no_automerge_labels(self) -> None:
        # never_automerge short-circuits automerge_eligible() before any live
        # required-checks lookup, so this stays fully offline/deterministic.
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        code = main([
            "run", "--repo", repo, "--risk", "critical", "--gates", "never_automerge",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(
            read_marker(Path(repo))["labels"], ["go:risk-critical", "go:no-automerge"],
        )
        # And check() now enforces that label set against a gh pr create command.
        verdict = check(
            Path(repo), command="gh pr create --title x --label go:risk-critical",
        )
        self.assertEqual(verdict["decision"], "deny")

    def test_run_success_leaves_no_running_lock(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        self.assertIsNone(read_running_lock(Path(repo)))

    def test_run_failure_leaves_no_running_lock(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "exit 3"\n')
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 3)
        self.assertIsNone(read_running_lock(Path(repo)))


class TestRunAddons(_GitRepoCase):
    def test_unconfigured_repo_sees_no_addon_invocation(self) -> None:
        # No `add_ons:` key in go-policy.yaml -- run_addons must iterate zero
        # entries, so addon_for is never even consulted.
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        with mock.patch("worktrail.addons.runner.addon_for") as mock_addon_for:
            code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        mock_addon_for.assert_not_called()

    def test_configured_addon_output_committed_before_gate_runs(self) -> None:
        # pre_pr_cmd itself asserts the add-on's commit is already HEAD,
        # so a passing gate proves the commit landed before the gate ran.
        repo = self._init_repo(
            'pre_pr_cmd: \'git log -1 --pretty=%s | grep -q '
            '"^chore(fakeaddon): synced$"\'\n'
        )
        policy = load_policy(Path(repo))
        policy["add_ons"] = {"fakeaddon": {}}

        def _run(ctx):
            path = Path(ctx.worktree) / "generated.txt"
            path.write_text("generated\n", encoding="utf-8")
            return AddOnResult(changed=True, detail="synced", paths=[path])

        fake_addon = mock.Mock()
        fake_addon.run.side_effect = _run

        with mock.patch(
            "worktrail.router.preflight.load_policy", return_value=policy,
        ), mock.patch(
            "worktrail.addons.runner.addon_for", return_value=fake_addon,
        ):
            code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        fake_addon.run.assert_called_once()
        subject = subprocess.run(
            ["git", "-C", repo, "log", "-1", "--pretty=%s"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(subject, "chore(fakeaddon): synced")

    def test_required_addon_failure_fails_preflight_run(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        policy = load_policy(Path(repo))
        policy["add_ons"] = {"fakeaddon": {"required": True}}

        fake_addon = mock.Mock()
        fake_addon.install.side_effect = RuntimeError("boom")

        with mock.patch(
            "worktrail.router.preflight.load_policy", return_value=policy,
        ), mock.patch(
            "worktrail.addons.runner.addon_for", return_value=fake_addon,
        ):
            code = main(["run", "--repo", repo])
        self.assertEqual(code, ADDON_REQUIRED_FAILURE_EXIT)
        self.assertIsNone(read_marker(Path(repo)))
        self.assertIsNone(read_running_lock(Path(repo)))


class TestWaitCli(_GitRepoCase):
    def test_wait_returns_immediately_when_not_running(self) -> None:
        repo = self._init_repo()
        code = main(["wait", "--repo", repo, "--timeout", "1", "--interval", "0.05"])
        self.assertEqual(code, 0)

    def test_wait_returns_once_lock_is_cleared_mid_poll(self) -> None:
        repo = self._init_repo()
        write_running_lock(Path(repo))

        def _clear_shortly() -> None:
            time.sleep(0.1)
            remove_running_lock(Path(repo))

        threading.Thread(target=_clear_shortly).start()
        code = main(["wait", "--repo", repo, "--timeout", "5", "--interval", "0.02"])
        self.assertEqual(code, 0)

    def test_wait_times_out_while_still_running(self) -> None:
        repo = self._init_repo()
        write_running_lock(Path(repo))  # own pid: genuinely alive
        code = main(["wait", "--repo", repo, "--timeout", "0.1", "--interval", "0.05"])
        self.assertEqual(code, 1)
        remove_running_lock(Path(repo))


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

#!/usr/bin/env python3
"""Delivery-ledger invariant: every task the run reports DONE must be provably
an ancestor of the base branch.

Regression coverage for brief 20260815-115257. In run `full-1786812908`
(spec `auto-dod-verification`), task 1.3 was reviewed-PASSED and journal-done,
but its commits never reached the squash-merged group PR -- #419 merged with
the title `base: 1.1, 1.2`, silently missing 1.3's entire `no_stub_markers`
check type. Downstream tasks then built against code that was never shipped.
Nothing on the orchestrator side raised a signal.

`integrate.detect_unreconciled_evidence` already performed exactly the right
check (`git merge-base --is-ancestor <task HEAD> <remote>/<base>`) and fired
correctly for tail task 3.3 in that very same run -- it simply skipped every
non-tail task. These tests pin the widened contract: the ancestry check is an
invariant over every DONE task, not a tail-kind special case.

Run: python3 -m pytest tests/orchestrator/test_delivery_ledger_invariant.py
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import integrate


def _run_git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _task(task_id, kind="impl", status="done"):
    return {"id": task_id, "status": status, "kind": kind}


class DeliveryLedgerInvariant(unittest.TestCase):
    def _init_repo(self, tmpdir):
        repo = Path(tmpdir) / "myrepo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        _run_git(
            repo,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        # Local stand-in for the remote-tracking ref the detector compares
        # against -- no real remote needed for these tests.
        _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")
        wt_base = repo.parent / f"{repo.name}-worktrees"
        wt_base.mkdir()
        return repo, wt_base

    def _add_task_worktree(self, repo, wt_base, spec_id, task_id):
        wt = wt_base / f"{spec_id}-{task_id.lower()}"
        _run_git(
            repo,
            "worktree",
            "add",
            "-B",
            f"{spec_id}/{task_id.lower()}",
            str(wt),
            "main",
        )
        return wt

    def _commit_in(self, wt, filename, body, message):
        (wt / filename).write_text(body)
        _run_git(wt, "add", filename)
        _run_git(
            wt,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            message,
        )

    def test_flags_done_impl_task_with_unmerged_commit(self):
        """The 20260815-115257 shape: kind='impl', status='done', a real
        commit on the task branch, branch not an ancestor of origin/main."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "auto-dod", "1.3")
            self._commit_in(
                wt,
                "check.py",
                "STUB_MARKER_PATTERN = 'TODO'\n",
                "feat: add no_stub_markers check",
            )

            findings = integrate.detect_unreconciled_evidence(
                repo,
                "origin",
                "main",
                "auto-dod",
                wt_base,
                [_task("1.3")],
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["task"], "1.3")
            self.assertEqual(findings[0]["worktree"], str(wt))
            self.assertTrue(findings[0]["head_sha"])

    def test_no_finding_when_impl_task_commit_is_on_base(self):
        """The delivered case must stay silent: a DONE impl task whose commit
        IS an ancestor of the base branch is not a finding. Guards against the
        widened check turning every successful run into a false alarm."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "auto-dod", "1.1")
            self._commit_in(
                wt,
                "check.py",
                "def file_tracked(): pass\n",
                "feat: add file_tracked check",
            )
            # Land the task branch on base, exactly as a merged group PR would.
            _run_git(repo, "merge", "-q", "--no-edit", "auto-dod/1.1")
            _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")

            findings = integrate.detect_unreconciled_evidence(
                repo,
                "origin",
                "main",
                "auto-dod",
                wt_base,
                [_task("1.1")],
            )
            self.assertEqual(findings, [])

    def test_non_terminal_impl_task_is_not_flagged(self):
        """A task still in flight has no delivery obligation yet -- only
        coordinator.DONE statuses are subject to the invariant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "auto-dod", "1.3")
            self._commit_in(wt, "check.py", "partial\n", "wip")

            findings = integrate.detect_unreconciled_evidence(
                repo,
                "origin",
                "main",
                "auto-dod",
                wt_base,
                [_task("1.3", status="implementing")],
            )
            self.assertEqual(findings, [])

    def test_tail_and_impl_tasks_both_flagged_together(self):
        """Widening must not regress tail coverage: run full-1786812908 had
        BOTH a stranded impl task (1.3) and a stranded tail task (3.3)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            for tid in ("1.3", "3.3"):
                wt = self._add_task_worktree(repo, wt_base, "auto-dod", tid)
                self._commit_in(wt, f"f{tid}.txt", "x\n", f"work for {tid}")

            findings = integrate.detect_unreconciled_evidence(
                repo,
                "origin",
                "main",
                "auto-dod",
                wt_base,
                [_task("1.3", kind="impl"), _task("3.3", kind="cleanup")],
            )
            self.assertEqual({f["task"] for f in findings}, {"1.3", "3.3"})

    def test_legacy_alias_still_resolves(self):
        """`detect_unreconciled_tail_evidence` remains importable so any
        out-of-tree caller or patch target keeps working after the rename."""
        self.assertIs(
            integrate.detect_unreconciled_tail_evidence,
            integrate.detect_unreconciled_evidence,
        )


if __name__ == "__main__":
    unittest.main()

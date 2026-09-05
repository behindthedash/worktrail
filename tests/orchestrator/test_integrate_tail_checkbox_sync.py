"""`integrate.sync_checkbox_status`: a tail task that completed with zero
commits gets its `tasks.md` checkbox landed by the run itself (a docs-only PR
through `land_pr.open_or_update_pull_request`, then the verify/merge path), so
the run no longer ends in `checkbox_status_divergence` needing a manual sync PR
(worktrail PR #982 existed only to tick 2.1 and archive)."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import integrate

REPO = Path("/repo")
GH_URL = "https://github.com/owner/repo/pull/77"


def _proc(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


class FakeGit:
    """Just enough `_git` to drive sync_checkbox_status: HEAD moves once the
    status write commits, so the "nothing written" short-circuit can be
    exercised by leaving `committed` False."""

    def __init__(self, committed: bool = True, push_rc: int = 0):
        self.committed = committed
        self.push_rc = push_rc
        self.calls: list[tuple] = []

    def __call__(self, repo, *args, check=True):
        self.calls.append(tuple(args))
        if args[:2] == ("rev-parse", "HEAD"):
            return _proc("head-sha\n" if self.committed else "base-sha\n")
        if args[0] == "rev-parse":
            return _proc("base-sha\n")
        if args[:2] == ("remote", "get-url"):
            return _proc("git@github.com:owner/repo.git\n")
        if args[0] == "push":
            return _proc(rc=self.push_rc)
        return _proc()


class FakeVerifier:
    def __init__(self, outcome: str = "merged"):
        self.outcome = outcome
        self.groups: list[dict] = []

    def verify_one(
        self, group, gb, delivered, merged, quarantined, lock, armed=None, **_
    ):
        self.groups.append(group)
        if self.outcome == "merged":
            merged.append(group["name"])
        elif self.outcome == "quarantined":
            quarantined[group["name"]] = "CI red"


def _tasks():
    return [
        {"id": "1.1", "status": "done", "kind": "impl"},
        {"id": "13.1", "status": "done", "kind": "e2e"},
    ]


class SyncCheckboxStatusTests(unittest.TestCase):
    def _run(self, findings, git, verifier, land_outcome=None, journal="/j.json"):
        writes: list[tuple] = []
        journal_writes: list[tuple] = []
        land_outcome = land_outcome or {
            "pr_url": GH_URL,
            "pr_number": 77,
            "refused_step": None,
            "detail": None,
        }
        with (
            patch.object(
                integrate, "detect_checkbox_status_divergence", return_value=findings
            ),
            patch.object(integrate, "_git", side_effect=git),
            patch.object(
                integrate,
                "_write_group_task_status",
                side_effect=lambda iw, spec, group, status: writes.append(
                    (spec, tuple(group["tasks"]))
                ),
            ),
            patch.object(
                integrate, "_spec_path_for", return_value=Path("/wt/docs/specs/s")
            ),
            patch.object(
                integrate,
                "_write_group_journal",
                side_effect=lambda *a, **k: journal_writes.append(a),
            ),
            patch.object(integrate, "shutil"),
            patch.object(
                integrate.land_pr,
                "open_or_update_pull_request",
                return_value=land_outcome,
            ) as land,
        ):
            result = integrate.sync_checkbox_status(
                REPO,
                "origin",
                "main",
                "spec-a",
                _tasks(),
                "run-1",
                journal,
                route="E",
                gates="g1",
                make_verifier=lambda: verifier,
            )
        return result, land, writes, journal_writes

    def test_zero_commit_tail_task_lands_checkbox_pr_and_merges(self):
        git = FakeGit()
        verifier = FakeVerifier("merged")
        result, land, writes, journal_writes = self._run(
            [{"task": "13.1", "base_status": "pending"}], git, verifier
        )
        self.assertEqual(writes, [("spec-a", ("13.1",))])
        self.assertEqual(land.call_count, 1)
        args, kwargs = land.call_args
        self.assertEqual(args[1], "main")
        self.assertEqual(args[2], "run-1/checkbox-sync")
        self.assertIn("13.1", args[3])
        self.assertEqual(kwargs["risk"], "low")
        self.assertIsNone(kwargs["labels"])
        self.assertEqual(kwargs["route"], "E")
        self.assertEqual(kwargs["gates"], ["g1"])
        self.assertEqual(kwargs["base_slug"], "owner/repo")
        self.assertIn(("push", "-u", "origin", "HEAD:run-1/checkbox-sync"), git.calls)
        self.assertEqual(verifier.groups[0]["name"], "checkbox-sync")
        self.assertEqual(result["state"], "MERGED")
        self.assertEqual(result["pr_url"], GH_URL)
        self.assertEqual(result["tasks"], ["13.1"])
        self.assertEqual(
            [(w[1], w[4]) for w in journal_writes],
            [("checkbox-sync", "OPEN"), ("checkbox-sync", "MERGED")],
        )
        # the disposable worktree is torn down either way
        self.assertTrue(any(c[:2] == ("worktree", "remove") for c in git.calls))

    def test_no_divergence_opens_nothing(self):
        git = FakeGit()
        result, land, writes, _ = self._run([], git, FakeVerifier())
        self.assertIsNone(result)
        self.assertEqual(land.call_count, 0)
        self.assertEqual(writes, [])
        self.assertFalse(any(c[:2] == ("worktree", "add") for c in git.calls))

    def test_nothing_written_opens_nothing(self):
        git = FakeGit(committed=False)
        result, land, _writes, _ = self._run(
            [{"task": "13.1", "base_status": "pending"}], git, FakeVerifier()
        )
        self.assertIsNone(result)
        self.assertEqual(land.call_count, 0)
        self.assertFalse(any(c[0] == "push" for c in git.calls))

    def test_refused_open_quarantines_without_verify(self):
        git = FakeGit()
        verifier = FakeVerifier("merged")
        result, _land, _w, journal_writes = self._run(
            [{"task": "13.1", "base_status": "pending"}],
            git,
            verifier,
            land_outcome={
                "pr_url": None,
                "pr_number": None,
                "refused_step": "pr_create",
                "detail": "label not found",
            },
        )
        self.assertEqual(result["state"], "QUARANTINED")
        self.assertEqual(result["detail"], "label not found")
        self.assertEqual(verifier.groups, [])
        self.assertEqual(journal_writes[-1][4], "QUARANTINED")

    def test_verify_quarantine_is_reported(self):
        git = FakeGit()
        result, _l, _w, journal_writes = self._run(
            [{"task": "13.1", "base_status": "pending"}],
            git,
            FakeVerifier("quarantined"),
        )
        self.assertEqual(result["state"], "QUARANTINED")
        self.assertEqual(result["pr_url"], GH_URL)
        self.assertEqual(journal_writes[-1][4], "QUARANTINED")


if __name__ == "__main__":
    unittest.main()

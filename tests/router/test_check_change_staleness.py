#!/usr/bin/env python3
"""Unit tests for the pre-orchestrator-dispatch OpenSpec change-staleness
guard (stdlib unittest).

Exercises a real throwaway git repo fixture, mirroring
`test_check_brief_staleness.py`'s fixture pattern and philosophy: this module
is a thin adapter over `check_brief_staleness.check()`, so these tests focus
on the OpenSpec-specific plumbing (`tasks.md` parsing, the change's own
first-commit-on-base anchor, `proposal.md` feeding the probe text) rather
than re-testing probe extraction or history search, which
`test_check_brief_staleness.py` already covers directly.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from worktrail.router import check_change_staleness as ccs

TASKS_MD_MIXED = """## 1. Setup

- [x] 1.1 Already done

## 2. Tests

- [ ] 2.1 Add widget_helper coverage
- [ ] 2.2 Wire the frobnicate endpoint
"""

TASKS_MD_ALL_DONE = """## 1. Setup

- [x] 1.1 Already done
"""


def _git(repo: str, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True, env=env,
    )


def _init_repo(branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="change-staleness-")
    _git(d, "init", "-q", "-b", branch)
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("base\n", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _write(repo: str, name: str, content: str) -> None:
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repo: str, message: str, date_iso: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_DATE=date_iso, GIT_COMMITTER_DATE=date_iso)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message, env=env)
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


def _write_change(repo: str, change_id: str, tasks_md: str, date_iso: str, proposal: str = None) -> str:
    """Author and commit an OpenSpec change directory, returning the commit sha."""
    _write(repo, f"openspec/changes/{change_id}/tasks.md", tasks_md)
    if proposal is not None:
        _write(repo, f"openspec/changes/{change_id}/proposal.md", proposal)
    return _commit(repo, f"openspec: propose {change_id}", date_iso)


class TestMissingOrEmptyInputs(unittest.TestCase):
    def test_missing_tasks_md_is_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            res = ccs.check_change(Path(t), "does-not-exist")
            self.assertFalse(res["checked"])
            self.assertIsNotNone(res["warning"])
            self.assertIsNone(res["change_dir"])

    def test_all_tasks_completed_is_unchecked_with_empty_pending(self):
        repo = _init_repo()
        _write_change(repo, "add-export", TASKS_MD_ALL_DONE, "2026-06-01T00:00:00")

        res = ccs.check_change(Path(repo), "add-export")

        self.assertFalse(res["checked"])
        self.assertEqual(res["pending_task_ids"], [])
        self.assertIn("no pending tasks", res["warning"])

    def test_change_dir_present_but_never_committed_is_unchecked(self):
        repo = _init_repo()
        # Written to disk but never committed -- git has no history for this
        # path on any ref, so there is nothing to anchor `since` to.
        _write(repo, "openspec/changes/uncommitted-change/tasks.md", TASKS_MD_MIXED)

        res = ccs.check_change(Path(repo), "uncommitted-change")

        self.assertFalse(res["checked"])
        self.assertIn("no history on", res["warning"])


class TestFlagsShippedWork(unittest.TestCase):
    def test_pending_task_whose_symbol_already_shipped_is_flagged(self):
        repo = _init_repo()
        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")

        # The delivering commit lands after the change was proposed -- this
        # is the PR #547/#548/#610 shape: work shipped, checkbox never flipped.
        _write(repo, "src/widget.py", "def widget_helper():\n    pass\n")
        sha = _commit(repo, "Add widget_helper", "2026-06-02T00:00:00")

        res = ccs.check_change(Path(repo), "add-widget")

        self.assertTrue(res["checked"])
        self.assertEqual(sorted(res["pending_task_ids"]), ["2.1", "2.2"])
        found_shas = {m["sha"] for m in res["matches"]}
        self.assertIn(sha, found_shas)

    def test_clean_pending_task_reports_no_matches(self):
        repo = _init_repo()
        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")

        res = ccs.check_change(Path(repo), "add-widget")

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])
        self.assertEqual(res["pull_requests"], [])

    def test_proposal_md_content_also_feeds_probes(self):
        repo = _init_repo()
        proposal = "This change will introduce `frobnicate_gateway` as the new entrypoint."
        _write_change(repo, "add-gateway", TASKS_MD_MIXED, "2026-06-01T00:00:00", proposal=proposal)

        _write(repo, "src/gateway.py", "def frobnicate_gateway():\n    pass\n")
        sha = _commit(repo, "Add frobnicate_gateway", "2026-06-02T00:00:00")

        res = ccs.check_change(Path(repo), "add-gateway")

        self.assertTrue(res["checked"])
        found_shas = {m["sha"] for m in res["matches"] if m["probe"] == "frobnicate_gateway"}
        self.assertIn(sha, found_shas)

    def test_delivering_commit_before_change_was_proposed_is_not_evidence(self):
        repo = _init_repo()
        _write(repo, "src/widget.py", "def widget_helper():\n    pass\n")
        _commit(repo, "Add widget_helper", "2026-05-01T00:00:00")

        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")

        res = ccs.check_change(Path(repo), "add-widget")

        self.assertTrue(res["checked"])
        self.assertEqual(res["matches"], [])


class TestFirstCommitDate(unittest.TestCase):
    def test_anchors_to_earliest_commit_not_latest(self):
        repo = _init_repo()
        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")
        # A later edit to the same change directory must not move the anchor
        # forward -- the search boundary is the change's own proposal date.
        _write(repo, "openspec/changes/add-widget/tasks.md", TASKS_MD_MIXED + "\n- [ ] 3.1 More work\n")
        _commit(repo, "openspec: refine add-widget tasks", "2026-06-05T00:00:00")

        since = ccs._change_first_commit_date(Path(repo), "HEAD", "add-widget")

        self.assertEqual(since, "2026-06-01")

    def test_unknown_change_id_yields_none(self):
        repo = _init_repo()
        since = ccs._change_first_commit_date(Path(repo), "HEAD", "never-existed")
        self.assertIsNone(since)


class TestCli(unittest.TestCase):
    def _run_cli(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ccs.main(argv)
        return code, buf.getvalue()

    def test_missing_change_reports_unknown_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as t:
            code, out = self._run_cli(["--repo", t, "--change-id", "nope"])
            self.assertEqual(code, 0)
            self.assertIn("unknown:", out)

    def test_json_shape_has_every_documented_key(self):
        repo = _init_repo()
        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")

        code, out = self._run_cli(["--repo", repo, "--change-id", "add-widget", "--json"])

        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in (
            "checked", "change_dir", "since", "pending_task_ids",
            "probes", "matches", "pull_requests", "research_notes", "warning",
        ):
            self.assertIn(key, data)

    def test_clean_change_reports_no_evidence(self):
        repo = _init_repo()
        _write_change(repo, "add-widget", TASKS_MD_MIXED, "2026-06-01T00:00:00")

        code, out = self._run_cli(["--repo", repo, "--change-id", "add-widget"])

        self.assertEqual(code, 0)
        self.assertIn("no evidence", out)


if __name__ == "__main__":
    unittest.main()

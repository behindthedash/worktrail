#!/usr/bin/env python3
"""Regression coverage for brief 20260807-175851: `_full_real_inner`'s serial
(non-pipeline) code paths never persisted a group's QUARANTINED state back to
the run journal after `verify.verify_and_cleanup()` ran. Without this, a
resume re-reads the stale pre-verify journal record (head_branch/pr_url
unchanged) instead of seeing QUARANTINED -- unlike the pipeline path's
`_integrate_verify_group`, which calls `_record_group_fn(..., "QUARANTINED")`.

Covers both affected call sites: the `--from-verify` entry point and the
default sequential flow's shared post-integrate verify step (which serves
both the all_integrated fast path -- integrate skipped, journal reused as-is
-- and the finish_real branch -- integrate just ran).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import verify  # noqa: E402


def _init_repo(root: Path) -> Path:
    """Real git repo with a 3-task spec that plan_groups() splits into
    base / feature-1 / feature-2 (same topology as test_pipeline.py's fixture)."""
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)

    tasks_fm = {
        "TASK-001": (
            "---\nid: TASK-001\nstatus: done\ndependencies: []\n"
            "files: [src/task-001.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-002": (
            "---\nid: TASK-002\nstatus: done\ndependencies: [TASK-001]\n"
            "files: [src/task-002.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-003": (
            "---\nid: TASK-003\nstatus: done\ndependencies: [TASK-001]\n"
            "files: [src/task-003.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
    }
    for tid, fm in tasks_fm.items():
        (spec_dir / f"{tid}.md").write_text(fm)
    (repo / "README.md").write_text("x\n")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class FromVerifyPersistsQuarantineTest(unittest.TestCase):
    """--from-verify: a group newly quarantined by verify_and_cleanup must be
    written into the on-disk journal as state=QUARANTINED."""

    def test_quarantined_group_state_written_to_journal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            (repo.parent / f"{repo.name}-worktrees").mkdir(parents=True, exist_ok=True)

            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps({
                "run_id": "full-test",
                "spec_id": "001-x",
                "entries": [],
                "groups": {
                    "base": {"pr_url": "http://pr/base", "head_branch": "full-test/base", "state": "OPEN"},
                    "feature-1": {"pr_url": "http://pr/f1", "head_branch": "full-test/feature-1", "state": "OPEN"},
                    "feature-2": {"pr_url": "http://pr/f2", "head_branch": "full-test/feature-2", "state": "OPEN"},
                },
                "integrate_complete": True,
            }))

            def mock_vac(repo_, remote, base_, spec_id, groups, group_branch, delivered, **kw):
                return {"merged": ["base", "feature-2"], "quarantined": {"feature-1": "CI failed after 3 retries"}}

            with unittest.mock.patch.object(verify, "verify_and_cleanup", mock_vac):
                live._full_real_inner(
                    str(repo),
                    "docs/specs/001-x",
                    remote="origin",
                    base="main",
                    from_verify=True,
                )

            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["feature-1"]["state"], "QUARANTINED")
            # Groups verify_and_cleanup did not quarantine keep their existing record.
            self.assertEqual(journal["groups"]["base"]["state"], "OPEN")
            self.assertEqual(journal["groups"]["feature-2"]["state"], "OPEN")


class AllIntegratedFastPathPersistsQuarantineTest(unittest.TestCase):
    """Default sequential flow, all_integrated=True (fan-out mocked done, every
    group already has a pr_url so integrate is skipped): a group newly
    quarantined by verify_and_cleanup must be written into the on-disk journal
    as state=QUARANTINED, not left as the stale pre-verify OPEN record."""

    def test_quarantined_group_state_written_to_journal(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            (repo.parent / f"{repo.name}-worktrees").mkdir(parents=True, exist_ok=True)

            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps({
                "run_id": "full-test",
                "spec_id": "001-x",
                "entries": [],
                "groups": {
                    "base": {"pr_url": "http://pr/base", "head_branch": "full-test/base", "state": "OPEN"},
                    "feature-1": {"pr_url": "http://pr/f1", "head_branch": "full-test/feature-1", "state": "OPEN"},
                    "feature-2": {"pr_url": "http://pr/f2", "head_branch": "full-test/feature-2", "state": "OPEN"},
                },
                "integrate_complete": True,
            }))

            fake_tasks = [
                {"id": "TASK-001", "status": "done", "retry_count": 0},
                {"id": "TASK-002", "status": "done", "retry_count": 0},
                {"id": "TASK-003", "status": "done", "retry_count": 0},
            ]

            def mock_vac(repo_, remote, base_, spec_id, groups, group_branch, delivered, **kw):
                return {"merged": ["base", "feature-2"], "quarantined": {"feature-1": "CI failed after 3 retries"}}

            with unittest.mock.patch.object(verify, "verify_and_cleanup", mock_vac), \
                 unittest.mock.patch.object(
                     live, "live_run_real",
                     return_value={"spec_id": "001-x", "tasks": fake_tasks, "entries": 0, "done": 3, "total": 3},
                 ):
                live._full_real_inner(
                    str(repo),
                    "docs/specs/001-x",
                    remote="origin",
                    base="main",
                    resume=True,
                )

            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["feature-1"]["state"], "QUARANTINED")
            self.assertEqual(journal["groups"]["base"]["state"], "OPEN")
            self.assertEqual(journal["groups"]["feature-2"]["state"], "OPEN")


if __name__ == "__main__":
    unittest.main()

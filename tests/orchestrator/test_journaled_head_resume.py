#!/usr/bin/env python3
"""End-to-end resume coverage for retained task-head mismatches.

The journal records one valid task commit, while the retained task branch and
worktree point at a different valid commit from the same base.  Both resume
paths must reject that state without repairing the user's branch implicitly.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import (
    live,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    task_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK-001.md").write_text(
        "---\n"
        "id: TASK-001\n"
        "status: pending\n"
        "dependencies: []\n"
        "files: [src/task-001.txt]\n"
        "kind: impl\n"
        "review: skip\n"
        "---\nbody\n"
    )
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _seed_mismatched_resume(root: Path) -> tuple[Path, Path, str, str, str]:
    """Return repo, journal, task branch, retained worktree, and journaled SHA."""
    repo = _init_repo(root)
    base_sha = _git(repo, "rev-parse", "HEAD")
    task_branch = "001-x/task-001"
    retained_wt = root / "retained-task"
    journaled_wt = root / "journaled-head"

    # Keep the journaled commit reachable by a named ref, but do not put it on
    # the retained task branch.  This makes the mismatch deterministic while
    # keeping both commits valid and inspectable.
    _git(repo, "worktree", "add", "-b", "journaled-head", str(journaled_wt), base_sha)
    (journaled_wt / "src").mkdir()
    (journaled_wt / "src" / "task-001.txt").write_text("journaled\n")
    _git(journaled_wt, "add", "-A")
    _git(journaled_wt, "commit", "-q", "-m", "journaled task head")
    journaled_sha = _git(journaled_wt, "rev-parse", "HEAD")
    _git(repo, "worktree", "remove", "--force", str(journaled_wt))

    _git(repo, "worktree", "add", "-b", task_branch, str(retained_wt), base_sha)
    (retained_wt / "src").mkdir()
    (retained_wt / "src" / "task-001.txt").write_text("retained alternate\n")
    _git(retained_wt, "add", "-A")
    _git(retained_wt, "commit", "-q", "-m", "alternate retained task head")
    retained_sha = _git(retained_wt, "rev-parse", "HEAD")
    assert retained_sha != journaled_sha

    journal = root / "run-001-x.json"
    journal.write_text(
        json.dumps(
            {
                "spec_id": "001-x",
                "run_id": "resume-1",
                "entries": [
                    {
                        "task": "TASK-001",
                        "role": "implement",
                        "report": {
                            "status": "success",
                            "head_sha": journaled_sha,
                        },
                    }
                ],
            }
        )
    )
    return repo, journal, task_branch, retained_wt, retained_sha


class JournaledHeadMismatchResume(unittest.TestCase):
    def _assert_resume_rejects_mismatch(self, resume, branch_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            repo, journal, branch, retained_wt, retained_sha = _seed_mismatched_resume(
                Path(tmp)
            )
            if branch_only:
                _git(repo, "worktree", "remove", "--force", str(retained_wt))
            spawn_calls = []

            def unexpected_spawn(*args):
                spawn_calls.append(args)
                raise AssertionError(
                    "mismatched retained worktree must be rejected before spawn"
                )

            kwargs = {
                "repo": repo,
                "spec_rel": "docs/specs/001-x",
                "remote": "origin",
                "base": "main",
                "model": "haiku",
                "max_workers": 1,
                "timeout": 30,
                "resume": True,
                "only": None,
                "role_models": None,
                "run_budget": None,
                "journal_path": str(journal),
                "run_id": "resume-1",
                "_spawn": unexpected_spawn,
            }

            if resume == "sequential":
                live.live_run_real(
                    repo,
                    "docs/specs/001-x",
                    max_workers=1,
                    out_cassette=str(journal),
                    run_id="resume-1",
                    resume=True,
                    spawn=unexpected_spawn,
                )
                entries = json.loads(journal.read_text())["entries"]
            else:

                def integrate_one(*args, **kwargs):
                    group = args[0]
                    quarantined = args[10]
                    quarantined[group["name"]] = (
                        "no deliverable after retained-head rejection"
                    )

                kwargs["_integrate_one"] = integrate_one
                result = live._pipeline_scheduler(**kwargs)
                entries = json.loads(journal.read_text())["entries"]
                self.assertIn("feature-1", result["quarantined"])

            self.assertEqual(spawn_calls, [])
            self.assertTrue(
                any(
                    "journaled task head" in e["report"].get("notes", "")
                    for e in entries
                )
            )
            self.assertEqual(_git(repo, "rev-parse", branch), retained_sha)
            if branch_only:
                self.assertFalse(retained_wt.exists())
            else:
                self.assertTrue(retained_wt.exists())
                self.assertEqual(_git(retained_wt, "rev-parse", "HEAD"), retained_sha)

    def test_sequential_resume_rejects_journaled_head_mismatch(self):
        self._assert_resume_rejects_mismatch("sequential")

    def test_pipeline_resume_rejects_journaled_head_mismatch(self):
        self._assert_resume_rejects_mismatch("pipeline")

    def test_sequential_resume_rejects_branch_only_mismatch(self):
        self._assert_resume_rejects_mismatch("sequential", branch_only=True)

    def test_pipeline_resume_rejects_branch_only_mismatch(self):
        self._assert_resume_rejects_mismatch("pipeline", branch_only=True)


if __name__ == "__main__":
    unittest.main()

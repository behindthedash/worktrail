#!/usr/bin/env python3
"""Regression: `_integrate_verify_group` must skip `verify_one` for a group whose
tasks are ALL already integrated (a group `integrate_one` journals MERGED via its
own implicit-merge branch, with no PR ever opened).

Incident: `_pipeline_scheduler`'s real `integrate_one` correctly detects an
all-already-integrated group, journals `state: MERGED` (via `_record_group_fn`,
which mutates the shared `groups_journal` dict in place), sets `group_branch[name]`
so dependent groups can stack on it, and returns `None` (no PR). The caller,
`_integrate_verify_group`, only skipped verify when `name not in group_branch` --
but the implicit-merge branch DOES set `group_branch[name]`, so that guard never
fired. Verify then ran against a synthetic `f"{run_id}/{name}"` branch that was
never pushed or opened as a PR, always failing "no pull requests found" and
wrongly quarantining an already-fully-merged group -- cascading to every
dependent group (reproduced live resuming worktrail's own concurrent-drain-workers
spec: the already-merged "base" group was quarantined on every resume attempt,
each time blocking its dependent "feature-1" group from ever integrating).

Run: python3 tests/orchestrator/test_implicit_merge_skips_verify.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live


def _init_already_merged_repo(root: Path) -> Path:
    """One task, already `status: completed` -- simulates a group whose only
    task was fully integrated on a prior run before this invocation started."""
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)
    (spec_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: completed\ndependencies: []\n"
        "files: [src/task-001.txt]\nkind: impl\nreview: skip\n---\nbody\n"
    )
    (repo / "src").mkdir()
    (repo / "src" / "task-001.txt").write_text("done\n")
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


def _unreachable_spawn(role, task, wt):
    raise AssertionError(
        f"spawn should never be called -- TASK-001 is already 'completed' "
        f"(role={role!r}, task={task.get('id')!r})"
    )


class _UnreachableVerifier:
    def verify_one(
        self,
        group,
        group_branch,
        delivered,
        merged,
        quarantined,
        lock,
        self_merged=None,
        armed=None,
        post_merge_regressed=None,
    ):
        raise AssertionError(
            f"verify_one should never be called for an all-already-integrated "
            f"group ({group.get('name')!r}, branch={group_branch!r}) -- nothing "
            f"was ever pushed or opened as a PR for it"
        )


class ImplicitMergeSkipsVerifyTest(unittest.TestCase):
    def test_all_already_integrated_group_skips_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_already_merged_repo(Path(tmp))
            journal_path = str(Path(tmp) / "run-001-x.json")

            result = live._pipeline_scheduler(
                repo=repo,
                spec_rel="docs/specs/001-x",
                remote="origin",
                base="main",
                model="haiku",
                max_workers=2,
                timeout=30,
                resume=False,
                only=None,
                role_models=None,
                run_budget=None,
                journal_path=journal_path,
                run_id="implicit-merge-test",
                _spawn=_unreachable_spawn,
                _make_verifier=lambda: _UnreachableVerifier(),
            )

            self.assertEqual(
                result["quarantined"],
                {},
                f"already-merged group was wrongly quarantined: {result['quarantined']}",
            )
            self.assertEqual(
                len(result["group_prs"]),
                1,
                f"expected exactly one already-merged group recorded: {result['group_prs']}",
            )


if __name__ == "__main__":
    unittest.main()

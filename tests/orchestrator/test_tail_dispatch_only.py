#!/usr/bin/env python3
"""Regression: `_dispatch_pending_tail` must thread `--only` through to its
internal `live_run_real()` call.

Incident: `_pipeline_scheduler` pre-marks every task outside `--only` as
"completed" for its OWN fan-out frontier, but never passed that same `only`
list into `_dispatch_pending_tail` -- whose signature didn't even accept it.
`_dispatch_pending_tail`'s internal `live_run_real()` call reloads every task
FRESH from the TaskSource (independent of the scheduler's in-memory list), so
with no `only` to re-apply the pre-mark, any task not yet terminal on disk
(e.g. not-yet-synced/ticked despite its work already merging via an earlier
group's PR) was silently re-dispatched by the tail phase -- reproduced live on
worktrail's own concurrent-drain-workers run (go-20260814-202451), which
re-implemented 5 already-merged tasks and crashed with a WorktreeAddError.

Uses `live_run_real`/`_dispatch_pending_tail` directly with an injected fake
spawn against a throwaway git repo (test_fanout_concurrency.py's pattern) --
no gh/PR machinery needed, since dispatch (this layer) is a distinct concern
from integrate/PR (the pipeline-scheduler layer).

Run: python3 scripts/test_tail_dispatch_only.py
"""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _init_repo(root: Path, tasks_frontmatter: dict) -> Path:
    repo = root / "repo"
    (repo / "docs" / "specs" / "001-x" / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    for tid, fm in tasks_frontmatter.items():
        (repo / "docs" / "specs" / "001-x" / "tasks" / f"{tid}.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _fm(tid, files, deps="", status="pending", **extra):
    lines = [
        f"id: {tid}",
        f"status: {status}",
        f"dependencies: [{deps}]",
        f"files: [{files}]",
        "kind: impl",
    ]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(lines) + "\n---\n\nbody\n"


class FakeSpawn:
    """Records (role, task) calls; commits real work for implement/fix."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def __call__(self, role, task, wt):
        with self.lock:
            self.calls.append((role, task["id"]))
        if role in ("implement", "fix"):
            f = Path(wt) / "src" / f"{task['id'].lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{task['id']} {role}\n")
            subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"{role} {task['id']}"],
                check=True,
                capture_output=True,
            )
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        rs = '"PASSED"' if role == "review" else "null"
        return spawnlib.SpawnResult(
            text=(
                f'```json\n{{"task":"{task["id"]}","step":"{role}","status":"success",'
                f'"head_sha":"{sha[:8]}","review_status":{rs}}}\n```'
            ),
            usage={},
        )


class TailDispatchRespectsOnly(unittest.TestCase):
    def test_only_excludes_non_tail_task_from_tail_dispatch(self):
        """TASK-001 is not in --only and is still 'pending' on disk (the
        not-yet-synced state that exposed the bug). TASK-002 (kind: e2e) is
        the tail task and IS in --only. Before the fix, TASK-001 gets
        silently re-dispatched once the tail phase reloads tasks fresh;
        after the fix, `only` excludes it just like the fan-out frontier."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm(
                        "TASK-002", "src/task-002.txt", deps="TASK-001", kind="e2e"
                    ),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake = FakeSpawn()

            # Minimal in-memory task list, only used for the tail_held_out_task_ids
            # gate check -- must show TASK-002 as a pending tail task.
            gate_tasks = [
                {"id": "TASK-001", "status": "completed", "kind": "impl", "deps": []},
                {"id": "TASK-002", "status": "pending", "kind": "e2e", "deps": ["TASK-001"]},
            ]

            result = live._dispatch_pending_tail(
                repo,
                "docs/specs/001-x",
                journal,
                "test-tail-only",
                gate_tasks,
                3,  # max_workers
                live.DEFAULT_AGENT,
                None,  # model
                60,  # timeout
                None,  # role_models
                None,  # role_agents
                None,  # fallback_agent
                None,  # tier_map
                None,  # purpose_tier_map
                None,  # fallback_chain
                None,  # effort
                None,  # run_budget
                spawn=fake,
                only=["TASK-002"],
            )

            self.assertIsNotNone(result, "tail dispatch was a no-op; test setup is wrong")
            dispatched_ids = {tid for _role, tid in fake.calls}
            self.assertEqual(
                dispatched_ids, {"TASK-002"},
                f"tail dispatch re-drove task(s) outside --only: {dispatched_ids}",
            )

            by_id = {t["id"]: t for t in result["tasks"]}
            self.assertEqual(
                by_id["TASK-001"]["status"], "completed",
                "TASK-001 (outside --only) was reset instead of pre-marked completed",
            )
            self.assertIn(by_id["TASK-002"]["status"], ("done", "reviewing"))

    def test_already_completed_tail_task_is_noop(self):
        """Regression: a tail (kind: e2e/cleanup) task already marked
        `status: completed` on disk before this run started (e.g. a checkbox
        ticked to reflect work merged outside the orchestrator) must be
        skipped, not crash. Before the fix, `orchestrate.TERMINAL` didn't
        include "completed", so `drive()`'s `while status not in TERMINAL`
        guard let it into the loop body and crashed on
        `ROLE_BY_STATUS["completed"]` -- confirmed live 2026-08-27 on
        worktrail's own `consolidate-operator-config-into-routing` run,
        `KeyError: 'completed'`."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt", status="completed"),
                    "TASK-002": _fm(
                        "TASK-002", "src/task-002.txt", deps="TASK-001",
                        kind="cleanup", status="completed",
                    ),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake = FakeSpawn()

            # Mirrors the real incident: the scheduler's own in-memory task
            # list (what gates `tail_held_out_task_ids`) still shows TASK-002
            # as "pending" -- its own status hasn't been reloaded since disk
            # was ticked. No `only` restriction here: `live_run_real`'s fresh
            # reload picks up TASK-002's on-disk "completed" status directly
            # (the real run's other path to the same crash: a tail task
            # already ticked before the run started, independent of --only).
            gate_tasks = [
                {"id": "TASK-001", "status": "completed", "kind": "impl", "deps": []},
                {"id": "TASK-002", "status": "pending", "kind": "cleanup", "deps": ["TASK-001"]},
            ]

            result = live._dispatch_pending_tail(
                repo,
                "docs/specs/001-x",
                journal,
                "test-tail-already-completed",
                gate_tasks,
                3,  # max_workers
                live.DEFAULT_AGENT,
                None,  # model
                60,  # timeout
                None,  # role_models
                None,  # role_agents
                None,  # fallback_agent
                None,  # tier_map
                None,  # purpose_tier_map
                None,  # fallback_chain
                None,  # effort
                None,  # run_budget
                spawn=fake,
                only=None,
            )

            self.assertEqual(fake.calls, [], "an already-completed tail task must not spawn a worker")
            if result is not None:
                by_id = {t["id"]: t for t in result["tasks"]}
                self.assertEqual(by_id["TASK-002"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()

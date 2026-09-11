#!/usr/bin/env python3
"""Tests for missing-context auto-recovery (design D1-D4 of
quarantine-missing-context-auto-recovery).

A worker report that would land a task in `failed`/`escalated` while naming a
sibling task's file that is absent from the task worktree but merged to the
live base after the fork is an orchestrator defect: the task is reset to
pending, its stale worktree and branch removed, and it is re-dispatched from
a fresh stacked worktree -- once per task per run, journaled as an auditable
`missing_context_auto_recovery` event.

All git fixtures are real throwaway repos; spawns are injected (no `claude -p`).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch, live, spawnlib

SPEC_REL = "docs/specs/001-x"
SIBLING_FILE = "bin/sync-doc.py"
OTHER_SIBLING_FILE = "bin/other.py"


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True
    )


def _commit_all(cwd: Path, msg: str) -> str:
    _run(cwd, "add", "-A")
    _run(cwd, "commit", "-q", "--allow-empty", "-m", msg)
    return _run(cwd, "rev-parse", "HEAD").stdout.strip()


def _write(root: Path, rel: str, content: str) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)


def _task_md(tid: str, files: list[str]) -> str:
    return (
        "---\n"
        f"id: {tid}\n"
        "status: pending\n"
        "dependencies: []\n"
        f"files: [{', '.join(files)}]\n"
        "kind: impl\n"
        "---\nbody\n"
    )


def _init_repo(root: Path, sibling_files: list[str] | None = None) -> Path:
    """Repo on `main` with two independent devkit tasks: TASK-001 declares the
    sibling file(s); TASK-002 declares src/foo.py."""
    repo = root / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "t@t")
    _run(repo, "config", "user.name", "T")
    _write(
        repo,
        f"{SPEC_REL}/tasks/TASK-001.md",
        _task_md("TASK-001", sibling_files or [SIBLING_FILE]),
    )
    _write(repo, f"{SPEC_REL}/tasks/TASK-002.md", _task_md("TASK-002", ["src/foo.py"]))
    _write(repo, "README.md", "x\n")
    _commit_all(repo, "init")
    return repo


def _fork_worktree(repo: Path, task_id: str = "TASK-002") -> Path:
    wt = repo.parent / f"{repo.name}-worktrees" / f"001-x-{task_id.lower()}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(
        repo, "worktree", "add", "-q", "-b", f"001-x/{task_id.lower()}", str(wt), "main"
    )
    return wt


def _merge_to_base(repo: Path, rel: str, content: str = "print('hi')\n") -> str:
    """Commit `rel` on main in the primary checkout AFTER the task forked."""
    _write(repo, rel, content)
    return _commit_all(repo, f"merge {rel}")


def _tasks() -> tuple[dict, dict, dict]:
    t1 = {"id": "TASK-001", "status": "pending", "files": [SIBLING_FILE]}
    t2 = {"id": "TASK-002", "status": "implementing", "files": ["src/foo.py"]}
    return t1, t2, {t["id"]: t for t in (t1, t2)}


def _failed_report(paths: list[str]) -> dict:
    return {
        "task": "TASK-002",
        "step": "implement",
        "status": "failed",
        "missing_context": paths,
        "context_quality": "insufficient",
    }


class MissingContextRecoveryDetection(unittest.TestCase):
    """Design D1: the trigger is evidence-based and narrow."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _init_repo(Path(self._tmp.name))
        self.wt = _fork_worktree(self.repo)
        self.t1, self.task, self.by_id = _tasks()

    def tearDown(self):
        self._tmp.cleanup()

    def _detect(self, paths, task=None, base="main"):
        return live._missing_context_recovery(
            task or self.task,
            _failed_report(paths),
            self.wt,
            self.by_id,
            self.repo,
            "origin",
            base,
        )

    def test_sibling_file_merged_after_fork_qualifies(self):
        sha = _merge_to_base(self.repo, SIBLING_FILE)
        self.assertFalse((self.wt / SIBLING_FILE).exists())
        hit = self._detect([SIBLING_FILE])
        self.assertIsNotNone(hit)
        self.assertEqual(hit["paths"], [SIBLING_FILE])
        self.assertEqual(hit["sibling_tasks"], ["TASK-001"])
        self.assertEqual(hit["base_ref"], "main")
        self.assertEqual(hit["base_sha"], sha)

    def test_path_present_in_worktree_does_not_qualify(self):
        _merge_to_base(self.repo, SIBLING_FILE)
        _write(self.wt, SIBLING_FILE, "local\n")
        self.assertIsNone(self._detect([SIBLING_FILE]))

    def test_path_not_declared_by_sibling_does_not_qualify(self):
        _merge_to_base(self.repo, "docs/undeclared.md", "x\n")
        self.assertIsNone(self._detect(["docs/undeclared.md"]))

    def test_own_declared_file_does_not_count_as_sibling(self):
        _merge_to_base(self.repo, "src/foo.py")
        self.assertIsNone(self._detect(["src/foo.py"]))

    def test_sibling_not_yet_on_base_does_not_qualify(self):
        self.assertIsNone(self._detect([SIBLING_FILE]))

    def test_empty_blob_on_base_does_not_qualify(self):
        _merge_to_base(self.repo, SIBLING_FILE, "")
        self.assertIsNone(self._detect([SIBLING_FILE]))

    def test_unresolvable_base_ref_does_not_fire(self):
        _merge_to_base(self.repo, SIBLING_FILE)
        self.assertIsNone(self._detect([SIBLING_FILE], base="no-such-branch"))
        self.assertIsNone(
            live._missing_context_recovery(
                self.task,
                _failed_report([SIBLING_FILE]),
                self.wt,
                self.by_id,
                self.repo,
                None,
                None,
            )
        )

    def test_malformed_paths_are_ignored(self):
        _merge_to_base(self.repo, SIBLING_FILE)
        self.assertIsNone(
            self._detect([str(self.repo / SIBLING_FILE), "bin/sync doc.py", "", "../x"])
        )

    def test_once_only_guard(self):
        _merge_to_base(self.repo, SIBLING_FILE)
        self.task["_missing_context_recovered"] = True
        self.assertIsNone(self._detect([SIBLING_FILE]))

    def test_mixed_list_returns_only_qualifying_paths(self):
        _merge_to_base(self.repo, SIBLING_FILE)
        _write(self.wt, "src/present.py", "x\n")
        hit = self._detect([SIBLING_FILE, "src/present.py", "docs/nope.md"])
        self.assertEqual(hit["paths"], [SIBLING_FILE])

    def test_would_land_terminal_predicts_failed_and_escalated_only(self):
        rep = _failed_report([SIBLING_FILE])
        self.assertTrue(live._would_land_terminal(self.task, "implement", rep))
        ok = {**rep, "status": "success"}
        self.assertFalse(live._would_land_terminal(self.task, "implement", ok))
        review = {**ok, "review_status": "FAILED"}
        self.assertFalse(
            live._would_land_terminal({**self.task, "retry_count": 0}, "review", review)
        )
        self.assertTrue(
            live._would_land_terminal({**self.task, "retry_count": 2}, "review", review)
        )
        self.assertFalse(
            live._would_land_terminal(
                self.task, "review", {**ok, "review_status": None}
            )
        )


class MissingContextRecoveryApply(unittest.TestCase):
    """Design D2/D4: journal shape, task reset, worktree/branch removal, replay."""

    def _hit(self):
        return {
            "paths": [SIBLING_FILE],
            "sibling_tasks": ["TASK-001"],
            "base_ref": "main",
            "base_sha": "abc123",
        }

    def test_apply_journals_event_and_resets_task_without_terminal_status(self):
        t1, task, _ = _tasks()
        task.update(
            {
                "retry_count": 2,
                "_extra_reads": ["x"],
                "_scope_escalated": True,
                "_scope_escalation_files": ["y"],
                "_scope_pending": True,
            }
        )
        entries: list = []
        actives = {"TASK-002": {"role": "implement"}}
        recorded = []
        rep = _failed_report([SIBLING_FILE])
        rep["head_sha"] = "deadbeef"
        live._apply_missing_context_recovery(
            tasks=[t1, task],
            entries=entries,
            actives=actives,
            record_fn=lambda: recorded.append(len(entries)),
            task=task,
            role="implement",
            rep=rep,
            hit=self._hit(),
            t0=1.0,
            t1=2.0,
            agent="claude",
        )
        self.assertEqual(recorded, [2], "persisted once, after both entries")
        trig, event = entries
        self.assertTrue(trig["auto_recovered"])
        self.assertNotIn("terminal_status", trig["report"])
        self.assertEqual(trig["report"]["status"], "failed")
        self.assertEqual(trig["agent"], "claude")
        self.assertEqual(event["event"], "missing_context_auto_recovery")
        self.assertEqual(event["task"], "TASK-002")
        self.assertEqual(event["paths"], [SIBLING_FILE])
        self.assertEqual(event["sibling_tasks"], ["TASK-001"])
        self.assertEqual(event["base_ref"], "main")
        self.assertEqual(event["base_sha"], "abc123")
        self.assertEqual(event["role"], "implement")
        self.assertEqual(event["category"], "orchestrator_defect")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["retry_count"], 0)
        self.assertTrue(task["_missing_context_recovered"])
        for k in (
            "_extra_reads",
            "_scope_escalated",
            "_scope_escalation_files",
            "_scope_pending",
        ):
            self.assertNotIn(k, task)
        self.assertNotIn("TASK-002", actives)

    def test_remove_worktree_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            wt = _fork_worktree(repo)
            _write(wt, "src/attempt1.py", "x\n")
            _commit_all(wt, "attempt 1")
            self.assertTrue(wt.exists())
            live._remove_task_worktree_and_branch(repo, wt, "001-x", "TASK-002")
            self.assertFalse(wt.exists())
            self.assertFalse(live._branch_exists(repo, "001-x/task-002"))
            self.assertNotIn(str(wt), _run(repo, "worktree", "list").stdout)

    def test_replay_restores_pending_and_guard(self):
        t1, task, _ = _tasks()
        task["status"] = "pending"
        journal = {
            "entries": [
                {
                    "task": "TASK-002",
                    "role": "implement",
                    "report": {"status": "failed", "head_sha": "deadbeef"},
                    "auto_recovered": True,
                },
                {
                    "event": "missing_context_auto_recovery",
                    "task": "TASK-002",
                    "paths": [SIBLING_FILE],
                    "sibling_tasks": ["TASK-001"],
                    "category": "orchestrator_defect",
                },
            ]
        }
        live.reconcile_from_journal([t1, task], journal)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["retry_count"], 0)
        self.assertTrue(task["_missing_context_recovered"])
        # The deleted branch's head must not be expected on the fresh fork.
        self.assertEqual(live._journaled_task_heads(journal["entries"]), {})
        self.assertEqual(
            live.journal_foreign_task_ids(journal["entries"], [t1, task]), set()
        )

    def test_replay_never_downgrades_a_done_task(self):
        t1, task, _ = _tasks()
        task["status"] = "done"
        journal = {
            "entries": [{"event": "missing_context_auto_recovery", "task": "TASK-002"}]
        }
        live.reconcile_from_journal([t1, task], journal)
        self.assertEqual(task["status"], "done")

    def test_clear_tasks_finds_nothing_terminal_for_recovered_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            jp = live.journal_path_for(repo, SPEC_REL)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(
                json.dumps(
                    {
                        "spec_id": "001-x",
                        "entries": [
                            {
                                "task": "TASK-002",
                                "role": "implement",
                                "report": {"status": "failed"},
                                "auto_recovered": True,
                            },
                            {
                                "event": "missing_context_auto_recovery",
                                "task": "TASK-002",
                                "category": "orchestrator_defect",
                            },
                        ],
                    }
                )
            )
            before = jp.read_text()
            self.assertEqual(live.clear_tasks(repo, SPEC_REL, ["TASK-002"]), 1)
            self.assertEqual(jp.read_text(), before)


# --------------------------------------------------------------------------- #
# End-to-end through live_run_real's drive()
# --------------------------------------------------------------------------- #
def _report(task_id, role, sha, *, status="success", review_status=None, missing=None):
    rs = json.dumps(review_status)
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"{status}",'
            f'"head_sha":"{sha[:8]}","review_status":{rs},"tests":"passed",'
            f'"context_quality":"insufficient","missing_context":{json.dumps(missing or [])}}}\n```'
        ),
        usage={},
    )


class RecoverySpawn:
    """TASK-001 succeeds. TASK-002's first implement simulates the sibling
    merging to main behind its back and reports failed naming the file; its
    second implement (fresh fork) sees the file and succeeds."""

    def __init__(self, repo: Path, second_missing: list[str] | None = None):
        self.repo = repo
        self.second_missing = second_missing
        self.calls: list = []
        self.task2_implements = 0
        self.fresh_fork_had_sibling = None
        self.fresh_fork_had_attempt1 = None

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append((task["id"], role))
        head = _run(wt, "rev-parse", "HEAD").stdout.strip()
        if role == dispatch.ROLE_REVIEW:
            return _report(task["id"], role, head, review_status="PASSED")
        if task["id"] == "TASK-001":
            _write(wt, SIBLING_FILE, "from task 1\n")
            return _report(task["id"], role, _commit_all(wt, "t1"))
        self.task2_implements += 1
        if self.task2_implements == 1:
            assert not (wt / SIBLING_FILE).exists()
            _write(wt, "src/attempt1.py", "partial\n")
            sha = _commit_all(wt, "attempt 1")
            _merge_to_base(self.repo, SIBLING_FILE)
            return _report(
                task["id"], role, sha, status="failed", missing=[SIBLING_FILE]
            )
        self.fresh_fork_had_sibling = (wt / SIBLING_FILE).exists()
        self.fresh_fork_had_attempt1 = (wt / "src/attempt1.py").exists()
        if self.second_missing:
            for rel in self.second_missing:
                _merge_to_base(self.repo, rel)
            return _report(
                task["id"], role, head, status="failed", missing=self.second_missing
            )
        _write(wt, "src/foo.py", "done\n")
        return _report(task["id"], role, _commit_all(wt, "attempt 2"))


class MissingContextRecoveryEndToEnd(unittest.TestCase):
    def _run(self, tmp: Path, spawn: RecoverySpawn):
        journal_path = tmp / "run-001-x.json"
        result = live.live_run_real(
            spawn.repo,
            SPEC_REL,
            max_workers=1,
            out_cassette=str(journal_path),
            run_id="test-recovery",
            spawn=spawn,
            remote="origin",
            base="main",
        )
        task = next(t for t in result["tasks"] if t["id"] == "TASK-002")
        return task, json.loads(journal_path.read_text())

    def test_failed_report_naming_merged_sibling_file_recovers_and_redispatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            spawn = RecoverySpawn(repo)
            task, journal = self._run(Path(tmp), spawn)
        self.assertEqual(spawn.task2_implements, 2)
        self.assertTrue(spawn.fresh_fork_had_sibling, "fresh fork carries sibling file")
        self.assertFalse(spawn.fresh_fork_had_attempt1, "stale branch was deleted")
        self.assertEqual(task["status"], "done")
        self.assertTrue(task["_missing_context_recovered"])
        entries = journal["entries"]
        events = [
            e for e in entries if e.get("event") == "missing_context_auto_recovery"
        ]
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["task"], "TASK-002")
        self.assertEqual(ev["paths"], [SIBLING_FILE])
        self.assertEqual(ev["sibling_tasks"], ["TASK-001"])
        self.assertEqual(ev["base_ref"], "main")
        self.assertEqual(ev["category"], "orchestrator_defect")
        self.assertEqual(ev["role"], "implement")
        self.assertTrue(ev["base_sha"])
        trig = [e for e in entries if e.get("auto_recovered")]
        self.assertEqual(len(trig), 1)
        self.assertEqual(trig[0]["task"], "TASK-002")
        self.assertNotIn("terminal_status", trig[0]["report"])
        self.assertEqual(entries.index(trig[0]) + 1, entries.index(ev))
        self.assertFalse(
            any(
                (e.get("report") or {}).get("terminal_status")
                in ("failed", "escalated")
                for e in entries
                if e.get("task") == "TASK-002"
            )
        )

    def test_second_qualifying_report_is_an_ordinary_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(
                Path(tmp), sibling_files=[SIBLING_FILE, OTHER_SIBLING_FILE]
            )
            spawn = RecoverySpawn(repo, second_missing=[OTHER_SIBLING_FILE])
            task, journal = self._run(Path(tmp), spawn)
        self.assertEqual(spawn.task2_implements, 2)
        self.assertEqual(task["status"], "failed")
        events = [
            e
            for e in journal["entries"]
            if e.get("event") == "missing_context_auto_recovery"
        ]
        self.assertEqual(len(events), 1)
        terminal = [
            e
            for e in journal["entries"]
            if e.get("task") == "TASK-002"
            and (e.get("report") or {}).get("terminal_status") == "failed"
        ]
        self.assertEqual(len(terminal), 1)


if __name__ == "__main__":
    unittest.main()

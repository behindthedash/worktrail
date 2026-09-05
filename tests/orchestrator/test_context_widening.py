#!/usr/bin/env python3
"""Integration test for adaptive context read-widening.

When a review worker reports context_quality=insufficient with non-empty
missing_context, drive() stages those paths as task["_extra_reads"] so
LiveSpawn injects them into the next fix prompt. This file verifies:

  1. insufficient review → missing_context reaches the fix dispatch
  2. sufficient review with populated missing_context → fix is NOT widened
  3. too_much review → fix is NOT widened

Uses an injected RecordingSpawn (no real claude -p) against a throwaway
git repo so tests are hermetic and fast.
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
    dispatch,
    live,
    spawnlib,
)


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "docs" / "specs" / "001-x" / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    fm = (
        "---\n"
        "id: TASK-001\n"
        "status: pending\n"
        "dependencies: []\n"
        "files: [src/foo.py]\n"
        "kind: impl\n"
        "---\nbody\n"
    )
    (repo / "docs" / "specs" / "001-x" / "tasks" / "TASK-001.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


def _commit_file(wt: Path, name: str, content: str) -> str:
    f = Path(wt) / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", name],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _report(
    task_id: str,
    role: str,
    sha: str,
    *,
    review_status=None,
    context_quality="sufficient",
    missing_context=None,
) -> spawnlib.SpawnResult:
    rs = f'"{review_status}"' if review_status else "null"
    mc = json.dumps(missing_context or [])
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":{rs},'
            f'"context_quality":"{context_quality}","missing_context":{mc}}}\n```'
        ),
        usage={},
    )


class _BaseRecordingSpawn:
    """Base spawn that records what _extra_reads was set on the task at fix time."""

    def __init__(self):
        self.calls: list = []
        self.fix_extra_reads_seen: list = []

    def _review_count(self) -> int:
        return sum(1 for c in self.calls if c == "review")

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append(role)
        # Capture whatever drive() staged before the fix spawn
        if role == dispatch.ROLE_FIX:
            self.fix_extra_reads_seen = list(task.get("_extra_reads") or [])
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if role in ("implement", "fix"):
            sha = _commit_file(wt, "foo.py", f"{role}-{len(self.calls)}\n")
        return self._report_for(task["id"], role, sha)

    def _report_for(self, task_id: str, role: str, sha: str) -> spawnlib.SpawnResult:
        raise NotImplementedError


class InsufficientReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED with context_quality=insufficient + missing_context."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="insufficient",
                missing_context=["src/helper.py", "tests/test_helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class SufficientReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED but context_quality=sufficient (missing_context populated but ignored)."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="sufficient",
                missing_context=["src/helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class TooMuchReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED with context_quality=too_much."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="too_much",
                missing_context=["src/helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


def _add_committed_file(repo: Path, rel: str, content: str = "x\n") -> None:
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", f"add {rel}"],
        check=True,
        capture_output=True,
    )


class ReviewFailedEscalationSpawn(_BaseRecordingSpawn):
    """First review: FAILED with `missing_context` naming an existing file
    outside the task's declared scope -- design D5's reviewer-triggered
    escalation, distinct from a fixer's own failure."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="sufficient",
                missing_context=["src/helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class FixerSuccessEscalationSpawn(_BaseRecordingSpawn):
    """First review FAILED (no escalation paths); the fix succeeds but itself
    names a missing_context path -- design D5's "all fix reports" trigger,
    not gated on the fix having failed."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(task_id, role, sha, review_status="FAILED")
        if role == "fix":
            return spawnlib.SpawnResult(
                text=(
                    f'```json\n{{"task":"{task_id}","step":"fix","status":"success",'
                    f'"head_sha":"{sha[:8]}","review_status":null,'
                    f'"tests":"passed","missing_context":["src/helper.py"]}}\n```'
                ),
                usage={},
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class ThirdStrikeEscalationSpawn(_BaseRecordingSpawn):
    """Reviews 1-2 FAIL with no missing_context (ordinary strikes); review 3
    -- which would trip the 3-strikes circuit breaker (escalated) -- FAILS
    naming a valid missing path. The widened scope must rescue it back to
    "fixing" instead (design D5), and review 4 then passes."""

    def _report_for(self, task_id, role, sha):
        if role == "review":
            n = self._review_count()
            if n == 3:
                return _report(
                    task_id,
                    role,
                    sha,
                    review_status="FAILED",
                    missing_context=["src/helper.py"],
                )
            if n < 3:
                return _report(task_id, role, sha, review_status="FAILED")
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class TestContextWidening(unittest.TestCase):
    def test_scope_escalation_journal_resumes_at_widened_fix(self):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        journal = {
            "entries": [
                {
                    "task": "TASK-001",
                    "role": "fix",
                    "report": {"status": "failed"},
                    "scope_escalated": True,
                    "scope_added_files": ["src/helper.py"],
                }
            ]
        }

        live.reconcile_from_journal([task], journal)

        self.assertEqual(task["status"], "fixing")
        self.assertEqual(task["files"], ["src/foo.py", "src/helper.py"])
        self.assertTrue(task["_scope_escalated"])

    def test_failed_fix_can_widen_once_to_existing_noncolliding_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "helper.py").write_text("x\n")
            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            report = {"status": "failed", "missing_context": ["src/helper.py"]}
            self.assertEqual(
                live._scope_escalation_files(
                    task, report, wt, {task["id"]: task}, failed=True
                ),
                ["src/helper.py"],
            )
            task["_scope_escalated"] = True
            self.assertEqual(
                live._scope_escalation_files(
                    task, report, wt, {task["id"]: task}, failed=True
                ),
                [],
            )

    def test_scope_widening_rejects_inflight_file_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "helper.py").write_text("x\n")
            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            other = {
                "id": "TASK-002",
                "status": "implementing",
                "files": ["src/helper.py"],
            }
            report = {"status": "failed", "missing_context": ["src/helper.py"]}
            self.assertEqual(
                live._scope_escalation_files(
                    task,
                    report,
                    wt,
                    {task["id"]: task, other["id"]: other},
                    failed=True,
                ),
                [],
            )

    def _run(self, spawn_cls):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal = str(Path(tmp) / "run-001-x.json")
            spawn = spawn_cls()
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal,
                run_id="test-widening",
                spawn=spawn,
            )
            return spawn

    def test_insufficient_review_widens_fix_reads(self):
        """context_quality=insufficient → missing_context staged for next fix dispatch."""
        spawn = self._run(InsufficientReviewSpawn)
        self.assertIn(
            "src/helper.py",
            spawn.fix_extra_reads_seen,
            "src/helper.py must be staged as extra read for fix after insufficient review",
        )
        self.assertIn(
            "tests/test_helper.py",
            spawn.fix_extra_reads_seen,
            "tests/test_helper.py must be staged as extra read for fix after insufficient review",
        )

    def test_sufficient_review_does_not_widen(self):
        """context_quality=sufficient must NOT widen fix reads, even if missing_context is set."""
        spawn = self._run(SufficientReviewSpawn)
        self.assertEqual(
            spawn.fix_extra_reads_seen,
            [],
            "sufficient context_quality must not stage extra reads for fix",
        )

    def test_too_much_review_does_not_widen(self):
        """context_quality=too_much must NOT widen fix reads."""
        spawn = self._run(TooMuchReviewSpawn)
        self.assertEqual(
            spawn.fix_extra_reads_seen,
            [],
            "too_much context_quality must not stage extra reads for fix",
        )

    def _run_with_helper_file(self, spawn_cls):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _add_committed_file(repo, "src/helper.py")
            journal_path = Path(tmp) / "run-001-x.json"
            spawn = spawn_cls()
            result = live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=str(journal_path),
                run_id="test-widening",
                spawn=spawn,
            )
            task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
            journal = json.loads(journal_path.read_text())
            return spawn, task, journal

    def test_review_failed_report_with_paths_escalates_scope(self):
        """A FAILED review naming an existing out-of-scope path widens the
        fix's file scope (design D5), not only a fixer's own failure."""
        spawn, task, _ = self._run_with_helper_file(ReviewFailedEscalationSpawn)
        self.assertIn("src/helper.py", task.get("files") or [])
        self.assertTrue(task.get("_scope_escalated"))
        self.assertIn("src/helper.py", spawn.fix_extra_reads_seen)

    def test_fixer_success_report_with_paths_escalates_scope(self):
        """A fix report carrying missing_context escalates even when its own
        status is success -- design D5 drops the failed-fix precondition."""
        _spawn, task, _ = self._run_with_helper_file(FixerSuccessEscalationSpawn)
        self.assertIn("src/helper.py", task.get("files") or [])
        self.assertTrue(task.get("_scope_escalated"))

    def test_review_escalation_never_fires_twice(self):
        """Once a task has escalated, a later FAILED review naming the same
        path must not escalate again (still bounded to one widening)."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "helper.py").write_text("x\n")
            task = {
                "id": "TASK-001",
                "status": "reviewing",
                "files": ["src/foo.py", "src/helper.py"],
                "_scope_escalated": True,
            }
            report = {
                "status": "success",
                "review_status": "FAILED",
                "missing_context": ["src/helper.py"],
            }
            self.assertEqual(
                live._scope_escalation_files(
                    task, report, wt, {task["id"]: task}, failed=True
                ),
                [],
            )

    def test_review_escalation_strike_refunded(self):
        """A FAILED review that escalates must not cost a retry strike --
        `retry_count` is restored to its pre-transition value."""
        _spawn, task, _ = self._run_with_helper_file(ReviewFailedEscalationSpawn)
        self.assertEqual(task.get("retry_count"), 0)

    def test_third_strike_review_with_paths_returns_to_fixing_not_escalated(self):
        """A FAILED review that would trip the 3-strikes circuit breaker is
        rescued back to "fixing" instead of "escalated" when it names a valid
        missing-context path (design D5)."""
        _spawn, task, _ = self._run_with_helper_file(ThirdStrikeEscalationSpawn)
        # escalated is terminal -- reaching "done" proves the would-be 3rd
        # strike was rescued back to "fixing" rather than tripping the breaker.
        self.assertEqual(task["status"], "done")

    def test_scope_escalation_journal_entry_carries_scope_escalated_files(self):
        """The journal entry for a scope-escalating step names the new key
        `scope_escalated_files` (not the legacy `scope_added_files`)."""
        _spawn, _task, journal = self._run_with_helper_file(ReviewFailedEscalationSpawn)
        hit = next(
            e
            for e in journal["entries"]
            if e.get("task") == "TASK-001" and e.get("scope_escalated")
        )
        self.assertEqual(hit["scope_escalated_files"], ["src/helper.py"])
        self.assertNotIn("scope_added_files", hit)

    def test_resume_from_review_scope_escalation_entry_restores_scope_and_fixing(
        self,
    ):
        """reconcile_from_journal replays a review-role scope-escalation entry
        (new key) identically to a fix-role one: scope widened, status fixing."""
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        journal = {
            "entries": [
                {
                    "task": "TASK-001",
                    "role": "review",
                    "report": {"status": "success", "review_status": "FAILED"},
                    "scope_escalated": True,
                    "scope_escalated_files": ["src/helper.py"],
                }
            ]
        }

        live.reconcile_from_journal([task], journal)

        self.assertEqual(task["status"], "fixing")
        self.assertEqual(task["files"], ["src/foo.py", "src/helper.py"])
        self.assertTrue(task["_scope_escalated"])

    def test_scope_escalation_excludes_gitignored_path(self):
        """A missing_context path that exists but matches a .gitignore pattern
        is excluded from scope escalation and returns []."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            # Initialize as a git repo
            subprocess.run(["git", "init", "-q", str(wt)], check=True)
            subprocess.run(
                ["git", "-C", str(wt), "config", "user.email", "t@t"], check=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "config", "user.name", "T"], check=True
            )
            # Create .gitignore with cache pattern
            gitignore = wt / ".gitignore"
            gitignore.write_text(".claude/tsc-cache/\n")
            subprocess.run(
                ["git", "-C", str(wt), "add", ".gitignore"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", "add gitignore"],
                check=True,
                capture_output=True,
            )
            # Create a file under the gitignored directory
            cache_dir = wt / ".claude" / "tsc-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "cache.json").write_text("x\n")

            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            report = {
                "status": "failed",
                "missing_context": [".claude/tsc-cache/cache.json"],
            }
            self.assertEqual(
                live._scope_escalation_files(
                    task, report, wt, {task["id"]: task}, failed=True
                ),
                [],
            )

    def test_scope_escalation_excludes_gitignored_but_keeps_tracked(self):
        """A missing_context list with one gitignored path and one ordinary path
        returns only the ordinary tracked path."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            # Initialize as a git repo
            subprocess.run(["git", "init", "-q", str(wt)], check=True)
            subprocess.run(
                ["git", "-C", str(wt), "config", "user.email", "t@t"], check=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "config", "user.name", "T"], check=True
            )
            # Create .gitignore with cache pattern
            gitignore = wt / ".gitignore"
            gitignore.write_text(".claude/tsc-cache/\n")
            # Create both a gitignored file and a tracked file
            cache_dir = wt / ".claude" / "tsc-cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "cache.json").write_text("x\n")
            src_dir = wt / "src"
            src_dir.mkdir()
            (src_dir / "helper.py").write_text("y\n")
            # Commit gitignore and helper.py
            subprocess.run(
                ["git", "-C", str(wt), "add", "-A"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", "init"],
                check=True,
                capture_output=True,
            )

            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            report = {
                "status": "failed",
                "missing_context": [".claude/tsc-cache/cache.json", "src/helper.py"],
            }
            result = live._scope_escalation_files(
                task, report, wt, {task["id"]: task}, failed=True
            )
            self.assertEqual(result, ["src/helper.py"])


if __name__ == "__main__":
    unittest.main()

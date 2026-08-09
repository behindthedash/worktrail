#!/usr/bin/env python3
"""Sequential-scheduler (_full_real_inner) tail-dispatch coverage.

Companion to test_pipeline_e2e.py::E2ETailDispatchTest. PR #235 fixed
kind:e2e/cleanup tail-task dispatch in BOTH full-real schedulers via a shared
`_dispatch_pending_tail()` helper, but the regression tests it shipped
(E2ETailDispatchTest) only exercise the --pipeline scheduler path, since
that's what the original bug report used. The sequential/non-pipeline
`_full_real_inner` path got the identical fix (same helper, same
integrate_complete gating) but had zero direct test coverage proving it
actually dispatches a tail task -- on a fresh run or a cold resume.

`_full_real_inner` has no `_spawn`/`_integrate_one` test-injection seams like
`_pipeline_scheduler` does, so these tests patch at the module-function level
instead: `live.LiveSpawn` (constructed internally whenever a `live_run_real`
call's `spawn=` is None -- true for both of `_full_real_inner`'s own fan-out
calls) and `verify.verify_and_cleanup` (real gh/git calls). The fresh-run case
additionally mocks `integrate._git`/`integrate.subprocess.run` since no
pre-existing journal means `finish_real()` runs for real.

Run: python3 test_full_real_tail_dispatch.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402
from worktrail.orchestrator import verify  # noqa: E402

Proc = namedtuple("Proc", "returncode stdout stderr")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _init_repo_with_tail(root: Path, with_origin: bool = False) -> Path:
    """3 impl tasks (TASK-001 root, TASK-002/003 depend on it) + TASK-004
    (kind:e2e, depends on both TASK-002/003) -- mirrors
    test_pipeline_e2e.py::_init_repo_with_tail's topology.

    Branch is created as 'main' directly (not the system git default, which
    on this machine is 'master') so `base="main"` -- the value every test in
    this file passes -- names a real local ref.

    with_origin: also create a local bare repo and wire it up as `origin`
    with `main` pushed, so finish_real()'s real `git push` (fresh-run case;
    only `gh` calls are faked, everything else is real git) has somewhere to
    land group branches.
    """
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)
    tasks_fm = {
        "TASK-001": (
            "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
            "files: [src/task-001.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-002": (
            "---\nid: TASK-002\nstatus: pending\ndependencies: [TASK-001]\n"
            "files: [src/task-002.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-003": (
            "---\nid: TASK-003\nstatus: pending\ndependencies: [TASK-001]\n"
            "files: [src/task-003.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-004": (
            "---\nid: TASK-004\nstatus: pending\ndependencies: [TASK-002, TASK-003]\n"
            "files: [src/task-004.txt]\nkind: e2e\nreview: skip\n---\nbody\n"
        ),
    }
    for tid, fm in tasks_fm.items():
        (spec_dir / f"{tid}.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True, capture_output=True,
    )
    if with_origin:
        bare = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True
        )
    return repo


def _fake_report(task_id: str, role: str, sha: str = "deadbeef") -> spawnlib.SpawnResult:
    rs = '"PASSED"' if role == "review" else "null"
    text = (
        f'```json\n{{"task":"{task_id}","step":"{role}",'
        f'"status":"success","head_sha":"{sha}","review_status":{rs}}}\n```'
    )
    return spawnlib.SpawnResult(text=text, usage={})


class FakeSpawn:
    """Makes a real git commit per task and returns a valid report-back."""

    def __init__(self):
        self.calls: list = []
        self._lock = threading.Lock()

    def __call__(self, role, task, wt):
        tid = task["id"]
        with self._lock:
            self.calls.append((tid, role))
        if role in ("implement", "fix"):
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"feat({tid})"],
                check=True, capture_output=True,
            )
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()[:8] or "00000000"
        return _fake_report(tid, role, sha)


def _fake_verify_and_cleanup(*_a, **_kw) -> dict:
    return {"merged": [], "quarantined": {}, "self_merged": {}, "automerge_evidence": {}}


_REAL_SUBPROCESS_RUN = subprocess.run


class FakeGh:
    """Fakes `gh` CLI calls only; every other subprocess.run() call (git, in
    particular) passes through to the real implementation.

    `unittest.mock.patch("worktrail.orchestrator.integrate.subprocess.run", ...)`
    patches the `run` attribute on the ONE shared `subprocess` module object
    every file's `import subprocess` binds to -- it is not scoped to
    integrate.py's own calls. live.py's `_git()` (used for the real fan-out
    worktrees) calls the same `subprocess.run`, so a naive always-fake
    dispatcher silently breaks real git operations too. Only `gh` needs
    faking here (no real GitHub in a test): git itself runs for real against
    the local repo + local bare `origin` remote _init_repo_with_tail sets up,
    so this test exercises finish_real()'s actual worktree/merge/push
    mechanics, not a re-implementation of them.
    """

    def __init__(self):
        self._n = 0

    def __call__(self, cmd, *args, **kwargs):
        if cmd and cmd[0] == "gh":
            if cmd[1:3] == ["pr", "view"]:
                return Proc(1, "", "no pr")
            if cmd[1:3] == ["pr", "list"]:
                return Proc(0, "[]", "")
            if cmd[1:3] == ["pr", "create"]:
                self._n += 1
                return Proc(0, f"https://github.com/o/r/pull/{self._n}\n", "")
            return Proc(0, "", "")
        return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)


def _journal_entry(tid: str, role: str, review_status=None) -> dict:
    return {
        "task": tid,
        "role": role,
        "report": {
            "status": "success",
            "head_sha": "abc123",
            "tests": "passed",
            "review_status": review_status,
            "critical_issues": 0,
            "major_issues": 0,
            "notes": "ok",
        },
    }


def _all_done_entries() -> list:
    entries = []
    for tid in ("TASK-001", "TASK-002", "TASK-003"):
        entries.extend([
            _journal_entry(tid, "implement"),
            _journal_entry(tid, "review", review_status="PASSED"),
            _journal_entry(tid, "cleanup"),
        ])
    return entries


class SequentialTailDispatchTest(unittest.TestCase):
    """`full-real` (sequential/default, i.e. NOT --pipeline) must dispatch
    kind:e2e/cleanup tail tasks once every impl group has integrated -- the
    same guarantee E2ETailDispatchTest proves for --pipeline, but through
    `_full_real_inner`'s own default code path."""

    def test_tail_task_dispatched_after_all_groups_integrate_fresh_run(self):
        """Fresh run, no prior journal: TASK-004 (kind:e2e) is driven once its
        deps (TASK-002/003) are done, within the same _full_real_inner call."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp), with_origin=True)
            spawn = FakeSpawn()

            with unittest.mock.patch.object(live, "LiveSpawn", return_value=spawn):
                with unittest.mock.patch.object(
                    verify, "verify_and_cleanup", side_effect=_fake_verify_and_cleanup
                ):
                    with unittest.mock.patch(
                        "worktrail.orchestrator.integrate.subprocess.run",
                        side_effect=FakeGh(),
                    ):
                        live._full_real_inner(
                            str(repo),
                            "docs/specs/001-x",
                            remote="origin",
                            base="main",
                            max_workers=3,
                            timeout=30,
                            pipeline=False,
                        )

            tail_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-004"]
            self.assertTrue(
                tail_calls,
                f"tail task TASK-004 was never dispatched; spawn.calls={spawn.calls}",
            )

    def test_tail_task_dispatched_on_resume_after_prior_process_merged_everything(self):
        """Reproduces the reported bug for the SEQUENTIAL scheduler: a fresh
        process resumes a journal where every impl group already reached
        MERGED (integrate_complete True, PR#238's fix populating group_branch
        from the journal) but the tail task has no journal entries at all --
        it must still be dispatched instead of full-real bailing at 'nothing
        to assemble' or silently skipping the tail task forever."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp))
            # Pre-merged invariant: TASK-001/002/003 journal-marked done with no
            # branch materialized here -- commit their declared files so
            # TASK-004's dependency-file guard sees them present (mirrors
            # E2ETailDispatchTest's identical resume-test setup).
            (repo / "src").mkdir(parents=True, exist_ok=True)
            for tid in ("task-001", "task-002", "task-003"):
                (repo / "src" / f"{tid}.txt").write_text(f"{tid}\n")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "pre-merged for resume test"],
                check=True, capture_output=True,
            )

            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps({
                "run_id": "full-test",
                "spec_id": "001-x",
                "entries": _all_done_entries(),
                "groups": {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "MERGED",
                    },
                    "feature-1": {
                        "pr_url": "http://pr/feature-1",
                        "head_branch": "full-test/feature-1",
                        "state": "MERGED",
                    },
                    "feature-2": {
                        "pr_url": "http://pr/feature-2",
                        "head_branch": "full-test/feature-2",
                        "state": "MERGED",
                    },
                },
                "integrate_complete": True,
            }))

            spawn = FakeSpawn()

            with unittest.mock.patch.object(live, "LiveSpawn", return_value=spawn):
                with unittest.mock.patch.object(
                    verify, "verify_and_cleanup", side_effect=_fake_verify_and_cleanup
                ):
                    result = live._full_real_inner(
                        str(repo),
                        "docs/specs/001-x",
                        remote="origin",
                        base="main",
                        max_workers=3,
                        timeout=30,
                        pipeline=False,
                    )

            self.assertIsNotNone(result, "_full_real_inner must not bail out early")
            impl_calls = [(tid, role) for tid, role in spawn.calls if tid != "TASK-004"]
            self.assertEqual(
                impl_calls, [],
                f"already-done impl tasks must not be re-driven; spawn.calls={spawn.calls}",
            )
            tail_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-004"]
            self.assertTrue(
                tail_calls,
                "tail task TASK-004 must be dispatched on a resume where every impl "
                f"group already merged; spawn.calls={spawn.calls}",
            )


if __name__ == "__main__":
    unittest.main()

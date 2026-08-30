#!/usr/bin/env python3
"""End-to-end integration tests for per-task timeout (spec 011-orchestrator-per-task-timeout).

Drives live_run_real with a ScriptedSpawn-style fake worker against a throwaway
git repo. Verifies that the loader correctly projects timeout: frontmatter into
the task dict and that each spawned worker receives its correct effective timeout
across the full pipeline. No real claude -p, no network calls.

AC coverage (integration layer):
  AC-2  — run-level timeout override (--timeout 600 equivalent)
  AC-5  — per-task timeout: 2700 > run default → effective 2700
  AC-6  — per-task timeout: 900 < run default → effective 900
  AC-7  — no per-task timeout → effective equals run default (1800)
  AC-8  — concurrent tasks each get their own timeout; no bleed
  AC-10 — malformed timeout: abc → graceful fallback to run default, no crash
  AC-12 — verify._make_live_spawn signature unaffected (no task param, default 1800)

External checkpoint (FR-7):
  orchestrate.py check exits 0 (golden fixtures unchanged).
"""

import inspect
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worktrail.orchestrator import (
    live,
    spawnlib,
)
from worktrail.orchestrator import verify as verify_module

# ---------------------------------------------------------------------------
# Repo and frontmatter helpers
# ---------------------------------------------------------------------------


def _init_repo(root: Path, tasks_fm: dict) -> Path:
    """Create a throwaway git repo with tasks/TASK-*.md from the given dict."""
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-timeout-e2e" / "tasks"
    spec_dir.mkdir(parents=True)
    for tid, fm_text in tasks_fm.items():
        (spec_dir / f"{tid}.md").write_text(fm_text)
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


def _fm(tid: str, *, timeout: str = "") -> str:
    """Minimal TASK-*.md frontmatter. review:skip so only implement is spawned."""
    lines = [
        f"id: {tid}",
        "status: pending",
        "dependencies: []",
        f"files: [src/{tid.lower()}.txt]",
        "kind: impl",
        "review: skip",
    ]
    if timeout:
        lines.append(f"timeout: {timeout}")
    return "---\n" + "\n".join(lines) + "\n---\n\nbody\n"


# ---------------------------------------------------------------------------
# TimingSpawn: injectable fake that records effective timeout per task
# ---------------------------------------------------------------------------


class TimingSpawn:
    """ScriptedSpawn-style fake worker.

    Records ``effective_timeout = task.get("timeout") or self.run_level_timeout``
    for each call. This mirrors the LiveSpawn.__call__ formula and verifies that
    the loader correctly projected the frontmatter timeout into the task dict
    before the fan-out dispatched the task.
    """

    def __init__(self, run_level_timeout: int = 1800) -> None:
        self.run_level_timeout = run_level_timeout
        self.recorded: dict = {}  # task_id -> effective_timeout
        self._lock = threading.Lock()

    def __call__(self, role: str, task: dict, wt: Path) -> "spawnlib.SpawnResult":
        effective = task.get("timeout") or self.run_level_timeout
        with self._lock:
            self.recorded[task["id"]] = effective

        if role == "implement":
            f = wt / "src" / f"{task['id'].lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{task['id']}\n")
            subprocess.run(
                ["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"feat({task['id']})"],
                check=True,
                capture_output=True,
            )

        sha = (
            subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()[:8]
            or "00000000"
        )
        rs = '"PASSED"' if role == "review" else "null"
        return spawnlib.SpawnResult(
            text=(
                f'```json\n{{"task":"{task["id"]}","step":"{role}",'
                f'"status":"success","head_sha":"{sha}","review_status":{rs}}}\n```'
            ),
            usage={},
        )


def _run(
    repo: Path,
    tmp: str,
    spawn: TimingSpawn,
    run_level_timeout: int = 1800,
    max_workers: int = 3,
) -> dict:
    """Run live_run_real with the given fake spawn; return its result."""
    return live.live_run_real(
        repo,
        "docs/specs/001-timeout-e2e",
        max_workers=max_workers,
        out_cassette=str(Path(tmp) / "journal.json"),
        run_id="e2e-timeout-test",
        timeout=run_level_timeout,
        spawn=spawn,
    )


# ---------------------------------------------------------------------------
# AC-5, AC-6, AC-7: correct effective timeout reaches each worker
# ---------------------------------------------------------------------------


class EffectiveTimeoutFanoutTest(unittest.TestCase):
    """Full fan-out with 3 tasks (timeout:2700, timeout:900, no timeout) verifies
    each worker receives its correct effective timeout (AC-5, AC-6, AC-7)."""

    def test_per_task_timeouts_applied_correctly(self):
        """AC-5/6/7: 2700, 900, and run-default tasks each get the right cap."""
        tasks_fm = {
            "TASK-001": _fm("TASK-001", timeout="2700"),  # AC-5: override up
            "TASK-002": _fm("TASK-002", timeout="900"),  # AC-6: override down
            "TASK-003": _fm("TASK-003"),  # AC-7: no override → run default
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp), tasks_fm)
            spawn = TimingSpawn(run_level_timeout=1800)
            res = _run(repo, tmp, spawn, run_level_timeout=1800)

            self.assertEqual(res["done"], 3, "all 3 tasks must complete")
            self.assertEqual(
                spawn.recorded.get("TASK-001"),
                2700,
                "AC-5: task with timeout:2700 must receive effective timeout 2700",
            )
            self.assertEqual(
                spawn.recorded.get("TASK-002"),
                900,
                "AC-6: task with timeout:900 must receive effective timeout 900",
            )
            self.assertEqual(
                spawn.recorded.get("TASK-003"),
                1800,
                "AC-7: task with no timeout must receive run-level default 1800",
            )


# ---------------------------------------------------------------------------
# AC-8: concurrent tasks each get their own timeout — no bleed
# ---------------------------------------------------------------------------


class ConcurrencyNoBleedTest(unittest.TestCase):
    """Two dependency-free tasks with different per-task timeouts run in the same
    concurrent batch; each must receive its own effective timeout (AC-8)."""

    def test_concurrent_tasks_each_get_own_effective_timeout(self):
        """AC-8: TASK-001(900) and TASK-002(3600) concurrent; no timeout leaks between them."""
        tasks_fm = {
            "TASK-001": _fm("TASK-001", timeout="900"),
            "TASK-002": _fm("TASK-002", timeout="3600"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp), tasks_fm)
            spawn = TimingSpawn(run_level_timeout=1800)
            _run(repo, tmp, spawn, run_level_timeout=1800, max_workers=2)

            self.assertEqual(
                spawn.recorded.get("TASK-001"),
                900,
                "AC-8: TASK-001 must see its own timeout 900, not the other task's",
            )
            self.assertEqual(
                spawn.recorded.get("TASK-002"),
                3600,
                "AC-8: TASK-002 must see its own timeout 3600, not the other task's",
            )
            # The run_level_timeout on the spawn object must not have been mutated.
            self.assertEqual(
                spawn.run_level_timeout,
                1800,
                "AC-8: shared run_level_timeout must not be mutated by concurrent calls",
            )


# ---------------------------------------------------------------------------
# AC-2 / SEF: run-level timeout (--timeout 600 equivalent) honored for tasks
# without a per-task field
# ---------------------------------------------------------------------------


class RunLevelTimeoutOverrideTest(unittest.TestCase):
    """AC-2: when timeout=600 is passed to live_run_real (simulating --timeout 600
    or ORCH_WORKER_TIMEOUT=600), a task with no per-task timeout gets 600."""

    def test_task_without_per_task_timeout_uses_run_level_600(self):
        """AC-2: timeout=600 as run level → task with no timeout: field gets 600."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp), {"TASK-001": _fm("TASK-001")})
            spawn = TimingSpawn(run_level_timeout=600)
            _run(repo, tmp, spawn, run_level_timeout=600, max_workers=1)

            self.assertEqual(
                spawn.recorded.get("TASK-001"),
                600,
                "AC-2: no per-task timeout → worker must use run-level timeout 600",
            )

    def test_per_task_timeout_overrides_run_level_600(self):
        """AC-2 + AC-5: even when run level is 600, a per-task timeout:2700 wins."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp), {"TASK-001": _fm("TASK-001", timeout="2700")})
            spawn = TimingSpawn(run_level_timeout=600)
            _run(repo, tmp, spawn, run_level_timeout=600, max_workers=1)

            self.assertEqual(
                spawn.recorded.get("TASK-001"),
                2700,
                "AC-5+AC-2: per-task timeout:2700 must win over run-level 600",
            )


# ---------------------------------------------------------------------------
# AC-10 / SEF: malformed timeout value → graceful fallback, no crash
# ---------------------------------------------------------------------------


class MalformedTimeoutFallbackTest(unittest.TestCase):
    """AC-10: timeout: abc in frontmatter → loader normalises to None → run default used;
    other tasks in the same spec are unaffected."""

    def test_malformed_timeout_fallback_no_crash(self):
        """AC-10: timeout:abc → effective timeout is run default 1800; run does not crash."""
        tasks_fm = {
            "TASK-001": _fm("TASK-001", timeout="abc"),  # malformed
            "TASK-002": _fm("TASK-002"),  # normal, no timeout
        }
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp), tasks_fm)
            spawn = TimingSpawn(run_level_timeout=1800)
            try:
                res = _run(repo, tmp, spawn, run_level_timeout=1800, max_workers=2)
            except Exception as exc:  # noqa: BLE001
                self.fail(
                    f"AC-10: malformed timeout must not crash the fan-out; got {exc!r}"
                )

            self.assertEqual(
                res["done"], 2, "both tasks must complete despite malformed timeout"
            )
            self.assertEqual(
                spawn.recorded.get("TASK-001"),
                1800,
                "AC-10: malformed timeout:abc → loader yields None → effective is run default 1800",
            )
            self.assertEqual(
                spawn.recorded.get("TASK-002"),
                1800,
                "AC-10: normal task with no timeout still uses run default 1800",
            )


# ---------------------------------------------------------------------------
# AC-12: verify._make_live_spawn is unaffected by per-task timeout logic
# ---------------------------------------------------------------------------


class VerifyStageUnaffectedTest(unittest.TestCase):
    """AC-12: verify._make_live_spawn defaults timeout to 1800 and has no 'task'
    parameter — the verify stage never reads per-task frontmatter."""

    def test_make_live_spawn_timeout_default_is_1800(self):
        """AC-12: verify._make_live_spawn has timeout defaulting to 1800."""
        sig = inspect.signature(verify_module._make_live_spawn)
        self.assertIn(
            "timeout",
            sig.parameters,
            "worktrail.orchestrator.verify._make_live_spawn must have a 'timeout' parameter",
        )
        actual_default = sig.parameters["timeout"].default
        self.assertEqual(
            actual_default,
            1800,
            f"AC-12: verify._make_live_spawn timeout default must be 1800, got {actual_default}",
        )

    def test_make_live_spawn_has_no_task_parameter(self):
        """AC-12: verify._make_live_spawn has no 'task' parameter (per-task override absent)."""
        sig = inspect.signature(verify_module._make_live_spawn)
        self.assertNotIn(
            "task",
            sig.parameters,
            "AC-12: verify._make_live_spawn must not have a 'task' parameter — "
            "verify workers are not governed by per-task timeout",
        )

    def test_make_live_spawn_parameters_unchanged(self):
        """AC-12: verify._make_live_spawn has no task-driven parameters."""
        sig = inspect.signature(verify_module._make_live_spawn)
        param_names = set(sig.parameters.keys())
        unexpected = param_names - {"model", "timeout", "agent"}
        self.assertEqual(
            unexpected,
            set(),
            f"AC-12: unexpected params in verify._make_live_spawn: {unexpected}; "
            "per-task timeout override must not bleed into the verify stage",
        )


# ---------------------------------------------------------------------------
# FR-7 golden checkpoint: orchestrate.py check exits 0
# ---------------------------------------------------------------------------


class GoldenCheckTest(unittest.TestCase):
    """FR-7: orchestrate.py check exits 0 — default raised to 1800 did not drift the golden."""

    def test_orchestrate_check_exits_zero(self):
        """worktrail.orchestrator.orchestrate.py check must exit 0 (no golden drift from the timeout changes)."""
        _here = Path(__file__).resolve().parent
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.orchestrate", "check"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(_here),
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Golden drift detected!\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

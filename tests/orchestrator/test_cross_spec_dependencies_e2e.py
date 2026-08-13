#!/usr/bin/env python3
"""End-to-end tests for cross-spec task dependencies (spec 025, TASK-007).

Drives the whole cross-spec dependency seam together -- loader.py parsing of
`external-dependencies:` -> live.py precheck resolution/reporting ->
coordinator.py frontier gating -> git worktree stacking -> dispatch.py
worker-prompt read hints -- against REAL on-disk sibling spec folders (no
mocked resolution), matching the real-world datalena PR #1936 098/099 shape
the spec itself cites as evidence.

Covers (see docs/specs/025-cross-spec-task-dependencies/tasks/TASK-007.md):
  - Primary flow: precheck resolution/reporting (AC-009, AC-010) -> frontier
    gating on a same-spec dep AND an external dep together (AC-004, AC-012) ->
    the sibling flipping to done unblocks the frontier on a later tick without
    restarting anything (AC-013) -> worktree stacking on the sibling's
    materialized branch (AC-016) -> falling back to the base ref when that
    branch is not materialized (AC-017) -> the implement-role worker prompt
    naming the sibling's delivered files as read hints (AC-015).
  - Alternative paths: a typo'd sibling spec-id folder (AC-005, AC-009,
    AC-012), a sibling spec-id folder that exists but has no matching task
    file (AC-006), a malformed `external-dependencies:` entry cross-checked
    at the full-flow level (AC-003), reciprocal cross-spec dependencies
    converging independently per spec run (matches the spec's Non-Goals), and
    a permanently-blocked task surfacing through the existing `fanout_failed`
    heartbeat sidecar (AC-014).
  - Supplemental: running the same fixture through precheck twice produces
    stable output (idempotency sanity check).
  - Regression (AC-019 [SEF]): a spec with zero `external-dependencies:`
    entries anywhere is unaffected by every new code path.

Run: python3 scripts/test_cross_spec_dependencies_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import dispatch  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.taskformats.devkit import source as loader  # noqa: E402
from worktrail.orchestrator import progress  # noqa: E402

_HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Fixture helpers (mirrors conventions in test_precheck_e2e.py,
# test_pipeline_e2e.py, test_routing_e2e.py, and test_dependency_fixes.py)
# --------------------------------------------------------------------------- #


def _live_module_argv() -> list:
    """`python -m worktrail.orchestrator.live`, not direct file execution --
    `live.py` uses package-relative imports, so it must run as a module."""
    return [sys.executable, "-m", "worktrail.orchestrator.live"]


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")


def _branch_with_file(
    repo: Path, branch: str, fname: str, content: str, start: str = "HEAD"
) -> None:
    """Create `branch` off `start` carrying a single new (possibly nested) file,
    then return to the base branch -- this is what materializes a sibling
    task's real deliverable branch for worktree-stacking assertions.

    Stages ONLY `fname` (never `add -A`): this repo's working tree also holds
    untracked real TASK-*.md fixture files used for live resolution elsewhere
    in the same test, and a broad `add -A` would sweep them into this branch's
    commit and then delete them on the `checkout` back to base (they are not
    part of base's tree)."""
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-B", branch, start)
    target = repo / fname
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git(repo, "add", "--", fname)
    _git(repo, "commit", "-q", "-m", f"add {fname}")
    _git(repo, "checkout", "-q", base)


def _write_task_file(
    repo: Path,
    spec_id: str,
    task_id: str,
    status: str = "pending",
    deps=None,
    external_deps=None,
    files=None,
    kind: str = "impl",
) -> Path:
    tasks_dir = repo / "docs" / "specs" / spec_id / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {task_id}",
        f'title: "Test task {task_id}"',
        f"spec: docs/specs/{spec_id}/spec.md",
        "lang: python",
        f"status: {status}",
        f"dependencies: {list(deps or [])}",
        f"files: {list(files or [])}",
        f"kind: {kind}",
    ]
    if external_deps is not None:
        lines.append(f"external-dependencies: {list(external_deps)}")
    lines.append("---")
    content = "\n".join(lines) + f"\n\n# {task_id}\n"
    path = tasks_dir / f"{task_id}.md"
    path.write_text(content)
    return path


def _set_status(path: Path, status: str) -> None:
    """Flip a task file's `status:` in place -- simulates the sibling task's
    own orchestrator run (or a concurrent invocation) completing, without
    restarting anything in the caller's process (REQ-013)."""
    new_lines = []
    for line in path.read_text().splitlines(keepends=True):
        if line.startswith("status:"):
            new_lines.append(f"status: {status}\n")
        else:
            new_lines.append(line)
    path.write_text("".join(new_lines))


def _run_precheck(repo: Path, spec_rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        _live_module_argv() + ["precheck", "--repo", str(repo), spec_rel],
        capture_output=True,
        text=True,
    )


def _load(repo: Path, spec_rel: str) -> list:
    """Load + annotate a spec's tasks exactly like a real orchestrator tick
    does immediately before calling `coordinator.runnable_frontier`."""
    _, tasks = loader.load_spec(str(repo / spec_rel))
    live._annotate_external_deps(repo, tasks)
    return tasks


def _by_id(tasks: list) -> dict:
    return {t["id"]: t for t in tasks}


def _frontier_ids(tasks: list, max_workers: int = 5) -> set:
    return {t["id"] for t in coordinator.runnable_frontier(tasks, max_workers)}


# --------------------------------------------------------------------------- #
# Primary flow (happy path)
# --------------------------------------------------------------------------- #


class TestPrimaryFlowHappyPath(unittest.TestCase):
    """Two real sibling spec folders (098-x, 099-y), matching the spec's own
    098/099 real-world evidence shape: 099-y/TASK-038 declares a same-spec
    `dependencies:` entry on 099-y/TASK-037 AND an `external-dependencies:`
    entry on 098-x/TASK-036."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        _init_repo(self.repo)
        self.sibling_task = _write_task_file(
            self.repo,
            "098-x",
            "TASK-036",
            status="pending",
            files=["src/sibling.py"],
        )
        self.same_spec_dep = _write_task_file(
            self.repo,
            "099-y",
            "TASK-037",
            status="pending",
            files=["src/y037.py"],
        )
        self.gated_task = _write_task_file(
            self.repo,
            "099-y",
            "TASK-038",
            status="pending",
            deps=["TASK-037"],
            external_deps=["098-x/TASK-036"],
            files=["src/y038.py"],
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_primary_flow(self):
        # --- Step 2 (AC-009, AC-010): precheck against 099-y while the
        # sibling is still pending -- the resolved reference is reported as
        # non-blocking INFO, naming the reference and its recorded status. ---
        result = _run_precheck(self.repo, "docs/specs/099-y")
        self.assertIn("INFO: TASK-038", result.stdout)
        self.assertIn("098-x/TASK-036", result.stdout)
        self.assertIn("status=pending", result.stdout)

        # --- Step 3 (AC-004, AC-012): TASK-038 is excluded from the runnable
        # frontier while BOTH its same-spec dependency and its external
        # dependency are unsatisfied. ---
        tasks = _load(self.repo, "docs/specs/099-y")
        frontier = _frontier_ids(tasks)
        self.assertIn("TASK-037", frontier)
        self.assertNotIn("TASK-038", frontier)

        # Flip only the SAME-SPEC dependency -- still gated on the external one
        # (AC-004: satisfying one half must not unblock the task).
        _set_status(self.same_spec_dep, "done")
        tasks = _load(self.repo, "docs/specs/099-y")
        frontier = _frontier_ids(tasks)
        self.assertNotIn(
            "TASK-038",
            frontier,
            "a satisfied same-spec dependency must not unblock a task whose "
            "external-dependencies entry is still unsatisfied",
        )

        # --- Step 4 (AC-013): flip the sibling's on-disk status to done
        # (simulating its own orchestrator run completing) and recompute the
        # frontier -- TASK-038 now appears, without restarting anything. ---
        _set_status(self.sibling_task, "done")
        tasks = _load(self.repo, "docs/specs/099-y")
        by_id = _by_id(tasks)
        frontier = _frontier_ids(tasks)
        self.assertIn("TASK-038", frontier)

        # --- Step 5 (AC-016): materialize a git branch for the sibling and
        # dispatch TASK-038's worktree creation -- it must stack on that
        # branch. ---
        sibling_branch = "098-x/task-036"
        _branch_with_file(self.repo, sibling_branch, "src/sibling.py", "sibling = 1\n")

        wt = Path(self._tmp.name) / "wt-038"
        live.add_stacked_worktree(self.repo, "099-y", by_id["TASK-038"], by_id, wt)
        self.assertTrue(
            (wt / "src" / "sibling.py").exists(),
            "TASK-038's worktree did not stack on the sibling's materialized branch",
        )

        # --- Step 5 (AC-015): the implement-role worker prompt for TASK-038
        # lists the sibling's declared files as read hints. external_deps_by_ref
        # is built via live.build_external_deps_by_ref -- the real function
        # LiveSpawn's caller uses, not a hand-rolled reconstruction -- so this
        # proves the actual production wiring, not just build_worker_prompt's
        # own contract in isolation. ---
        ref = "098-x/TASK-036"
        external_deps_by_ref = live.build_external_deps_by_ref(self.repo, tasks)
        self.assertIn(ref, external_deps_by_ref)
        self.assertEqual(external_deps_by_ref[ref]["files"], ["src/sibling.py"])
        ctx = {
            "spec_id": "099-y",
            "spec_folder": "docs/specs/099-y/",
            "worktree_path": str(wt),
            "branch": "099-y/task-038",
            "base_commit": "HEAD",
        }
        prompt = dispatch.build_worker_prompt(
            dispatch.ROLE_IMPLEMENT,
            by_id["TASK-038"],
            ctx,
            by_id=by_id,
            external_deps_by_ref=external_deps_by_ref,
        )
        read_section = prompt[prompt.index("Read first, in order:") :]
        self.assertIn("src/sibling.py", read_section)
        self.assertIn(f"delivered by {ref}", read_section)

    def test_step6_falls_back_to_base_ref_without_materialized_branch(self):
        """Step 6 (AC-017): repeat worktree dispatch WITHOUT materializing the
        sibling's branch -- falls back to the base ref instead of stacking."""
        _set_status(self.same_spec_dep, "done")
        _set_status(self.sibling_task, "done")
        tasks = _load(self.repo, "docs/specs/099-y")
        by_id = _by_id(tasks)
        self.assertIn("TASK-038", _frontier_ids(tasks))

        wt = Path(self._tmp.name) / "wt-038-no-branch"
        live.add_stacked_worktree(self.repo, "099-y", by_id["TASK-038"], by_id, wt)
        self.assertTrue(wt.is_dir())
        self.assertFalse(
            (wt / "src" / "sibling.py").exists(),
            "worktree must NOT contain the sibling's file when its branch was "
            "never materialized -- it should fall back to the base ref",
        )


# --------------------------------------------------------------------------- #
# Alternative paths / error scenarios (from the spec's own tables)
# --------------------------------------------------------------------------- #


class TestUnresolvedSpecIdTypo(unittest.TestCase):
    """A typo'd sibling spec-id folder never resolves -- WARN + non-zero exit
    (AC-005, AC-009), and the frontier withholds the task indefinitely across
    repeated ticks (AC-012)."""

    def test_precheck_warns_and_frontier_withholds_indefinitely(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            _write_task_file(repo, "099-y", "TASK-038", external_deps=["098-x-typo/TASK-036"])

            result = _run_precheck(repo, "docs/specs/099-y")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WARN: TASK-038", result.stdout)
            self.assertIn("098-x-typo/TASK-036", result.stdout)

            for _tick in range(2):  # repeated ticks -- never resolves
                tasks = _load(repo, "docs/specs/099-y")
                self.assertNotIn("TASK-038", _frontier_ids(tasks))


class TestUnresolvedTaskIdMissing(unittest.TestCase):
    """A sibling spec-id folder that exists but has no matching task file
    resolves as unresolved, same as a missing spec-id folder (AC-006)."""

    def test_precheck_warns_when_sibling_task_file_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            (repo / "docs" / "specs" / "098-x" / "tasks").mkdir(parents=True)
            _write_task_file(repo, "099-y", "TASK-038", external_deps=["098-x/TASK-036"])

            result = _run_precheck(repo, "docs/specs/099-y")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WARN: TASK-038", result.stdout)
            self.assertIn("098-x/TASK-036", result.stdout)

            tasks = _load(repo, "docs/specs/099-y")
            self.assertNotIn("TASK-038", _frontier_ids(tasks))


class TestMalformedEntryFullFlow(unittest.TestCase):
    """Malformed `external-dependencies:` entry (AC-003), cross-checked here
    at the full-flow level: warned by precheck AND excluded from the
    frontier, never silently permitted to run."""

    def test_malformed_entry_warned_and_treated_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            _write_task_file(repo, "099-y", "TASK-038", external_deps=["not-a-valid-ref"])

            result = _run_precheck(repo, "docs/specs/099-y")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("WARN: TASK-038", result.stdout)
            self.assertIn("not-a-valid-ref", result.stdout)
            self.assertIn("malformed", result.stdout)

            tasks = _load(repo, "docs/specs/099-y")
            self.assertNotIn(
                "TASK-038",
                _frontier_ids(tasks),
                "a malformed external-dependencies entry must never be silently "
                "permitted to run",
            )


class TestReciprocalCrossSpecDependencies(unittest.TestCase):
    """Both directions of the verified real-world 098/099 shape (reciprocal
    cross-spec dependencies pointing at DIFFERENT specific tasks in each
    direction) converge correctly when each spec's precheck/frontier is
    exercised independently -- no combined cross-spec DAG required (matches
    the spec's Non-Goals)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        _init_repo(self.repo)
        # 098-x has a root task and a task gated on 099-y/TASK-038 (already done).
        _write_task_file(self.repo, "098-x", "TASK-036", status="pending")
        _write_task_file(self.repo, "098-x", "TASK-040", external_deps=["099-y/TASK-038"])
        # 099-y has a root task (the one 098-x/TASK-040 depends on) and a task
        # gated on 098-x/TASK-036 (still pending) -- a DIFFERENT specific task
        # in each direction, matching the real shape.
        _write_task_file(self.repo, "099-y", "TASK-038", status="done")
        _write_task_file(self.repo, "099-y", "TASK-039", external_deps=["098-x/TASK-036"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_spec_precheck_converges_independently(self):
        result_098 = _run_precheck(self.repo, "docs/specs/098-x")
        self.assertIn("INFO: TASK-040", result_098.stdout)
        self.assertIn("099-y/TASK-038", result_098.stdout)
        self.assertIn("status=done", result_098.stdout)

        result_099 = _run_precheck(self.repo, "docs/specs/099-y")
        self.assertIn("INFO: TASK-039", result_099.stdout)
        self.assertIn("098-x/TASK-036", result_099.stdout)
        self.assertIn("status=pending", result_099.stdout)

    def test_each_spec_frontier_converges_independently(self):
        tasks_098 = _load(self.repo, "docs/specs/098-x")
        frontier_098 = _frontier_ids(tasks_098)
        self.assertIn("TASK-036", frontier_098)
        self.assertIn(
            "TASK-040",
            frontier_098,
            "098-x/TASK-040's external dependency (099-y/TASK-038) is done -- "
            "it must be runnable",
        )

        tasks_099 = _load(self.repo, "docs/specs/099-y")
        frontier_099 = _frontier_ids(tasks_099)
        self.assertNotIn(
            "TASK-039",
            frontier_099,
            "099-y/TASK-039's external dependency (098-x/TASK-036) is still "
            "pending -- it must stay withheld",
        )


class TestStalledExternalDepSurfacesThroughHeartbeat(unittest.TestCase):
    """AC-014: a task blocked past the stalled-run detection threshold on an
    unresolved external dependency surfaces through the SAME `fanout_failed`
    heartbeat sidecar existing same-spec stalls use today -- exercised
    through the real `progress.set_phase()` write and `live.precheck()`'s own
    read-back, not a hand-built status dict."""

    def test_blocked_task_surfaces_and_precheck_warns_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            _write_task_file(
                repo,
                "099-y",
                "TASK-038",
                external_deps=["098-x-missing/TASK-036"],  # never resolves
            )
            tasks = _load(repo, "docs/specs/099-y")
            self.assertFalse(tasks[0]["external_deps_ok"])
            self.assertIn("098-x-missing/TASK-036", tasks[0]["external_deps_blockers"][0])

            journal_path = live.journal_path_for(repo, "docs/specs/099-y")
            # The detail shape a stalled run's fanout_failed heartbeat carries
            # (originally written by the deleted serial scheduler's
            # `_fanout_incomplete_detail`; legacy journals with this sidecar
            # still surface through precheck's read-back).
            progress.set_phase(
                journal_path,
                "fanout_failed",
                detail={
                    "failed_tasks": [],
                    "blocked_tasks": [
                        {
                            "id": "TASK-038",
                            "status": "pending",
                            "blocked_by": list(tasks[0]["external_deps_blockers"]),
                        }
                    ],
                },
            )

            # A resumed precheck must surface the stuck prior run -- the same
            # sidecar path an ordinary same-spec stall uses today.
            result = _run_precheck(repo, "docs/specs/099-y")
            self.assertIn("fanout_failed", result.stdout)
            self.assertIn("TASK-038", result.stdout)


class TestPrecheckIdempotent(unittest.TestCase):
    """Supplemental, non-blocking: running the same two-sibling-spec fixture
    through precheck twice in a row (no state change in between) produces
    stable, repeatable output for the read-only resolver."""

    def test_running_precheck_twice_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _init_repo(repo)
            _write_task_file(repo, "098-x", "TASK-036", status="pending")
            _write_task_file(repo, "099-y", "TASK-038", external_deps=["098-x/TASK-036"])

            first = _run_precheck(repo, "docs/specs/099-y")
            second = _run_precheck(repo, "docs/specs/099-y")

        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.returncode, second.returncode)


# --------------------------------------------------------------------------- #
# Regression check (AC-019 [SEF])
# --------------------------------------------------------------------------- #


class TestRegressionNoExternalDeps(unittest.TestCase):
    """AC-019 [SEF]: a spec fixture with zero `external-dependencies:` entries
    anywhere produces precheck/frontier/dispatch/worktree-stacking output
    byte-identical to pre-025 behavior."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        _init_repo(self.repo)
        _write_task_file(self.repo, "003-plain", "TASK-001", files=["src/one.py"])
        _write_task_file(
            self.repo, "003-plain", "TASK-002", deps=["TASK-001"], files=["src/two.py"]
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_precheck_output_unaffected(self):
        result = _run_precheck(self.repo, "docs/specs/003-plain")
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(result.returncode, 0)

    def test_frontier_unaffected_by_annotate_call(self):
        _, tasks_a = loader.load_spec(str(self.repo / "docs/specs/003-plain"))
        frontier_a = [t["id"] for t in coordinator.runnable_frontier(tasks_a, 5)]

        _, tasks_b = loader.load_spec(str(self.repo / "docs/specs/003-plain"))
        live._annotate_external_deps(self.repo, tasks_b)
        frontier_b = [t["id"] for t in coordinator.runnable_frontier(tasks_b, 5)]

        self.assertEqual(frontier_a, frontier_b)
        for t in tasks_b:
            self.assertNotIn(
                "external_deps_ok",
                t,
                "a task with no external_deps entries must " "never be annotated at all",
            )

    def test_dispatch_prompt_unaffected_by_external_deps_by_ref_param(self):
        _, tasks = loader.load_spec(str(self.repo / "docs/specs/003-plain"))
        by_id = _by_id(tasks)
        ctx = {
            "spec_id": "003-plain",
            "spec_folder": "docs/specs/003-plain/",
            "worktree_path": "/tmp/wt",
            "branch": "003-plain/task-002",
            "base_commit": "HEAD",
        }
        task = by_id["TASK-002"]
        prompt_without = dispatch.build_worker_prompt(
            dispatch.ROLE_IMPLEMENT, task, ctx, by_id=by_id
        )
        prompt_with_unrelated_ref = dispatch.build_worker_prompt(
            dispatch.ROLE_IMPLEMENT,
            task,
            ctx,
            by_id=by_id,
            external_deps_by_ref={
                "098-x/TASK-036": {"id": "TASK-036", "files": ["should-never-appear.py"]}
            },
        )
        self.assertEqual(prompt_without, prompt_with_unrelated_ref)

    def test_worktree_stacking_unaffected(self):
        _, tasks = loader.load_spec(str(self.repo / "docs/specs/003-plain"))
        by_id = _by_id(tasks)
        ref, extra = live.dependency_start_ref(self.repo, "003-plain", by_id["TASK-002"], by_id)
        self.assertEqual((ref, extra), ("HEAD", []))


class TestBuildExternalDepsByRef(unittest.TestCase):
    """Unit coverage for live.build_external_deps_by_ref -- the function that
    wires external-dependency read hints into LiveSpawn's real production
    call sites (live_run_real / live_run_pipeline), closing the gap where
    dispatch.build_worker_prompt's external_deps_by_ref param existed but was
    never populated by an actual caller."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        _init_repo(self.repo)
        _write_task_file(self.repo, "098-x", "TASK-036", files=["src/sibling.py"])
        _write_task_file(
            self.repo,
            "099-y",
            "TASK-038",
            external_deps=["098-x/TASK-036"],
            files=["src/y038.py"],
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _tasks(self):
        _, tasks_098 = loader.load_spec(str(self.repo / "docs/specs/098-x"))
        _, tasks_099 = loader.load_spec(str(self.repo / "docs/specs/099-y"))
        return tasks_098 + tasks_099

    def test_satisfied_ref_included_with_files(self):
        _set_status(self.repo / "docs/specs/098-x/tasks/TASK-036.md", "completed")
        result = live.build_external_deps_by_ref(self.repo, self._tasks())
        self.assertEqual(
            result, {"098-x/TASK-036": {"id": "TASK-036", "files": ["src/sibling.py"]}}
        )

    def test_unsatisfied_pending_ref_excluded(self):
        result = live.build_external_deps_by_ref(self.repo, self._tasks())
        self.assertEqual(result, {})

    def test_unresolvable_spec_id_excluded(self):
        _write_task_file(
            self.repo,
            "100-z",
            "TASK-001",
            external_deps=["999-nonexistent/TASK-001"],
        )
        _, tasks = loader.load_spec(str(self.repo / "docs/specs/100-z"))
        result = live.build_external_deps_by_ref(self.repo, tasks)
        self.assertEqual(result, {})

    def test_malformed_ref_excluded(self):
        _write_task_file(self.repo, "101-w", "TASK-001", external_deps=["not-a-valid-ref"])
        _, tasks = loader.load_spec(str(self.repo / "docs/specs/101-w"))
        result = live.build_external_deps_by_ref(self.repo, tasks)
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()

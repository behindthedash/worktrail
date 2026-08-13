#!/usr/bin/env python3
"""Lifecycle harness: drive the REAL full-real orchestrator end to end.

Every prior e2e suite injects fakes at the integrate/verify seams
(`_make_integrate_one`, `FakeVerifier`, patched `verify_and_cleanup`) — which
is exactly why the recurring production bug classes (journal state across
integrate/verify, CI-green detection, merge confirmation, resume after a dead
process) kept escaping to live runs: the code that broke was the code the
fakes replaced. This harness fakes ONLY two things that cannot run in CI:

  - the LLM worker (`live.LiveSpawn` → a scripted worker that makes real git
    commits), and
  - the `gh` binary (a PATH-level shim, `fake_gh.py`, backed by a JSON state
    file — so integrate/verify's real argument building, JSON parsing, and
    subprocess handling all execute).

Everything else is real: git pushes to a local bare `origin`, the real
`integrate.integrate_one` (group branches, PR creation, journal writes,
task-status commits), the real `verify` merge/confirm flow (the fake `gh pr
merge` genuinely advances the bare remote's base ref), the real journal
resume logic, and the real RunLock.

Matrix: {devkit, openspec} × {fresh, kill+resume}, on the pipelined engine
(full-real's only scheduler since scheduler-consolidation stage 2).

Run: pytest tests/orchestrator/lifecycle/ -q
"""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from worktrail.conductor import compile as conductor_compile
from worktrail.orchestrator import live, spawnlib, verify

_HERE = Path(__file__).resolve().parent
_FAKE_GH = _HERE / "fake_gh.py"

DEVKIT_SPEC_REL = "docs/specs/001-x"
OPENSPEC_SPEC_REL = "openspec/changes/001-x"


# --------------------------------------------------------------------------- #
# Environment: PATH-level fake gh + bare origin
# --------------------------------------------------------------------------- #

def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True
    )


def _install_fake_gh(tmp: Path, remote: Path) -> dict:
    """Write the `gh` shim + state file; return the env overrides to apply."""
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "gh"
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} {_FAKE_GH} \"$@\"\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    state = tmp / "gh-state.json"
    state.write_text(json.dumps({
        "remote": str(remote),
        "base": "main",
        "next_number": 1,
        "prs": {},
    }))
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_FAKE_STATE": str(state),
    }


def _publish_to_bare_origin(repo: Path, tmp: Path) -> Path:
    """Bare origin at a filesystem path that CONTAINS `github.com/fake/
    lifecycle.git`. Both owner/repo derivations in production code
    (`verify._derive_gh_repo`, `automerge_preflight.owner_repo_from_git`)
    parse the origin URL by splitting on the literal "github.com", so this
    path yields `fake/lifecycle` through the real code path while every git
    push/fetch stays a plain local filesystem operation. (An `insteadOf`
    rewrite doesn't work here: `git remote get-url` returns the REWRITTEN
    url, so derivation would see the bare path.)"""
    remote = tmp / "github.com" / "fake" / "lifecycle.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _git(repo, "branch", "-M", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "main")
    return remote


def _mk_devkit_repo(tmp: Path) -> Path:
    """base(TASK-001) ← feature-1(TASK-002), feature-2(TASK-003)."""
    repo = tmp / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)
    deps = {"TASK-001": "[]", "TASK-002": "[TASK-001]", "TASK-003": "[TASK-001]"}
    for tid, dep in deps.items():
        (spec_dir / f"{tid}.md").write_text(
            f"---\nid: {tid}\nstatus: pending\ndependencies: {dep}\n"
            f"files: [src/{tid.lower()}.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        )
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _mk_devkit_repo_with_tail(tmp: Path) -> Path:
    """base(TASK-001) ← feature-1(TASK-002), feature-2(TASK-003) ← e2e(TASK-004).

    Same base/feature topology as `_mk_devkit_repo`, plus a `kind: e2e` tail
    task depending on BOTH feature tasks -- the incident shape: `plan_groups`
    puts TASK-001 (2 transitive dependents) in its own BASE group and
    TASK-002/TASK-003 each in an independent FEATURE group, so by the time
    `_dispatch_pending_tail` fires for TASK-004, all three groups' PRs have
    been squash-merged and `verify.cleanup_group` has deleted all three task
    branches -- `dependency_start_ref` has nothing left to stack TASK-004 on.
    """
    repo = _mk_devkit_repo(tmp)
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    (spec_dir / "TASK-004.md").write_text(
        "---\nid: TASK-004\nstatus: pending\ndependencies: [TASK-002, TASK-003]\n"
        "files: [src/task-004.txt]\nkind: e2e\nreview: skip\n---\nbody\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add e2e tail task")
    return repo


OPENSPEC_TASKS_MD = (
    "## 1. Setup\n\n"
    "- [ ] 1.1 First thing\n"
    "- [ ] 1.2 Second thing\n\n"
    "## 2. Other\n\n"
    "- [ ] 2.1 Independent thing\n"
)


def _openspec_file_for(tid: str) -> str:
    return f"src/task-{tid.replace('.', '-')}.txt"


def _mk_openspec_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    change = repo / "openspec" / "changes" / "001-x"
    change.mkdir(parents=True)
    (change / "tasks.md").write_text(OPENSPEC_TASKS_MD)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _precompile_openspec_runplan(repo: Path) -> None:
    """Populate the DEFAULT runplan cache with an injected compile spawn, so
    full-real's own `apply_run_plan` takes the real cache-hit path with no
    model call (the same state a prior `worktrail-compile` leaves behind)."""
    from worktrail.taskformats.openspec.source import OpenSpecTaskSource

    _, tasks = OpenSpecTaskSource(repo).load("001-x")
    rows = [
        {"id": t["id"], "files": [_openspec_file_for(t["id"])], "deps": t["deps"]}
        for t in tasks
    ]
    reply = "Plan:\n\n```json\n" + json.dumps({"tasks": rows}) + "\n```\n"
    conductor_compile.compile_run_plan(
        repo / OPENSPEC_SPEC_REL,
        tasks,
        spec_id="001-x",
        repo=repo,
        spawn=lambda prompt, cwd, timeout, log: reply,
    )


# --------------------------------------------------------------------------- #
# The scripted worker (replaces LiveSpawn only)
# --------------------------------------------------------------------------- #

class ScriptedWorker:
    """Real git commit per implement/fix; valid report-backs; optional hard
    process death at a chosen (task, role) — simulating a harness/machine kill
    mid-run, the scenario behind the resume bug class (#235/#238)."""

    def __init__(self, exit_on: "tuple[str, str] | None" = None):
        self.exit_on = exit_on
        self.calls: "list[tuple[str, str]]" = []
        self._lock = threading.Lock()

    def __call__(self, role, task, wt):
        tid = task["id"]
        with self._lock:
            self.calls.append((tid, role))
        if self.exit_on == (tid, role):
            os._exit(3)  # hard death: no cleanup, journal left as-is
        if role in ("implement", "fix"):
            declared = (task.get("files") or [_openspec_file_for(tid)])[0]
            f = Path(wt) / declared
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            _git(wt, "add", "-A")
            # Skip the commit when there is nothing staged: on a kill+resume,
            # the dead child may have committed this task's content but died
            # before its journal entry was written, so the resume legitimately
            # re-runs the task against a worktree that already has the work. A
            # real worker reports success there; an unconditional `git commit`
            # exits 1 ("nothing to commit") and wrongly fails the task (flaked
            # on CI in PR #258's run).
            if _git(wt, "diff", "--cached", "--quiet", check=False).returncode != 0:
                _git(wt, "commit", "-q", "-m", f"feat({tid})")
        sha = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()[:8] or "00000000"
        rs = '"PASSED"' if role == "review" else "null"
        return spawnlib.SpawnResult(
            text=(
                f'```json\n{{"task":"{tid}","step":"{role}","status":"success",'
                f'"head_sha":"{sha}","review_status":{rs}}}\n```'
            ),
            usage={},
        )


def _run_full_real(repo: Path, spec_rel: str, env: dict, *,
                   resume: bool = True, worker: "ScriptedWorker | None" = None) -> dict:
    """Invoke the real `_full_real_inner` with only LiveSpawn + sleep patched."""
    worker = worker or ScriptedWorker()
    with unittest.mock.patch.dict(os.environ, env):
        with unittest.mock.patch.object(live, "LiveSpawn", return_value=worker):
            with unittest.mock.patch.object(time, "sleep", lambda s: None):
                result = live._full_real_inner(
                    str(repo),
                    spec_rel,
                    remote="origin",
                    base="main",
                    max_workers=3,
                    timeout=60,
                    resume=resume,
                )
    result["_worker"] = worker
    return result


# --------------------------------------------------------------------------- #
# Shared assertions
# --------------------------------------------------------------------------- #

def _journal(repo: Path, spec_rel: str) -> dict:
    return json.loads(live.journal_path_for(repo, spec_rel).read_text())


def _remote_file(remote: Path, path: str) -> "str | None":
    r = subprocess.run(
        ["git", "-C", str(remote), "show", f"main:{path}"],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else None


class _LifecycleCase(unittest.TestCase):
    def assert_all_groups_merged(self, repo, spec_rel):
        journal = _journal(repo, spec_rel)
        states = {n: g["state"] for n, g in journal["groups"].items()}
        self.assertTrue(states, "journal has no groups")
        self.assertTrue(
            all(s == "MERGED" for s in states.values()),
            f"not all groups MERGED: {states}",
        )
        self.assertTrue(journal.get("integrate_complete"), "integrate_complete unset")


# --------------------------------------------------------------------------- #
# Fresh lifecycle: devkit format
# --------------------------------------------------------------------------- #

class DevkitFreshLifecycle(_LifecycleCase):
    def _run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_devkit_repo(tmp)
            remote = _publish_to_bare_origin(repo, tmp)
            env = _install_fake_gh(tmp, remote)

            _run_full_real(repo, DEVKIT_SPEC_REL, env)

            self.assert_all_groups_merged(repo, DEVKIT_SPEC_REL)
            # The work actually landed on the REMOTE base branch.
            for tid in ("task-001", "task-002", "task-003"):
                self.assertIsNotNone(
                    _remote_file(remote, f"src/{tid}.txt"),
                    f"src/{tid}.txt missing from remote main",
                )
            # The committed spec artifact records completion (PR #29 contract,
            # devkit shape) — the resume-boundary source of truth.
            for tid in ("TASK-001", "TASK-002", "TASK-003"):
                fm = _remote_file(remote, f"docs/specs/001-x/tasks/{tid}.md")
                self.assertIsNotNone(fm)
                self.assertIn("status: completed", fm)

    def test_pipeline(self):
        self._run()


# --------------------------------------------------------------------------- #
# Fresh lifecycle + fresh-rerun skip: openspec format
# --------------------------------------------------------------------------- #

class OpenspecFreshLifecycle(_LifecycleCase):
    def _run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_openspec_repo(tmp)
            remote = _publish_to_bare_origin(repo, tmp)
            env = _install_fake_gh(tmp, remote)
            _precompile_openspec_runplan(repo)

            _run_full_real(repo, OPENSPEC_SPEC_REL, env)

            self.assert_all_groups_merged(repo, OPENSPEC_SPEC_REL)
            tasks_md = _remote_file(remote, "openspec/changes/001-x/tasks.md")
            self.assertIsNotNone(tasks_md)
            for tid in ("1.1", "1.2", "2.1"):
                self.assertIn(f"- [x] {tid}", tasks_md, f"{tid} not ticked on remote main")

            # The user-visible resume contract end-to-end (PR #246's unit-level
            # proof, now through the REAL pipeline): sync the checkout to the
            # merged remote main, discard the journal, run --fresh — nothing
            # gets re-implemented, because the committed tasks.md says done.
            _git(repo, "fetch", "-q", "origin")
            _git(repo, "reset", "-q", "--hard", "origin/main")
            rerun = _run_full_real(
                repo, OPENSPEC_SPEC_REL, env, resume=False
            )
            implements = [
                c for c in rerun["_worker"].calls if c[1] in ("implement", "fix")
            ]
            self.assertEqual(
                implements, [],
                f"fresh rerun redispatched completed tasks: {implements}",
            )

    def test_pipeline(self):
        self._run()


# --------------------------------------------------------------------------- #
# Kill mid-fan-out, then resume — the #235/#238 incident shape, for real
# --------------------------------------------------------------------------- #

def _child_run(repo_s: str, spec_rel: str, env: dict):
    """Child-process body: dies via os._exit inside the worker on TASK-003."""
    worker = ScriptedWorker(exit_on=("TASK-003", "implement"))
    _run_full_real(Path(repo_s), spec_rel, env, worker=worker)


class KillAndResume(_LifecycleCase):
    def _run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_devkit_repo(tmp)
            remote = _publish_to_bare_origin(repo, tmp)
            env = _install_fake_gh(tmp, remote)

            ctx = multiprocessing.get_context("fork")
            child = ctx.Process(
                target=_child_run, args=(str(repo), DEVKIT_SPEC_REL, env)
            )
            child.start()
            child.join(timeout=120)
            self.assertFalse(child.is_alive(), "killed child did not exit")
            self.assertEqual(child.exitcode, 3, "child should die via os._exit(3)")

            # Resume in-process: must finish everything without re-implementing
            # what the dead run already completed.
            result = _run_full_real(repo, DEVKIT_SPEC_REL, env)

            self.assert_all_groups_merged(repo, DEVKIT_SPEC_REL)
            for tid in ("task-001", "task-002", "task-003"):
                self.assertIsNotNone(
                    _remote_file(remote, f"src/{tid}.txt"),
                    f"src/{tid}.txt missing from remote main after resume",
                )
            resumed_implements = [
                t for t, r in result["_worker"].calls if r == "implement"
            ]
            self.assertNotIn(
                "TASK-001", resumed_implements,
                "resume re-implemented TASK-001, which the dead run completed",
            )

    def test_pipeline(self):
        self._run()


# --------------------------------------------------------------------------- #
# Squash-merged dependency carry: tail e2e task whose dependencies' groups are
# squash-merged and cleaned up before tail dispatch (stacked-worktree-conflict-
# resolution-squash-merged-dependency-carry 1.1)
# --------------------------------------------------------------------------- #

class SquashMergedDependencyCarryLifecycle(_LifecycleCase):
    def _run(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_devkit_repo_with_tail(tmp)
            remote = _publish_to_bare_origin(repo, tmp)
            env = _install_fake_gh(tmp, remote)

            # Before the fix, TASK-004's dispatch (`_dispatch_pending_tail`, fired
            # only once every group is integrated/verified/merged and cleaned up)
            # raised `WorktreeMissingDependencyFileError`: TASK-002/TASK-003's
            # branches are gone by then, so `dependency_start_ref` falls back to
            # the run-start local base, which predates their squash-merges. This
            # call must complete without raising it.
            _run_full_real(repo, DEVKIT_SPEC_REL, env)

            # The three impl groups (base/feature-1/feature-2) really did merge
            # into the remote base -- the squash-merge boundary TASK-004's carry
            # must cross.
            for tid in ("task-001", "task-002", "task-003"):
                self.assertIsNotNone(
                    _remote_file(remote, f"src/{tid}.txt"),
                    f"src/{tid}.txt missing from remote main",
                )
            # TASK-004 itself reached done (a "cleanup" journal entry is only
            # ever written on the terminal transition, see cleanup_task_in_python).
            journal = _journal(repo, DEVKIT_SPEC_REL)
            tail_roles = {
                e["role"] for e in journal["entries"] if e.get("task") == "TASK-004"
            }
            self.assertIn("cleanup", tail_roles, f"TASK-004 never reached cleanup: {tail_roles}")
            # Its carry actually landed TASK-002/TASK-003's content in its own
            # worktree (not just skipped the dependency-file check some other
            # way) -- the direct proof the fix engaged.
            tail_wt = repo.parent / f"{repo.name}-worktrees" / "001-x-task-004"
            for tid in ("task-002", "task-003"):
                self.assertTrue(
                    (tail_wt / "src" / f"{tid}.txt").exists(),
                    f"TASK-004 worktree missing carried {tid}.txt",
                )

    def test_pipeline(self):
        self._run()


if __name__ == "__main__":
    unittest.main(verbosity=2)

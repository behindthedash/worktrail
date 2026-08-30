#!/usr/bin/env python3
"""Regression test for handoff 20260814-190035-worktrail-live-full-real-a.

Bug report: a task that completes (real commits on its branch) right before the
run budget triggers an exit can have its whole GROUP quarantined as
"budget_exhausted", even when the completing task's own status is terminal.
Observed live: task 4.2 (in group "feature-1", alongside a sibling task) had 3
real commits but its group stayed QUARANTINED with quarantine_reason=budget_exhausted
across two resumes.

This test constructs the minimal reproducer: a 3-task spec where TASK-001 is
BASE and TASK-002/TASK-003 land in ONE feature group (TASK-003 depends on
TASK-002, so plan_groups() unions them -- verified interactively against
coordinator.plan_groups()). A budget calibrated to let TASK-001 and TASK-002
complete but not TASK-003 reproduces: TASK-002 reaches "done" with a real git
commit, but its group is force-quarantined budget_exhausted because TASK-003
never ran. A second phase resumes with unlimited budget and asserts the group
self-heals (TASK-003 completes, the group integrates) -- proving that a PLAIN
resume already recovers this once TASK-003 gets its own budget, i.e. the
group-level force-quarantine at live.py's post-fanout `if budget_exceeded:`
branch is not what actually strands the work; it just needs a following resume
with adequate budget, exactly like a fully-untouched group would.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import (
    live,
    spawnlib,
)


def _init_repo(root: Path) -> Path:
    """3-task spec: TASK-001 (base), TASK-002 -> TASK-003 chained (one feature group)."""
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
            "---\nid: TASK-003\nstatus: pending\ndependencies: [TASK-002]\n"
            "files: [src/task-003.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
    }
    for tid, fm in tasks_fm.items():
        (spec_dir / f"{tid}.md").write_text(fm)
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


def _fake_report(
    task_id: str, role: str, sha: str = "deadbeef"
) -> spawnlib.SpawnResult:
    rs = '"PASSED"' if role == "review" else "null"
    text = (
        f'```json\n{{"task":"{task_id}","step":"{role}",'
        f'"status":"success","head_sha":"{sha}","review_status":{rs}}}\n```'
    )
    return spawnlib.SpawnResult(text=text, usage={})


class FakeSpawn:
    """Makes a git commit per task and returns a valid report-back.

    sleep_after: {task_id: seconds} -- deterministically consumes wall-clock
    time AFTER a task's real commit lands, so a small run_budget can be made
    to expire strictly between two ticks without relying on raw git-op timing.
    """

    def __init__(self, sleep_after=None):
        self.calls = []
        self._lock = threading.Lock()
        self.sleep_after = sleep_after or {}

    def __call__(self, role, task, wt):
        tid = task["id"]
        with self._lock:
            self.calls.append((tid, role))
        if role in ("implement", "fix"):
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            subprocess.run(
                ["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"feat({tid})"],
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
        delay = self.sleep_after.get(tid)
        if delay:
            time.sleep(delay)
        return _fake_report(tid, role, sha)


def _make_integrate_one(events=None):
    events = events if events is not None else []

    def integrate_one(
        g,
        repo,
        spec_id,
        tasks,
        remote,
        run_id,
        base,
        journal_path,
        status,
        group_branch,
        quarantined,
        **kwargs,
    ):
        name = g["name"]
        events.append(name)
        deliverable = [t for t in g["tasks"] if status.get(t) in ("done", "completed")]
        if not deliverable:
            quarantined[name] = f"no deliverable tasks in {name}"
            return None
        group_branch[name] = f"full-test/{name}"
        record_group = kwargs.get("_record_group")
        if record_group:
            record_group(name, f"http://fake-pr/{name}", group_branch[name], "OPEN")
        return (name, base, f"http://fake-pr/{name}")

    return integrate_one, events


class FakeVerifier:
    def __init__(self):
        self.calls = []

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
        with lock:
            self.calls.append(group["name"])
            merged.append(group["name"])


def _run(
    repo,
    journal_path,
    spawn,
    integrate_one,
    verifier,
    run_budget=None,
    resume=False,
    only=None,
):
    kwargs = {
        "repo": repo,
        "spec_rel": "docs/specs/001-x",
        "remote": "origin",
        "base": "main",
        "model": "haiku",
        "max_workers": 1,  # serialize ticks so the budget cut lands deterministically
        "timeout": 30,
        "resume": resume,
        "only": only,
        "role_models": None,
        "run_budget": run_budget,
        "journal_path": journal_path,
        "run_id": "full-test",
        "_spawn": spawn,
        "_integrate_one": integrate_one,
        "_make_verifier": lambda: verifier,
    }
    return live._pipeline_scheduler(**kwargs)


class PartialGroupBudgetExhaustionTest(unittest.TestCase):
    """TASK-002 (in a 2-task feature group with TASK-003) completes with a real
    commit; TASK-003 never gets budget. The group is force-quarantined
    budget_exhausted even though TASK-002 is terminal -- confirms the reported
    defect exists exactly as described."""

    def test_group_with_one_done_task_is_quarantined_budget_exhausted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            integrate_one, _events = _make_integrate_one()

            # Budget check happens at the TOP of each tick, before frontier is
            # computed. max_workers=1 serializes ticks; sleeping 1s AFTER
            # TASK-002's real commit lands (its own git ops are done first, so
            # its "done" status and journal entry are genuine) deterministically
            # pushes elapsed time past a 0.5s budget before TASK-003's tick.
            # The margin is wide (worktree creation for TASK-001 alone can take
            # tens of ms; 0.5s budget vs a 1s forced sleep on TASK-002 leaves no
            # realistic room for flakiness either direction).
            spawn = FakeSpawn(sleep_after={"TASK-002": 1.0})

            result = _run(
                repo,
                journal_path,
                spawn,
                integrate_one,
                FakeVerifier(),
                run_budget=0.5,
            )

            # TASK-002 must have been driven (real commit made) -- prove it's not
            # a "zero progress" quarantine.
            task002_calls = [c for c in spawn.calls if c[0] == "TASK-002"]
            self.assertTrue(
                task002_calls, "TASK-002 should have been dispatched and completed"
            )
            self.assertFalse(
                any(c[0] == "TASK-003" for c in spawn.calls),
                "TASK-003 should NOT have been dispatched -- budget must expire before its tick",
            )

            # feature-1 (TASK-002 + TASK-003) must be quarantined for budget,
            # NOT integrated -- TASK-003 genuinely never ran.
            self.assertIn("feature-1", result["quarantined"])
            self.assertIn("budget", result["quarantined"]["feature-1"].lower())

            # The journal's group record must carry quarantine_reason
            # budget_exhausted (this is the exact signal the bug report names).
            import json

            journal = json.loads(Path(journal_path).read_text())
            self.assertEqual(journal["groups"]["feature-1"]["state"], "QUARANTINED")
            self.assertEqual(
                journal["groups"]["feature-1"].get("quarantine_reason"),
                "budget_exhausted",
            )


class ResumeAfterPartialGroupQuarantineTest(unittest.TestCase):
    """A plain resume (no --fresh, no --only) with adequate budget must finish
    TASK-003 and successfully integrate the group, reusing TASK-002's
    already-done status rather than re-running it or staying stuck. This is
    the behavior the bug report expected but reported as broken ("stayed
    QUARANTINED... across two resumes")."""

    def test_resume_completes_task003_and_integrates_group(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")

            # Phase 1: tiny budget -> TASK-001 + TASK-002 complete, TASK-003 and
            # its group ("feature-1") get force-quarantined budget_exhausted.
            integrate_one1, _events1 = _make_integrate_one()
            spawn1 = FakeSpawn()
            phase1 = _run(
                repo,
                journal_path,
                spawn1,
                integrate_one1,
                FakeVerifier(),
                run_budget=0.001,
            )
            self.assertIn("feature-1", phase1["quarantined"])
            self.assertFalse(any(c[0] == "TASK-003" for c in spawn1.calls))

            # Phase 2: plain resume, unlimited budget. TASK-003 must be
            # dispatched (its group still shows pending fan-out), and the group
            # must reach integrate -- proving the group is NOT permanently
            # stuck on the cached QUARANTINED verdict from phase 1.
            integrate_one2, events2 = _make_integrate_one()
            spawn2 = FakeSpawn()
            verifier2 = FakeVerifier()
            phase2 = _run(
                repo,
                journal_path,
                spawn2,
                integrate_one2,
                verifier2,
                run_budget=0,
                resume=True,
            )

            self.assertIn(
                "TASK-003",
                {c[0] for c in spawn2.calls},
                "resume should dispatch TASK-003 (still pending after phase 1)",
            )
            self.assertIn(
                "feature-1",
                events2,
                "resume should re-attempt integrate_one for the quarantined group "
                "once all its tasks are terminal",
            )
            self.assertNotIn(
                "feature-1",
                phase2["quarantined"],
                f"feature-1 should integrate cleanly on resume; quarantined={phase2['quarantined']}",
            )
            self.assertIn("feature-1", verifier2.calls)


class ResumeWithOnlyExcludingPendingSiblingTest(unittest.TestCase):
    """Mirrors the incident's second resume attempt (`--only 4.2,6.1,6.2` --
    naming the already-done task but NOT its still-pending sibling in the same
    group).

    Root cause found by this test (pre-fix): `_pipeline_scheduler` force-marks
    every task NOT named in --only as status "completed" regardless of whether
    it ever actually ran. `coordinator.deliverable_subset()` treats "completed"
    as ALREADY_INTEGRATED and drops it from the group's deliverable set --
    correct for a task genuinely already merged on a prior partial run, but
    silently wrong for a task that was simply never dispatched: the group was
    then judged integration-ready and its PR opened/re-attempted missing that
    task's real, still-needed work. No error, no quarantine -- just a quietly
    incomplete PR.

    Fix: --only now only fake-completes a task that either (a) is named in
    --only, or (b) was ALREADY in coordinator.DONE before this invocation
    (genuinely finished on a prior run). A task that is neither, but shares a
    group with an --only-included task, raises RuntimeError instead of being
    silently faked -- the operator must include it or wait for it to finish
    first."""

    def test_only_excluding_never_run_sibling_raises_instead_of_silently_dropping_it(
        self,
    ):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")

            integrate_one1, _ = _make_integrate_one()
            spawn1 = FakeSpawn(sleep_after={"TASK-002": 1.0})
            phase1 = _run(
                repo,
                journal_path,
                spawn1,
                integrate_one1,
                FakeVerifier(),
                run_budget=0.5,
            )
            self.assertIn("feature-1", phase1["quarantined"])

            # Second resume, --only TASK-002 -- exactly like the incident's
            # `--only 4.2,6.1,6.2` never naming TASK-003's analog. TASK-003 has
            # not completed (phase 1 never dispatched it) and shares feature-1
            # with the included TASK-002 -- must refuse, not silently drop it.
            integrate_one2, events2 = _make_integrate_one()
            spawn2 = FakeSpawn()
            with self.assertRaises(RuntimeError) as ctx:
                _run(
                    repo,
                    journal_path,
                    spawn2,
                    integrate_one2,
                    FakeVerifier(),
                    run_budget=0,
                    resume=True,
                    only=["TASK-002"],
                )
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("feature-1", str(ctx.exception))

            # Must fail BEFORE dispatching or integrating anything -- no partial
            # side effects from a rejected --only combination.
            self.assertFalse(spawn2.calls)
            self.assertFalse(events2)
            self.assertFalse((repo / "src" / "task-003.txt").exists())

    def test_only_excluding_a_genuinely_already_done_task_still_works(self):
        """A task real-completed on a PRIOR invocation (not --fresh) and then
        excluded from a later --only is the intended, safe use of --only --
        must NOT raise."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")

            # Phase 1: unlimited budget -- everything completes and integrates.
            integrate_one1, _ = _make_integrate_one()
            phase1 = _run(
                repo,
                journal_path,
                FakeSpawn(),
                integrate_one1,
                FakeVerifier(),
                run_budget=0,
            )
            self.assertEqual(phase1["quarantined"], {})

            # Phase 2: resume with --only naming just TASK-002 -- TASK-003 is
            # excluded but genuinely already done from phase 1, so this must
            # proceed normally (not raise).
            integrate_one2, _events2 = _make_integrate_one()
            phase2 = _run(
                repo,
                journal_path,
                FakeSpawn(),
                integrate_one2,
                FakeVerifier(),
                run_budget=0,
                resume=True,
                only=["TASK-002"],
            )
            self.assertEqual(phase2["quarantined"], {})


if __name__ == "__main__":
    unittest.main()

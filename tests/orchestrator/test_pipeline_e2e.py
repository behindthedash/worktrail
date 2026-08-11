#!/usr/bin/env python3
"""End-to-end tests for pipelined group integrate+verify (TASK-009).

Covers:
  - AC-002: base integrates+verifies while a later group's task is still in flight
  - AC-006: pipeline/sequential parity — same outcomes produce the same summary
  - AC-004, AC-005: all-fail quarantine + cascade, end-to-end
  - AC-013: kill-and-resume — completed groups skipped; merged group not re-merged
  - AC-019 [EXT]: all existing orchestrator suites stay green
  - AC-020 [EXT]: toy deterministic golden is unchanged

All scenarios use injected fake spawn + fake verifier against a throwaway git
repo so no real claude -p, gh, or CI is needed.

Run: python3 scripts/test_pipeline_e2e.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402

_HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_pipeline.py conventions)
# ---------------------------------------------------------------------------

def _init_repo(root: Path) -> Path:
    """Real git repo with a 3-task spec: TASK-001 (base) + TASK-002/003 (features).

    Topology mirrors test_pipeline.py: TASK-001 is root, TASK-002 and TASK-003
    both depend on TASK-001, so plan_groups assigns TASK-001 to "base" and the
    others to "feature-1" / "feature-2" (both depends_on: ["base"]).
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
    }
    for tid, fm in tasks_fm.items():
        (spec_dir / f"{tid}.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True, capture_output=True,
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
    """Makes a real git commit per task and returns a valid report-back.

    fail_task: task id whose implement always returns status:"failed".
    Hooks: per-(task_id, role) callables invoked before git work, letting tests
    synchronize concurrent threads.
    """

    def __init__(self, fail_task=None):
        self.fail_task = fail_task
        self.calls = []
        self._lock = threading.Lock()
        self._hooks = {}

    def add_hook(self, task_id, role, fn):
        self._hooks[(task_id, role)] = fn

    def __call__(self, role, task, wt):
        tid = task["id"]
        hook = self._hooks.get((tid, role))
        if hook:
            hook()
        with self._lock:
            self.calls.append((tid, role))
        if role in ("implement", "fix"):
            if tid == self.fail_task:
                return spawnlib.SpawnResult(
                    text=f'```json\n{{"task":"{tid}","step":"{role}","status":"failed"}}\n```',
                    usage={},
                )
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            subprocess.run(
                ["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"feat({tid})"],
                check=True, capture_output=True,
            )
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()[:8] or "00000000"
        return _fake_report(tid, role, sha)


def _make_integrate_one(events=None, force_quarantine=None):
    """Return (integrate_one_fn, events_list). Records integrate calls in events."""
    events = events if events is not None else []
    force_quarantine = force_quarantine or set()

    def integrate_one(g, repo, spec_id, tasks, remote, run_id, base,
                      journal_path, status, group_branch, quarantined, **kwargs):
        name = g["name"]
        events.append(name)
        deliverable = [t for t in g["tasks"] if status.get(t) in ("done", "completed")]
        if not deliverable or name in force_quarantine:
            quarantined[name] = f"no deliverable tasks in {name}"
            return None
        group_branch[name] = f"full-test/{name}"
        record_group = kwargs.get("_record_group")
        if record_group:
            record_group(name, f"http://fake-pr/{name}", group_branch[name], "OPEN")
        return (name, base, f"http://fake-pr/{name}")

    return integrate_one, events


class FakeVerifier:
    """Stand-in for verify.Verifier. Merges all groups; quarantines fail_for groups."""

    def __init__(self, fail_for=None):
        self.fail_for = fail_for or set()
        self.calls = []
        self._lock = threading.Lock()

    def verify_one(self, group, group_branch_ref, delivered, merged, quarantined, lock,
                   self_merged=None, armed=None, post_merge_regressed=None):
        name = group["name"]
        with self._lock:
            self.calls.append(name)
        if name in self.fail_for:
            with lock:
                quarantined[name] = f"verify failed for {name}"
            return
        with lock:
            merged.append(name)


def _run_pipeline(repo, tmp, spawn, integrate_one, verifier,
                  run_budget=None, resume=False, run_id="e2e-test"):
    """Run _pipeline_scheduler with injected fakes; return the summary dict."""
    journal_path = str(Path(tmp) / "pipeline-journal.json")
    return live._pipeline_scheduler(
        repo=repo,
        spec_rel="docs/specs/001-x",
        remote="origin",
        base="main",
        model="haiku",
        max_workers=3,
        timeout=30,
        resume=resume,
        only=None,
        role_models=None,
        run_budget=run_budget,
        journal_path=journal_path,
        run_id=run_id,
        _spawn=spawn,
        _integrate_one=integrate_one,
        _make_verifier=lambda: verifier,
    )


def _write_journal(path: str, entries: list, groups: dict,
                   run_id: str = "e2e-test") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "run_id": run_id,
        "spec_id": "001-x",
        "entries": entries,
        "groups": groups,
    }))


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
    """Journal entries that drive all three tasks to 'done' via replay."""
    entries = []
    for tid in ("TASK-001", "TASK-002", "TASK-003"):
        entries.extend([
            _journal_entry(tid, "implement"),
            _journal_entry(tid, "review", review_status="PASSED"),
            _journal_entry(tid, "cleanup"),
        ])
    return entries


# ---------------------------------------------------------------------------
# E2E Happy Path (AC-002, AC-006)
# ---------------------------------------------------------------------------

class E2EHappyPathTest(unittest.TestCase):
    """AC-002 + AC-006: complete happy path — base IV overlaps feature fan-out;
    all three groups reach MERGED; summary matches sequential expectation."""

    def test_all_groups_merged(self):
        """Happy path: all tasks succeed → all three groups reach MERGED."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()
            result = _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertEqual(result["quarantined"], {})
            for name in ("base", "feature-1", "feature-2"):
                self.assertIn(name, result["merged"],
                              f"'{name}' should be in merged; got {result['merged']}")
            self.assertIsNone(result["final"])

    def test_base_iv_overlaps_feature_fanout_both_merged(self):
        """AC-002: base IV starts while feature tasks are still fanning out; both reach MERGED.

        Mechanism: base integrate_one waits on feature_fanout_started (set when
        TASK-002's implement begins). If the scheduler serialized fan-out before
        IV, the wait would never resolve — overlap_confirmed stays unset and the
        test fails. The assert also confirms both feature groups end MERGED, so
        the overlap didn't prevent them from completing.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))

            feature_fanout_started = threading.Event()
            overlap_confirmed = threading.Event()
            base_fn, _ = _make_integrate_one()

            def hooked_integrate_one(g, repo_, spec_id, tasks, remote, run_id_,
                                     base_, journal_path, status, group_branch,
                                     quarantined, **kwargs):
                if g["name"] == "base":
                    if feature_fanout_started.wait(timeout=10):
                        overlap_confirmed.set()
                return base_fn(g, repo_, spec_id, tasks, remote, run_id_,
                               base_, journal_path, status, group_branch,
                               quarantined, **kwargs)

            spawn = FakeSpawn()
            spawn.add_hook("TASK-002", "implement", feature_fanout_started.set)
            spawn.add_hook("TASK-003", "implement", feature_fanout_started.set)

            result = _run_pipeline(repo, tmp, spawn, hooked_integrate_one, FakeVerifier())

            self.assertTrue(
                overlap_confirmed.is_set(),
                "base IV did not overlap with feature fan-out — possible serialization bug",
            )
            self.assertIn("base", result["merged"])
            for name in ("feature-1", "feature-2"):
                self.assertIn(name, result["merged"],
                              f"'{name}' should be MERGED after overlap; got {result['merged']}")

    def test_summary_shape_has_four_keys(self):
        """AC-006: pipeline summary has the same four keys as the sequential path."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()
            result = _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(key, result, f"summary missing key '{key}'")

    def test_group_prs_populated_for_all_integrated_groups(self):
        """group_prs has one entry per successfully integrated group."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()
            result = _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            pr_names = {pr[0] for pr in result["group_prs"]}
            for name in ("base", "feature-1", "feature-2"):
                self.assertIn(name, pr_names,
                              f"'{name}' should have a PR entry; pr_names={pr_names}")


def _init_repo_with_tail(root: Path) -> Path:
    """Extends `_init_repo`'s topology with a tail-kind TASK-004 (kind: e2e)
    depending on both feature groups -- mirrors the reported bug's TASK-CHG-007
    (kind: e2e, deps on two impl tasks from separate groups)."""
    repo = _init_repo(root)
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    (spec_dir / "TASK-004.md").write_text(
        "---\nid: TASK-004\nstatus: pending\ndependencies: [TASK-002, TASK-003]\n"
        "files: [src/task-004.txt]\nkind: e2e\nreview: skip\n---\nbody\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "add tail task"],
        check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# E2E Tail Dispatch (level-triggered pending_tail_tasks dispatch)
# ---------------------------------------------------------------------------

class E2ETailDispatchTest(unittest.TestCase):
    """`full-real --pipeline` must dispatch kind:e2e/cleanup tail tasks once
    every impl group has merged. Previously never dispatched at all: tail-kind
    tasks are excluded from `runnable_frontier` unconditionally, and
    `_pipeline_scheduler` never threaded `with_tail=True` through to
    `live_run_real` -- the journal's `pending_tail_tasks`/`pending_tail_reason`
    fields were bookkeeping with no consumer."""

    def test_tail_task_dispatched_after_all_groups_merged(self):
        """Fresh run: TASK-004 (kind:e2e) is driven once its deps (TASK-002/003)
        are done, within the same pipeline invocation."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp))
            spawn = FakeSpawn()
            integrate_one, _ = _make_integrate_one()
            _run_pipeline(repo, tmp, spawn, integrate_one, FakeVerifier())

            tail_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-004"]
            self.assertTrue(
                tail_calls,
                f"tail task TASK-004 was never dispatched; spawn.calls={spawn.calls}",
            )

    def test_tail_task_dispatched_on_resume_after_prior_process_merged_everything(self):
        """Reproduces the reported bug: a FRESH process resumes a journal where
        every impl group already reached MERGED (integrate_complete is True)
        but the tail task has no journal entries at all -- it must still be
        dispatched instead of being silently skipped on every future resume."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp))
            # Every group is journal-MERGED with no branch materialized here --
            # commit their declared files so TASK-004's dependency-file guard
            # (_require_dependency_files) sees them present, matching the same
            # pre-merged invariant test_completed_tasks_not_respawned_on_resume
            # sets up for TASK-001.
            (repo / "src").mkdir(parents=True, exist_ok=True)
            for tid in ("task-001", "task-002", "task-003"):
                (repo / "src" / f"{tid}.txt").write_text(f"{tid}\n")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "pre-merged for resume test"],
                check=True, capture_output=True,
            )
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(journal_path, _all_done_entries(), {
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
            })

            spawn = FakeSpawn()
            integrate_one, events = _make_integrate_one()
            live._pipeline_scheduler(
                repo=repo,
                spec_rel="docs/specs/001-x",
                remote="origin",
                base="main",
                model="haiku",
                max_workers=3,
                timeout=30,
                resume=True,
                only=None,
                role_models=None,
                run_budget=None,
                journal_path=journal_path,
                run_id="e2e-test",
                _spawn=spawn,
                _integrate_one=integrate_one,
                _make_verifier=lambda: FakeVerifier(),
            )

            tail_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-004"]
            self.assertTrue(
                tail_calls,
                "tail task TASK-004 must be dispatched on a resume where every "
                f"impl group already merged; spawn.calls={spawn.calls}",
            )
            self.assertEqual(
                events, [],
                f"already-MERGED impl groups must not be re-integrated; events={events}",
            )


# ---------------------------------------------------------------------------
# E2E Parity (AC-006)
# ---------------------------------------------------------------------------

class E2EParityTest(unittest.TestCase):
    """AC-006: pipeline produces the same merged/quarantined summary as the sequential
    path for identical task outcomes. The sequential path is simulated by running the
    fan-out to completion first (live_run_real with FakeSpawn), then integrating and
    verifying each group in order with the same fake integrate/verify callables."""

    def _run_sequential_like(self, repo: Path, tmp: str,
                              spawn: FakeSpawn, integrate_one_fn,
                              verifier_obj: FakeVerifier) -> dict:
        """Simulate sequential: complete ALL fan-out, then integrate+verify in group order."""
        journal = str(Path(tmp) / "seq-journal.json")
        fanout = live.live_run_real(
            repo, "docs/specs/001-x",
            max_workers=3, out_cassette=journal, run_id="seq-sim", spawn=spawn,
        )
        tasks = fanout["tasks"]
        groups = coordinator.plan_groups(tasks)
        status = {t["id"]: t.get("status") for t in tasks}
        group_branch: dict = {}
        quarantined: dict = {}
        merged: list = []
        prs: list = []
        lock = threading.Lock()

        for g in groups:
            name = g["name"]
            # Sequential cascade: if any dependency is quarantined, so is this group
            for dep in g.get("depends_on", []):
                if dep in quarantined:
                    quarantined[name] = f"base group '{dep}' quarantined"
                    break
            if name in quarantined:
                continue
            pr_tuple = integrate_one_fn(
                g, repo, "001-x", tasks, "origin", "seq-sim", "main",
                journal, status, group_branch, quarantined,
            )
            if pr_tuple:
                prs.append(pr_tuple)
            if name not in quarantined and name in group_branch:
                delivered_ids, _ = coordinator.deliverable_subset(g["tasks"], tasks, status)
                delivered = {name: delivered_ids}
                verifier_obj.verify_one(g, group_branch[name], delivered, merged, quarantined, lock)

        return {"merged": merged, "quarantined": quarantined, "group_prs": prs, "final": None}

    def test_all_success_parity(self):
        """All tasks succeed: pipeline merged == sequential merged; quarantined empty in both."""
        with tempfile.TemporaryDirectory() as main_tmp:
            tmp = Path(main_tmp)
            repo_p = _init_repo(tmp / "rp")
            repo_s = _init_repo(tmp / "rs")

            integrate_p, _ = _make_integrate_one()
            result_p = _run_pipeline(repo_p, str(tmp / "jp"), FakeSpawn(),
                                     integrate_p, FakeVerifier())

            integrate_s, _ = _make_integrate_one()
            result_s = self._run_sequential_like(
                repo_s, str(tmp / "js"), FakeSpawn(), integrate_s, FakeVerifier(),
            )

            self.assertEqual(
                set(result_p["merged"]), set(result_s["merged"]),
                f"pipeline merged={result_p['merged']} != sequential merged={result_s['merged']}",
            )
            self.assertEqual(
                result_p["quarantined"], result_s["quarantined"],
                f"quarantined mismatch: pipeline={result_p['quarantined']} "
                f"sequential={result_s['quarantined']}",
            )

    def test_all_fail_parity(self):
        """TASK-001 fails: pipeline quarantined set == sequential quarantined set."""
        with tempfile.TemporaryDirectory() as main_tmp:
            tmp = Path(main_tmp)
            repo_p = _init_repo(tmp / "rp")
            repo_s = _init_repo(tmp / "rs")

            integrate_p, _ = _make_integrate_one()
            result_p = _run_pipeline(repo_p, str(tmp / "jp"),
                                     FakeSpawn(fail_task="TASK-001"),
                                     integrate_p, FakeVerifier())

            integrate_s, _ = _make_integrate_one()
            result_s = self._run_sequential_like(
                repo_s, str(tmp / "js"),
                FakeSpawn(fail_task="TASK-001"),
                integrate_s, FakeVerifier(),
            )

            # Base must be quarantined in both (no deliverable tasks after TASK-001 fails)
            self.assertIn("base", result_p["quarantined"],
                          f"pipeline: base should be quarantined; got {result_p['quarantined']}")
            self.assertIn("base", result_s["quarantined"],
                          f"sequential: base should be quarantined; got {result_s['quarantined']}")
            # Nothing merged in either path
            self.assertEqual(result_p["merged"], [],
                             f"pipeline: merged should be empty when base fails; "
                             f"got {result_p['merged']}")
            self.assertEqual(result_s["merged"], [],
                             f"sequential: merged should be empty when base fails; "
                             f"got {result_s['merged']}")


# ---------------------------------------------------------------------------
# E2E Quarantine + Cascade (AC-004, AC-005)
# ---------------------------------------------------------------------------

class E2EQuarantineTest(unittest.TestCase):
    """AC-004 + AC-005: end-to-end quarantine and cascade without aborting the run."""

    def test_all_fail_group_quarantined_run_completes(self):
        """AC-004: TASK-001 fails → base quarantined; run returns with complete summary."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()
            result = _run_pipeline(repo, tmp, FakeSpawn(fail_task="TASK-001"),
                                   integrate_one, FakeVerifier())

            self.assertIn("base", result["quarantined"],
                          "base should be quarantined (all tasks failed)")
            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(key, result, f"summary key '{key}' missing")

    def test_quarantined_base_cascades_to_all_feature_groups(self):
        """AC-005: when base is quarantined, all dependent feature groups cascade."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(force_quarantine={"base"})
            result = _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertIn("base", result["quarantined"])
            for name in ("feature-1", "feature-2"):
                self.assertIn(name, result["quarantined"],
                              f"'{name}' should be quarantined (cascade from base)")

    def test_quarantine_cascade_reason_mentions_base(self):
        """Feature groups' quarantine reason must reference the failed base."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(force_quarantine={"base"})
            result = _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            for name in ("feature-1", "feature-2"):
                reason = result["quarantined"].get(name, "")
                self.assertIn("base", reason.lower(),
                              f"'{name}' quarantine reason should mention 'base'; got: {reason!r}")

    def test_quarantine_does_not_abort_run(self):
        """A quarantined group must not propagate an exception and abort the run."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(force_quarantine={"base"})
            try:
                _run_pipeline(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())
            except Exception as exc:
                self.fail(f"Run aborted by quarantine (should not happen): {exc!r}")


# ---------------------------------------------------------------------------
# E2E Resume (AC-013)
# ---------------------------------------------------------------------------

class E2EResumeTest(unittest.TestCase):
    """AC-013: kill-and-resume — completed groups not redone; merged group not re-merged."""

    def _resume_run(self, repo, journal_path, groups_state, spawn=None, integrate_one=None):
        """Call _pipeline_scheduler with resume=True and the given journal groups state."""
        return live._pipeline_scheduler(
            repo=repo,
            spec_rel="docs/specs/001-x",
            remote="origin",
            base="main",
            model="haiku",
            max_workers=3,
            timeout=30,
            resume=True,
            only=None,
            role_models=None,
            run_budget=None,
            journal_path=journal_path,
            run_id="e2e-test",
            _spawn=spawn or FakeSpawn(),
            _integrate_one=integrate_one or _make_integrate_one()[0],
            _make_verifier=lambda: FakeVerifier(),
        )

    def test_merged_group_not_reintegrated_on_resume(self):
        """AC-013: A MERGED group in the journal is not re-integrated on resume."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(journal_path, _all_done_entries(), {
                "base": {
                    "pr_url": "http://pr/base",
                    "head_branch": "full-test/base",
                    "state": "MERGED",
                },
            })

            integrate_one, events = _make_integrate_one()
            self._resume_run(repo, journal_path, {}, integrate_one=integrate_one)

            base_calls = [e for e in events if e == "base"]
            self.assertEqual(
                base_calls, [],
                f"integrate_one must NOT be called for MERGED 'base'; events={events}",
            )

    def test_resume_does_not_quarantine_merged_group(self):
        """A MERGED group on resume appears in merged (not quarantined)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(journal_path, _all_done_entries(), {
                "base": {
                    "pr_url": "http://pr/base",
                    "head_branch": "full-test/base",
                    "state": "MERGED",
                },
            })

            result = self._resume_run(repo, journal_path, {})

            self.assertNotIn(
                "base", result["quarantined"],
                "MERGED group must not appear in quarantined on resume",
            )

    def test_completed_tasks_not_respawned_on_resume(self):
        """AC-013: fan-out tasks already done in journal are not re-spawned."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            # TASK-001 is journal-marked done with no branch materialized --
            # dependency_start_ref's fallback (no branch -> start from HEAD)
            # assumes that means it's already merged into HEAD. Commit its
            # declared file to match that invariant, or the dependency-file
            # guard correctly flags TASK-002's worktree as missing it.
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "task-001.txt").write_text("TASK-001\n")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "TASK-001 (pre-merged for resume test)"],
                check=True, capture_output=True,
            )
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            # Only TASK-001 done; TASK-002/003 have no entries → must be driven
            _write_journal(journal_path, [
                _journal_entry("TASK-001", "implement"),
                _journal_entry("TASK-001", "review", review_status="PASSED"),
                _journal_entry("TASK-001", "cleanup"),
            ], {})

            spawn = FakeSpawn()
            integrate_one, _ = _make_integrate_one()
            self._resume_run(repo, journal_path, {}, spawn=spawn, integrate_one=integrate_one)

            t001_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-001"]
            self.assertEqual(
                t001_calls, [],
                f"TASK-001 already done in journal — must not be re-driven; "
                f"spawn.calls={spawn.calls}",
            )
            t002_calls = [tid for tid, _ in spawn.calls if tid == "TASK-002"]
            self.assertGreater(
                len(t002_calls), 0, "TASK-002 should be driven on resume",
            )

    def test_base_before_dependent_order_preserved_on_resume(self):
        """AC-013: base-before-dependent ordering holds across a resumed run."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            # All tasks done in journal; base MERGED → feature groups should integrate
            _write_journal(journal_path, _all_done_entries(), {
                "base": {
                    "pr_url": "http://pr/base",
                    "head_branch": "full-test/base",
                    "state": "MERGED",
                },
            })

            integrate_one, events = _make_integrate_one()
            self._resume_run(repo, journal_path, {}, integrate_one=integrate_one)

            feature_calls = [e for e in events if e.startswith("feature")]
            self.assertEqual(
                len(feature_calls), 2,
                f"Both feature groups should integrate after MERGED base; got events={events}",
            )

    def test_corrupt_journal_starts_fresh_no_crash(self):
        """An unreadable journal on resume starts fresh rather than crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
            Path(journal_path).write_text("{not valid json!!!")

            integrate_one, _ = _make_integrate_one()
            try:
                result = self._resume_run(repo, journal_path, {}, integrate_one=integrate_one)
            except Exception as exc:
                self.fail(f"Corrupt journal must not cause a crash; got {exc!r}")
            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(key, result, f"summary key '{key}' missing after corrupt-journal resume")


# ---------------------------------------------------------------------------
# Regression (AC-019, AC-020)
# ---------------------------------------------------------------------------

class RegressionTest(unittest.TestCase):
    """AC-019: all existing orchestrator suites stay green.
    AC-020: toy deterministic golden is unchanged."""

    def test_toy_golden_unchanged(self):
        """AC-020: `orchestrate.py check` must exit 0 (golden unchanged)."""
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.orchestrate", "check"],
            capture_output=True, text=True, cwd=str(_HERE), timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Golden drift detected!\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_existing_test_suites_pass(self):
        """AC-019: all test_*.py suites (except this file) must exit 0."""
        this_file = Path(__file__).name
        test_files = sorted(
            f for f in _HERE.glob("test_*.py")
            if f.name != this_file
        )
        failures = []
        child_env = os.environ.copy()
        src_root = Path(__file__).resolve().parents[2] / "src"
        child_env["PYTHONPATH"] = os.pathsep.join(
            [str(src_root), child_env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        for tf in test_files:
            # Suites exercise provider-capacity handling and may persist a
            # cooldown. Give each child an isolated cache so one suite's
            # simulated provider failure cannot poison the next suite.
            with tempfile.TemporaryDirectory(prefix="worktrail-capacity-suite-") as cache_dir:
                suite_env = child_env.copy()
                suite_env["GO_AGENT_CAPACITY_CACHE"] = str(
                    Path(cache_dir) / "agent-capacity.json"
                )
                try:
                    result = subprocess.run(
                        [sys.executable, str(tf)],
                        capture_output=True, text=True, cwd=str(_HERE),
                        env=suite_env, timeout=120,
                    )
                    if result.returncode != 0:
                        failures.append(
                            f"{tf.name}: exit {result.returncode}\n"
                            f"stderr tail: {result.stderr[-300:]}"
                        )
                except subprocess.TimeoutExpired:
                    failures.append(f"{tf.name}: TIMEOUT (>120s)")

        self.assertEqual(
            failures, [],
            "Existing suites FAILED (AC-019):\n" + "\n---\n".join(failures),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

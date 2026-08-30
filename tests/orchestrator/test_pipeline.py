#!/usr/bin/env python3
"""Tests for _pipeline_scheduler (TASK-004 AC-002, AC-003, AC-004, AC-005, AC-006, AC-014).

Each test class covers one acceptance criterion using injected fakes for spawn,
integrate_one, and the Verifier so no real claude -p, gh, or remote git is needed.
A real temp git repo is used so worktree/branch mechanics run against genuine git.

Run: python3 scripts/test_pipeline.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import (
    integrate,
    live,
    spawnlib,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _init_repo(root: Path) -> Path:
    """Create a real git repo with a 3-task spec that produces 3 groups.

    Task topology:
        TASK-001 (root, no deps)
        TASK-002 (depends on TASK-001)
        TASK-003 (depends on TASK-001)

    plan_groups() assigns TASK-001 to "base" (root with >= 2 transitive
    dependents).  TASK-002 → "feature-1", TASK-003 → "feature-2", both
    with depends_on: ["base"].
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

    fail_task: task id whose implement always returns status:"failed".
    Hooks: per-(task_id, role) callables invoked before doing git work,
    letting tests synchronize concurrent threads.
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
        return _fake_report(tid, role, sha)


def _make_integrate_one(events=None, force_quarantine=None, raise_for=None):
    """Return (integrate_one_fn, events_list).

    events       — list that records every group name as integrate_one is called.
    force_quarantine — set of group names to quarantine without opening a PR.
    raise_for    — set of group names to raise RuntimeError for.
    """
    events = events if events is not None else []
    force_quarantine = force_quarantine or set()
    raise_for = raise_for or set()

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
        if name in raise_for:
            raise RuntimeError(f"integrate exception for '{name}'")
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
    """Stand-in for verify.Verifier.

    verify_one adds the group to merged by default.  Groups in raise_for raise
    RuntimeError; groups in fail_for are quarantined instead.
    """

    def __init__(self, raise_for=None, fail_for=None):
        self.raise_for = raise_for or set()
        self.fail_for = fail_for or set()
        self.calls = []
        self._lock = threading.Lock()

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
        name = group["name"]
        with self._lock:
            self.calls.append(name)
        if name in self.raise_for:
            raise RuntimeError(f"verify exception for '{name}'")
        if name in self.fail_for:
            with lock:
                quarantined[name] = f"verify failed for {name}"
            return
        with lock:
            merged.append(name)


def _run(
    repo,
    tmp,
    spawn,
    integrate_one,
    verifier,
    run_budget=None,
    resume=False,
    re_integrate=False,
    journal_path=None,
):
    journal_path = journal_path or str(Path(tmp) / "pipeline-journal.json")
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
        run_id="full-test",
        re_integrate=re_integrate,
        _spawn=spawn,
        _integrate_one=integrate_one,
        _make_verifier=lambda: verifier,
    )


# ---------------------------------------------------------------------------
# AC-002: overlap
# ---------------------------------------------------------------------------


class OverlapTest(unittest.TestCase):
    """AC-002: a completed group is integrated+verified while a later group's
    fan-out task is still in flight."""

    def test_base_iv_overlaps_feature_fanout(self):
        """IV for 'base' runs concurrently with TASK-002/003 fan-out dispatch.

        Mechanism: base integrate_one waits for 'feature_fanout_started' (set by
        the fake spawn when TASK-002's implement begins).  If the scheduler
        serialised fan-out and IV, the wait would never complete and
        overlap_confirmed would stay unset — the test would fail.
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))

            feature_fanout_started = threading.Event()
            overlap_confirmed = threading.Event()

            base_fn, _ = _make_integrate_one()

            def hooked_integrate_one(
                g,
                repo_,
                spec_id,
                tasks,
                remote,
                run_id_,
                base_,
                journal_path,
                status,
                group_branch,
                quarantined,
                **kwargs,
            ):
                if g["name"] == "base" and feature_fanout_started.wait(timeout=10):
                    overlap_confirmed.set()
                return base_fn(
                    g,
                    repo_,
                    spec_id,
                    tasks,
                    remote,
                    run_id_,
                    base_,
                    journal_path,
                    status,
                    group_branch,
                    quarantined,
                    **kwargs,
                )

            spawn = FakeSpawn()
            spawn.add_hook("TASK-002", "implement", feature_fanout_started.set)
            spawn.add_hook("TASK-003", "implement", feature_fanout_started.set)

            result = _run(repo, tmp, spawn, hooked_integrate_one, FakeVerifier())

            self.assertTrue(
                overlap_confirmed.is_set(),
                "base IV did not overlap with feature fan-out — possible serialization bug",
            )
            self.assertEqual(result["quarantined"], {})
            self.assertIn("base", result["merged"])


# ---------------------------------------------------------------------------
# AC-003: ordering
# ---------------------------------------------------------------------------


class OrderingTest(unittest.TestCase):
    """AC-003: base group integrate+verify starts before any dependent group."""

    def test_base_integrated_before_feature_groups(self):
        """integrate_one must be called for 'base' before any 'feature-*'."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()

            _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertGreater(len(events), 0, "integrate_one never called")
            self.assertEqual(
                events[0],
                "base",
                f"base must be integrated first; actual order: {events}",
            )

    def test_dependent_group_held_until_base_merged(self):
        """Feature groups must not call integrate_one until base's done event fires."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            order = []
            order_lock = threading.Lock()

            def tracking_integrate_one(
                g,
                repo_,
                spec_id,
                tasks,
                remote,
                run_id_,
                base_,
                journal_path,
                status,
                group_branch,
                quarantined,
                **kwargs,
            ):
                name = g["name"]
                with order_lock:
                    order.append(name)
                deliverable = [
                    t for t in g["tasks"] if status.get(t) in ("done", "completed")
                ]
                if deliverable:
                    group_branch[name] = f"full-test/{name}"
                    return (name, base_, f"http://fake-pr/{name}")
                quarantined[name] = "no deliverable"
                return None

            _run(repo, tmp, FakeSpawn(), tracking_integrate_one, FakeVerifier())

            if "base" in order and any(n.startswith("feature") for n in order):
                base_idx = order.index("base")
                for name in order:
                    if name.startswith("feature"):
                        self.assertGreater(
                            order.index(name),
                            base_idx,
                            f"'{name}' integrated before 'base'",
                        )


# ---------------------------------------------------------------------------
# AC-004: quarantine — empty deliverable subset
# ---------------------------------------------------------------------------


class QuarantineEmptySubsetTest(unittest.TestCase):
    """AC-004: a group whose tasks all fail is quarantined; the run continues."""

    def test_failed_task_group_quarantined(self):
        """TASK-001 fails → base has no deliverable tasks → base quarantined."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(
                repo,
                tmp,
                FakeSpawn(fail_task="TASK-001"),
                integrate_one,
                FakeVerifier(),
            )

            self.assertIn("base", result["quarantined"])

    def test_quarantined_group_run_completes_normally(self):
        """Run returns normally (not aborted) even when a group is quarantined."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(
                repo,
                tmp,
                FakeSpawn(fail_task="TASK-001"),
                integrate_one,
                FakeVerifier(),
            )

            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(key, result, f"summary missing key '{key}'")


# ---------------------------------------------------------------------------
# AC-005: quarantine cascade — quarantined base cascades to dependents
# ---------------------------------------------------------------------------


class QuarantineCascadeTest(unittest.TestCase):
    """AC-005: when a base group is quarantined, its dependents are also quarantined."""

    def test_quarantined_base_cascades_to_feature_groups(self):
        """All feature groups must be quarantined when base is quarantined."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(force_quarantine={"base"})

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertIn("base", result["quarantined"])
            # Both feature groups must be quarantined (cascaded from base)
            for name in ("feature-1", "feature-2"):
                self.assertIn(
                    name,
                    result["quarantined"],
                    f"'{name}' should be quarantined (cascade from base)",
                )

    def test_quarantined_base_prevents_feature_integrate_one_call(self):
        """integrate_one must NOT be called for feature groups when base is quarantined."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one(force_quarantine={"base"})

            _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            feature_calls = [e for e in events if e.startswith("feature")]
            self.assertEqual(
                feature_calls,
                [],
                f"integrate_one should not be called for feature groups when base is "
                f"quarantined; called for: {feature_calls}",
            )

    def test_cascade_quarantine_reason_mentions_base(self):
        """Feature groups' quarantine reason should reference the failed base group."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(force_quarantine={"base"})

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            for name in ("feature-1", "feature-2"):
                reason = result["quarantined"].get(name, "")
                self.assertIn(
                    "base",
                    reason.lower(),
                    f"'{name}' quarantine reason should mention 'base'; got: {reason!r}",
                )


# ---------------------------------------------------------------------------
# AC-014: isolation — single group exception quarantines only that group
# ---------------------------------------------------------------------------


class IsolationTest(unittest.TestCase):
    """AC-014: a single group's integrate/verify exception quarantines that group
    and does NOT abort the rest of the run."""

    def test_integrate_exception_quarantines_group_run_continues(self):
        """integrate_one raising for 'feature-1' quarantines it; base and feature-2 merge."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(raise_for={"feature-1"})

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertIn("feature-1", result["quarantined"])
            self.assertIn(
                "base",
                result["merged"],
                "base should still merge despite feature-1 exception",
            )
            self.assertIn(
                "feature-2",
                result["merged"],
                "feature-2 should still merge despite feature-1 exception",
            )

    def test_verify_exception_quarantines_group_run_continues(self):
        """verify_one raising for 'feature-1' quarantines it; base and feature-2 merge."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()
            verifier = FakeVerifier(raise_for={"feature-1"})

            result = _run(repo, tmp, FakeSpawn(), integrate_one, verifier)

            self.assertIn("feature-1", result["quarantined"])
            self.assertIn("base", result["merged"])
            self.assertIn("feature-2", result["merged"])

    def test_integrate_exception_persists_detail_to_journal(self):
        """The exception's own text must survive into the on-disk journal, not
        just the 'integration_error' category -- otherwise a group quarantined
        by an unanticipated exception (e.g. a `git worktree add` failure) is
        undiagnosable after the run ends (see
        docs/specs/research/base-integration-group-worktree-add-quarantine.md)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            integrate_one, _ = _make_integrate_one(raise_for={"feature-1"})

            _run(
                repo,
                tmp,
                FakeSpawn(),
                integrate_one,
                FakeVerifier(),
                journal_path=journal_path,
            )

            journal = json.loads(Path(journal_path).read_text())
            record = journal["groups"]["feature-1"]
            self.assertEqual(
                record["quarantine_reason"], integrate.QUARANTINE_INTEGRATION_ERROR
            )
            self.assertIn("quarantine_detail", record)
            self.assertIn("feature-1", record["quarantine_detail"])

    def test_exception_does_not_abort_run(self):
        """An exception in one group's IV must not propagate and abort the run."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one(raise_for={"feature-1"})
            try:
                _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())
            except Exception as exc:  # noqa: BLE001
                self.fail(
                    f"Run was aborted by a group exception (should be isolated): {exc!r}"
                )


# ---------------------------------------------------------------------------
# Regression for brief 20260807-204909: an ordinary (non-exception) verify-stage
# quarantine -- verify_one() sets quarantined[name] directly and returns, as
# real CI-failure/merge-conflict quarantines do (verify.py's ensure_mergeable /
# wait_and_fix_ci / _merge_with_cumulative_gate paths) -- must still persist
# state=QUARANTINED to the on-disk journal, exactly like the exception-raised
# and pre-verify dependency-cascade quarantine paths already do. Without this,
# a pipeline-mode resume re-reads the stale pre-verify journal record instead
# of seeing QUARANTINED (same class of bug as brief 20260807-175851's serial-
# path gap, fixed in PR #221).
# ---------------------------------------------------------------------------


class OrdinaryVerifyQuarantinePersistenceTest(unittest.TestCase):
    """FakeVerifier.fail_for sets quarantined[name] without raising -- the
    ordinary quarantine case IsolationTest's raise_for tests do not cover."""

    def test_ordinary_quarantine_written_to_journal(self):
        """verify_one setting quarantined['feature-1'] (no raise) must land
        state=QUARANTINED in the on-disk journal, not be left unrecorded."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            integrate_one, _ = _make_integrate_one()
            verifier = FakeVerifier(fail_for={"feature-1"})

            result = _run(
                repo,
                tmp,
                FakeSpawn(),
                integrate_one,
                verifier,
                journal_path=journal_path,
            )

            self.assertIn("feature-1", result["quarantined"])

            journal = json.loads(Path(journal_path).read_text())
            self.assertIn(
                "feature-1",
                journal["groups"],
                "quarantined group missing from journal['groups'] entirely",
            )
            self.assertEqual(
                journal["groups"]["feature-1"]["state"],
                "QUARANTINED",
                "ordinary (non-exception) verify-stage quarantine was not persisted "
                "to the journal -- a resume would re-read the stale pre-verify state",
            )
            # Groups that merged normally are unaffected.
            self.assertEqual(journal["groups"]["base"]["state"], "MERGED")
            self.assertEqual(journal["groups"]["feature-2"]["state"], "MERGED")


# ---------------------------------------------------------------------------
# AC-006: parity — summary shape matches sequential path
# ---------------------------------------------------------------------------


class SummaryParityTest(unittest.TestCase):
    """AC-006: the pipelined run returns the same merged/quarantined summary shape
    as the sequential path for identical task outcomes."""

    def test_all_success_summary_has_expected_shape(self):
        """All-success run returns the four keys the sequential path returns."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(key, result, f"summary missing key '{key}'")
            self.assertIsNone(result["final"])

    def test_all_success_all_groups_merged(self):
        """When all tasks succeed, all groups appear in merged and quarantined is empty."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            self.assertEqual(result["quarantined"], {})
            for name in ("base", "feature-1", "feature-2"):
                self.assertIn(
                    name,
                    result["merged"],
                    f"'{name}' should be in merged when all succeed",
                )

    def test_group_prs_populated_for_all_groups(self):
        """group_prs contains one entry per successfully integrated group."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(repo, tmp, FakeSpawn(), integrate_one, FakeVerifier())

            pr_names = {pr[0] for pr in result["group_prs"]}
            for name in ("base", "feature-1", "feature-2"):
                self.assertIn(
                    name, pr_names, f"'{name}' should have a PR entry when all succeed"
                )


# ---------------------------------------------------------------------------
# Edge case: run budget exceeded
# ---------------------------------------------------------------------------


class RunBudgetTest(unittest.TestCase):
    """Alt path: run_budget exceeded stops new fan-out; already-complete groups
    still finish integrate+verify."""

    def test_budget_exceeded_feature_tasks_not_dispatched(self):
        """A near-zero budget expires after TASK-001's tick (real git ops > 1 ms),
        leaving TASK-002 and TASK-003 as 'pending' → quarantined as fan-out incomplete."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = _run(
                repo, tmp, FakeSpawn(), integrate_one, FakeVerifier(), run_budget=0.001
            )

            # Feature groups never ran → quarantined as "fan-out incomplete"
            fanout_incomplete = {
                k
                for k, v in result["quarantined"].items()
                if "incomplete" in v.lower() or "budget" in v.lower()
            }
            self.assertGreater(
                len(fanout_incomplete),
                0,
                f"Expected feature groups quarantined for budget; quarantined={result['quarantined']}",
            )


# ---------------------------------------------------------------------------
# AC-015 / TASK-006: atomic interleaved journal writes
# ---------------------------------------------------------------------------


class AtomicInterleavedWriteTest(unittest.TestCase):
    """AC-015: every journal write during a pipelined run is atomic; no record dropped."""

    def _run_with_record_group(self, tmp, repo, record_group_hook=None):
        """Run the pipeline with an integrate_one that calls _record_group."""
        journal_path = str(Path(tmp) / "pipeline-journal.json")

        def integrate_one(
            g,
            repo_,
            spec_id,
            tasks,
            remote,
            run_id_,
            base_,
            journal_path_,
            status,
            group_branch,
            quarantined,
            **kwargs,
        ):
            name = g["name"]
            deliverable = [
                t for t in g["tasks"] if status.get(t) in ("done", "completed")
            ]
            if not deliverable:
                quarantined[name] = "no deliverable"
                return None
            group_branch[name] = f"full-test/{name}"
            record_group = kwargs.get("_record_group")
            if record_group_hook:
                record_group_hook(name)
            if record_group:
                record_group(name, f"http://fake-pr/{name}", group_branch[name], "OPEN")
            return (name, base_, f"http://fake-pr/{name}")

        live._pipeline_scheduler(
            repo=repo,
            spec_rel="docs/specs/001-x",
            remote="origin",
            base="main",
            model="haiku",
            max_workers=3,
            timeout=30,
            resume=False,
            only=None,
            role_models=None,
            run_budget=None,
            journal_path=journal_path,
            run_id="full-test",
            _spawn=FakeSpawn(),
            _integrate_one=integrate_one,
            _make_verifier=lambda: FakeVerifier(),
        )
        return journal_path

    def test_journal_contains_both_entries_and_groups(self):
        """After a pipelined run, journal has entries (fan-out) and groups (integrate)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = self._run_with_record_group(tmp, repo)

            self.assertTrue(Path(journal_path).exists(), "journal file missing")
            journal = json.loads(Path(journal_path).read_text())

            self.assertIn("entries", journal, "journal missing 'entries'")
            self.assertGreater(
                len(journal["entries"]), 0, "no fan-out entries recorded"
            )

            self.assertIn("groups", journal, "journal missing 'groups'")
            self.assertGreater(len(journal["groups"]), 0, "no group records written")

    def test_subsequent_fanout_record_preserves_groups(self):
        """A fan-out _record() call after _record_group() must not erase the groups key."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            # Use a run_budget of None (unlimited) so all tasks complete and groups are written
            journal_path = self._run_with_record_group(tmp, repo)

            journal = json.loads(Path(journal_path).read_text())
            self.assertIn("groups", journal, "groups dropped by a later _record() call")
            # All three groups should be present
            for name in ("base", "feature-1", "feature-2"):
                self.assertIn(
                    name,
                    journal["groups"],
                    f"group '{name}' missing from journal['groups']",
                )

    def test_journal_file_never_torn(self):
        """Every write produces a valid JSON file (atomic temp-replace, never partial)."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path_str = str(Path(tmp) / "pipeline-journal.json")

            from worktrail.orchestrator import progress

            reads_ok = []
            orig_atomic = progress.atomic_write_text

            def checking_atomic(path, text):
                orig_atomic(path, text)
                # Read back immediately after write; must parse
                try:
                    json.loads(Path(path).read_text())
                    reads_ok.append(True)
                except json.JSONDecodeError:
                    reads_ok.append(False)

            with unittest.mock.patch.object(
                progress, "atomic_write_text", checking_atomic
            ):
                live._pipeline_scheduler(
                    repo=repo,
                    spec_rel="docs/specs/001-x",
                    remote="origin",
                    base="main",
                    model="haiku",
                    max_workers=3,
                    timeout=30,
                    resume=False,
                    only=None,
                    role_models=None,
                    run_budget=None,
                    journal_path=journal_path_str,
                    run_id="full-test",
                    _spawn=FakeSpawn(),
                    _integrate_one=_make_integrate_one()[0],
                    _make_verifier=lambda: FakeVerifier(),
                )

            self.assertTrue(
                all(reads_ok), f"torn journal detected; read results: {reads_ok}"
            )
            self.assertGreater(len(reads_ok), 0, "no journal writes occurred")


# ---------------------------------------------------------------------------
# AC-011 / TASK-006: RunLock — second invocation aborts; flock unavailable degrades
# ---------------------------------------------------------------------------


class RunLockTest(unittest.TestCase):
    """AC-011: second full-real --pipeline aborts; RunLock degrades on no-flock platforms."""

    def test_second_acquire_raises_run_lock_held(self):
        """A second RunLock.acquire() for the same journal raises RunLockHeld."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = Path(tmp) / "run.json"
            lock1 = live.RunLock(journal_path)
            lock2 = live.RunLock(journal_path)
            try:
                lock1.acquire()
                with self.assertRaises(live.RunLockHeld):
                    lock2.acquire()
            finally:
                lock1.release()

    def test_second_invocation_aborts_with_lock_held_message(self):
        """full_real() returns an 'aborted' key when a second run finds the lock held."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = str(Path(tmp) / "run.json")
            lock = live.RunLock(journal_path)
            try:
                lock.acquire()
                # Simulate a second full_real call: it should abort immediately.
                # We test RunLock.acquire() directly to avoid needing a real repo.
                with self.assertRaises(live.RunLockHeld) as ctx:
                    live.RunLock(journal_path).acquire()
                self.assertIn(
                    str(Path(journal_path).with_suffix(".lock")),
                    str(ctx.exception),
                    "lock-held message should name the lock file",
                )
            finally:
                lock.release()

    def test_run_lock_release_allows_reacquire(self):
        """After release(), a new RunLock.acquire() on the same path succeeds."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = Path(tmp) / "run.json"
            lock1 = live.RunLock(journal_path)
            lock1.acquire()
            lock1.release()
            lock2 = live.RunLock(journal_path)
            try:
                lock2.acquire()  # must not raise
            finally:
                lock2.release()

    def test_run_lock_degrades_when_fcntl_unavailable(self):
        """RunLock.acquire() is a no-op when fcntl cannot be imported (non-POSIX)."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl on this platform")
            return real_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = Path(tmp) / "run.json"
            with unittest.mock.patch("builtins.__import__", side_effect=mock_import):
                lock = live.RunLock(journal_path)
                result = lock.acquire()  # must not raise
                self.assertIs(result, lock, "acquire() should return self (no-op)")
                lock.release()  # must not raise


# ---------------------------------------------------------------------------
# AC-013 / TASK-007: pipelined resume — interleaved journal replay
# ---------------------------------------------------------------------------


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
    """Entries that drive TASK-001/002/003 each to 'done' via journal replay."""
    entries = []
    for tid in ("TASK-001", "TASK-002", "TASK-003"):
        entries.extend(
            [
                _journal_entry(tid, "implement"),
                _journal_entry(tid, "review", review_status="PASSED"),
                _journal_entry(tid, "cleanup"),
            ]
        )
    return entries


def _write_journal(path: str, entries: list, groups: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(
            {
                "run_id": "full-test",
                "spec_id": "001-x",
                "entries": entries,
                "groups": groups,
            }
        )
    )


class PipelineResumeTest(unittest.TestCase):
    """AC-013: Pipeline resume replays interleaved journal entries and reconstructs
    per-group/per-task state; skips already-integrated/merged groups; preserves order."""

    def _resume_run(self, tmp, repo, integrate_one, verifier, journal_groups):
        journal_path = str(Path(tmp) / "pipeline-journal.json")
        _write_journal(journal_path, _all_done_entries(), journal_groups)
        return _run(repo, tmp, FakeSpawn(), integrate_one, verifier, resume=True)

    def test_merged_group_not_reintegrated_on_resume(self):
        """AC-013/REQ-NR002: A group with state==MERGED is not re-integrated or re-merged."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()

            self._resume_run(
                tmp,
                repo,
                integrate_one,
                FakeVerifier(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "MERGED",
                    },
                },
            )

            base_calls = [e for e in events if e == "base"]
            self.assertEqual(
                base_calls,
                [],
                f"integrate_one must NOT be called for MERGED 'base'; events={events}",
            )

    def test_merged_group_not_in_quarantined(self):
        """A MERGED group is terminal success — not quarantined — on resume."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            result = self._resume_run(
                tmp,
                repo,
                integrate_one,
                FakeVerifier(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "MERGED",
                    },
                },
            )

            self.assertNotIn(
                "base",
                result["quarantined"],
                "MERGED group must not appear in quarantined",
            )

    def test_open_integrated_group_skips_integrate_resumes_at_verify(self):
        """AC-013: A group with pr_url but state==OPEN is not re-integrated; resumes at verify."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()
            verifier = FakeVerifier()

            self._resume_run(
                tmp,
                repo,
                integrate_one,
                verifier,
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "OPEN",
                    },
                },
            )

            base_integrate_calls = [e for e in events if e == "base"]
            self.assertEqual(
                base_integrate_calls,
                [],
                "integrate_one must NOT be called for already-integrated 'base'",
            )
            self.assertIn(
                "base",
                verifier.calls,
                "verifier must be called for 'base' (resume at verify)",
            )

    def test_completed_fanout_tasks_skipped_on_resume(self):
        """AC-013: Completed fan-out tasks are reconstructed as done and not re-driven."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            # TASK-001 is journal-marked done with no branch materialized --
            # dependency_start_ref's fallback (no branch -> start from HEAD)
            # assumes that means it's already merged into HEAD. Commit its
            # declared file to match that invariant, or the dependency-file
            # guard correctly flags TASK-002's worktree as missing it.
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "task-001.txt").write_text("TASK-001\n")
            subprocess.run(
                ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "commit",
                    "-q",
                    "-m",
                    "TASK-001 (pre-merged for resume test)",
                ],
                check=True,
                capture_output=True,
            )
            spawn = FakeSpawn()
            integrate_one, _ = _make_integrate_one()

            journal_path = str(Path(tmp) / "pipeline-journal.json")
            # Only TASK-001 done; TASK-002/003 have no entries → remain pending
            _write_journal(
                journal_path,
                [
                    _journal_entry("TASK-001", "implement"),
                    _journal_entry("TASK-001", "review", review_status="PASSED"),
                    _journal_entry("TASK-001", "cleanup"),
                ],
                {},
            )
            _run(repo, tmp, spawn, integrate_one, FakeVerifier(), resume=True)

            # TASK-001 was done from journal; FakeSpawn must NOT see it again
            t001_calls = [(tid, role) for tid, role in spawn.calls if tid == "TASK-001"]
            self.assertEqual(
                t001_calls,
                [],
                f"TASK-001 already done in journal — must not be re-driven; calls={spawn.calls}",
            )
            # TASK-002/003 were pending and must have been driven
            t002_calls = [tid for tid, _ in spawn.calls if tid == "TASK-002"]
            self.assertGreater(
                len(t002_calls), 0, "TASK-002 should be driven from resume"
            )

    def test_base_before_dependent_order_preserved_on_resume(self):
        """AC-013: Base-before-dependent order is preserved across the resumed run."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()

            # All tasks done; base MERGED so feature groups proceed normally
            self._resume_run(
                tmp,
                repo,
                integrate_one,
                FakeVerifier(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "MERGED",
                    },
                },
            )

            # Both feature groups integrated (base's done event fired before them)
            feature_calls = [e for e in events if e.startswith("feature")]
            self.assertEqual(
                len(feature_calls),
                2,
                f"Both feature groups should integrate after base; got={events}",
            )

    def test_corrupt_journal_starts_fresh_no_crash(self):
        """Error scenario: an unreadable journal on resume starts fresh rather than crashing."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            Path(journal_path).write_text("{not valid json!!!")
            integrate_one, _ = _make_integrate_one()
            try:
                result = _run(
                    repo, tmp, FakeSpawn(), integrate_one, FakeVerifier(), resume=True
                )
            except Exception as exc:  # noqa: BLE001
                self.fail(f"Corrupt journal must not cause a crash; got {exc!r}")
            for key in ("group_prs", "final", "quarantined", "merged"):
                self.assertIn(
                    key,
                    result,
                    f"summary key '{key}' missing after corrupt-journal resume",
                )


class PipelineReIntegrateTest(unittest.TestCase):
    """brief 20260723-102500 Bug 2: --re-integrate was silently inert under
    --pipeline -- _pipeline_scheduler never received the flag at all, so a
    group with a (possibly bogus) recorded pr_url was always treated as
    already-integrated and skipped straight to verify, exactly like an
    ordinary resume. --re-integrate must force a rebuild, same as the
    non-pipeline path."""

    def test_re_integrate_forces_reintegration_of_open_group(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(
                journal_path,
                _all_done_entries(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "OPEN",
                    },
                },
            )

            _run(
                repo,
                tmp,
                FakeSpawn(),
                integrate_one,
                FakeVerifier(),
                resume=True,
                re_integrate=True,
                journal_path=journal_path,
            )

            base_calls = [e for e in events if e == "base"]
            self.assertEqual(
                len(base_calls),
                1,
                f"--re-integrate must force integrate_one to run again for 'base'; events={events}",
            )

    def test_re_integrate_preserves_merged_group(self):
        """A MERGED group cannot be reopened -- re-integrate must not touch it."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(
                journal_path,
                _all_done_entries(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "MERGED",
                    },
                },
            )

            _run(
                repo,
                tmp,
                FakeSpawn(),
                integrate_one,
                FakeVerifier(),
                resume=True,
                re_integrate=True,
                journal_path=journal_path,
            )

            base_calls = [e for e in events if e == "base"]
            self.assertEqual(
                base_calls,
                [],
                f"a MERGED group must never be re-integrated, even with --re-integrate; events={events}",
            )

    def test_without_re_integrate_open_group_still_skipped(self):
        """Baseline: resume alone (no --re-integrate) keeps today's skip behavior."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            _write_journal(
                journal_path,
                _all_done_entries(),
                {
                    "base": {
                        "pr_url": "http://pr/base",
                        "head_branch": "full-test/base",
                        "state": "OPEN",
                    },
                },
            )

            _run(
                repo,
                tmp,
                FakeSpawn(),
                integrate_one,
                FakeVerifier(),
                resume=True,
                re_integrate=False,
                journal_path=journal_path,
            )

            base_calls = [e for e in events if e == "base"]
            self.assertEqual(
                base_calls, [], f"no --re-integrate; must still skip; events={events}"
            )


# ---------------------------------------------------------------------------
# AD-004 / TASK-007: --from-verify backward compat
# ---------------------------------------------------------------------------


class FromVerifyBackwardCompatTest(unittest.TestCase):
    """AD-004: --from-verify still reconstructs group_branch from journal['groups']."""

    def test_from_verify_reconstructs_group_branch_from_journal(self):
        """--from-verify reads journal groups and passes correct group_branch to verify."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            (repo.parent / f"{repo.name}-worktrees").mkdir(parents=True, exist_ok=True)

            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(
                json.dumps(
                    {
                        "run_id": "full-test",
                        "spec_id": "001-x",
                        "entries": [],
                        "groups": {
                            "base": {
                                "pr_url": "http://pr/base",
                                "head_branch": "full-test/base",
                                "state": "OPEN",
                            },
                            "feature-1": {
                                "pr_url": "http://pr/f1",
                                "head_branch": "full-test/feature-1",
                                "state": "OPEN",
                            },
                            "feature-2": {
                                "pr_url": "http://pr/f2",
                                "head_branch": "full-test/feature-2",
                                "state": "OPEN",
                            },
                        },
                        "integrate_complete": True,
                    }
                )
            )

            captured = {}

            def mock_vac(
                repo_, remote, base_, spec_id, groups, group_branch, delivered, **kw
            ):
                captured["group_branch"] = dict(group_branch)
                return {"merged": [], "quarantined": {}}

            from worktrail.orchestrator import verify

            with unittest.mock.patch.object(verify, "verify_and_cleanup", mock_vac):
                live._full_real_inner(
                    str(repo),
                    "docs/specs/001-x",
                    remote="origin",
                    base="main",
                    from_verify=True,
                )

            self.assertIn("group_branch", captured, "verify_and_cleanup was not called")
            expected = {
                "base": "full-test/base",
                "feature-1": "full-test/feature-1",
                "feature-2": "full-test/feature-2",
            }
            self.assertEqual(
                captured["group_branch"],
                expected,
                f"group_branch mismatch; got {captured.get('group_branch')}",
            )

    def test_from_verify_returns_automerge_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            (repo.parent / f"{repo.name}-worktrees").mkdir(parents=True, exist_ok=True)

            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(
                json.dumps(
                    {
                        "run_id": "full-test",
                        "spec_id": "001-x",
                        "entries": [],
                        "groups": {
                            "feature-1": {
                                "pr_url": "http://pr/f1",
                                "head_branch": "full-test/feature-1",
                                "state": "OPEN",
                            },
                        },
                        "integrate_complete": True,
                    }
                )
            )

            def mock_vac(
                repo_, remote, base_, spec_id, groups, group_branch, delivered, **kw
            ):
                return {
                    "merged": ["feature-1"],
                    "quarantined": {},
                    "self_merged": {},
                    "automerge_evidence": {
                        "feature-1": {
                            "enabledBy": "app/github-actions",
                            "mergedBy": "app/github-actions",
                        }
                    },
                }

            from worktrail.orchestrator import verify

            with unittest.mock.patch.object(verify, "verify_and_cleanup", mock_vac):
                result = live._full_real_inner(
                    str(repo),
                    "docs/specs/001-x",
                    remote="origin",
                    base="main",
                    from_verify=True,
                )

            self.assertEqual(
                result["automerge_evidence"],
                {
                    "feature-1": {
                        "enabledBy": "app/github-actions",
                        "mergedBy": "app/github-actions",
                    }
                },
            )


# ---------------------------------------------------------------------------
# --role-agent-map coverage for the default assembly-resolve spawn
# ---------------------------------------------------------------------------


class AssemblyResolveRoleAgentMapTest(unittest.TestCase):
    """The scheduler's default assembly_resolve_spawn must honor
    --role-agent-map / --model-map for dispatch.ROLE_ASSEMBLY_RESOLVE instead
    of silently inheriting the primary --agent (the review spawn path already
    honors the map; this role previously did not)."""

    def _captured_make_live_spawn(
        self, role_agents=None, role_models=None, agent="opencode", model="oc-model"
    ):
        """Run the scheduler with fakes; return the kwargs verify._make_live_spawn
        received for the assembly-resolve default (the only call site left live —
        the Verifier's own _make_live_spawn calls are bypassed via _make_verifier)."""
        captured = []

        def fake_make_live_spawn(model_, timeout=1800, agent="claude"):
            captured.append({"model": model_, "agent": agent})
            return lambda prompt, wt: ""

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            spawn = FakeSpawn()
            integrate_one, _ = _make_integrate_one()
            verifier = FakeVerifier()
            with unittest.mock.patch(
                "worktrail.orchestrator.verify._make_live_spawn",
                side_effect=fake_make_live_spawn,
            ):
                live._pipeline_scheduler(
                    repo=repo,
                    spec_rel="docs/specs/001-x",
                    remote="origin",
                    base="main",
                    model=model,
                    max_workers=3,
                    timeout=30,
                    resume=False,
                    only=None,
                    role_models=role_models,
                    run_budget=None,
                    journal_path=str(Path(tmp) / "pipeline-journal.json"),
                    run_id="full-test",
                    agent=agent,
                    role_agents=role_agents,
                    _spawn=spawn,
                    _integrate_one=integrate_one,
                    _make_verifier=lambda: verifier,
                )
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_mapped_role_routes_to_override_agent_with_its_own_default_model(self):
        got = self._captured_make_live_spawn(
            role_agents={live.dispatch.ROLE_ASSEMBLY_RESOLVE: "claude"}
        )
        self.assertEqual(got["agent"], "claude")
        # Overridden agent resolves its OWN default model, not the run's
        # opencode model (an invalid model id for the claude CLI).
        self.assertEqual(got["model"], live._default_model_for_agent("claude"))

    def test_explicit_role_model_wins_over_override_agent_default(self):
        got = self._captured_make_live_spawn(
            role_agents={live.dispatch.ROLE_ASSEMBLY_RESOLVE: "claude"},
            role_models={live.dispatch.ROLE_ASSEMBLY_RESOLVE: "opus"},
        )
        self.assertEqual(got["agent"], "claude")
        self.assertEqual(got["model"], "opus")

    def test_no_map_falls_back_to_primary_agent_and_model(self):
        got = self._captured_make_live_spawn(role_agents=None)
        self.assertEqual(got["agent"], "opencode")
        self.assertEqual(got["model"], "oc-model")

    def test_map_without_assembly_resolve_key_stays_on_primary_agent(self):
        # A map naming only review (the common review=claude split) does not
        # implicitly move assembly-resolve; the role must be mapped explicitly.
        got = self._captured_make_live_spawn(role_agents={"review": "claude"})
        self.assertEqual(got["agent"], "opencode")
        self.assertEqual(got["model"], "oc-model")


if __name__ == "__main__":
    unittest.main(verbosity=2)

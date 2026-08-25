#!/usr/bin/env python3
"""The `budget_exceeded` quarantine branch (live.py's post-fanout `if
budget_exceeded:` case) must name which task(s) were actually stuck, not just
say "fan-out incomplete (run budget exceeded)" with zero signal.

Regression coverage for worktrail-go brief
20260824-180046-worktrail-orchestrator-the-fan-out. PR #688 wired
`diagnose_stuck_group()` into the sibling non-budget quarantine branch
(`else:`, `live.py:4809-4824`); this covers the `budget_exceeded` branch
(`live.py:4793-4802`) just above it, left unwired by that PR's scope. Mirrors
`test_diagnose_stuck_group.py`'s tail-kind-blocker case, exercised through
the real `_pipeline_scheduler` post-fanout path instead of calling
`diagnose_stuck_group` directly.

Run: python3 -m pytest tests/orchestrator/test_pipeline_budget_exceeded_diagnosis.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import spawnlib  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402


def _init_repo(root: Path) -> Path:
    """3-task spec: TASK-001 (base, its own group), TASK-002 (tail-kind
    'cleanup', unioned with TASK-003 into one feature group) <- TASK-003."""
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)

    tasks_fm = {
        "TASK-001": (
            "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
            "files: [src/task-001.txt]\nkind: impl\nreview: skip\n---\nbody\n"
        ),
        "TASK-002": (
            "---\nid: TASK-002\nstatus: pending\ndependencies: []\n"
            "files: [src/task-002.txt]\nkind: cleanup\nreview: skip\n---\nbody\n"
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
    """Makes a git commit per task and returns a valid report-back."""

    def __init__(self):
        self.calls = []

    def __call__(self, role, task, wt):
        tid = task["id"]
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


def _make_integrate_one():
    events = []

    def integrate_one(g, repo, spec_id, tasks, remote, run_id, base,
                      journal_path, status, group_branch, quarantined, **kwargs):
        name = g["name"]
        events.append(name)
        deliverable = [t for t in g["tasks"] if status.get(t) in ("done", "completed")]
        if not deliverable:
            quarantined[name] = "no deliverable tasks in {}".format(name)
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

    def verify_one(self, group, group_branch, delivered, merged, quarantined, lock,
                   self_merged=None, armed=None, post_merge_regressed=None):
        with lock:
            self.calls.append(group["name"])
            merged.append(group["name"])


class BudgetExceededDiagnosisTest(unittest.TestCase):
    """TASK-002 is tail-kind ('cleanup') so it never enters the main-loop
    frontier; TASK-003 depends on it so it's tail-blocked too. Their group is
    still non-terminal when the run budget is exhausted -- the
    `if budget_exceeded:` branch fires for it, not the sibling `else` branch
    PR #688 already fixed. The quarantine reason must name TASK-002 as the
    tail blocker, the same signal `diagnose_stuck_group` already proves for
    the non-budget branch."""

    def test_budget_exceeded_quarantine_names_tail_kind_blocker(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = str(Path(tmp) / "pipeline-journal.json")
            integrate_one, _events = _make_integrate_one()

            result = live._pipeline_scheduler(
                repo=repo,
                spec_rel="docs/specs/001-x",
                remote="origin",
                base="main",
                model="haiku",
                max_workers=1,
                timeout=30,
                resume=False,
                only=None,
                role_models=None,
                run_budget=0.001,
                journal_path=journal_path,
                run_id="full-test",
                _spawn=FakeSpawn(),
                _integrate_one=integrate_one,
                _make_verifier=lambda: FakeVerifier(),
            )

            # TASK-002/TASK-003 form their own connected-component group,
            # independent of whatever index plan_groups() assigns it.
            matches = [r for r in result["quarantined"].values() if "TASK-003" in r]
            self.assertEqual(
                len(matches), 1,
                f"expected exactly one quarantined group naming TASK-003; got {result['quarantined']}",
            )
            reason = matches[0]
            self.assertIn("run budget exceeded", reason)
            self.assertIn("tail task(s) TASK-002", reason)
            self.assertIn("tail phase after fan-out", reason)


if __name__ == "__main__":
    unittest.main()

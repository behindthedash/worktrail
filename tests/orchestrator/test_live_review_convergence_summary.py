"""A task that burns all three review rounds escalates to a human, and the
human otherwise has to reconstruct the whole argument by hand from journal
entries scattered across the run. `_apply_step_commit` now rolls this run's
review history for the escalating task -- every prior round plus the report
tripping the circuit breaker -- into a round-ordered `convergence_summary`
list on the escalating entry.

Only the escalating entry carries it: a `FAILED` review that still has rounds
left routes to `fixing` and must journal no `convergence_summary` key at all.

Mirrors the injected-spawn hermetic pattern of
`test_live_run_circuit_breaker_terminal_status.py` against a throwaway git
repo (as `test_fix_role_failed_terminal_status.py` does), so no real `claude -p`
runs and the fixture's golden cassettes are never touched. Drives
`live_run_real`, which is the path that actually goes through
`_apply_step_commit`.
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
    live,
    spawnlib,
)

SPEC_REL = "docs/specs/001-x"


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / SPEC_REL / "tasks").mkdir(parents=True)
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
    (repo / SPEC_REL / "tasks" / "TASK-001.md").write_text(fm)
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


def _report(task_id: str, role: str, sha: str) -> spawnlib.SpawnResult:
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":null}}\n```'
        ),
        usage={},
    )


def _failed_review(
    task_id: str, sha: str, critical: int, major: int, notes: str
) -> spawnlib.SpawnResult:
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"review","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":"FAILED",'
            f'"critical_issues":{critical},"major_issues":{major},'
            f'"notes":"{notes}"}}\n```'
        ),
        usage={},
    )


# Round-by-round verdicts the injected reviewer returns, in order.
ROUNDS = [
    (1, 2, "missing null check in parse()"),
    (1, 1, "null check still missing; new: unbounded loop"),
    (2, 0, "null check still missing; loop still unbounded"),
]


class AlwaysFailReviewSpawn:
    """Every review comes back FAILED with distinguishable counts/notes, so the
    3-strikes circuit breaker trips naturally (dispatch.transition ->
    "escalated") instead of being injected as a synthetic journal entry."""

    def __init__(self):
        self._commit_count = 0
        self.review_round = 0

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if role in ("implement", "fix"):
            self._commit_count += 1
            sha = _commit_file(wt, "foo.py", f"{role}-{self._commit_count}\n")
            return _report(task["id"], role, sha)
        if role == "review":
            critical, major, notes = ROUNDS[self.review_round]
            self.review_round += 1
            return _failed_review(task["id"], sha, critical, major, notes)
        return _report(task["id"], role, sha)


def _run(tmp: Path) -> list:
    repo = _init_repo(tmp)
    journal_path = live.journal_path_for(repo, SPEC_REL)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    live.live_run_real(
        repo,
        SPEC_REL,
        max_workers=1,
        out_cassette=str(journal_path),
        run_id="test-convergence-summary",
        spawn=AlwaysFailReviewSpawn(),
    )
    return json.loads(journal_path.read_text())["entries"]


class TestLiveReviewConvergenceSummary(unittest.TestCase):
    def test_escalating_entry_records_every_review_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = _run(Path(tmp))

        review_entries = [e for e in entries if e.get("role") == "review"]
        self.assertEqual(len(review_entries), 3, "expected exactly 3 strikes")
        escalating = review_entries[-1]
        self.assertEqual(escalating["report"].get("terminal_status"), "escalated")

        summary = escalating.get("convergence_summary")
        self.assertIsNotNone(
            summary,
            "the escalating review entry must carry the round-by-round history "
            "so a human does not have to reconstruct it from the journal",
        )
        self.assertEqual([item["round"] for item in summary], [1, 2, 3])
        self.assertEqual(
            summary,
            [
                {
                    "round": i + 1,
                    "review_status": "FAILED",
                    "critical_issues": critical,
                    "major_issues": major,
                    "notes": notes,
                }
                for i, (critical, major, notes) in enumerate(ROUNDS)
            ],
        )
        # The final item is the escalating report's own verdict, not a copy of
        # an already-journaled round.
        self.assertEqual(
            summary[-1],
            {
                "round": 3,
                "review_status": escalating["report"]["review_status"],
                "critical_issues": escalating["report"]["critical_issues"],
                "major_issues": escalating["report"]["major_issues"],
                "notes": escalating["report"]["notes"],
            },
        )

    def test_non_escalating_failed_review_has_no_convergence_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = _run(Path(tmp))

        review_entries = [e for e in entries if e.get("role") == "review"]
        for i, entry in enumerate(review_entries[:-1]):
            self.assertNotIn(
                "convergence_summary",
                entry,
                f"review round {i + 1} routed to fixing (retry count still below "
                "MAX_REVIEW_RETRIES) and must not carry a convergence summary",
            )


if __name__ == "__main__":
    unittest.main()

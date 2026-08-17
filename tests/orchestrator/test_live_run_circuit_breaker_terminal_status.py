#!/usr/bin/env python3
"""Regression for brief 20260817-073955: `live_run()` (the cassette/demo recording
path used by `orchestrate live-run`/`full`) had the same pre-#496 bug shape as
`_commit_step` -- it built the journal entry's report fields from raw report data
BEFORE calling `dispatch.apply_report()` to compute the transition, and never
stamped `report.terminal_status="escalated"` at all (unlike `_apply_step_commit`,
which PR #496 fixed and PR #498 later deduped into a single shared helper).
`live_run()` was never migrated onto that shared helper, so it kept the bug
independently. A downstream matcher like `clear_tasks()` would silently find
nothing for an entry that should carry a specific terminal_status/outcome field.

Uses an injected AlwaysFailReviewSpawn (no real claude -p) against the real
sample-spec fixture's TASK-001 (no dependencies), constrained via `only=`, so the
test is hermetic and fast and never touches the fixture's own golden cassettes.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _commit_file(wt: Path, name: str, content: str) -> str:
    f = Path(wt) / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", name], check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _report(task_id: str, role: str, sha: str, *, review_status=None) -> spawnlib.SpawnResult:
    rs = f'"{review_status}"' if review_status else "null"
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":{rs}}}\n```'
        ),
        usage={},
    )


class AlwaysFailReviewSpawn:
    """Every review comes back FAILED so the 3-strikes circuit breaker trips
    naturally (dispatch.transition -> "escalated") instead of being injected
    as a synthetic journal entry."""

    def __init__(self):
        self._commit_count = 0

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        if role in ("implement", "fix"):
            self._commit_count += 1
            sha = _commit_file(wt, "schema.ts", f"{role}-{self._commit_count}\n")
        if role == "review":
            return _report(task["id"], role, sha, review_status="FAILED")
        return _report(task["id"], role, sha)


class TestLiveRunCircuitBreakerTerminalStatus(unittest.TestCase):
    def test_naturally_escalated_task_stamps_terminal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "url-shortener-target"
            cassette = Path(tmp) / "cassette.json"
            live.live_run(
                dest=dest,
                max_workers=1,
                out_cassette=str(cassette),
                only=["TASK-001"],
                spawn=AlwaysFailReviewSpawn(),
            )

            journal = json.loads(cassette.read_text())
            review_entries = [e for e in journal["entries"] if e.get("role") == "review"]
            self.assertEqual(len(review_entries), 3, "expected exactly 3 strikes")
            escalating_entry = review_entries[-1]
            self.assertEqual(
                escalating_entry["report"].get("terminal_status"),
                "escalated",
                "the review entry that trips the circuit breaker must carry "
                "terminal_status=escalated so a downstream matcher like "
                "clear_tasks() can find it",
            )


if __name__ == "__main__":
    unittest.main()

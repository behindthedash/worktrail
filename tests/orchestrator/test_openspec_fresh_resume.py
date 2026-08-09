#!/usr/bin/env python3
"""End-to-end regression: a fresh OpenSpec rerun skips tasks already completed
in tasks.md (brief 20260726-162545, following PR #29's write-path fix).

PR #29 fixed `integrate._write_group_task_status` to commit the completion
checkbox for OpenSpec's shared `tasks.md`, and added unit coverage for that
write in isolation (`test_resilience_helpers.py::WriteGroupTaskStatus`). The
parser is unit-tested separately (`test_openspec_source.py`). Neither proves
the full resume contract: that `coordinator.runnable_frontier` actually
excludes a task whose *committed* status is `completed` when a run starts
from a fresh journal (no in-flight/prior-run state at all).

This drives the real read path (`OpenSpecTaskSource.load`), the real
frontier selection (`coordinator.runnable_frontier`), and the real write path
(`integrate._write_group_task_status`) against a committed git fixture --
without spawning a headless agent or running the full `live.py` driver, which
would additionally require mocking RunPlan compilation (a model call) and
worker spawn. `coordinator.runnable_frontier` is fed directly from
`OpenSpecTaskSource.load()`'s output with no journal object anywhere in the
chain, which is what proves "reads the committed tasks.md checkbox rather
than journal-only state": there is no journal-only state to fall back to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from worktrail.orchestrator import coordinator, integrate
from worktrail.taskformats.openspec.source import OpenSpecTaskSource

CHANGE_ID = "001-x"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(root: Path, tasks_md: str) -> Path:
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    change = repo / "openspec" / "changes" / CHANGE_ID
    change.mkdir(parents=True)
    (change / "tasks.md").write_text(tasks_md)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


# Group 1: 1.1 already completed, 1.2 sequential after it.
# Group 2: 2.1 independent of group 1.
TWO_GROUPS_ONE_COMPLETED = (
    "## 1. Setup\n\n"
    "- [x] 1.1 done already\n"
    "- [ ] 1.2 next in group\n\n"
    "## 2. Other track\n\n"
    "- [ ] 2.1 independent\n"
)

TWO_GROUPS_ALL_PENDING = (
    "## 1. Setup\n\n"
    "- [ ] 1.1 done already\n"
    "- [ ] 1.2 next in group\n\n"
    "## 2. Other track\n\n"
    "- [ ] 2.1 independent\n"
)


class TestFreshRunSkipsCommittedCompletedTask:
    """Suggested-approach steps 1-2: committed fixture, fresh journal, completed
    task omitted while the pending frontier still retains its parallel tick."""

    def test_completed_task_absent_from_fresh_frontier(self, tmp_path):
        repo = _init_repo(tmp_path, TWO_GROUPS_ONE_COMPLETED)
        _, tasks = OpenSpecTaskSource(repo).load(CHANGE_ID)

        by_id = {t["id"]: t for t in tasks}
        assert by_id["1.1"]["status"] == "completed"

        # No journal, no in-flight state: this IS the fresh-run frontier.
        frontier = coordinator.runnable_frontier(tasks, max_workers=4)
        frontier_ids = {t["id"] for t in frontier}

        assert "1.1" not in frontier_ids

    def test_pending_frontier_retains_the_parallel_first_tick(self, tmp_path):
        repo = _init_repo(tmp_path, TWO_GROUPS_ONE_COMPLETED)
        _, tasks = OpenSpecTaskSource(repo).load(CHANGE_ID)

        frontier = coordinator.runnable_frontier(tasks, max_workers=4)
        frontier_ids = {t["id"] for t in frontier}

        # 1.2's only dep (1.1) is satisfied by the committed "completed" status;
        # 2.1 has no deps. Both are runnable in the same tick.
        assert frontier_ids == {"1.2", "2.1"}


class TestPostIntegrationFreshRunReadsCommittedCheckbox:
    """Suggested-approach step 3: exercise the write path
    (`integrate._write_group_task_status`), then load fresh again and assert
    the NEXT run reads the committed checkbox -- there is no journal in this
    chain at all, so this is a direct proof against "journal-only state"."""

    def test_next_fresh_load_excludes_task_committed_by_integrate(self, tmp_path):
        repo = _init_repo(tmp_path, TWO_GROUPS_ALL_PENDING)

        # First fresh run: nothing completed yet.
        _, tasks_before = OpenSpecTaskSource(repo).load(CHANGE_ID)
        frontier_before = {t["id"] for t in coordinator.runnable_frontier(tasks_before, max_workers=4)}
        assert frontier_before == {"1.1", "2.1"}
        assert "1.2" not in frontier_before  # blocked on 1.1

        # Simulate group "1" integrating: the real write path commits 1.1's checkbox.
        integrate._write_group_task_status(
            repo, CHANGE_ID, {"name": "group-1", "tasks": ["1.1"]}, {"1.1": "completed"},
        )
        assert "- [x] 1.1 done already" in (repo / "openspec/changes" / CHANGE_ID / "tasks.md").read_text()

        # Next fresh run (no journal survives a --fresh discard): re-load from disk.
        _, tasks_after = OpenSpecTaskSource(repo).load(CHANGE_ID)
        by_id_after = {t["id"]: t for t in tasks_after}
        assert by_id_after["1.1"]["status"] == "completed"

        frontier_after = {t["id"] for t in coordinator.runnable_frontier(tasks_after, max_workers=4)}
        assert "1.1" not in frontier_after
        assert "1.2" in frontier_after  # now unblocked by the committed completion

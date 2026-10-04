#!/usr/bin/env python3
"""The tail phase's journal rewrite must not erase the pipeline phase's group records.

`live_run_real` doubles as the tail phase's writer: `_dispatch_pending_tail`
re-enters it with `out_cassette=journal_path`, and its `record()` rebuilds the
journal dict from the writer's own keys only (spec_id, entries,
gitnexus_capability, run_id, ...). The pipeline phase, by contrast, owns
`groups` (its `_record()`/`_record_group_fn`) and `integrate_complete`
(`integrate._mark_integrate_complete_if_terminal`). Before the declared-key
carry-forward, the tail rewrite wiped both: a QUARANTINED group record vanished
from the journal mid-run (`worktrail-resume-group` then failed with "no record
in journal"), and the post-tail `_mark_integrate_complete_if_terminal` re-read
saw an empty `groups` map.

`live_run_real.record()` now declares `PLAN_PIN_KEYS + PIPELINE_PHASE_KEYS`
through `_carry_forward_keys`; the pipeline scheduler's `_record()` keeps
carrying the pin keys only (`_preserve_plan_pin`), because it owns `groups`
and writes them itself.

Run: python3 -m pytest tests/orchestrator/test_tail_journal_group_survival.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import live, resume_group, spawnlib

SPEC = "docs/specs/001-x"

QUARANTINED = {
    "state": "QUARANTINED",
    "quarantine_reason": "ci_failed",
    "quarantine_detail": "CI failed after 3 retries",
    "pr_url": "https://example.test/pr/1",
    "head_branch": "full-1/base",
}
MERGED = {
    "state": "MERGED",
    "pr_url": "https://example.test/pr/2",
    "head_branch": "full-1/feature-1",
}


def _tail_shaped_rebuild(journal_path: Path) -> dict:
    """Rewrite `journal_path` exactly the way the tail phase's writer does.

    Mirrors `live_run_real.record()`: build the dict from the writer's OWN keys
    only, run the declared-key carry-forward with the union the production
    closure uses, and persist. `test_real_tail_dispatch_rewrite_keeps_group_records`
    below drives the actual closure; these shaped tests cover arrangements that
    are awkward to reach through a live tail dispatch.
    """
    journal_dict = {
        "spec_id": "001-x",
        "entries": [],
        "gitnexus_capability": {},
        "run_id": "full-tail",
    }
    live._carry_forward_keys(
        journal_path, journal_dict, live.PLAN_PIN_KEYS + live.PIPELINE_PHASE_KEYS
    )
    journal_path.write_text(json.dumps(journal_dict, indent=2, sort_keys=True) + "\n")
    return journal_dict


class TailShapedRewriteKeepsGroupRecords(unittest.TestCase):
    def test_quarantined_record_and_integrate_complete_survive(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-001-x.json"
            jp.write_text(
                json.dumps(
                    {
                        "spec_id": "001-x",
                        "entries": [],
                        "groups": {"base": dict(QUARANTINED)},
                        "integrate_complete": True,
                    }
                )
            )
            _tail_shaped_rebuild(jp)
            after = json.loads(jp.read_text())
            self.assertEqual(after["groups"]["base"], QUARANTINED)
            self.assertIs(after["integrate_complete"], True)
            self.assertIn("base", after["groups"])
            self.assertIn("integrate_complete", after)

    def test_merged_record_survives(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-001-x.json"
            jp.write_text(json.dumps({"groups": {"feature-1": dict(MERGED)}}))
            _tail_shaped_rebuild(jp)
            after = json.loads(jp.read_text())
            self.assertEqual(after["groups"]["feature-1"], MERGED)

    def test_surviving_records_keep_the_plan_pin_too(self):
        """The tail writer's declared union carries both tuples: group state
        AND the plan pin, in one rewrite."""
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-001-x.json"
            jp.write_text(
                json.dumps(
                    {
                        "groups": {"base": dict(QUARANTINED)},
                        "plan_fingerprint": "a" * 64,
                        "plan_fingerprints": ["a" * 64],
                    }
                )
            )
            _tail_shaped_rebuild(jp)
            after = json.loads(jp.read_text())
            self.assertEqual(after["plan_fingerprint"], "a" * 64)
            self.assertEqual(after["groups"]["base"], QUARANTINED)

    def test_cleared_quarantine_is_not_resurrected_by_a_later_rewrite(self):
        """The `worktrail-resume-group` direction: once a QUARANTINED record is
        cleared from disk, a later tail rewrite must not bring it back."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            (Path(td) / "repo-worktrees").mkdir()
            jp = live.journal_path_for(repo, SPEC)
            jp.write_text(
                json.dumps(
                    {
                        "spec_id": "001-x",
                        "groups": {
                            "base": dict(QUARANTINED),
                            "feature-1": dict(MERGED),
                        },
                        "integrate_complete": True,
                    }
                )
            )
            rc = resume_group.main(
                ["--repo", str(repo), "--spec", SPEC, "--group", "base"]
            )
            self.assertEqual(rc, 0)
            cleared = json.loads(jp.read_text())
            self.assertNotIn("base", cleared["groups"])
            self.assertNotIn("integrate_complete", cleared)

            _tail_shaped_rebuild(jp)
            after = json.loads(jp.read_text())
            self.assertNotIn("base", after["groups"])
            self.assertNotIn("integrate_complete", after)
            self.assertEqual(after["groups"]["feature-1"], MERGED)

    def test_re_integrate_cleared_records_stay_cleared(self):
        """The `--re-integrate` direction: `_clear_integration_state` drops
        `integrate_complete` and every non-MERGED record before the pipeline
        resumes; a later tail rewrite must not resurrect them."""
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-001-x.json"
            reset = {
                "groups": {"base": dict(QUARANTINED), "feature-1": dict(MERGED)},
                "integrate_complete": True,
            }
            self.assertTrue(live._clear_integration_state(reset))
            jp.write_text(json.dumps(reset))
            self.assertNotIn("base", reset["groups"])
            self.assertNotIn("integrate_complete", reset)

            _tail_shaped_rebuild(jp)
            after = json.loads(jp.read_text())
            self.assertNotIn("base", after["groups"])
            self.assertNotIn("integrate_complete", after)
            self.assertEqual(after["groups"]["feature-1"], MERGED)

    def test_absent_or_unreadable_journal_leaves_rebuild_keys_intact(self):
        """Journal I/O never takes a run down: a missing, malformed,
        non-object, or non-UTF-8 journal is a no-op, so the rebuild persists
        exactly its own keys instead of raising."""
        cases = {
            "missing.json": None,
            "malformed.json": b"{not json",
            "nonobject.json": b"[1, 2, 3]",
            "nonutf8.json": b"\xff\xfe\x00\x01",
        }
        for name, content in cases.items():
            with self.subTest(journal=name), tempfile.TemporaryDirectory() as td:
                jp = Path(td) / name
                if content is not None:
                    jp.write_bytes(content)
                rebuilt = _tail_shaped_rebuild(jp)
                self.assertEqual(
                    rebuilt,
                    {
                        "spec_id": "001-x",
                        "entries": [],
                        "gitnexus_capability": {},
                        "run_id": "full-tail",
                    },
                )
                self.assertEqual(json.loads(jp.read_text()), rebuilt)


def _init_repo(root: Path) -> Path:
    """Scratch git repo: TASK-001 done impl, TASK-002 pending e2e depending on it."""
    repo = root / "repo"
    tasks_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    tasks_dir.mkdir(parents=True)
    for tid, status, deps, kind in (
        ("TASK-001", "done", "", "impl"),
        ("TASK-002", "pending", "TASK-001", "e2e"),
    ):
        (tasks_dir / f"{tid}.md").write_text(
            "---\n"
            f"id: {tid}\n"
            f"status: {status}\n"
            f"dependencies: [{deps}]\n"
            f"files: [src/{tid.lower()}.txt]\n"
            f"kind: {kind}\n"
            "---\n\nbody\n"
        )
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


class FakeSpawn:
    """Commits real work for implement/fix and reports success (test_fanout_concurrency pattern)."""

    def __init__(self):
        self.calls = []

    def __call__(self, role, task, wt):
        self.calls.append((role, task["id"]))
        if role in ("implement", "fix"):
            f = Path(wt) / "src" / f"{task['id'].lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{task['id']} {role}\n")
            subprocess.run(
                ["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"{role} {task['id']}"],
                check=True,
                capture_output=True,
            )
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        rs = '"PASSED"' if role == "review" else "null"
        return spawnlib.SpawnResult(
            text=(
                f'```json\n{{"task":"{task["id"]}","step":"{role}","status":"success",'
                f'"head_sha":"{sha[:8]}","review_status":{rs}}}\n```'
            ),
            usage={},
        )


class RealTailDispatchKeepsGroupRecords(unittest.TestCase):
    """The end-to-end shape: a real `_dispatch_pending_tail` run rewrites the
    canonical journal through `live_run_real.record()`, and the pipeline
    phase's group records must still be there afterwards."""

    def test_real_tail_dispatch_rewrite_keeps_group_records(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = live.journal_path_for(repo, SPEC)
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(
                json.dumps(
                    {
                        "spec_id": "001-x",
                        "entries": [],
                        "groups": {
                            "base": dict(QUARANTINED),
                            "feature-1": dict(MERGED),
                        },
                        "integrate_complete": True,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            fake = FakeSpawn()
            # Mirrors `_pipeline_scheduler`'s in-memory task list: the e2e task
            # is still pending, so the tail phase has outstanding work.
            gate_tasks = [
                {"id": "TASK-001", "status": "done", "kind": "impl", "deps": []},
                {
                    "id": "TASK-002",
                    "status": "pending",
                    "kind": "e2e",
                    "deps": ["TASK-001"],
                },
            ]

            result = live._dispatch_pending_tail(
                repo,
                SPEC,
                str(journal_path),
                "test-tail-groups",
                gate_tasks,
                3,  # max_workers
                live.DEFAULT_AGENT,
                None,  # model
                60,  # timeout
                None,  # role_models
                None,  # role_agents
                None,  # fallback_agent
                None,  # tier_map
                None,  # purpose_tier_map
                None,  # fallback_chain
                None,  # effort
                None,  # run_budget
                spawn=fake,
            )

            self.assertIsNotNone(
                result, "tail dispatch was a no-op; test setup is wrong"
            )
            self.assertIn(
                "TASK-002",
                {tid for _role, tid in fake.calls},
                "the tail task never reached a worker; no rewrite happened",
            )

            after = json.loads(journal_path.read_text())
            # The tail writer's own entry proves its rebuild rewrote this file.
            self.assertTrue(after["entries"], "tail writer never rewrote the journal")
            self.assertIn("groups", after, "tail rewrite erased the group records")
            self.assertEqual(after["groups"]["base"], QUARANTINED)
            self.assertEqual(after["groups"]["feature-1"], MERGED)
            self.assertIs(after["integrate_complete"], True)
            # `apply_run_plan` stamped a real plan pin before the tail phase;
            # the same rewrite must carry it forward too.
            self.assertTrue(after.get("plan_fingerprint"))
            self.assertIn(after["plan_fingerprint"], after.get("plan_fingerprints", []))


if __name__ == "__main__":
    unittest.main()

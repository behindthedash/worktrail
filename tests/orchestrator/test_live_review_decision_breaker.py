"""A second FAILED review that names a planner/human decision escalates early
with a `pending_decision` envelope and files one idempotent decision record.

Mirrors the injected-spawn hermetic pattern of
`test_live_review_convergence_summary.py`: throwaway git repo, `live_run_real`,
scripted review/fix reports, `WORK_QUEUE_DIR` pointed at a tmp dir so
`decisions.ask()` never touches `~/work-queue`.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live, spawnlib
from worktrail.workqueue import decisions

SPEC_REL = "docs/specs/001-x"
DECISION_TEXT = "AC 2 requires refusing symlinks but test_symlink_ok asserts they pass"


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
    body = {
        "task": task_id,
        "step": role,
        "status": "success",
        "head_sha": sha[:8],
        "review_status": None,
    }
    return spawnlib.SpawnResult(text=f"```json\n{json.dumps(body)}\n```", usage={})


def _failed_review(
    task_id: str, sha: str, notes: str, decision_required: str | None
) -> spawnlib.SpawnResult:
    body = {
        "task": task_id,
        "step": "review",
        "status": "success",
        "head_sha": sha[:8],
        "review_status": "FAILED",
        "critical_issues": 1,
        "major_issues": 0,
        "notes": notes,
        "decision_required": decision_required,
    }
    return spawnlib.SpawnResult(text=f"```json\n{json.dumps(body)}\n```", usage={})


class ScriptedSpawn:
    """Implement/fix commit a file and succeed; each review returns the next
    scripted (notes, decision_required) verdict, always FAILED."""

    def __init__(self, rounds: list):
        self.rounds = rounds
        self.review_round = 0
        self.calls: list = []
        self._commit_count = 0

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append((role, task["id"]))
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
            notes, decision = self.rounds[self.review_round]
            self.review_round += 1
            return _failed_review(task["id"], sha, notes, decision)
        return _report(task["id"], role, sha)


DECISION_ON_ROUND_2 = [
    ("missing test", None),
    ("still missing; contradicts existing test", DECISION_TEXT),
    ("should never be reached", None),
]
DECISION_ON_ROUND_1 = [
    ("contradicts existing test", DECISION_TEXT),
    ("still contradicts", DECISION_TEXT),
]
ORDINARY = [
    ("missing null check", None),
    ("null check still missing", None),
    ("null check still missing; loop unbounded", None),
]


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = _init_repo(self.tmp)
        self.queue = self.tmp / "wq"
        self.queue.mkdir()
        self._prev_wq = os.environ.get("WORK_QUEUE_DIR")
        os.environ["WORK_QUEUE_DIR"] = str(self.queue)
        self.journal_path = live.journal_path_for(self.repo, SPEC_REL)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self._prev_wq is None:
            os.environ.pop("WORK_QUEUE_DIR", None)
        else:
            os.environ["WORK_QUEUE_DIR"] = self._prev_wq
        self._tmp.cleanup()

    def _run(self, spawn, run_id="test-decision-breaker", resume=False) -> dict:
        return live.live_run_real(
            self.repo,
            SPEC_REL,
            max_workers=1,
            out_cassette=str(self.journal_path),
            run_id=run_id,
            spawn=spawn,
            resume=resume,
        )

    def _entries(self) -> list:
        return json.loads(self.journal_path.read_text())["entries"]

    def _reviews(self) -> list:
        return [e for e in self._entries() if e.get("role") == "review"]

    def _open_records(self) -> list:
        d = decisions.decisions_dir(self.queue) / "open"
        return sorted(d.glob("*.md")) if d.exists() else []


class TestDecisionBreaker(_Base):
    def test_second_failed_review_naming_decision_escalates(self):
        spawn = ScriptedSpawn(DECISION_ON_ROUND_2)
        res = self._run(spawn)

        by_id = {t["id"]: t for t in res["tasks"]}
        self.assertEqual(by_id["TASK-001"]["status"], "escalated")
        reviews = self._reviews()
        self.assertEqual(len(reviews), 2)
        self.assertEqual([r for r, _ in spawn.calls].count("fix"), 1)
        self.assertEqual(
            [r for r, _ in spawn.calls], ["implement", "review", "fix", "review"]
        )

        first, second = reviews
        self.assertNotIn("escalation_reason", first)
        self.assertNotIn("pending_decision", first)
        self.assertNotIn("convergence_summary", first)

        self.assertEqual(second["report"]["terminal_status"], "escalated")
        self.assertEqual(second["escalation_reason"], "decision-required")
        self.assertEqual([c["round"] for c in second["convergence_summary"]], [1, 2])
        env = second["pending_decision"]
        self.assertEqual(env["schema"], decisions.DECISION_ENVELOPE_SCHEMA)
        self.assertEqual(env["question"], DECISION_TEXT)
        self.assertEqual(env["provenance"]["subject"], f"{SPEC_REL}/TASK-001")
        self.assertEqual(env["provenance"]["source"], "orchestrator-review-loop")
        self.assertEqual(env["provenance"]["repo"], str(self.repo))
        self.assertEqual(env["provenance"]["run_id"], "test-decision-breaker")
        self.assertEqual(len(env["options"]), 2)
        self.assertEqual(
            env["decision_id"],
            decisions.decision_identity(
                "orchestrator-review-loop",
                str(self.repo),
                f"{SPEC_REL}/TASK-001",
                DECISION_TEXT,
            ),
        )

    def test_round1_decision_naming_failed_still_routes_to_fixing(self):
        spawn = ScriptedSpawn(DECISION_ON_ROUND_1)
        self._run(spawn)
        first = self._reviews()[0]
        self.assertNotIn("pending_decision", first)
        self.assertNotIn("escalation_reason", first)
        self.assertNotIn("terminal_status", first["report"] or {})
        self.assertIsNone(first["report"].get("terminal_status"))
        # Round 1 got its fix round; round 2 then tripped the decision breaker.
        self.assertIn(("fix", "TASK-001"), spawn.calls)

    def test_ordinary_repeated_failed_reviews_keep_three_round_breaker(self):
        spawn = ScriptedSpawn(ORDINARY)
        res = self._run(spawn)
        by_id = {t["id"]: t for t in res["tasks"]}
        self.assertEqual(by_id["TASK-001"]["status"], "escalated")
        reviews = self._reviews()
        self.assertEqual(len(reviews), 3)
        self.assertIsNone(reviews[1]["report"].get("terminal_status"))
        self.assertEqual(reviews[2]["report"]["terminal_status"], "escalated")
        for r in reviews:
            self.assertNotIn("escalation_reason", r)
            self.assertNotIn("pending_decision", r)
        self.assertEqual(self._open_records(), [])

    def test_resume_from_escalated_journal_redispatches_nothing(self):
        self._run(ScriptedSpawn(DECISION_ON_ROUND_2))
        self.assertEqual(len(self._open_records()), 1)
        entries_before = self._entries()

        spawn2 = ScriptedSpawn(DECISION_ON_ROUND_2)
        res = self._run(spawn2, resume=True)
        by_id = {t["id"]: t for t in res["tasks"]}
        self.assertEqual(by_id["TASK-001"]["status"], "escalated")
        self.assertEqual(
            [c for c in spawn2.calls if c[0] in ("review", "fix", "implement")], []
        )
        self.assertEqual(
            len(self._reviews()),
            len([e for e in entries_before if e.get("role") == "review"]),
        )
        self.assertEqual(len(self._open_records()), 1)


class TestDecisionRecordFiling(_Base):
    def test_record_lands_in_decisions_open(self):
        self._run(ScriptedSpawn(DECISION_ON_ROUND_2))
        env = self._reviews()[-1]["pending_decision"]
        records = self._open_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].stem, env["decision_id"])
        rec = decisions.find_decision(env["decision_id"], self.queue)
        self.assertIsNotNone(rec)
        fm = rec["fm"]
        self.assertEqual(fm["id"], env["decision_id"])
        self.assertEqual(fm["source"], "orchestrator-review-loop")
        self.assertEqual(fm["run-id"], "test-decision-breaker")
        self.assertEqual(fm["subject"], f"{SPEC_REL}/TASK-001")
        body = records[0].read_text()
        self.assertEqual(len(decisions._extract_options(body)), 2)
        self.assertIn("worktrail-live clear-task --tasks TASK-001", body)
        self.assertIn(DECISION_TEXT, body)

    def test_unwritable_queue_dir_leaves_status_and_envelope_intact(self):
        missing = self.tmp / "nope" / "deeper"
        blocker = self.tmp / "nope"
        blocker.write_text("not a directory\n")
        os.environ["WORK_QUEUE_DIR"] = str(missing)
        res = self._run(ScriptedSpawn(DECISION_ON_ROUND_2))
        by_id = {t["id"]: t for t in res["tasks"]}
        self.assertEqual(by_id["TASK-001"]["status"], "escalated")
        last = self._reviews()[-1]
        self.assertEqual(last["escalation_reason"], "decision-required")
        self.assertEqual(last["pending_decision"]["question"], DECISION_TEXT)
        self.assertFalse(missing.exists())

    def test_second_ask_for_same_identity_converges_on_one_record(self):
        self._run(ScriptedSpawn(DECISION_ON_ROUND_2))
        env = self._reviews()[-1]["pending_decision"]
        self.assertEqual(len(self._open_records()), 1)
        # A second escalation on identical (repo, subject, question): same
        # envelope identity, so ask() must return the existing record.
        live._file_review_decision(
            env,
            repo=self.repo,
            spec_rel=SPEC_REL,
            run_id="test-decision-breaker",
            task_id="TASK-001",
            question=DECISION_TEXT,
            rounds=2,
        )
        self.assertEqual(len(self._open_records()), 1)
        result = decisions.ask(
            DECISION_TEXT,
            background="b",
            why="w",
            context="c",
            options=list(env["options"]),
            decision_id=env["decision_id"],
            source="orchestrator-review-loop",
            subject=env["provenance"]["subject"],
            repo=str(self.repo),
            queue_base=self.queue,
        )
        self.assertEqual(result["status"], "existing")
        self.assertEqual(len(self._open_records()), 1)


if __name__ == "__main__":
    unittest.main()


class TestBreakerCallersAndD5(_Base):
    def test_every_apply_step_commit_caller_threads_decision_context(self):
        """Both schedulers (`live_run_real` and `_pipeline_scheduler`) must pass
        repo/spec_rel/run_id, or the breaker escalates without an envelope on
        the production full-real path."""
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(live))
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_apply_step_commit"
        ]
        self.assertEqual(
            len(calls), 2, "expected exactly two _apply_step_commit callers"
        )
        for call in calls:
            kws = {k.arg for k in call.keywords}
            self.assertTrue(
                {"repo", "spec_rel", "run_id"} <= kws,
                f"line {call.lineno}: missing { ({'repo', 'spec_rel', 'run_id'} - kws) }",
            )

    def test_scope_pending_refund_suppresses_decision_breaker(self):
        """Design D5: a pending scope escalation is forced back to `fixing` with
        its strike refunded; the decision breaker must not override that."""
        task = {"id": "TASK-001", "status": "reviewing", "retry_count": 2}
        task["_scope_pending"] = True
        rep = {
            "task": "TASK-001",
            "step": "review",
            "status": "success",
            "review_status": "FAILED",
            "critical_issues": 1,
            "major_issues": 0,
            "notes": "conflict",
            "decision_required": DECISION_TEXT,
            "missing_context": ["tests/test_other.py"],
        }
        entries: list = []
        _old, new = live._apply_step_commit(
            tasks=[task],
            entries=entries,
            actives={},
            record_fn=lambda: None,
            task=task,
            role="review",
            rep=rep,
            t0=0.0,
            t1=1.0,
            repo=self.repo,
            spec_rel=SPEC_REL,
            run_id="d5",
        )
        self.assertEqual(new, "fixing")
        self.assertEqual(task["status"], "fixing")
        self.assertEqual(task["retry_count"], 2)
        entry = entries[-1]
        self.assertNotIn("escalation_reason", entry)
        self.assertNotIn("pending_decision", entry)
        self.assertIsNone(entry["report"].get("terminal_status"))
        self.assertEqual(self._open_records(), [])

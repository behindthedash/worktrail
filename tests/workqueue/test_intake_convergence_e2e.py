"""End-to-end regression for the motivating brief that drove
autonomous-intake-brief-convergence: `20260902-080526-worktrail-drain-resume-pass-close`.

Reconstructs that brief (`repo: null`, focus quoting a drain-log error line and
ending with a `Repo:` token) against a `git init`'d fixture checkout, then
drives the real `queue_triage.main(["evaluate", ...])` -> `main(["apply",
"--confirm", ...])` pipeline (spawnlib's evaluator agent stubbed, `gh`
short-circuited) to prove the brief converges on the first pass instead of
looping on `keep` forever, per the proposal's "Why" section.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.shared.brief_frontmatter import read_frontmatter, serialize_frontmatter
from worktrail.workqueue import queue_triage as qt

BRIEF_ID = "20260902-080526-worktrail-drain-resume-pass-close"

# The literal fragment the fixture drain.py must contain, and that the
# premise check must confirm -- per the task's own wording.
DRAIN_LINE = "f\"no TASK-*.md found for {repo_name} {spec_id}: {', '.join(missing)}\"\n"

FOCUS_WITH_REPO = (
    "The drain loop's close-stale-bookkeeping pass logged "
    '"close-stale-bookkeeping error: ... no TASK-*.md found ..." before '
    "exiting; investigate whether the resume pass silently drops work. "
    "Repo: worktrail, src/worktrail/drain/drain.py close-stale resume pass"
)

# Captured before any patching, so `_gh_unavailable`'s pass-through for real
# `git grep` calls never recurses into the patched `subprocess.run` itself.
_REAL_SUBPROCESS_RUN = subprocess.run

FOCUS_NULL_REPO = (
    "The drain loop's close-stale-bookkeeping pass logged "
    '"close-stale-bookkeeping error: ... no TASK-*.md found ..." before '
    "exiting; investigate whether the resume pass silently drops work."
)


class IntakeConvergenceE2ETestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.queue_base = self.base / "queue-base"
        self.queue = self.queue_base / "queue"
        self.queue.mkdir(parents=True, exist_ok=True)
        self.repos_root = self.base / "repos"
        self.repos_root.mkdir(parents=True, exist_ok=True)
        self.out_dir = self.base / "out"

        self._previous_queue_dir = os.environ.get("WORK_QUEUE_DIR")
        os.environ["WORK_QUEUE_DIR"] = str(self.queue_base)

    def tearDown(self):
        if self._previous_queue_dir is None:
            os.environ.pop("WORK_QUEUE_DIR", None)
        else:
            os.environ["WORK_QUEUE_DIR"] = self._previous_queue_dir
        self._tmp.cleanup()

    def _init_worktrail_fixture(self) -> Path:
        """A `git init`'d `<repos_root>/worktrail/` with the drain.py needle
        and an empty `openspec/changes/`."""
        repo = self.repos_root / "worktrail"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        drain_py = repo / "src" / "worktrail" / "drain" / "drain.py"
        drain_py.parent.mkdir(parents=True, exist_ok=True)
        drain_py.write_text(DRAIN_LINE, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "src/worktrail/drain/drain.py"],
            check=True,
        )
        (repo / "openspec" / "changes").mkdir(parents=True, exist_ok=True)
        return repo

    def _write_brief(self, brief_id: str, focus: str) -> Path:
        frontmatter = {
            "id": brief_id,
            "created": "2026-09-02T08:05:26",
            "focus": focus,
            "repo": None,
            "status": "queued",
            "recommended-route": "E",
        }
        content = "---\n" + serialize_frontmatter(frontmatter) + "---\n\n"
        path = self.queue / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _gh_unavailable(cmd, *args, **kwargs):
        """Fails only `gh` invocations; real `git grep` calls pass through
        unpatched, since `queue_triage.subprocess` and `premise_check`'s own
        `subprocess` are the same module object."""
        if cmd and cmd[0] == "gh":
            raise OSError("gh not found")
        return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)


class TestMotivatingBriefConvergesOnFirstPass(IntakeConvergenceE2ETestBase):
    def test_repo_inferred_premise_confirmed_work_directly_applied(self):
        repo = self._init_worktrail_fixture()
        brief_path = self._write_brief(BRIEF_ID, FOCUS_WITH_REPO)

        captured_prompts: list[str] = []

        def fake_spawn_agent(prompt, cwd, *args, **kwargs):
            captured_prompts.append(prompt)
            self.assertIn("Mechanical premise check", prompt)
            self.assertIn("[CONFIRMED] quoted:", prompt)
            self.assertIn("no TASK-*.md found", prompt)
            self.assertIn("[CONFIRMED] path:", prompt)
            self.assertIn("src/worktrail/drain/drain.py", prompt)
            from worktrail.orchestrator.spawnlib import SpawnResult

            raw = json.dumps(
                {
                    "brief_id": BRIEF_ID,
                    "verdict": "work-directly",
                    "duplicate_of": None,
                    "evidence": (
                        "inspected drain.py:1494-1503 by hand; the resume pass "
                        "logic matches the brief's description"
                    ),
                    "confidence": "high",
                }
            )
            return SpawnResult(text=raw, usage={})

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                side_effect=self._gh_unavailable,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=fake_spawn_agent,
            ),
        ):
            exit_code = qt.main(
                [
                    "evaluate",
                    "--queue-dir",
                    str(self.queue_base),
                    "--repos-root",
                    str(self.repos_root),
                    "--out-dir",
                    str(self.out_dir),
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(captured_prompts), 1)

        # repo inference ran before the evaluator was spawned: the brief's
        # frontmatter now names the fixture checkout.
        fm_after_evaluate = read_frontmatter(brief_path)
        self.assertEqual(fm_after_evaluate["repo"], str(repo))

        notes = qt.triage_history(brief_path)
        repo_inferred_notes = [n for n in notes if n.verdict == "repo-inferred"]
        self.assertEqual(len(repo_inferred_notes), 1)
        body = brief_path.read_text(encoding="utf-8")
        self.assertIn("rule: a", body)

        verdict_path = self.out_dir / "verdict.json"
        verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v["brief_id"], BRIEF_ID)
        self.assertEqual(v["verdict"], "work-directly")
        self.assertIsNone(v["escalation"])

        premise_check = v["premise_check"]
        quoted_entry = next(p for p in premise_check if p["kind"] == "quoted")
        self.assertTrue(quoted_entry["confirmed"])
        self.assertIn("no TASK-*.md found", quoted_entry["needle"])
        path_entry = next(
            p
            for p in premise_check
            if p["kind"] == "path" and p["needle"] == "src/worktrail/drain/drain.py"
        )
        self.assertTrue(path_entry["confirmed"])

        # apply --confirm: work-directly is accepted on the confirmed premise
        # alone, even though the evidence text itself cites no test/command
        # (`_REPRODUCTION_EVIDENCE_RE` would reject it on its own).
        exit_code = qt.main(
            [
                "apply",
                "--verdict-file",
                str(verdict_path),
                "--confirm",
                "--repos-root",
                str(self.repos_root),
            ]
        )
        self.assertEqual(exit_code, 0)

        fm_after_apply = read_frontmatter(brief_path)
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertEqual(fm_after_apply["seeded-from"], f"triage:{today}:direct")
        self.assertEqual(fm_after_apply["recommended-route"], "F")
        self.assertTrue(brief_path.exists())

        # a second inventory() call must not re-infer or re-note the brief:
        # its repo is already set, so it never re-enters the pre-pass.
        _groups, _skipped, _escalate, inferred_again, _unresolvable = qt.inventory(
            25, str(self.repos_root)
        )
        self.assertEqual(inferred_again, [])
        notes_again = qt.triage_history(brief_path)
        repo_inferred_notes_again = [
            n for n in notes_again if n.verdict == "repo-inferred"
        ]
        self.assertEqual(len(repo_inferred_notes_again), 1)


class TestNullRepoBriefFirstPassNeedsDecision(IntakeConvergenceE2ETestBase):
    def test_no_repo_signal_yields_needs_decision_and_open_decision_record(self):
        brief_path = self._write_brief(BRIEF_ID, FOCUS_NULL_REPO)

        def fake_spawn_agent(prompt, cwd, *args, **kwargs):
            from worktrail.orchestrator.spawnlib import SpawnResult

            raw = json.dumps(
                {
                    "brief_id": BRIEF_ID,
                    "verdict": "keep",
                    "duplicate_of": None,
                    "evidence": "still seems relevant, no further evidence found",
                    "confidence": "low",
                }
            )
            return SpawnResult(text=raw, usage={})

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                side_effect=self._gh_unavailable,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=fake_spawn_agent,
            ),
        ):
            exit_code = qt.main(
                [
                    "evaluate",
                    "--queue-dir",
                    str(self.queue_base),
                    "--repos-root",
                    str(self.repos_root),
                    "--out-dir",
                    str(self.out_dir),
                ]
            )

        self.assertEqual(exit_code, 0)

        # repo stays null: no rule in repo_inference matched anything.
        fm_after_evaluate = read_frontmatter(brief_path)
        self.assertIsNone(fm_after_evaluate.get("repo"))

        verdict_path = self.out_dir / "verdict.json"
        verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v["brief_id"], BRIEF_ID)
        self.assertEqual(v["verdict"], "needs-decision")
        self.assertEqual(v["question"], qt.REPO_ASSIGNMENT_QUESTION)

        exit_code = qt.main(
            [
                "apply",
                "--verdict-file",
                str(verdict_path),
                "--confirm",
                "--repos-root",
                str(self.repos_root),
            ]
        )
        self.assertEqual(exit_code, 0)

        fm_after_apply = read_frontmatter(brief_path)
        self.assertIn("awaiting-decision", fm_after_apply)

        from worktrail.workqueue import decisions

        found = decisions.find_decision(
            fm_after_apply["awaiting-decision"], self.queue_base
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["status"], "open")


if __name__ == "__main__":
    unittest.main()

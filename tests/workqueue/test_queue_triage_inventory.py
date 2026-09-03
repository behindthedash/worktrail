#!/usr/bin/env python3
"""Tests for 4.1's inventory/repo-inference pre-pass, `parse_verdicts()`'s
`no_repo`/`premise_by_brief` handling, `evaluate_group()`'s premise-check
wiring, and the `--json`/`report.md` escalation/repos-inferred surfacing.

Run: python3 -m pytest tests/workqueue/test_queue_triage_inventory.py -q
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.workqueue import decisions
from worktrail.workqueue import queue_triage as qt


def _brief(focus: str, repo: str | None = None, body: str = "") -> str:
    fm = [f"focus: {focus}", "status: queued"]
    if repo is not None:
        fm.append(f"repo: {repo}")
    return "---\n" + "\n".join(fm) + "\n---\n\n" + body


class QueueTriageTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.environ["WORK_QUEUE_DIR"] = str(self.base)
        self.queue = self.base / "queue"
        self.queue.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def write(self, name: str, repo: str | None = None, body: str = "") -> Path:
        p = self.queue / name
        p.write_text(_brief(name, repo=repo, body=body), encoding="utf-8")
        return p


class TestRepoInferencePrePass(QueueTriageTestBase):
    def test_null_repo_brief_is_written_back_noted_and_grouped_in_one_call(self):
        target = self.base / "worktrail"
        (target / ".git").mkdir(parents=True)
        path = self.write("a.md")
        qt._set_fm_fields(path, {"focus": "Repo: worktrail -- fix this"})

        groups, inferred, unresolvable = qt.group_queue_by_repo(str(self.base))

        self.assertEqual(unresolvable, [])
        self.assertEqual(len(inferred), 1)
        self.assertNotIn(qt.NO_REPO_KEY, groups)
        [(key, paths)] = [(k, v) for k, v in groups.items() if path in v]
        self.assertEqual(key, str(target.resolve()))
        self.assertEqual(paths, [path])

        fm = qt.read_frontmatter(path)
        self.assertEqual(fm["repo"], str(target.resolve()))
        history = qt.triage_history(path)
        self.assertEqual(history[-1].verdict, "repo-inferred")

    def test_ambiguous_focus_stays_in_no_repo_key(self):
        self.write("a.md", body="## Focus\n\nnothing repo-specific here\n")

        groups, inferred, unresolvable = qt.group_queue_by_repo(str(self.base))

        self.assertEqual(inferred, [])
        self.assertEqual(unresolvable, [])
        self.assertIn(qt.NO_REPO_KEY, groups)

    def test_already_inferred_brief_is_not_re_noted_on_a_second_call(self):
        target = self.base / "worktrail"
        (target / ".git").mkdir(parents=True)
        path = self.write("a.md")
        qt._set_fm_fields(path, {"focus": "Repo: worktrail -- fix this"})

        _groups, first_inferred, _ = qt.group_queue_by_repo(str(self.base))
        self.assertEqual(len(first_inferred), 1)

        _groups2, second_inferred, _ = qt.group_queue_by_repo(str(self.base))
        self.assertEqual(second_inferred, [])
        history = qt.triage_history(path)
        self.assertEqual(sum(1 for n in history if n.verdict == "repo-inferred"), 1)


class TestInventoryDedupAndEscalation(QueueTriageTestBase):
    def test_due_brief_bypasses_dedup_window_while_non_due_recent_one_is_skipped(self):
        repo = self.base / "repo"
        repo.mkdir()
        due_path = self.write("due.md", repo=str(repo))
        skip_path = self.write("skip.md", repo=str(repo))
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="due", verdict="keep", duplicate_of=None, evidence="ok"
                ),
                today,
            )
        qt._apply_keep(
            qt.Verdict(
                brief_id="skip", verdict="keep", duplicate_of=None, evidence="ok"
            ),
            today,
        )

        groups, skipped, escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25, repos_root=str(self.base))
        )

        self.assertIn(skip_path, skipped)
        self.assertNotIn(due_path, skipped)
        self.assertEqual(groups[str(repo)], [due_path])
        self.assertEqual(escalate_without_evaluator, [])

    def test_due_null_repo_brief_appears_in_escalate_without_evaluator(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="a", verdict="keep", duplicate_of=None, evidence="ok"
                ),
                today,
            )

        groups, _skipped, escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25, repos_root=str(self.base))
        )

        self.assertNotIn(qt.NO_REPO_KEY, groups)
        self.assertEqual(escalate_without_evaluator, [path])


class TestConsumeRepoDecision(QueueTriageTestBase):
    def test_answered_repo_assignment_decision_is_consumed_and_record_resolved(self):
        target = self.base / "known-repo"
        target.mkdir()
        path = self.write("a.md")
        result = decisions.ask(
            qt.REPO_ASSIGNMENT_QUESTION,
            background="ambiguous",
            why="cannot infer",
            context="checked, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], str(target), queue_base=self.base)

        outcome = qt.consume_repo_decision(path, str(self.base))

        self.assertIsNotNone(outcome)
        self.assertTrue(outcome["resolved"])
        self.assertEqual(outcome["repo"], str(target.resolve()))

        fm = qt.read_frontmatter(path)
        self.assertEqual(fm["repo"], str(target.resolve()))
        self.assertNotIn("awaiting-decision", fm)

        history = qt.triage_history(path)
        self.assertEqual(history[-1].verdict, "repo-inferred")

        self.assertEqual(decisions.decision_status(result["id"], self.base), "resolved")
        resolved_dir = decisions.decisions_dir(self.base) / "resolved"
        self.assertTrue(any(p.stem == result["id"] for p in resolved_dir.iterdir()))

    def test_unresolvable_answer_leaves_brief_and_record_untouched_and_reported(self):
        path = self.write("a.md")
        result = decisions.ask(
            qt.REPO_ASSIGNMENT_QUESTION,
            background="ambiguous",
            why="cannot infer",
            context="checked, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], "no-such-repo-anywhere", queue_base=self.base)
        before = path.read_text(encoding="utf-8")

        outcome = qt.consume_repo_decision(path, str(self.base))

        self.assertIsNotNone(outcome)
        self.assertFalse(outcome["resolved"])
        self.assertEqual(outcome["decision_id"], result["id"])
        self.assertEqual(outcome["answer"], "no-such-repo-anywhere")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(decisions.decision_status(result["id"], self.base), "answered")


class TestParseVerdictsNoRepo(unittest.TestCase):
    def test_clean_keep_becomes_needs_decision(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "keep",
                "duplicate_of": None,
                "evidence": "still relevant",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_fallback_keep_becomes_needs_decision(self):
        [v] = qt.parse_verdicts(
            "the evaluator rambled with no JSON", ["a"], no_repo=True
        )
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_malformed_verdict_becomes_needs_decision(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "not-a-real-verdict-type",
                "duplicate_of": None,
                "evidence": "whatever",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_stale_close_is_not_touched(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "stale-close",
                "duplicate_of": None,
                "evidence": "gone",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "stale-close")


class TestPremiseCheckRoundTrip(unittest.TestCase):
    def test_premise_by_brief_lands_on_verdict_and_round_trips(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "stale-close",
                "duplicate_of": None,
                "evidence": "gone",
            }
        )
        premise = [{"kind": "path", "needle": "x", "confirmed": True, "detail": "d"}]
        [v] = qt.parse_verdicts(raw, ["a"], premise_by_brief={"a": premise})
        self.assertEqual(v.premise_check, premise)

        with tempfile.TemporaryDirectory() as tmp:
            path = qt.write_verdict_file([v], tmp)
            entries = json.loads(path.read_text(encoding="utf-8"))
        [entry] = entries
        rebuilt = qt.Verdict(**entry)
        self.assertEqual(rebuilt.premise_check, premise)


class TestEvaluatorPromptMechanicalPremiseCheck(unittest.TestCase):
    def test_prompt_mentions_mechanical_premise_check_and_d6_sentence(self):
        self.assertIn("Mechanical premise check", qt.EVALUATOR_PROMPT_TEMPLATE)
        self.assertIn(
            "counts as reproduction evidence on its own",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )


class TestEvaluateGroupPremiseCheckWiring(QueueTriageTestBase):
    def test_run_premise_check_called_only_for_non_no_repo_groups(self):
        from worktrail.orchestrator.spawnlib import SpawnResult

        repo_root = self.base / "repo"
        repo_brief = self.write(
            "a.md", repo=str(repo_root), body="## Focus\n\nsome claim here\n"
        )
        no_repo_brief = self.write("b.md", body="## Focus\n\nsome claim here\n")

        with (
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=SpawnResult(text="", usage={}),
            ),
            mock.patch(
                "worktrail.workqueue.premise_check.run_premise_check",
                return_value=[],
            ) as mock_premise,
        ):
            qt.evaluate_group(str(repo_root), [repo_brief], cwd=repo_root)
            self.assertEqual(mock_premise.call_count, 1)

            mock_premise.reset_mock()
            qt.evaluate_group(qt.NO_REPO_KEY, [no_repo_brief], cwd=self.base)
            self.assertEqual(mock_premise.call_count, 0)


class TestJsonSummaryAndReportEscalationCounts(QueueTriageTestBase):
    def test_json_summary_and_report_carry_matching_escalation_and_repos_inferred(
        self,
    ):
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="reproduces via pytest tests/foo -k bar",
                escalation="keep-limit",
            ),
            qt.Verdict(
                brief_id="b",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="due",
                question="repo over cap",
                escalation="queue-age",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            qt.write_verdict_file(verdicts, tmp)
            report_path = qt.write_report(verdicts, [], tmp, repos_inferred=3)
            report_text = report_path.read_text(encoding="utf-8")

        summary = qt.compute_run_summary(verdicts, repos_inferred=3)

        self.assertEqual(summary["repos_inferred"], 3)
        self.assertEqual(
            summary["escalations"]["by_reason"],
            {"keep-limit": 1, "queue-age": 1},
        )
        self.assertIn("repos_inferred: 3", report_text)
        self.assertIn("keep-limit: 1", report_text)
        self.assertIn("queue-age: 1", report_text)


if __name__ == "__main__":
    unittest.main()

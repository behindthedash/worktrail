#!/usr/bin/env python3
"""Tests for 4.1's escalation matrix (design D5/D6): `triage_history()`,
`consecutive_keep_count()`, `is_recently_triaged()`, `_apply_keep()`,
`escalation_due()`, `escalate()`, and `_work_directly_accepted()`.

Run: python3 -m pytest tests/workqueue/test_queue_triage_escalation.py -q
"""

from __future__ import annotations

import datetime
import os
import tempfile
import unittest
from pathlib import Path

from worktrail.shared.brief_frontmatter import split_frontmatter
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


def _keep_verdict(brief_id: str = "a", evidence: str = "still ok") -> qt.Verdict:
    return qt.Verdict(
        brief_id=brief_id, verdict="keep", duplicate_of=None, evidence=evidence
    )


class TestTriageHistory(QueueTriageTestBase):
    def test_parses_typed_verdict_and_keep_count(self):
        path = self.write("a.md")
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.rstrip("\n")
            + "\n\n## Triage 2026-01-01\n\nverdict: keep\nkeep-count: 1\n\nstill ok\n",
            encoding="utf-8",
        )
        [note] = qt.triage_history(path)
        self.assertEqual(note.date, datetime.date(2026, 1, 1))
        self.assertEqual(note.verdict, "keep")
        self.assertEqual(note.keep_count, 1)

    def test_parses_legacy_plain_text_note(self):
        path = self.write("a.md")
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.rstrip("\n")
            + "\n\n## Triage 2026-01-02\n\nsome plain-text note, no verdict: line\n",
            encoding="utf-8",
        )
        [note] = qt.triage_history(path)
        self.assertEqual(note.verdict, "legacy")
        self.assertIsNone(note.keep_count)

    def test_parses_repo_inferred_note(self):
        path = self.write("a.md")
        result = qt.repo_inference.InferenceResult(
            repo=str(self.base / "target"), rule="focus-mention", candidates=[]
        )
        qt._write_repo_inference(path, result)
        [note] = qt.triage_history(path)
        self.assertEqual(note.verdict, "repo-inferred")
        self.assertIsNone(note.keep_count)


class TestConsecutiveKeepCount(QueueTriageTestBase):
    def test_resets_on_a_non_keep_note(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._apply_keep(_keep_verdict(), today)
        qt._apply_keep(_keep_verdict(), today)
        self.assertEqual(qt.consecutive_keep_count(path), 2)

        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.rstrip("\n") + "\n\n## Triage 2026-02-01\n\nneeds-update\n",
            encoding="utf-8",
        )
        qt._apply_keep(_keep_verdict(), today)
        self.assertEqual(qt.consecutive_keep_count(path), 1)


class TestIsRecentlyTriagedIgnoresRepoInferred(QueueTriageTestBase):
    def test_repo_inferred_note_alone_does_not_count_as_recent(self):
        path = self.write("a.md")
        result = qt.repo_inference.InferenceResult(
            repo=str(self.base / "target"), rule="focus-mention", candidates=[]
        )
        qt._write_repo_inference(path, result)
        self.assertFalse(qt.is_recently_triaged(path, within_days=30))

    def test_a_real_triage_note_alongside_a_repo_inferred_one_counts(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._apply_keep(_keep_verdict(), today)
        result = qt.repo_inference.InferenceResult(
            repo=str(self.base / "target"), rule="focus-mention", candidates=[]
        )
        qt._write_repo_inference(path, result)
        self.assertTrue(qt.is_recently_triaged(path, within_days=30))


class TestApplyKeep(QueueTriageTestBase):
    def test_writes_keep_count_1_then_2_and_leaves_frontmatter_byte_identical(self):
        path = self.write("a.md")
        fm_before, _body_before = split_frontmatter(path.read_text(encoding="utf-8"))
        today = datetime.date.today().isoformat()  # noqa: DTZ011

        qt._apply_keep(_keep_verdict(), today)
        body = path.read_text(encoding="utf-8")
        self.assertIn("keep-count: 1", body)
        fm_after_1, _ = split_frontmatter(body)
        self.assertEqual(fm_after_1, fm_before)

        qt._apply_keep(_keep_verdict(), today)
        body = path.read_text(encoding="utf-8")
        self.assertIn("keep-count: 2", body)
        fm_after_2, _ = split_frontmatter(body)
        self.assertEqual(fm_after_2, fm_before)

    def test_preview_does_not_write(self):
        path = self.write("a.md")
        before = path.read_text(encoding="utf-8")
        today = datetime.date.today().isoformat()  # noqa: DTZ011

        preview = qt.apply_verdicts([_keep_verdict()], confirm=False)

        self.assertEqual(preview[0]["status"], "planned")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertNotIn("## Triage", path.read_text(encoding="utf-8"))
        del today  # unused; kept for readability of the assertion above


class TestEscalationDue(QueueTriageTestBase):
    def test_defaults_apply_for_null_repo(self):
        self.assertEqual(qt._escalation_limits(None), (2, 14))
        self.assertEqual(qt._escalation_limits(qt.NO_REPO_KEY), (2, 14))

    def test_policy_file_overrides_both_limits(self):
        repo = self.base / "configured-repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "triage_keep_limit: 5\ntriage_max_queue_age_days: 30\n", encoding="utf-8"
        )
        self.assertEqual(qt._escalation_limits(str(repo)), (5, 30))

    def test_due_by_keep_limit(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(_keep_verdict(), today)
        self.assertEqual(qt.escalation_due(path, None), "keep-limit")

    def test_due_by_queue_age(self):
        path = self.write("a.md")
        old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()  # noqa: DTZ011
        qt._set_fm_fields(path, {"created": old})
        self.assertEqual(qt.escalation_due(path, None), "queue-age")

    def test_neither_condition_met_returns_none(self):
        path = self.write("a.md")
        today_created = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._set_fm_fields(path, {"created": today_created})
        self.assertIsNone(qt.escalation_due(path, None))

    def test_policy_overriding_both_limits_changes_due_outcome(self):
        repo = self.base / "configured-repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "triage_keep_limit: 1\ntriage_max_queue_age_days: 9999\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._apply_keep(_keep_verdict(), today)
        self.assertEqual(qt.escalation_due(path, str(repo)), "keep-limit")


class TestEscalateMatrix(QueueTriageTestBase):
    """Every row of `escalate()`'s design D5 matrix."""

    def _due_keep(self, path: Path, evidence="still relevant", premise_check=None):
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(_keep_verdict(path.stem), today)
        return qt.Verdict(
            brief_id=path.stem,
            verdict="keep",
            duplicate_of=None,
            evidence=evidence,
            premise_check=premise_check or [],
        )

    def test_not_due_returns_verdict_unchanged(self):
        path = self.write("a.md")
        v = qt.Verdict(
            brief_id="a", verdict="keep", duplicate_of=None, evidence="still relevant"
        )
        self.assertIs(qt.escalate(v, path, None, []), v)

    def test_confirmed_premise_becomes_work_directly(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        v = self._due_keep(path, evidence="reproduces via pytest tests/foo.py -k bar")
        result = qt.escalate(v, path, str(repo), [])
        self.assertEqual(result.verdict, "work-directly")
        self.assertEqual(result.escalation, "keep-limit")
        self.assertTrue(qt._work_directly_accepted(result))

    def test_under_cap_becomes_propose_change_with_kebab_name(self):
        repo = self.base / "repo"
        repo.mkdir()
        brief_id = "20260901-120000-fix-the-widget-exporter"
        path = self.write(f"{brief_id}.md", repo=str(repo))
        v = self._due_keep(path)
        result = qt.escalate(v, path, str(repo), [])
        self.assertEqual(result.verdict, "propose-change")
        self.assertEqual(result.target_repo, str(repo))
        self.assertEqual(result.proposed_change_name, "fix-the-widget-exporter")

    def test_over_cap_with_candidates_folds_into_first_candidate(self):
        repo = self.base / "repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )
        change_dir = repo / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text("# existing-change\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 x\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        v = self._due_keep(path)
        result = qt.escalate(v, path, str(repo), ["widget-export-pipeline"])
        self.assertEqual(result.verdict, "fold-into-change")
        self.assertEqual(result.target_change, f"{repo}:change:widget-export-pipeline")

    def test_over_cap_without_candidates_becomes_needs_decision(self):
        repo = self.base / "repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )
        change_dir = repo / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text("# existing-change\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 x\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        v = self._due_keep(path)
        result = qt.escalate(v, path, str(repo), [])
        self.assertEqual(result.verdict, "needs-decision")
        self.assertIsNotNone(result.question)
        self.assertNotEqual(result.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_null_repo_becomes_needs_decision_with_repo_assignment_question(self):
        path = self.write("a.md")
        v = self._due_keep(path)
        result = qt.escalate(v, path, None, [])
        self.assertEqual(result.verdict, "needs-decision")
        self.assertEqual(result.question, qt.REPO_ASSIGNMENT_QUESTION)
        self.assertEqual(result.escalation, "keep-limit")

    def test_escalation_not_applied_to_a_non_due_brief(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        v = qt.Verdict(
            brief_id="a",
            verdict="keep",
            duplicate_of=None,
            evidence="reproduces via pytest tests/foo.py -k bar",
        )
        self.assertIs(qt.escalate(v, path, str(repo), []), v)

    def test_applied_to_a_due_work_directly_failing_acceptance_rule(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        v = self._due_keep(path, evidence="this brief describes a real problem")
        result = qt.escalate(v, path, str(repo), [])
        self.assertNotEqual(result.verdict, "work-directly")
        self.assertEqual(result.verdict, "propose-change")

    def test_applied_to_a_due_over_cap_propose_change(self):
        repo = self.base / "repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )
        change_dir = repo / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text("# existing-change\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 x\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        v = self._due_keep(path)
        result = qt.escalate(v, path, str(repo), ["widget-export-pipeline"])
        self.assertEqual(result.verdict, "fold-into-change")
        self.assertEqual(result.escalation, "keep-limit")


class TestWorkDirectlyAcceptedAcrossApplyAndPreview(QueueTriageTestBase):
    """`_work_directly_accepted()` agrees at both `_apply_work_directly()` and
    `_preview_verdict()` -- a `--confirm` run and its preview never disagree."""

    def test_true_on_regex_alone(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="reproduces via pytest tests/foo.py -k bar",
        )
        self.assertTrue(qt._work_directly_accepted(v))

    def test_true_on_premise_alone(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="this brief describes a real problem",
            premise_check=[
                {"kind": "path", "needle": "src/x.py", "confirmed": True, "detail": ""}
            ],
        )
        self.assertTrue(qt._work_directly_accepted(v))

    def test_false_on_neither(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="this brief describes a real problem",
            premise_check=[
                {"kind": "path", "needle": "src/x.py", "confirmed": False, "detail": ""}
            ],
        )
        self.assertFalse(qt._work_directly_accepted(v))

    def test_apply_work_directly_and_preview_agree_when_accepted(self):
        self.write("a.md")
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="reproduces via pytest tests/foo.py -k bar",
        )
        today = datetime.date.today().isoformat()  # noqa: DTZ011

        applied = qt._apply_work_directly(v, today)
        self.assertEqual(applied["status"], "executed")

        previewed = qt._preview_verdict(v, today)
        self.assertEqual(previewed["status"], "planned")
        self.assertEqual(previewed["action"], "stamp-frontmatter")

    def test_apply_work_directly_and_preview_agree_when_rejected(self):
        self.write("a.md")
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="this brief describes a real problem",
        )
        today = datetime.date.today().isoformat()  # noqa: DTZ011

        applied = qt._apply_work_directly(v, today)
        self.assertEqual(applied["status"], "downgraded-to-keep")

        previewed = qt._preview_verdict(v, today)
        self.assertEqual(previewed["status"], "planned-downgrade-to-keep")


if __name__ == "__main__":
    unittest.main()

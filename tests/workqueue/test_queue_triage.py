#!/usr/bin/env python3
"""Tests for queue_triage.py -- repo grouping and dedup-marker detection.

Run: python3 -m pytest tests/workqueue/test_queue_triage.py -q
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

from worktrail.workqueue import queue_triage as qt


def _brief(focus: str, repo: str = None, body: str = "") -> str:
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

    def write(self, name: str, repo: str = None, body: str = "") -> Path:
        p = self.queue / name
        p.write_text(_brief(name, repo=repo, body=body), encoding="utf-8")
        return p


class TestGroupQueueByRepo(QueueTriageTestBase):
    def test_groups_by_repo_value(self):
        self.write("a.md", repo="behindthedash/worktrail")
        self.write("b.md", repo="behindthedash/worktrail")
        self.write("c.md", repo="behindthedash/devops")

        groups = qt.group_queue_by_repo()

        self.assertEqual(
            {k: sorted(p.name for p in v) for k, v in groups.items()},
            {
                "behindthedash/worktrail": ["a.md", "b.md"],
                "behindthedash/devops": ["c.md"],
            },
        )

    def test_missing_repo_field_collapses_to_none_key(self):
        self.write("a.md")  # no repo: field at all

        groups = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])

    def test_null_and_blank_repo_collapse_to_none_key(self):
        self.write("a.md", repo="null")
        self.write("b.md", repo='""')

        groups = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )

    def test_none_and_named_repo_briefs_collapse_into_same_none_bucket(self):
        self.write("a.md")
        self.write("b.md", repo="null")
        self.write("c.md", repo="behindthedash/worktrail")

        groups = qt.group_queue_by_repo()

        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )
        self.assertEqual(
            [p.name for p in groups["behindthedash/worktrail"]], ["c.md"]
        )

    def test_empty_queue_dir_yields_no_groups(self):
        self.assertEqual(qt.group_queue_by_repo(), {})

    def test_missing_queue_dir_yields_no_groups(self):
        for f in self.queue.iterdir():
            f.unlink()
        self.queue.rmdir()
        self.assertEqual(qt.group_queue_by_repo(), {})

    def test_non_markdown_files_are_ignored(self):
        (self.queue / "notes.txt").write_text("not a brief", encoding="utf-8")
        self.write("a.md")

        groups = qt.group_queue_by_repo()

        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])


class TestIsRecentlyTriaged(QueueTriageTestBase):
    def test_recent_triage_section_is_within_window(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)
        p = self.write(
            "a.md",
            body=f"## Triage {recent.isoformat()}\n\nkeep\n",
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_stale_triage_section_is_outside_window(self):
        p = self.write(
            "a.md",
            body="## Triage 2020-01-01\n\nkeep\n",
        )
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_no_triage_section_is_not_recently_triaged(self):
        p = self.write("a.md", body="## Focus\n\nsome brief\n")
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_most_recent_of_multiple_triage_sections_wins(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)
        p = self.write(
            "a.md",
            body=(
                "## Triage 2020-01-01\n\nstale\n\n"
                f"## Triage {recent.isoformat()}\n\nrecent\n"
            ),
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_triage_date_is_ignored_not_raised(self):
        p = self.write("a.md", body="## Triage not-a-date\n\nkeep\n")
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_date_does_not_shadow_a_valid_one(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)
        p = self.write(
            "a.md",
            body=(
                "## Triage not-a-date\n\nkeep\n\n"
                f"## Triage {recent.isoformat()}\n\nkeep\n"
            ),
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_missing_file_returns_false(self):
        missing = self.queue / "does-not-exist.md"
        self.assertFalse(qt.is_recently_triaged(missing, within_days=30))

    def test_boundary_at_exactly_within_days_is_recent(self):
        # today() is 2026-08-05 per environment context; use a relative date
        # computed the same way the implementation does to avoid brittleness.
        import datetime

        today = datetime.date.today()
        boundary_date = today - datetime.timedelta(days=30)
        p = self.write(
            "a.md",
            body=f"## Triage {boundary_date.isoformat()}\n\nkeep\n",
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_one_day_past_boundary_is_stale(self):
        import datetime

        today = datetime.date.today()
        past_boundary = today - datetime.timedelta(days=31)
        p = self.write(
            "a.md",
            body=f"## Triage {past_boundary.isoformat()}\n\nkeep\n",
        )
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))


class TestParseVerdicts(unittest.TestCase):
    def test_well_formed_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null, '
            '"evidence": "PR #42 already shipped this", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v.brief_id, "a")
        self.assertEqual(v.verdict, "stale-close")
        self.assertIsNone(v.duplicate_of)
        self.assertEqual(v.evidence, "PR #42 already shipped this")
        self.assertEqual(v.confidence, "high")

    def test_well_formed_duplicate_of_verdict_retains_target(self):
        raw = (
            '{"brief_id": "a", "verdict": "duplicate-of", "duplicate_of": "b", '
            '"evidence": "same premise as b.md", "confidence": "medium"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(verdicts[0].verdict, "duplicate-of")
        self.assertEqual(verdicts[0].duplicate_of, "b")

    def test_multiple_wellformed_verdicts_parsed_in_expected_order(self):
        raw = (
            'reasoning text here\n'
            '{"brief_id": "b", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "still relevant", "confidence": "low"}\n'
            'more reasoning\n'
            '{"brief_id": "a", "verdict": "needs-update", "duplicate_of": null, '
            '"evidence": "target file renamed", "confidence": "high"}\n'
        )
        verdicts = qt.parse_verdicts(raw, ["a", "b"])

        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        self.assertEqual(verdicts[0].verdict, "needs-update")
        self.assertEqual(verdicts[1].verdict, "keep")

    def test_unparsable_json_falls_back_to_keep_with_full_raw_text_retained(self):
        raw = "the evaluator rambled and never emitted any JSON at all"
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v.brief_id, "a")
        self.assertEqual(v.verdict, "keep")
        self.assertIsNone(v.duplicate_of)
        self.assertEqual(v.evidence, raw)
        self.assertIsNone(v.confidence)

    def test_invalid_verdict_type_falls_back_to_keep_with_snippet_retained(self):
        snippet = (
            '{"brief_id": "a", "verdict": "not-a-real-verdict", "duplicate_of": null, '
            '"evidence": "some evidence", "confidence": "high"}'
        )
        raw = f"here is my answer: {snippet}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_duplicate_of_without_target_falls_back_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "duplicate-of", "duplicate_of": null, '
            '"evidence": "looks like a dupe but no target cited", "confidence": "low"}'
        )
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_missing_evidence_falls_back_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null, '
            '"evidence": "", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_evidence_field_entirely_absent_falls_back_to_keep(self):
        snippet = '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null}'
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_missing_brief_id_still_appears_with_keep_fallback(self):
        raw = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "confirmed still needed", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a", "b"])

        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        self.assertEqual(verdicts[1].verdict, "keep")
        self.assertEqual(verdicts[1].evidence, raw)

    def test_no_expected_brief_ids_yields_no_verdicts(self):
        self.assertEqual(qt.parse_verdicts("{}", []), [])

    def test_second_candidate_used_when_first_is_malformed(self):
        good = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "second attempt is valid", "confidence": "medium"}'
        )
        bad = '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, "evidence": ""}'
        raw = f"{bad}\n{good}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, "second attempt is valid")


class TestEvaluateGroupArchivedShortCircuit(QueueTriageTestBase):
    """2.4's archived-repo short-circuit: `_check_repo_archived()` gates whether
    `evaluate_group()` synthesizes verdicts itself or spawns an evaluator agent.
    Faked via `subprocess.run`/`spawn_agent` patches -- never hits the network.
    """

    def _completed(self, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["gh"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_confirmed_archival_synthesizes_group_wide_stale_close_without_spawning(self):
        repo = "behindthedash/retired-repo"
        briefs = [self.write("a.md", repo=repo), self.write("b.md", repo=repo)]

        gh_stdout = json.dumps({"isArchived": True, "name": repo})
        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run",
            return_value=self._completed(0, gh_stdout),
        ) as mock_run, mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent"
        ) as mock_spawn:
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_not_called()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["repo"], repo)
        self.assertEqual(result[0]["brief_ids"], ["a", "b"])

        verdicts = qt.parse_verdicts(result[0]["raw_text"], ["a", "b"])
        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        for v in verdicts:
            self.assertEqual(v.verdict, "stale-close")
            self.assertIsNone(v.duplicate_of)
            self.assertIn(repo, v.evidence)
            self.assertEqual(v.confidence, "high")

    def test_gh_check_failure_falls_through_to_normal_evaluation(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo)]

        from worktrail.orchestrator.spawnlib import SpawnResult

        evaluator_text = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "still relevant", "confidence": "medium"}'
        )
        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run",
            return_value=self._completed(1, ""),
        ) as mock_run, mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text=evaluator_text, usage={}),
        ) as mock_spawn:
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_called_once()

        self.assertEqual(result[0]["raw_text"], evaluator_text)
        verdicts = qt.parse_verdicts(result[0]["raw_text"], ["a"])
        self.assertEqual(verdicts[0].verdict, "keep")

    def test_gh_check_exception_falls_through_to_normal_evaluation(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo)]

        from worktrail.orchestrator.spawnlib import SpawnResult

        evaluator_text = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "gh unavailable, kept as fail-open", "confidence": "low"}'
        )
        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run",
            side_effect=OSError("gh not found"),
        ) as mock_run, mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text=evaluator_text, usage={}),
        ) as mock_spawn:
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_called_once()
        self.assertEqual(result[0]["raw_text"], evaluator_text)


class TestApplyVerdicts(QueueTriageTestBase):
    """4.2's `apply_verdicts()`: `--confirm` false must be a pure dry run (no
    filesystem mutation at all), `--confirm` true must actually execute
    `stale-close` (claim+done with note) and `needs-update` (in-place append)
    against a temp `$WORK_QUEUE_DIR` fixture.
    """

    def _verdicts(self):
        return [
            qt.Verdict(
                brief_id="a",
                verdict="stale-close",
                duplicate_of=None,
                evidence="PR #42 already shipped this",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="b",
                verdict="needs-update",
                duplicate_of=None,
                evidence="target file renamed, brief needs a refresh",
                confidence="medium",
            ),
        ]

    def test_confirm_false_is_a_pure_dry_run(self):
        a_path = self.write("a.md", body="## Focus\n\nsome brief\n")
        b_path = self.write("b.md", body="## Focus\n\nanother brief\n")
        a_before = a_path.read_text(encoding="utf-8")
        b_before = b_path.read_text(encoding="utf-8")

        log = qt.apply_verdicts(self._verdicts(), confirm=False)

        self.assertEqual(len(log), 2)
        for entry in log:
            self.assertEqual(entry["status"], "planned")
            self.assertFalse(entry["confirm"])
            self.assertIsNone(entry["path"])
            self.assertIsNone(entry["error"])
        self.assertEqual(log[0]["action"], "claim+done")
        self.assertEqual(log[0]["note"], "PR #42 already shipped this")
        self.assertEqual(log[1]["action"], "append-triage-note")
        self.assertEqual(log[1]["note"], "target file renamed, brief needs a refresh")

        # no file was moved, created, or rewritten anywhere under WORK_QUEUE_DIR
        self.assertEqual(a_path.read_text(encoding="utf-8"), a_before)
        self.assertEqual(b_path.read_text(encoding="utf-8"), b_before)
        self.assertTrue(a_path.exists())
        self.assertTrue(b_path.exists())
        picked = self.base / "picked"
        self.assertFalse(picked.exists() and any(picked.iterdir()))

    def test_confirm_true_executes_stale_close_via_claim_and_done(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        self.write("b.md", body="## Focus\n\nanother brief\n")

        log = qt.apply_verdicts(self._verdicts(), confirm=True)

        close_entry = log[0]
        self.assertEqual(close_entry["brief_id"], "a")
        self.assertEqual(close_entry["status"], "executed")
        self.assertEqual(close_entry["action"], "claim+done")
        self.assertTrue(close_entry["confirm"])
        self.assertIsNone(close_entry["error"])

        picked_path = Path(close_entry["path"])
        self.assertEqual(picked_path.parent, self.base / "picked")
        self.assertFalse((self.queue / "a.md").exists())

        content = picked_path.read_text(encoding="utf-8")
        fm = qt.read_frontmatter(picked_path)
        self.assertEqual(fm["status"], "done")
        self.assertIn("## Closure Note", content)
        self.assertIn("PR #42 already shipped this", content)

    def test_confirm_true_executes_needs_update_via_inplace_append(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        b_path = self.write("b.md", body="## Focus\n\nanother brief\n")

        log = qt.apply_verdicts(self._verdicts(), confirm=True)

        update_entry = log[1]
        self.assertEqual(update_entry["brief_id"], "b")
        self.assertEqual(update_entry["status"], "executed")
        self.assertEqual(update_entry["action"], "append-triage-note")
        self.assertTrue(update_entry["confirm"])
        self.assertIsNone(update_entry["error"])
        self.assertEqual(update_entry["path"], str(b_path))

        # brief is left in place -- unlike stale-close, needs-update never claims it
        self.assertTrue(b_path.exists())
        content = b_path.read_text(encoding="utf-8")
        run_date = datetime.date.today().isoformat()
        self.assertIn(f"## Triage {run_date}", content)
        self.assertIn("target file renamed, brief needs a refresh", content)
        self.assertTrue(qt.is_recently_triaged(b_path, within_days=1))

    def test_keep_verdict_is_always_a_noop_regardless_of_confirm(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="keep",
                duplicate_of=None,
                evidence="still relevant",
                confidence="low",
            )
        ]

        for confirm in (False, True):
            log = qt.apply_verdicts(verdicts, confirm=confirm)
            self.assertEqual(log[0]["status"], "noop")
            self.assertEqual(log[0]["action"], "noop")
            self.assertTrue((self.queue / "a.md").exists())


class TestResolveDuplicateTargets(unittest.TestCase):
    """6.5's dangling-`duplicate-of` resolution: a `duplicate-of` verdict whose
    target is verdicted non-`keep` in the same batch is downgraded to a no-op
    `keep`, with a warning logged, rather than acted on against a moving target.
    """

    def _dup(self, brief_id: str, target: str, evidence: str = "same premise") -> qt.Verdict:
        return qt.Verdict(
            brief_id=brief_id,
            verdict="duplicate-of",
            duplicate_of=target,
            evidence=evidence,
            confidence="medium",
        )

    def test_target_verdicted_stale_close_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b", verdict="stale-close", duplicate_of=None,
                evidence="already shipped", confidence="high",
            ),
        ]

        with self.assertLogs("worktrail.workqueue.queue_triage", level="WARNING") as cm:
            resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].brief_id, "a")
        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)
        self.assertEqual(resolved[0].evidence, "same premise")
        self.assertEqual(resolved[0].confidence, "medium")
        # target verdict is untouched
        self.assertEqual(resolved[1].verdict, "stale-close")
        self.assertTrue(any("dangling duplicate-of" in msg for msg in cm.output))

    def test_target_verdicted_needs_update_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b", verdict="needs-update", duplicate_of=None,
                evidence="file renamed", confidence="high",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)

    def test_target_also_duplicate_of_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            self._dup("b", "c"),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)
        # "b"'s own duplicate-of target ("c") is absent from the batch, so it
        # is left as-is
        self.assertEqual(resolved[1].verdict, "duplicate-of")
        self.assertEqual(resolved[1].duplicate_of, "c")

    def test_target_verdicted_keep_is_left_as_duplicate_of(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b", verdict="keep", duplicate_of=None,
                evidence="still relevant", confidence="low",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "duplicate-of")
        self.assertEqual(resolved[0].duplicate_of, "b")

    def test_target_absent_from_batch_is_left_as_duplicate_of(self):
        verdicts = [self._dup("a", "not-in-this-batch")]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "duplicate-of")
        self.assertEqual(resolved[0].duplicate_of, "not-in-this-batch")

    def test_non_duplicate_of_verdicts_pass_through_unchanged(self):
        verdicts = [
            qt.Verdict(
                brief_id="a", verdict="stale-close", duplicate_of=None,
                evidence="shipped", confidence="high",
            ),
            qt.Verdict(
                brief_id="b", verdict="keep", duplicate_of=None,
                evidence="still relevant", confidence="low",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved, verdicts)


class TestReportAndVerdictFileOutput(QueueTriageTestBase):
    """6.6: the Markdown report's verdict counts must match `verdict.json`'s
    contents exactly. `test_report_verdict_counts_match_json_file_exactly` and
    friends exercise `write_verdict_file()`/`write_report()` directly on a flat,
    hand-built `List[Verdict]` covering every verdict type (including a `keep`
    fallback); `test_multi_group_evaluate_run_report_matches_json` drives the
    actual multi-group aggregation path -- briefs across two distinct `repo:`
    values plus the `__none__` bucket, a skipped-via-dedup brief, through
    `qt.main(["evaluate", ...])` with `evaluate_group()` patched -- the same
    path `cmd_evaluate()` uses to accumulate verdicts across groups before
    handing them to the writers.
    """

    @staticmethod
    def _report_counts(report_text: str) -> dict:
        """Parse the `## Verdict counts` section's `- <type>: <n>` lines back into a dict."""
        section = report_text.split("## Verdict counts", 1)[1].split("## Skipped via dedup", 1)[0]
        counts = {}
        for line in section.strip().splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            vtype, n = line[2:].rsplit(": ", 1)
            counts[vtype] = int(n)
        return counts

    def _verdicts(self):
        return [
            qt.Verdict(
                brief_id="repo-a-1", verdict="stale-close", duplicate_of=None,
                evidence="PR #42 already shipped this", confidence="high",
            ),
            qt.Verdict(
                brief_id="repo-a-2", verdict="stale-close", duplicate_of=None,
                evidence="repo archived", confidence="high",
            ),
            qt.Verdict(
                brief_id="repo-b-1", verdict="needs-update", duplicate_of=None,
                evidence="target file renamed", confidence="medium",
            ),
            qt.Verdict(
                brief_id="repo-b-2", verdict="duplicate-of", duplicate_of="repo-a-1",
                evidence="same premise as repo-a-1", confidence="medium",
            ),
            qt.Verdict(
                brief_id="__none__-1", verdict="keep", duplicate_of=None,
                evidence="still relevant", confidence="low",
            ),
            qt.Verdict(
                brief_id="__none__-2", verdict="keep", duplicate_of=None,
                evidence="the evaluator rambled and never emitted any JSON at all",
                confidence=None,
            ),
        ]

    def test_report_verdict_counts_match_json_file_exactly(self):
        verdicts = self._verdicts()
        skipped = [self.write("skipped-1.md"), self.write("skipped-2.md")]
        out_dir = self.base / "out"

        verdict_path = qt.write_verdict_file(verdicts, out_dir)
        report_path = qt.write_report(verdicts, skipped, out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        json_counts: dict = {}
        for entry in json_verdicts:
            json_counts[entry["verdict"]] = json_counts.get(entry["verdict"], 0) + 1

        report_text = report_path.read_text(encoding="utf-8")
        report_counts = self._report_counts(report_text)

        # every verdict type is listed in the report (zero-filled), so compare
        # against the JSON counts zero-filled the same way rather than assuming
        # the report only lists types that actually occurred. The zero-fill set
        # is a literal here (not `qt.VALID_VERDICT_TYPES`) so a wrong member in
        # that constant can't cancel out on both sides of the comparison.
        expected_counts = {
            vtype: json_counts.get(vtype, 0)
            for vtype in ("keep", "stale-close", "needs-update", "duplicate-of")
        }
        self.assertEqual(report_counts, expected_counts)

        # and the JSON file itself carries no verdict type absent from the report
        for vtype in json_counts:
            self.assertIn(vtype, report_counts)

        self.assertEqual(len(json_verdicts), len(verdicts))
        self.assertIn(f"Briefs evaluated: {len(verdicts)}", report_text)
        self.assertIn(f"Briefs skipped (recently triaged): {len(skipped)}", report_text)
        self.assertIn("## Skipped via dedup", report_text)
        for path in skipped:
            self.assertIn(f"- {path.stem}", report_text)

    def test_multi_group_evaluate_run_report_matches_json(self):
        """Drives `cmd_evaluate()`'s real accumulate-across-groups step
        (`queue_triage.py:701-712`), not the writers in isolation: two distinct
        `repo:` groups plus the `__none__` bucket, `evaluate_group()` patched per
        group, one brief skipped via a recent `## Triage` section.
        """
        self.write("a1.md", repo="behindthedash/repo-a")
        self.write("a2.md", repo="behindthedash/repo-a")
        self.write("b1.md", repo="behindthedash/repo-b")
        self.write("b2.md", repo="behindthedash/repo-b")
        self.write("n1.md")
        self.write("n2.md")
        today = datetime.date.today().isoformat()
        self.write(
            "skipped.md",
            repo="behindthedash/repo-a",
            body=f"## Triage {today}\n\nstill fresh, do not re-evaluate\n",
        )

        def fake_evaluate_group(repo, briefs, *, agent="claude", model=None, cwd=None):
            ids = [p.stem for p in briefs]
            if repo == "behindthedash/repo-a":
                raw = "\n".join([
                    json.dumps({
                        "brief_id": ids[0], "verdict": "stale-close", "duplicate_of": None,
                        "evidence": "PR #42 already shipped this", "confidence": "high",
                    }),
                    json.dumps({
                        "brief_id": ids[1], "verdict": "stale-close", "duplicate_of": None,
                        "evidence": "repo archived", "confidence": "high",
                    }),
                ])
            elif repo == "behindthedash/repo-b":
                raw = "\n".join([
                    json.dumps({
                        "brief_id": ids[0], "verdict": "needs-update", "duplicate_of": None,
                        "evidence": "target file renamed", "confidence": "medium",
                    }),
                    json.dumps({
                        "brief_id": ids[1], "verdict": "duplicate-of", "duplicate_of": ids[0],
                        "evidence": "same premise", "confidence": "medium",
                    }),
                ])
            else:
                raw = "\n".join([
                    json.dumps({
                        "brief_id": ids[0], "verdict": "keep", "duplicate_of": None,
                        "evidence": "still relevant", "confidence": "low",
                    }),
                    "the evaluator rambled and never emitted valid JSON for the second brief",
                ])
            return [{"repo": repo, "brief_ids": ids, "raw_text": raw}]

        out_dir = self.base / "out-run"
        with mock.patch(
            "worktrail.workqueue.queue_triage.evaluate_group", side_effect=fake_evaluate_group,
        ) as mock_eval:
            exit_code = qt.main(["evaluate", "--out-dir", str(out_dir)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            {call.args[0] for call in mock_eval.call_args_list},
            {"behindthedash/repo-a", "behindthedash/repo-b", qt.NO_REPO_KEY},
        )

        verdict_path = out_dir / "verdict.json"
        report_path = out_dir / "report.md"
        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        json_counts: dict = {}
        for entry in json_verdicts:
            json_counts[entry["verdict"]] = json_counts.get(entry["verdict"], 0) + 1

        report_text = report_path.read_text(encoding="utf-8")
        report_counts = self._report_counts(report_text)

        expected_counts = {"keep": 0, "stale-close": 0, "needs-update": 0, "duplicate-of": 0}
        expected_counts.update(json_counts)
        self.assertEqual(report_counts, expected_counts)
        self.assertEqual(
            report_counts, {"keep": 2, "stale-close": 2, "needs-update": 1, "duplicate-of": 1},
        )

        self.assertEqual(len(json_verdicts), 6)
        self.assertIn("Briefs evaluated: 6", report_text)
        self.assertIn("Briefs skipped (recently triaged): 1", report_text)
        self.assertIn("- skipped", report_text)

    def test_report_and_json_agree_on_empty_verdict_list(self):
        out_dir = self.base / "out-empty"

        verdict_path = qt.write_verdict_file([], out_dir)
        report_path = qt.write_report([], [], out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        self.assertEqual(json_verdicts, [])

        report_counts = self._report_counts(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report_counts, {vtype: 0 for vtype in qt.VALID_VERDICT_TYPES})

    def test_json_file_preserves_every_verdict_field_the_report_summarizes(self):
        verdicts = self._verdicts()
        out_dir = self.base / "out-fields"

        verdict_path = qt.write_verdict_file(verdicts, out_dir)
        report_path = qt.write_report(verdicts, [], out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        report_text = report_path.read_text(encoding="utf-8")

        self.assertEqual([e["brief_id"] for e in json_verdicts], [v.brief_id for v in verdicts])
        for entry in json_verdicts:
            # tie brief_id and verdict together, not just each present somewhere in
            # the report: `write_report()` zero-fills every verdict type in the
            # counts section regardless of the per-brief table, so checking
            # `entry["verdict"]` alone can never fail.
            self.assertIn(f"| {entry['brief_id']} | {entry['verdict']} |", report_text)
            if entry["duplicate_of"] is not None:
                self.assertIn(entry["duplicate_of"], report_text)
            if entry["confidence"] is not None:
                self.assertIn(entry["confidence"], report_text)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""2.1: the triage evaluator fails closed when its spawn gave up.

An exhausted spawn (`SpawnResult.exhausted`, 1.1) hands back the provider's
capacity/error stream, not evaluator output. These tests pin that no verdict is
ever manufactured from it: `evaluate_group()` blanks `raw_text`,
`evaluate_briefs()` raises `EvaluatorUnavailable` before `parse_verdicts()` runs,
the briefs on disk are untouched, and `cmd_evaluate()` counts the group as
unevaluated and exits non-zero while keeping every other group's verdicts.

Run: python3 -m pytest tests/workqueue/test_triage_evaluator_exhaustion.py -q
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from worktrail.workqueue import queue_triage as qt


def _brief(focus: str, repo: str | None = None, body: str = "") -> str:
    fm = [f"focus: {focus}", "status: queued"]
    if repo is not None:
        fm.append(f"repo: {repo}")
    return "---\n" + "\n".join(fm) + "\n---\n\n" + body


def _spawn_result(
    text: str, *, exhausted: bool = False, failure_class: str = ""
) -> types.SimpleNamespace:
    """A `SpawnResult`-shaped stand-in carrying 1.1's exhaustion fields.

    A namespace rather than the real `SpawnResult` so this test pins the
    contract `queue_triage` consumes (`.text`/`.exhausted`/`.failure_class`)
    without depending on the NamedTuple's field order.
    """
    return types.SimpleNamespace(
        text=text,
        usage={},
        exhausted=exhausted,
        failure_class=failure_class,
        session_id="",
        served_target="",
        served_model="",
        served_harness="",
    )


USAGE_LIMIT_TEXT = (
    "Claude AI usage limit reached|1757000000 -- your limit will reset later."
)


class ExhaustionTestBase(unittest.TestCase):
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


class TestEvaluateGroupExhausted(ExhaustionTestBase):
    def test_exhausted_spawn_yields_empty_raw_text_and_failure_class(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo), self.write("b.md", repo=repo)]

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage._check_repo_archived",
                return_value=False,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=_spawn_result(
                    USAGE_LIMIT_TEXT, exhausted=True, failure_class="billing"
                ),
            ),
        ):
            groups = qt.evaluate_group(repo, briefs, cwd=self.base)

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertIs(group["exhausted"], True)
        self.assertEqual(group["failure_class"], "billing")
        self.assertEqual(group["raw_text"], "")
        self.assertNotIn("usage limit", group["raw_text"])
        self.assertEqual(group["repo"], repo)
        self.assertEqual(group["brief_ids"], ["a", "b"])

    def test_normal_spawn_is_untouched(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo)]
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "keep",
                "duplicate_of": None,
                "evidence": "still relevant",
                "confidence": "medium",
            }
        )

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage._check_repo_archived",
                return_value=False,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=_spawn_result(raw),
            ),
        ):
            groups = qt.evaluate_group(repo, briefs, cwd=self.base)

        self.assertEqual(groups[0]["raw_text"], raw)
        self.assertFalse(groups[0].get("exhausted"))


class TestEvaluateBriefsRaises(ExhaustionTestBase):
    def test_raises_evaluator_unavailable_without_parsing(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo), self.write("b.md", repo=repo)]

        before = {p: p.read_bytes() for p in briefs}
        keeps_before = {p: qt.consecutive_keep_count(p) for p in briefs}

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage._check_repo_archived",
                return_value=False,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=_spawn_result(
                    USAGE_LIMIT_TEXT, exhausted=True, failure_class="billing"
                ),
            ),
            mock.patch("worktrail.workqueue.queue_triage.parse_verdicts") as mock_parse,
            self.assertRaises(qt.EvaluatorUnavailable) as ctx,
        ):
            qt.evaluate_briefs(repo, briefs, cwd=self.base)

        mock_parse.assert_not_called()

        exc = ctx.exception
        self.assertEqual(exc.repo, repo)
        self.assertEqual(exc.brief_ids, ["a", "b"])
        self.assertEqual(exc.failure_class, "billing")
        self.assertIn(repo, str(exc))
        self.assertIn("billing", str(exc))
        self.assertIn("a", str(exc))
        self.assertIn("b", str(exc))

        for p in briefs:
            self.assertEqual(p.read_bytes(), before[p])
            self.assertEqual(qt.consecutive_keep_count(p), keeps_before[p])


class TestCmdEvaluateExhaustedGroup(ExhaustionTestBase):
    def test_second_group_exhausted_is_omitted_counted_and_exits_non_zero(self):
        self.write("a1.md", repo="behindthedash/repo-a")
        self.write("b1.md", repo="behindthedash/repo-b")

        def fake_evaluate_group(repo, briefs, **kwargs):
            ids = [p.stem for p in briefs]
            if repo == "behindthedash/repo-b":
                return [
                    {
                        "repo": repo,
                        "brief_ids": ids,
                        "raw_text": "",
                        "exhausted": True,
                        "failure_class": "billing",
                        "candidates_by_brief": {bid: [] for bid in ids},
                        "premise_by_brief": {bid: [] for bid in ids},
                        "known_repos_by_brief": {},
                    }
                ]
            raw = json.dumps(
                {
                    "brief_id": ids[0],
                    "verdict": "stale-close",
                    "duplicate_of": None,
                    "evidence": "PR #42 already shipped this",
                    "confidence": "high",
                }
            )
            return [
                {
                    "repo": repo,
                    "brief_ids": ids,
                    "raw_text": raw,
                    "candidates_by_brief": {bid: [] for bid in ids},
                    "premise_by_brief": {bid: [] for bid in ids},
                    "known_repos_by_brief": {},
                }
            ]

        out_dir = self.base / "out-run"
        buf = io.StringIO()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.evaluate_group",
                side_effect=fake_evaluate_group,
            ),
            redirect_stdout(buf),
        ):
            exit_code = qt.main(["evaluate", "--out-dir", str(out_dir), "--json"])

        self.assertNotEqual(exit_code, 0)

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["groups_unevaluated"], 1)
        self.assertEqual(payload["groups_evaluated"], 1)

        entries = json.loads((out_dir / "verdict.json").read_text(encoding="utf-8"))
        self.assertEqual([e["brief_id"] for e in entries], ["a1"])
        self.assertNotIn("b1", [e["brief_id"] for e in entries])

    def test_text_summary_reports_groups_unevaluated(self):
        self.write("a1.md", repo="behindthedash/repo-a")
        self.write("b1.md", repo="behindthedash/repo-b")

        def fake_evaluate_group(repo, briefs, **kwargs):
            ids = [p.stem for p in briefs]
            return [
                {
                    "repo": repo,
                    "brief_ids": ids,
                    "raw_text": "",
                    "exhausted": True,
                    "failure_class": "billing",
                    "candidates_by_brief": {bid: [] for bid in ids},
                    "premise_by_brief": {bid: [] for bid in ids},
                    "known_repos_by_brief": {},
                }
            ]

        out_dir = self.base / "out-text"
        buf = io.StringIO()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.evaluate_group",
                side_effect=fake_evaluate_group,
            ),
            redirect_stdout(buf),
        ):
            exit_code = qt.main(["evaluate", "--out-dir", str(out_dir)])

        self.assertNotEqual(exit_code, 0)
        self.assertIn("groups unevaluated: 2", buf.getvalue())
        self.assertEqual(
            json.loads((out_dir / "verdict.json").read_text(encoding="utf-8")), []
        )


if __name__ == "__main__":
    unittest.main()

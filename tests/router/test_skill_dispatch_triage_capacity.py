"""Capacity exhaustion and verdictless payloads at the single-brief triage CLI.

Covers 2.3: `EvaluatorUnavailable` propagates out of `evaluate_single_brief()`
and is rendered by `main()` as a `blocked_no_capacity:` exit 2 -- distinct from
today's exit-1 `null` "no identifiable verdict" -- and `--apply-brief-triage`
refuses a payload with no verdict before `Verdict(**...)` is constructed.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from worktrail.router import skill_dispatch
from worktrail.workqueue.queue_triage import EvaluatorUnavailable


class TriageCapacityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue_base = Path(self.tmp.name) / "work-queue"
        (self.queue_base / "queue").mkdir(parents=True)
        patcher = patch.dict(
            os.environ, {"WORK_QUEUE_DIR": str(self.queue_base)}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.brief = self.queue_base / "queue" / "20260101-000000-example.md"
        self.brief.write_text(
            "---\nfocus: example brief\nstatus: queued\n---\n\nBody.\n",
            encoding="utf-8",
        )

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = skill_dispatch.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_evaluator_unavailable_prints_null_and_exits_two_with_capacity_block(self):
        exc = EvaluatorUnavailable(
            "behindthedash/worktrail", [self.brief.stem], "billing"
        )
        with patch.object(
            skill_dispatch, "evaluate_single_brief", side_effect=exc
        ):
            code, out, err = self._run(
                ["--evaluate-brief-triage", str(self.brief)]
            )

        self.assertEqual(code, 2)
        self.assertIsNone(json.loads(out.strip()))
        self.assertIn("blocked_no_capacity:", err)
        self.assertIn("behindthedash/worktrail/billing", err)
        self.assertIn(self.brief.stem, err)

    def test_evaluator_unavailable_propagates_out_of_evaluate_single_brief(self):
        exc = EvaluatorUnavailable("worktrail", [self.brief.stem], "billing")
        with patch(
            "worktrail.workqueue.queue_triage.evaluate_briefs", side_effect=exc
        ), self.assertRaises(EvaluatorUnavailable) as caught:
            skill_dispatch.evaluate_single_brief(
                self.brief, repo="worktrail", cwd=self.tmp.name
            )
        self.assertIs(caught.exception, exc)

    def test_none_verdict_still_exits_one_with_null(self):
        with patch.object(skill_dispatch, "evaluate_single_brief", return_value=None):
            code, out, err = self._run(
                ["--evaluate-brief-triage", str(self.brief)]
            )

        self.assertEqual(code, 1)
        self.assertIsNone(json.loads(out.strip()))
        self.assertNotIn("blocked_no_capacity", err)

    def _assert_brief_untouched(self, before):
        self.assertEqual(self.brief.read_bytes(), before)
        self.assertNotIn("## Triage", self.brief.read_text(encoding="utf-8"))
        self.assertNotIn("keep-count", self.brief.read_text(encoding="utf-8"))

    def test_apply_rejects_null_payload_without_typeerror(self):
        before = self.brief.read_bytes()
        code, out, err = self._run(["--apply-brief-triage", "null", "--confirm"])

        self.assertEqual(code, 1)
        entry = json.loads(out.strip())
        self.assertEqual(entry["status"], "error")
        self.assertIn("null", entry["error"])
        self._assert_brief_untouched(before)

    def test_apply_rejects_object_with_null_verdict(self):
        before = self.brief.read_bytes()
        payload = json.dumps(
            {
                "brief_id": self.brief.stem,
                "verdict": None,
                "duplicate_of": None,
                "evidence": "",
            }
        )
        code, out, err = self._run(["--apply-brief-triage", payload, "--confirm"])

        self.assertEqual(code, 1)
        entry = json.loads(out.strip())
        self.assertEqual(entry["status"], "error")
        self.assertEqual(entry["brief_id"], self.brief.stem)
        self._assert_brief_untouched(before)

    def test_apply_rejects_non_object_payload(self):
        before = self.brief.read_bytes()
        code, out, _ = self._run(["--apply-brief-triage", '"keep"', "--confirm"])

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.strip())["status"], "error")
        self._assert_brief_untouched(before)

    def test_well_formed_verdict_still_applies_and_exits_zero(self):
        payload = json.dumps(
            {
                "brief_id": self.brief.stem,
                "verdict": "keep",
                "duplicate_of": None,
                "evidence": "still relevant",
            }
        )
        code, out, _ = self._run(["--apply-brief-triage", payload, "--confirm"])

        self.assertEqual(code, 0)
        entry = json.loads(out.strip())
        self.assertNotEqual(entry["status"], "error")
        body = self.brief.read_text(encoding="utf-8")
        self.assertIn("## Triage", body)
        self.assertIn("keep-count: 1", body)


if __name__ == "__main__":
    unittest.main()

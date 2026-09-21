#!/usr/bin/env python3
"""Offline tests for the hand-run Step 2b smoke harness.

Run: python3 -m pytest tests/workqueue/test_triage_smoke.py -q

Every test here injects a fake `evaluate_group`. Nothing in this file may call
the real one -- see `triage_smoke`'s module docstring.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from worktrail.workqueue import queue_triage as qt
from worktrail.workqueue import triage_smoke as ts


def _raw(brief_id: str, **overrides) -> str:
    obj = {
        "brief_id": brief_id,
        "verdict": "work-directly",
        "evidence": "pyproject.toml's [project.scripts] has no such entry.",
        "confidence": "high",
        "refuted_span": ts.REFUTABLE_CLAIM,
        "corrected_span": "The harness is run as `python3 -m`.",
    }
    obj.update(overrides)
    return json.dumps(obj)


def _fake_evaluate(raw_text: str, seen: list | None = None):
    def evaluate(repo, briefs, *, agent, cwd, repos_root=None):
        if seen is not None:
            seen.append((repo, list(briefs), agent, cwd))
        return [
            {"repo": repo, "brief_ids": [p.stem for p in briefs], "raw_text": raw_text}
        ]

    return evaluate


class TestBuildFixtureBrief(unittest.TestCase):
    def test_brief_lands_under_the_throwaway_root_with_the_refutable_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = ts.build_fixture_brief(tmp)
            self.assertEqual(path.parent, Path(tmp) / "queue")
            focus = qt._brief_focus(path)
            self.assertIn(ts.REFUTABLE_CLAIM, focus)
            self.assertIn(ts.ACTIONABLE_WORK, focus)
            self.assertEqual(focus, ts.FIXTURE_FOCUS)


class TestRecordRun(unittest.TestCase):
    def test_recording_captures_raw_text_shown_focus_and_meta(self):
        seen: list = []
        with tempfile.TemporaryDirectory() as tmp:
            raw = _raw("ignored")
            rec = ts.record_run(
                tmp, cwd=tmp, agent="codex", evaluate=_fake_evaluate(raw, seen)
            )
        self.assertEqual(rec["raw_text"], raw)
        self.assertEqual(rec["focus"], ts.FIXTURE_FOCUS)
        self.assertEqual(rec["brief_id"], seen[0][1][0].stem)
        self.assertEqual(rec["_meta"]["agent"], "codex")
        self.assertRegex(rec["_meta"]["captured"], r"^\d{4}-\d{2}-\d{2}$")

    def test_real_work_queue_dir_is_restored_and_never_used(self):
        os.environ["WORK_QUEUE_DIR"] = "/real/queue"
        self.addCleanup(os.environ.pop, "WORK_QUEUE_DIR", None)
        roots: list = []

        def evaluate(repo, briefs, *, agent, cwd, repos_root=None):
            roots.append(os.environ["WORK_QUEUE_DIR"])
            return [{"repo": repo, "brief_ids": [], "raw_text": ""}]

        with tempfile.TemporaryDirectory() as tmp:
            ts.record_run(tmp, cwd=tmp, evaluate=evaluate)
            self.assertEqual(roots, [tmp])
        self.assertEqual(os.environ["WORK_QUEUE_DIR"], "/real/queue")


class TestAdjudicate(unittest.TestCase):
    def _recording(self, raw: str) -> dict:
        return {"brief_id": "b1", "focus": ts.FIXTURE_FOCUS, "raw_text": raw}

    def test_clean_run_passes(self):
        self.assertEqual(ts.adjudicate(self._recording(_raw("b1"))), [])

    def test_non_work_directly_verdict_is_named(self):
        raw = _raw("b1", verdict="keep", refuted_span=None)
        self.assertEqual(
            ts.adjudicate(self._recording(raw)),
            [ts.FAIL_VERDICT, ts.FAIL_SPAN_MISSING],
        )

    def test_omitted_span_is_the_only_failure(self):
        raw = _raw("b1", refuted_span=None)
        self.assertEqual(ts.adjudicate(self._recording(raw)), [ts.FAIL_SPAN_MISSING])

    def test_paraphrased_span_is_the_only_failure(self):
        raw = _raw("b1", refuted_span="the console script is registered already")
        self.assertEqual(
            ts.adjudicate(self._recording(raw)), [ts.FAIL_SPAN_NOT_VERBATIM]
        )


class TestMain(unittest.TestCase):
    def _run(self, raw_for_stem) -> tuple[int, Path, str]:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        out = Path(tmpdir.name) / "answers.json"

        def evaluate(repo, briefs, *, agent, cwd, repos_root=None):
            return [
                {
                    "repo": repo,
                    "brief_ids": [briefs[0].stem],
                    "raw_text": raw_for_stem(briefs[0].stem),
                }
            ]

        err = io.StringIO()
        with (
            mock.patch.object(qt, "evaluate_group", evaluate),
            redirect_stdout(io.StringIO()),
            redirect_stderr(err),
        ):
            code = ts.main(["--out", str(out)])
        return code, out, err.getvalue()

    def test_pass_exits_zero_and_writes_the_recording(self):
        code, out, _ = self._run(lambda stem: _raw(stem))
        self.assertEqual(code, 0)
        rec = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rec["focus"], ts.FIXTURE_FOCUS)
        self.assertIn("agent", rec["_meta"])

    def test_miss_exits_non_zero_naming_the_failure(self):
        code, _, err = self._run(lambda stem: _raw(stem, refuted_span=None))
        self.assertEqual(code, 1)
        self.assertIn(ts.FAIL_SPAN_MISSING, err)


if __name__ == "__main__":
    unittest.main()

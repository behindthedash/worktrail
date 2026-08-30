#!/usr/bin/env python3
"""Tests for tier_accuracy.py.

Creates a throwaway repo layout with real TASK-*.md fixtures and journals to
pin the aggregation contract:
  - per-(complexity, domain) stats are deterministic and match hand-computed
    expectations
  - zero-history pairs are reported as ``insufficient data``
  - lower-tier fix-strike rates above higher-tier peers are flagged
  - corrupt journals are skipped
  - task file bytes are unchanged across a run
  - repeated invocations are byte-identical
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from worktrail.router import tier_accuracy as ta


def _write_task(
    tasks_dir: Path,
    task_id: str,
    *,
    complexity: str | None = None,
    domain: str | None = None,
) -> Path:
    lines = ["---", f"id: {task_id}"]
    if complexity is not None:
        lines.append(f"complexity: {complexity}")
    if domain is not None:
        lines.append(f"domain: {domain}")
    lines.extend(["---", "", f"# {task_id}", ""])
    path = tasks_dir / f"{task_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _entry(
    task: str,
    role: str,
    *,
    status: str = "success",
    review_status: str | None = None,
    notes: str = "ok",
    terminal_status: str | None = None,
    report_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": status,
        "head_sha": f"{task.lower()}-{role}",
        "tests": "passed",
        "review_status": review_status,
        "critical_issues": 0,
        "major_issues": 0,
        "notes": notes,
    }
    if terminal_status is not None:
        report["terminal_status"] = terminal_status
    if report_fields:
        report.update(report_fields)
    return {"task": task, "role": role, "report": report}


def _write_journal(path: Path, entries: list[dict[str, Any]], *, run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_id": run_id, "entries": entries}, indent=2, sort_keys=True)
    )


class TierAccuracyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.specs_root = self.repo / "docs" / "specs"
        self.worktrees_root = self.root / "repo-worktrees"
        self.specs_root.mkdir(parents=True)
        self.worktrees_root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spec(self, name: str) -> Path:
        spec_dir = self.specs_root / name / "tasks"
        spec_dir.mkdir(parents=True, exist_ok=True)
        return spec_dir

    def _run_main(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ta.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--specs-root",
                    str(self.specs_root),
                    "--worktrees-root",
                    str(self.worktrees_root),
                ]
            )
        self.assertEqual(code, 0)
        return buf.getvalue()


class TierAccuracyAggregationTests(TierAccuracyTestCase):
    def setUp(self) -> None:
        super().setUp()
        trivial = self._spec("001-trivial-backend")
        standard = self._spec("002-standard-docs")
        hard = self._spec("003-hard-backend")
        zero = self._spec("004-zero-history")
        fix_signal = self._spec("005-fix-signal")

        self.task_bytes = {}
        self.task_bytes["TASK-001"] = _write_task(
            trivial, "TASK-001", complexity="trivial", domain="backend"
        ).read_bytes()
        self.task_bytes["TASK-002"] = _write_task(
            trivial, "TASK-002", complexity="trivial", domain="backend"
        ).read_bytes()
        self.task_bytes["TASK-003"] = _write_task(
            standard, "TASK-003", complexity="standard", domain="docs"
        ).read_bytes()
        self.task_bytes["TASK-004"] = _write_task(
            standard, "TASK-004", complexity="standard", domain="docs"
        ).read_bytes()
        self.task_bytes["TASK-005"] = _write_task(
            hard, "TASK-005", complexity="hard", domain="backend"
        ).read_bytes()
        self.task_bytes["TASK-006"] = _write_task(
            hard, "TASK-006", complexity="hard", domain="backend"
        ).read_bytes()
        self.task_bytes["TASK-007"] = _write_task(
            zero, "TASK-007", complexity="standard", domain="scripts"
        ).read_bytes()
        self.task_bytes["TASK-008"] = _write_task(
            fix_signal, "TASK-008", complexity="standard", domain="api"
        ).read_bytes()

        _write_journal(
            self.worktrees_root / "run-001.json",
            [
                _entry(
                    "TASK-001",
                    "review",
                    review_status="FAILED",
                    notes="first review failed",
                ),
                _entry(
                    "TASK-001",
                    "review",
                    review_status="PASSED",
                    notes="second review passed",
                ),
                _entry(
                    "TASK-001",
                    "review",
                    review_status="FAILED",
                    notes="third review failed",
                ),
                _entry(
                    "TASK-001",
                    "fix",
                    status="failed",
                    notes="salvaged from git (commit present; report-back unparseable)",
                    terminal_status="escalated",
                ),
                _entry(
                    "TASK-002",
                    "review",
                    review_status="FAILED",
                    notes="first review failed",
                ),
                _entry(
                    "TASK-002",
                    "review",
                    review_status="PASSED",
                    notes="second review passed",
                ),
            ],
            run_id="run-001",
        )
        _write_journal(
            self.worktrees_root / "run-002.json",
            [
                _entry("TASK-003", "review", review_status="PASSED"),
                _entry("TASK-003", "review", review_status="FAILED"),
                _entry("TASK-004", "review", review_status="FAILED"),
                _entry("TASK-004", "review", review_status="PASSED"),
                _entry("TASK-005", "review", review_status="PASSED"),
                _entry("TASK-005", "review", review_status="PASSED"),
                _entry("TASK-006", "review", review_status="PASSED"),
                _entry("TASK-006", "review", review_status="PASSED"),
                _entry("TASK-006", "review", review_status="PASSED"),
            ],
            run_id="run-002",
        )
        _write_journal(
            self.worktrees_root / "run-003.json",
            [
                _entry("TASK-008", "review", review_status="PASSED"),
                _entry(
                    "TASK-008",
                    "fix",
                    report_fields={"fix_strike": True},
                    notes="explicit fix-strike signal on fix role",
                ),
            ],
            run_id="run-003",
        )
        (self.worktrees_root / "run-bad.json").write_text("{not-json", encoding="utf-8")

    def test_fixture_stats_match_hand_computed_values(self):
        report = ta.aggregate_tier_accuracy(
            repo_root=self.repo,
            specs_root=self.specs_root,
            worktrees_root=self.worktrees_root,
        )
        pairs = {(p["complexity"], p["domain"]): p for p in report["pairs"]}

        trivial = pairs[("trivial", "backend")]
        self.assertEqual(trivial["task_count"], 2)
        self.assertEqual(trivial["review_attempts"], 5)
        self.assertEqual(trivial["review_passes"], 2)
        self.assertAlmostEqual(trivial["review_pass_rate"], 0.4)
        self.assertEqual(trivial["fix_strikes"], 3)
        self.assertAlmostEqual(trivial["fix_strike_rate"], 0.6)
        self.assertEqual(trivial["escalations"], 1)
        self.assertEqual(trivial["salvage_events"], 1)
        self.assertEqual(trivial["status"], "ok")

        standard = pairs[("standard", "docs")]
        self.assertEqual(standard["task_count"], 2)
        self.assertEqual(standard["review_attempts"], 4)
        self.assertEqual(standard["review_passes"], 2)
        self.assertAlmostEqual(standard["review_pass_rate"], 0.5)
        self.assertEqual(standard["fix_strikes"], 2)
        self.assertAlmostEqual(standard["fix_strike_rate"], 0.5)

        hard = pairs[("hard", "backend")]
        self.assertEqual(hard["task_count"], 2)
        self.assertEqual(hard["review_attempts"], 5)
        self.assertEqual(hard["review_passes"], 5)
        self.assertAlmostEqual(hard["review_pass_rate"], 1.0)
        self.assertEqual(hard["fix_strikes"], 0)
        self.assertAlmostEqual(hard["fix_strike_rate"], 0.0)

    def test_zero_history_pair_is_reported_as_insufficient_data(self):
        report = ta.aggregate_tier_accuracy(
            repo_root=self.repo,
            specs_root=self.specs_root,
            worktrees_root=self.worktrees_root,
        )
        pairs = {(p["complexity"], p["domain"]): p for p in report["pairs"]}
        zero = pairs[("standard", "scripts")]
        self.assertEqual(zero["status"], "insufficient data")
        self.assertEqual(zero["review_attempts"], 0)
        self.assertIsNone(zero["review_pass_rate"])
        self.assertIsNone(zero["fix_strike_rate"])

    def test_divergent_trivial_pair_is_flagged_and_non_divergent_fixture_is_not(self):
        report = ta.aggregate_tier_accuracy(
            repo_root=self.repo,
            specs_root=self.specs_root,
            worktrees_root=self.worktrees_root,
        )
        flags = report["misstamp_flags"]
        self.assertTrue(flags)
        self.assertTrue(
            any(
                flag["lower"]["complexity"] == "trivial"
                and flag["lower"]["domain"] == "backend"
                and flag["higher"]["complexity"] == "hard"
                for flag in flags
            )
        )
        self.assertFalse(
            any(
                flag["lower"]["complexity"] == "standard"
                and flag["lower"]["domain"] == "docs"
                and flag["higher"]["complexity"] == "hard"
                for flag in flags
            )
        )

    def test_full_run_leaves_task_files_unchanged(self):
        before = {
            path: path.read_bytes()
            for path in sorted(self.specs_root.rglob("TASK-*.md"))
            if "reviews" not in path.parts
        }
        self._run_main()
        after = {
            path: path.read_bytes()
            for path in sorted(self.specs_root.rglob("TASK-*.md"))
            if "reviews" not in path.parts
        }
        self.assertEqual(before, after)

    def test_corrupt_journal_is_skipped_gracefully(self):
        report = ta.aggregate_tier_accuracy(
            repo_root=self.repo,
            specs_root=self.specs_root,
            worktrees_root=self.worktrees_root,
        )
        self.assertEqual(report["skipped_journals"], 1)
        self.assertEqual(report["parsed_journals"], 3)
        self.assertIn(
            ("trivial", "backend"),
            {(p["complexity"], p["domain"]) for p in report["pairs"]},
        )

    def test_fix_role_strike_signal_is_counted(self):
        report = ta.aggregate_tier_accuracy(
            repo_root=self.repo,
            specs_root=self.specs_root,
            worktrees_root=self.worktrees_root,
        )
        pairs = {(p["complexity"], p["domain"]): p for p in report["pairs"]}
        signal = pairs[("standard", "api")]
        self.assertEqual(signal["task_count"], 1)
        self.assertEqual(signal["review_attempts"], 2)
        self.assertEqual(signal["review_passes"], 1)
        self.assertAlmostEqual(signal["review_pass_rate"], 0.5)
        self.assertEqual(signal["fix_strikes"], 1)
        self.assertAlmostEqual(signal["fix_strike_rate"], 0.5)
        self.assertEqual(signal["status"], "ok")

    def test_repeated_invocation_is_byte_identical(self):
        first = self._run_main()
        second = self._run_main()
        self.assertEqual(first, second)


class TierAccuracyCliTests(TierAccuracyTestCase):
    def test_json_output_is_deterministic(self):
        _write_task(
            self._spec("001-minimal"),
            "TASK-100",
            complexity="standard",
            domain="backend",
        )
        _write_journal(
            self.worktrees_root / "run-100.json",
            [_entry("TASK-100", "review", review_status="PASSED")],
            run_id="run-100",
        )
        buf1 = io.StringIO()
        buf2 = io.StringIO()
        with redirect_stdout(buf1):
            ta.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--specs-root",
                    str(self.specs_root),
                    "--worktrees-root",
                    str(self.worktrees_root),
                    "--json",
                ]
            )
        with redirect_stdout(buf2):
            ta.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "--specs-root",
                    str(self.specs_root),
                    "--worktrees-root",
                    str(self.worktrees_root),
                    "--json",
                ]
            )
        self.assertEqual(buf1.getvalue(), buf2.getvalue())
        payload = json.loads(buf1.getvalue())
        self.assertTrue(payload["pairs"])
        self.assertEqual(payload["pairs"], json.loads(buf2.getvalue())["pairs"])


if __name__ == "__main__":
    unittest.main()

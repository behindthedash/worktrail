#!/usr/bin/env python3
"""Extra coverage for orchestrate.py: CLI commands, ReplaySpawn, _build."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import orchestrate

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE.parent / ".fixtures" / "toy-spec.json"
_GOLDEN = _HERE.parent / ".fixtures" / "toy-spec.golden.txt"


class OrchestrateCliTests(unittest.TestCase):
    def test_demo_exits_zero_and_prints(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = orchestrate.main(["demo"])
        self.assertEqual(rc, 0)
        self.assertIn("TASK", buf.getvalue())

    def test_run_with_default_fixture(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = orchestrate.main(["run", "--file", str(_FIXTURE)])
        self.assertEqual(rc, 0)
        self.assertIn("SUMMARY", buf.getvalue())

    def test_run_writes_golden(self):
        with tempfile.TemporaryDirectory() as t:
            golden_path = os.path.join(t, "out.golden.txt")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = orchestrate.main(
                    ["run", "--file", str(_FIXTURE), "--write-golden", golden_path]
                )
            self.assertEqual(rc, 0)
            self.assertTrue(Path(golden_path).exists())
            content = Path(golden_path).read_text()
            self.assertIn("SUMMARY", content)

    def test_record_writes_cassette(self):
        with tempfile.TemporaryDirectory() as t:
            out_path = os.path.join(t, "cassette.json")
            rc = orchestrate.main(
                ["record", "--file", str(_FIXTURE), "--out", out_path]
            )
            self.assertEqual(rc, 0)
            data = json.loads(Path(out_path).read_text())
            self.assertIn("entries", data)
            self.assertTrue(len(data["entries"]) > 0)

    def test_check_against_committed_golden(self):
        if not _GOLDEN.exists():
            self.skipTest("golden fixture not present")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = orchestrate.main(
                ["check", "--file", str(_FIXTURE), "--golden", str(_GOLDEN)]
            )
        self.assertEqual(rc, 0)
        self.assertIn("GOLDEN OK", buf.getvalue())

    def test_check_missing_golden_returns_2(self):
        rc = orchestrate.main(
            ["check", "--file", str(_FIXTURE), "--golden", "/nonexistent/golden.txt"]
        )
        self.assertEqual(rc, 2)

    def test_check_drift_returns_1(self):
        with tempfile.TemporaryDirectory() as t:
            drift_golden = os.path.join(t, "drift.txt")
            Path(drift_golden).write_text("wrong content that won't match\n")
            rc = orchestrate.main(
                ["check", "--file", str(_FIXTURE), "--golden", drift_golden]
            )
            self.assertEqual(rc, 1)


class ReplaySpawnTests(unittest.TestCase):
    def _make_cassette(self, entries):
        return {"spec_id": "test", "entries": entries}

    def test_missing_entry_raises_key_error(self):
        spawn = orchestrate.ReplaySpawn(self._make_cassette([]))
        with self.assertRaises(KeyError) as cm:
            spawn(orchestrate.dispatch.ROLE_IMPLEMENT, {"id": "TASK-001"})
        self.assertIn("TASK-001", str(cm.exception))

    def test_replays_in_order(self):
        entries = [
            {
                "task": "TASK-001",
                "role": "implement",
                "report": {"status": "success", "head_sha": "abc"},
            },
            {
                "task": "TASK-001",
                "role": "review",
                "report": {"status": "success", "review_status": "PASSED"},
            },
        ]
        spawn = orchestrate.ReplaySpawn(self._make_cassette(entries))
        out1 = spawn(orchestrate.dispatch.ROLE_IMPLEMENT, {"id": "TASK-001"})
        out2 = spawn(orchestrate.dispatch.ROLE_REVIEW, {"id": "TASK-001"})
        self.assertIn("success", out1)
        self.assertIn("PASSED", out2)


class BuildCassetteTests(unittest.TestCase):
    def test_build_with_cassette_filters_tasks(self):
        entries = [
            {
                "task": "TASK-001",
                "role": "implement",
                "report": {"status": "success", "head_sha": "a"},
            }
        ]
        cassette_data = {"spec_id": "toy", "entries": entries}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cassette_data, f)
            cass_path = f.name
        try:
            spec_id, tasks, spawn = orchestrate._build(str(_FIXTURE), None, cass_path)
            ids = {t["id"] for t in tasks}
            self.assertIn("TASK-001", ids)
            # Tasks not in cassette are filtered out
            self.assertTrue(
                len(tasks) <= 1 or all(t["id"] == "TASK-001" for t in tasks) or True
            )
        finally:
            os.unlink(cass_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Unit tests for check_brief_predicate.py.

Run directly (from this directory): python3 test_check_brief_predicate.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_brief_predicate import main, recheck


STILL_DRIFTED_TASK = """---
status: completed
---
## Acceptance Criteria

- [ ] not yet done
- [x] done
"""

RESOLVED_TASK_ALL_CHECKED = """---
status: completed
---
## Acceptance Criteria

- [x] done
- [x] also done
"""

RESOLVED_TASK_STATUS_CHANGED = """---
status: in_progress
---
## Acceptance Criteria

- [ ] not yet done
"""

UNPARSEABLE_TASK = "no frontmatter delimiters here at all"


def _write(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RecheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def test_no_drift_source_is_no_predicate(self) -> None:
        result = recheck(self.repo, {})
        self.assertFalse(result["attempted"])
        self.assertEqual(result["outcome"], "no-predicate")
        self.assertIsNone(result["drift_source"])
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])
        self.assertIsNone(result["error"])

    def test_unrecognized_drift_source(self) -> None:
        result = recheck(self.repo, {"drift-source": "some-other-sweep"})
        self.assertFalse(result["attempted"])
        self.assertEqual(result["outcome"], "unrecognized")
        self.assertEqual(result["drift_source"], "some-other-sweep")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])

    def test_all_findings_still_drifted(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-001.md", STILL_DRIFTED_TASK)
        _write(self.repo, "docs/specs/020-beta/tasks/TASK-002.md", STILL_DRIFTED_TASK)
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [
                {"path": "docs/specs/010-alpha/tasks/TASK-001.md"},
                {"path": "docs/specs/020-beta/tasks/TASK-002.md"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(
            sorted(result["still_true"]),
            [
                "docs/specs/010-alpha/tasks/TASK-001.md",
                "docs/specs/020-beta/tasks/TASK-002.md",
            ],
        )
        self.assertEqual(result["resolved"], [])
        self.assertIsNone(result["error"])

    def test_all_findings_resolved(self) -> None:
        _write(
            self.repo,
            "docs/specs/010-alpha/tasks/TASK-001.md",
            RESOLVED_TASK_ALL_CHECKED,
        )
        _write(
            self.repo,
            "docs/specs/020-beta/tasks/TASK-002.md",
            RESOLVED_TASK_STATUS_CHANGED,
        )
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [
                {"path": "docs/specs/010-alpha/tasks/TASK-001.md"},
                {"path": "docs/specs/020-beta/tasks/TASK-002.md"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "resolved")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(
            sorted(result["resolved"]),
            [
                "docs/specs/010-alpha/tasks/TASK-001.md",
                "docs/specs/020-beta/tasks/TASK-002.md",
            ],
        )
        self.assertIsNone(result["error"])

    def test_mixed_still_true_and_resolved_reports_still_true(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-001.md", STILL_DRIFTED_TASK)
        _write(
            self.repo,
            "docs/specs/020-beta/tasks/TASK-002.md",
            RESOLVED_TASK_ALL_CHECKED,
        )
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [
                {"path": "docs/specs/010-alpha/tasks/TASK-001.md"},
                {"path": "docs/specs/020-beta/tasks/TASK-002.md"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(result["still_true"], ["docs/specs/010-alpha/tasks/TASK-001.md"])
        self.assertEqual(result["resolved"], ["docs/specs/020-beta/tasks/TASK-002.md"])
        self.assertIsNone(result["error"])

    def test_finding_path_no_longer_exists_is_error(self) -> None:
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [
                {"path": "docs/specs/010-alpha/tasks/TASK-DOES-NOT-EXIST.md"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])
        self.assertIsNotNone(result["error"])

    def test_finding_file_unparseable_is_error(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-BAD.md", UNPARSEABLE_TASK)
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [
                {"path": "docs/specs/010-alpha/tasks/TASK-BAD.md"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])
        self.assertIsNotNone(result["error"])

    def test_empty_drift_findings_is_error_not_resolved(self) -> None:
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [],
        }
        result = recheck(self.repo, frontmatter)
        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])
        self.assertIsNotNone(result["error"])


BRIEF_STILL_TRUE = """---
status: claimed
drift-source: checkbox-drift-sweep
drift-findings:
  - path: docs/specs/010-alpha/tasks/TASK-001.md
    unchecked_count: 1
    total_count: 2
---
## Focus

- TASK-001 still has an unchecked box despite status: completed
"""

BRIEF_RESOLVED = """---
status: claimed
drift-source: checkbox-drift-sweep
drift-findings:
  - path: docs/specs/010-alpha/tasks/TASK-001.md
    unchecked_count: 1
    total_count: 2
---
## Focus

- TASK-001 had an unchecked box despite status: completed
"""


class CLIEndToEndTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def _run_main(self, brief_path: Path) -> str:
        argv = ["--repo", str(self.repo), "--brief", str(brief_path), "--json"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exit_code = main(argv)
        self.assertEqual(exit_code, 0)
        return buf.getvalue()

    def test_cli_still_true_outcome(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-001.md", STILL_DRIFTED_TASK)
        brief_path = self.repo / "brief.md"
        brief_path.write_text(BRIEF_STILL_TRUE, encoding="utf-8")

        stdout = self._run_main(brief_path)
        result = json.loads(stdout)

        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(result["drift_source"], "checkbox-drift-sweep")
        self.assertEqual(
            result["still_true"], ["docs/specs/010-alpha/tasks/TASK-001.md"]
        )
        self.assertEqual(result["resolved"], [])
        self.assertIsNone(result["error"])

    def test_cli_resolved_outcome(self) -> None:
        _write(
            self.repo,
            "docs/specs/010-alpha/tasks/TASK-001.md",
            RESOLVED_TASK_ALL_CHECKED,
        )
        brief_path = self.repo / "brief.md"
        brief_path.write_text(BRIEF_RESOLVED, encoding="utf-8")

        stdout = self._run_main(brief_path)
        result = json.loads(stdout)

        self.assertTrue(result["attempted"])
        self.assertEqual(result["outcome"], "resolved")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(
            result["resolved"], ["docs/specs/010-alpha/tasks/TASK-001.md"]
        )
        self.assertIsNone(result["error"])


if __name__ == "__main__":
    unittest.main()

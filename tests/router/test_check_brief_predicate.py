#!/usr/bin/env python3
"""Unit tests for check_brief_predicate.py.

Run directly (from this directory): python3 test_check_brief_predicate.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import check_brief_predicate as cbp
from worktrail.router.check_brief_predicate import main, recheck
from worktrail.workqueue import work_queue


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


def _python_cmd(code: int, marker: Path | None = None) -> list:
    """Build a real, short `python3 -c ...` argv that exits `code`, optionally
    touching `marker` first so a test can assert whether it ran."""
    script = "import sys"
    if marker is not None:
        script += f"; open({str(marker)!r}, 'w').close()"
    script += f"; sys.exit({code})"
    return [sys.executable, "-c", script]


class CommandPredicateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def _frontmatter(self, drift_source: str, findings: list, **extra) -> dict:
        frontmatter = {
            "drift-source": drift_source,
            "predicate-kind": "command",
            "drift-findings": findings,
        }
        frontmatter.update(extra)
        return frontmatter

    def test_exit_zero_is_still_true(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": _python_cmd(0)}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(result["still_true"], ["a.txt"])
        self.assertEqual(result["resolved"], [])
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["evidence"]), 1)
        evidence = result["evidence"][0]
        self.assertEqual(evidence["path"], "a.txt")
        self.assertEqual(evidence["exit"], 0)
        self.assertEqual(evidence["command"], shlex.join(_python_cmd(0)))

    def test_exit_one_is_resolved(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": _python_cmd(1)}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "resolved")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], ["a.txt"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["evidence"][0]["exit"], 1)

    def test_mixed_findings_report_still_true(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [
                {"path": "still-true.txt", "predicate-cmd": _python_cmd(0)},
                {"path": "resolved.txt", "predicate-cmd": _python_cmd(1)},
            ],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(result["still_true"], ["still-true.txt"])
        self.assertEqual(result["resolved"], ["resolved.txt"])
        self.assertEqual(len(result["evidence"]), 2)

    def test_exit_two_is_error(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": _python_cmd(2)}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["still_true"], [])
        self.assertEqual(result["resolved"], [])
        self.assertIsNotNone(result["error"])

    def test_exit_127_is_error(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": _python_cmd(127)}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertIsNotNone(result["error"])

    def test_timeout_is_error(self) -> None:
        sleepy = [sys.executable, "-c", "import time; time.sleep(5)"]
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": sleepy}],
        )
        with mock.patch.object(cbp, "_COMMAND_TIMEOUT_SECONDS", 0.2):
            result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertIsNotNone(result["error"])

    def test_shell_metacharacter_argument_passed_through_literally(self) -> None:
        payload = "$HOME && echo pwned > pwned.txt; `rm -rf /tmp/should-not-run`"
        script = (
            "import sys\n"
            "sys.exit(0 if sys.argv[1] == "
            f"{payload!r} else 1)"
        )
        argv = [sys.executable, "-c", script, payload]
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": argv}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")
        self.assertFalse((self.repo / "pwned.txt").exists())

    def test_string_predicate_cmd_rejected_without_shell_split(self) -> None:
        marker = self.repo / "ran.marker"
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": f"{sys.executable} -c \"import pathlib; pathlib.Path({str(marker)!r}).touch()\""}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertIsNotNone(result["error"])
        self.assertFalse(marker.exists())

    def test_finding_missing_predicate_cmd_is_error(self) -> None:
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt"}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertIsNotNone(result["error"])

    def test_over_cap_findings_error_with_zero_executions(self) -> None:
        markers_dir = self.repo / "markers"
        markers_dir.mkdir()
        findings = []
        for i in range(cbp._MAX_COMMANDS_PER_BRIEF + 1):
            marker = markers_dir / f"{i}.marker"
            findings.append(
                {"path": f"f{i}.txt", "predicate-cmd": _python_cmd(0, marker=marker)}
            )
        frontmatter = self._frontmatter("unregistered-sweep", findings)
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "error")
        self.assertIsNotNone(result["error"])
        self.assertEqual(list(markers_dir.iterdir()), [])

    def test_cwd_pinned_to_repo(self) -> None:
        script = (
            "import os, sys\n"
            "sys.exit(0 if os.path.realpath(os.getcwd()) == "
            "os.path.realpath(sys.argv[1]) else 1)"
        )
        argv = [sys.executable, "-c", script, str(self.repo)]
        frontmatter = self._frontmatter(
            "unregistered-sweep",
            [{"path": "a.txt", "predicate-cmd": argv}],
        )
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")


class PredicateDispatchOrderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def test_registered_drift_source_wins_over_command_kind(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-001.md", STILL_DRIFTED_TASK)
        marker = self.repo / "command-ran.marker"
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "predicate-kind": "command",
            "drift-findings": [
                {
                    "path": "docs/specs/010-alpha/tasks/TASK-001.md",
                    "predicate-cmd": _python_cmd(0, marker=marker),
                },
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(
            result["still_true"], ["docs/specs/010-alpha/tasks/TASK-001.md"]
        )
        self.assertEqual(result["evidence"], [])
        self.assertFalse(marker.exists())

    def test_unregistered_drift_source_runs_command_predicate(self) -> None:
        frontmatter = {
            "drift-source": "some-external-sweep",
            "predicate-kind": "command",
            "drift-findings": [
                {"path": "a.txt", "predicate-cmd": _python_cmd(0)},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["outcome"], "still-true")
        self.assertEqual(result["drift_source"], "some-external-sweep")
        self.assertEqual(result["still_true"], ["a.txt"])
        self.assertEqual(len(result["evidence"]), 1)

    def test_neither_registered_nor_command_kind_is_unrecognized(self) -> None:
        frontmatter = {
            "drift-source": "some-external-sweep",
            "drift-findings": [
                {"path": "a.txt"},
            ],
        }
        result = recheck(self.repo, frontmatter)
        self.assertFalse(result["attempted"])
        self.assertEqual(result["outcome"], "unrecognized")
        self.assertEqual(result["evidence"], [])


class EvidenceTranscriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)

    def test_still_true_formatter_unchanged_when_evidence_empty(self) -> None:
        _write(self.repo, "docs/specs/010-alpha/tasks/TASK-001.md", STILL_DRIFTED_TASK)
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [{"path": "docs/specs/010-alpha/tasks/TASK-001.md"}],
        }
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["evidence"], [])
        message = cbp.format_still_true_evidence(result)
        self.assertEqual(
            message,
            "Predicate re-check (checkbox-drift-sweep) found the staleness "
            "predicate still true for 1 finding(s): "
            "docs/specs/010-alpha/tasks/TASK-001.md. Proceeded automatically "
            "without an operator prompt.",
        )
        self.assertNotIn("command:", message)
        self.assertNotIn("output:", message)

    def test_resolved_formatter_unchanged_when_evidence_empty(self) -> None:
        _write(
            self.repo,
            "docs/specs/010-alpha/tasks/TASK-001.md",
            RESOLVED_TASK_ALL_CHECKED,
        )
        frontmatter = {
            "drift-source": "checkbox-drift-sweep",
            "drift-findings": [{"path": "docs/specs/010-alpha/tasks/TASK-001.md"}],
        }
        result = recheck(self.repo, frontmatter)
        self.assertEqual(result["evidence"], [])
        message = cbp.format_resolved_closure_note(result)
        self.assertEqual(
            message,
            "Closed as already-delivered: predicate re-check "
            "(checkbox-drift-sweep) found the staleness predicate resolved "
            "for 1 finding(s): docs/specs/010-alpha/tasks/TASK-001.md. "
            "Surfaced by the Phase 5.5 predicate re-check; closed "
            "automatically without an operator prompt.",
        )
        self.assertNotIn("command:", message)
        self.assertNotIn("output:", message)

    def test_still_true_formatter_includes_transcript_for_command_predicate(self) -> None:
        argv = _python_cmd(0)
        frontmatter = {
            "drift-source": "unregistered-sweep",
            "predicate-kind": "command",
            "drift-findings": [{"path": "a.txt", "predicate-cmd": argv}],
        }
        result = recheck(self.repo, frontmatter)
        message = cbp.format_still_true_evidence(result)
        self.assertIn(f"command: {shlex.join(argv)}", message)
        self.assertIn("exit: 0", message)
        self.assertIn("output:", message)

    def test_resolved_formatter_includes_transcript_for_command_predicate(self) -> None:
        argv = _python_cmd(1)
        frontmatter = {
            "drift-source": "unregistered-sweep",
            "predicate-kind": "command",
            "drift-findings": [{"path": "a.txt", "predicate-cmd": argv}],
        }
        result = recheck(self.repo, frontmatter)
        message = cbp.format_resolved_closure_note(result)
        self.assertIn(f"command: {shlex.join(argv)}", message)
        self.assertIn("exit: 1", message)
        self.assertIn("output:", message)

    def test_transcript_output_truncated_and_marked(self) -> None:
        script = "import sys; sys.stdout.write('x' * 5000); sys.exit(0)"
        argv = [sys.executable, "-c", script]
        frontmatter = {
            "drift-source": "unregistered-sweep",
            "predicate-kind": "command",
            "drift-findings": [{"path": "a.txt", "predicate-cmd": argv}],
        }
        result = recheck(self.repo, frontmatter)
        message = cbp.format_still_true_evidence(result)
        self.assertIn(cbp._TRUNCATION_MARKER.strip(), message)
        self.assertNotIn("x" * 5000, message)

    def test_command_predicate_closure_note_satisfies_reverification_evidence_gate(
        self,
    ) -> None:
        frontmatter = {
            "drift-source": "unregistered-sweep",
            "predicate-kind": "command",
            "drift-findings": [{"path": "a.txt", "predicate-cmd": _python_cmd(1)}],
        }
        result = recheck(self.repo, frontmatter)
        note = cbp.format_resolved_closure_note(result)
        self.assertFalse(work_queue._reverification_claim_missing_evidence(note))


if __name__ == "__main__":
    unittest.main()

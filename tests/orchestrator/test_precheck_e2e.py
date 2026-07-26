#!/usr/bin/env python3
"""End-to-end tests for live.py precheck subcommand (spec-013).

Invokes `live.py precheck` as a real subprocess against a temporary spec
directory with real files on disk.  No mocking.

Covers AC-1, AC-2, AC-3, AC-8, AC-9 (subprocess) and
AC-4, AC-5, AC-6 (sdd-workflow SKILL.md structural checks).

Run: python3 scripts/test_precheck_e2e.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _live_module_argv() -> list:
    """`python -m worktrail.orchestrator.live`, not direct file execution --
    `live.py` uses package-relative imports, so it must run as a module."""
    return [sys.executable, "-m", "worktrail.orchestrator.live"]


def _make_task_file(
    tasks_dir: Path,
    task_id: str,
    status: str = "pending",
    kind: str = "impl",
    files: list = None,
    external_deps: list = None,
) -> None:
    files_yaml = str(files or [])
    lines = [
        "---",
        f"id: {task_id}",
        f'title: "Test task {task_id}"',
        "spec: docs/specs/test-spec/spec.md",
        "lang: python",
        f"status: {status}",
        "dependencies: []",
        "timeout: 900",
        f"files: {files_yaml}",
        f"kind: {kind}",
    ]
    if external_deps is not None:
        lines.append(f"external-dependencies: {external_deps}")
    lines.append("---")
    content = "\n".join(lines) + f"\n\n# {task_id}\n"
    (tasks_dir / f"{task_id}.md").write_text(content)


def _make_files(repo_dir: Path, rel_paths: list) -> None:
    for p in rel_paths:
        target = repo_dir / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()


def _write_status_file(repo_dir: Path, spec_id: str, payload: dict) -> None:
    status_dir = repo_dir.parent / f"{repo_dir.name}-worktrees"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / f"run-{spec_id}.status.json").write_text(json.dumps(payload))


def _run_precheck(repo_dir: Path, spec_rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        _live_module_argv() + ["precheck", "--repo", str(repo_dir), spec_rel],
        capture_output=True,
        text=True,
    )


class TestPrecheckE2EWarn(unittest.TestCase):
    """AC-1: all listed files exist → WARN line + exit code 1."""

    def test_warn_all_files_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", files=["src/foo.py", "src/bar.py"])
            _make_files(repo, ["src/foo.py", "src/bar.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 1)
        self.assertIn("WARN: TASK-001", result.stdout)
        self.assertIn("all listed files already exist", result.stdout)

    def test_warn_when_prior_run_is_fanout_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", files=["src/foo.py"])
            _write_status_file(
                repo,
                "test-spec",
                {"phase": "fanout_failed", "failed_tasks": [{"id": "TASK-001"}]},
            )

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 1)
        self.assertIn("fanout_failed", result.stdout)
        self.assertIn("TASK-001", result.stdout)


class TestPrecheckE2ESilent(unittest.TestCase):
    """AC-2: no files exist → no output + exit code 0."""

    def test_silent_no_files_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", files=["src/missing.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class TestPrecheckE2EInfo(unittest.TestCase):
    """AC-3: some files exist → INFO line + exit code 0."""

    def test_info_partial_files_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", files=["src/present.py", "src/absent.py"])
            _make_files(repo, ["src/present.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 0)
        self.assertIn("INFO: TASK-001", result.stdout)
        self.assertIn("1 of 2 listed files already exist (partial)", result.stdout)


class TestPrecheckE2EKindFilter(unittest.TestCase):
    """AC-8: e2e and cleanup tasks are skipped even when their files exist."""

    def test_e2e_kind_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", kind="e2e", files=["src/exists.py"])
            _make_task_file(tasks_dir, "TASK-002", kind="cleanup", files=["src/exists.py"])
            _make_files(repo, ["src/exists.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")

    def test_cleanup_kind_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", kind="cleanup", files=["src/exists.py"])
            _make_files(repo, ["src/exists.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class TestPrecheckE2EAllCompleted(unittest.TestCase):
    """AC-9: all tasks completed → no output + exit code 0."""

    def test_all_completed_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            spec_rel = "docs/specs/test-spec"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)

            _make_task_file(tasks_dir, "TASK-001", status="completed", files=["src/done.py"])
            _make_files(repo, ["src/done.py"])

            result = _run_precheck(repo, spec_rel)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")


class TestPrecheckE2EExternalDeps(unittest.TestCase):
    """AC-009, AC-010: real two-sibling-spec-folder scenario for
    external-dependencies resolution/reporting."""

    def test_resolved_cross_spec_dependency_reports_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            other_tasks_dir = repo / "docs" / "specs" / "098-x" / "tasks"
            other_tasks_dir.mkdir(parents=True)
            _make_task_file(other_tasks_dir, "TASK-036", status="pending")

            spec_rel = "docs/specs/099-y"
            tasks_dir = repo / spec_rel / "tasks"
            tasks_dir.mkdir(parents=True)
            _make_task_file(tasks_dir, "TASK-038", external_deps=["098-x/TASK-036"])

            result = _run_precheck(repo, spec_rel)

        self.assertIn("INFO: TASK-038", result.stdout)
        self.assertIn("098-x/TASK-036", result.stdout)
        self.assertIn("status=pending", result.stdout)


# `TestSkillMdContents` (AC-4/5/6) is intentionally not ported: it asserted
# prose content of the old `specs-sdd-workflow` SKILL.md, a devkit-only Claude Code
# skill file that documents this engine rather than being part of it. That
# assertion belongs with the devkit thin-wrapper skill docs, not this package.


if __name__ == "__main__":
    unittest.main()

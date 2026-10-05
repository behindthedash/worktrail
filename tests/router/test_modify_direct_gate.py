"""Hermetic tests for the modify-pipeline direct-mode gate.

Every fixture is a synthesized OpenSpec change tree under a tmp dir; nothing
touches the repo's own openspec/ tree or the network.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from worktrail.router import modify_direct_gate as gate

_SMALL_TASK = (
    "## 1. Fix\n\n"
    "- [ ] 1.1 Do the thing.\n"
    "      files: src/widget.py, tests/test_widget.py\n"
)
_SMALL_DELTA = "## MODIFIED Requirements\n\n### Requirement: W\n\nSmall change.\n"
_SMALL_BASE = "### Requirement: W\n\nSmall change.\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_change(
    root: Path,
    tasks_md: str | None = _SMALL_TASK,
    delta_spec: str | None = _SMALL_DELTA,
    base_spec: str | None = _SMALL_BASE,
) -> Path:
    """`<root>/openspec/changes/test-change` plus a base spec at
    `<root>/openspec/specs/widget/spec.md` when `base_spec` is not None."""
    change = root / "openspec" / "changes" / "test-change"
    change.mkdir(parents=True, exist_ok=True)
    if tasks_md is not None:
        _write(change / "tasks.md", tasks_md)
    if delta_spec is not None:
        _write(change / "specs" / "widget" / "spec.md", delta_spec)
    if base_spec is not None:
        _write(root / "openspec" / "specs" / "widget" / "spec.md", base_spec)
    return change


def _verdict(root: Path, **kwargs) -> dict:
    return gate.evaluate(_make_change(root, **kwargs))


class EligibleTests(unittest.TestCase):
    def test_happy_path_shape_and_facts(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(Path(t))
        self.assertEqual(
            {"eligible", "reason", "task_count", "files"}, set(verdict)
        )
        self.assertTrue(verdict["eligible"])
        self.assertEqual(1, verdict["task_count"])
        self.assertEqual(
            ["src/widget.py", "tests/test_widget.py"], verdict["files"]
        )
        self.assertTrue(verdict["reason"].startswith("eligible"))

    def test_motivating_specimens_file_is_not_routing_surface(self):
        """The specimen this gate was built for declared run_record.py -- a
        router file, but not on the routing/classification surface -- and must
        stay eligible."""
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                tasks_md=(
                    "## 1. Fix\n\n"
                    "- [ ] 1.1 Fix it.\n"
                    "      files: src/worktrail/router/run_record.py, "
                    "tests/router/test_run_record.py\n"
                ),
            )
        self.assertTrue(verdict["eligible"])

    def test_restated_base_lines_do_not_count_as_new(self):
        """A long delta spec that restates base lines verbatim stays eligible
        when only its new-to-base lines are within the cap."""
        long_body = "\n".join(f"line {i}" for i in range(100))
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                delta_spec=(
                    "## MODIFIED Requirements\n\n### Requirement: W\n\n"
                    f"{long_body}\n\nOne new line.\n"
                ),
                base_spec=f"### Requirement: W\n\n{long_body}\n",
            )
        self.assertTrue(verdict["eligible"])


class IneligibleTests(unittest.TestCase):
    def test_missing_tasks_md_is_change_shape(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(Path(t), tasks_md=None)
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("change_shape"))
        self.assertIn("tasks.md", verdict["reason"])

    def test_missing_specs_dir_is_change_shape(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(Path(t), delta_spec=None)
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("change_shape"))
        self.assertIn("specs/**/spec.md", verdict["reason"])

    def test_two_tasks_is_task_count(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                tasks_md=(
                    "## 1. Fix\n\n"
                    "- [ ] 1.1 One.\n"
                    "      files: src/a.py\n"
                    "- [ ] 1.2 Two.\n"
                    "      files: src/b.py\n"
                ),
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("task_count"))
        self.assertEqual(2, verdict["task_count"])

    def test_no_files_line_is_files_undeclared(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t), tasks_md="## 1. Fix\n\n- [ ] 1.1 Do it.\n"
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("files_undeclared"))

    def test_four_distinct_files_is_files_over_cap(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                tasks_md=(
                    "## 1. Fix\n\n"
                    "- [ ] 1.1 Do it.\n"
                    "      files: src/a.py, src/b.py, src/c.py, src/d.py\n"
                ),
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("files_over_cap"))
        self.assertEqual(4, len(verdict["files"]))

    def test_declared_cassette_file_is_routing_surface(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                tasks_md=(
                    "## 1. Fix\n\n"
                    "- [ ] 1.1 Do it.\n"
                    "      files: src/worktrail/router/cassettes/routing_cassette.json\n"
                ),
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("routing_surface"))
        self.assertIn("routing_cassette.json", verdict["reason"])

    def test_declared_classify_py_is_routing_surface(self):
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                tasks_md=(
                    "## 1. Fix\n\n"
                    "- [ ] 1.1 Do it.\n"
                    "      files: src/worktrail/router/classify.py\n"
                ),
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("routing_surface"))
        self.assertIn("classify.py", verdict["reason"])

    def test_oversized_delta_is_delta_over_cap(self):
        big_body = "\n".join(f"new line {i}" for i in range(60))
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                delta_spec=f"## MODIFIED Requirements\n\n{big_body}\n",
                base_spec="### Requirement: W\n\nUnrelated.\n",
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("delta_over_cap"))

    def test_missing_base_spec_counts_every_line(self):
        big_body = "\n".join(f"line {i}" for i in range(45))
        with TemporaryDirectory() as t:
            verdict = _verdict(
                Path(t),
                delta_spec=f"## MODIFIED Requirements\n\n{big_body}\n",
                base_spec=None,
            )
        self.assertFalse(verdict["eligible"])
        self.assertTrue(verdict["reason"].startswith("delta_over_cap"))


class MainEntryTests(unittest.TestCase):
    def test_main_prints_verdict_json_and_exits_zero(self):
        with TemporaryDirectory() as t:
            change = _make_change(Path(t))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = gate.main([str(change), "--json"])
        self.assertEqual(0, rc)
        self.assertTrue(json.loads(out.getvalue())["eligible"])

    def test_nonexistent_dir_is_usage_error_without_verdict(self):
        src = Path(gate.__file__).resolve().parents[2]
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "worktrail.router.modify_direct_gate",
                "/definitely/not/a/dir",
                "--json",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(src)},
            timeout=60,
        )
        self.assertEqual(2, proc.returncode)
        self.assertEqual("", proc.stdout.strip())
        self.assertIn("not a directory", proc.stderr)


if __name__ == "__main__":
    unittest.main()

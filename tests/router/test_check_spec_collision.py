#!/usr/bin/env python3
"""Unit tests for the pre-dispatch spec-collision guard (stdlib unittest).

Exercises real throwaway fixture directories/git repos rather than mocking --
`verify()`'s logic *is* the git-tracking plumbing (via dashboard.py's
`_git_tracked`/`_task_files_are_shipped`), so a fake would just re-assert the
mock. Mirrors test_check_repo_freshness.py's shape and philosophy.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_spec_collision as csc


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_repo(branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="spec-collision-")
    _git(d, "init", "-q", "-b", branch)
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("base\n", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_spec(
    repo: str,
    spec_id: str,
    status: str = "Implemented",
    feature_summary: str = "Example feature for tests.",
    spec_name: str = "example-feature",
) -> Path:
    """Writes docs/specs/<spec_id>/<date>--<spec_name>.md with a **Status**:
    header and a **Feature Summary**: line. Returns the spec dir."""
    spec_dir = Path(repo) / "docs" / "specs" / spec_id
    _write(
        spec_dir / f"2026-01-01--{spec_name}.md",
        f"# {spec_id}\n\n**Status**: {status}\n\n"
        f"**Feature Summary**: {feature_summary}\n",
    )
    return spec_dir


def _make_task(spec_dir: Path, task_id: str, files: list, status: str = "completed") -> None:
    files_literal = ", ".join(files)
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"---\nid: {task_id}\nstatus: {status}\nfiles: [{files_literal}]\n---\n"
        f"# {task_id}\n",
    )


class TestCheckNoSpecsDir(unittest.TestCase):
    def test_missing_docs_specs_dir_is_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = csc.check(Path(tmp))
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNone(res["warning"])


class TestCheckEmptySpecsDir(unittest.TestCase):
    def test_existing_empty_specs_dir_is_checked_with_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs" / "specs").mkdir(parents=True)
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            self.assertEqual(res["candidates"], [])


class TestCheckWithCandidates(unittest.TestCase):
    def test_populates_candidates_with_spec_id_and_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "007-example-feature", feature_summary="Donors can search nonprofits.")
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            self.assertEqual(len(res["candidates"]), 1)
            candidate = res["candidates"][0]
            self.assertEqual(candidate["spec_id"], "007-example-feature")
            self.assertEqual(candidate["title"], "Example Feature")
            self.assertEqual(candidate["feature_summary"], "Donors can search nonprofits.")
            self.assertIn("stage", candidate)

    def test_multiple_specs_all_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-first", spec_name="first")
            _make_spec(tmp, "002-second", spec_name="second")
            res = csc.check(Path(tmp))
            self.assertTrue(res["checked"])
            ids = {c["spec_id"] for c in res["candidates"]}
            self.assertEqual(ids, {"001-first", "002-second"})


class TestCheckDegrade(unittest.TestCase):
    def test_scan_import_failure_degrades(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            original = csc._scan
            csc._scan = None
            try:
                res = csc.check(Path(tmp))
            finally:
                csc._scan = original
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNotNone(res["warning"])
            self.assertIn("overlap_check import failed", res["warning"])

    def test_scan_exception_degrades_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            original = csc._scan

            def _raiser(_specs_root):
                raise RuntimeError("simulated malformed frontmatter")

            csc._scan = _raiser
            try:
                res = csc.check(Path(tmp))
            finally:
                csc._scan = original
            self.assertFalse(res["checked"])
            self.assertEqual(res["candidates"], [])
            self.assertIsNotNone(res["warning"])


class TestVerifyStatusNotImplemented(unittest.TestCase):
    def test_non_implemented_status_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example", status="Draft")
            res = csc.verify(Path(tmp), "001-example")
            self.assertFalse(res["confirmed"])
            self.assertEqual(res["status"], "Draft")
            self.assertIsNotNone(res["warning"])


class TestVerifyFilesNotTracked(unittest.TestCase):
    def test_untracked_task_files_are_not_confirmed(self):
        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('untracked')\n")  # written but never git add/commit
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertEqual(res["status"], "Implemented")
        self.assertEqual(res["files"], ["src/feature.py"])
        self.assertIsNotNone(res["warning"])


class TestVerifyFilesTracked(unittest.TestCase):
    def test_all_committed_task_files_are_confirmed(self):
        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        res = csc.verify(Path(repo), "001-example")
        self.assertTrue(res["confirmed"])
        self.assertEqual(res["status"], "Implemented")
        self.assertEqual(res["files"], ["src/feature.py"])
        self.assertIsNone(res["warning"])


class TestVerifyEdgeCases(unittest.TestCase):
    def test_no_tasks_dir_degrades_without_raising(self):
        repo = _init_repo()
        _make_spec(repo, "001-example", status="Implemented")
        # No tasks/ dir and no traceability-matrix.md -- pre-task-split spec.
        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertEqual(res["files"], [])
        self.assertIsNotNone(res["warning"])
        self.assertIn("no task files found", res["warning"])

    def test_missing_spec_document_degrades_without_raising(self):
        repo = _init_repo()
        spec_dir = Path(repo) / "docs" / "specs" / "001-example"
        spec_dir.mkdir(parents=True)
        # No spec .md file at all -- find_spec_file() finds nothing to read
        # a Status:/frontmatter header from.
        res = csc.verify(Path(repo), "001-example")
        self.assertFalse(res["confirmed"])
        self.assertIsNotNone(res["warning"])

    def test_unknown_spec_id_degrades_without_raising(self):
        repo = _init_repo()
        res = csc.verify(Path(repo), "999-does-not-exist")
        self.assertFalse(res["confirmed"])
        self.assertIsNotNone(res["warning"])


class TestCheckThenVerifyIntegration(unittest.TestCase):
    def test_full_chain_matches_confirmed_collision_shape(self):
        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented",
                              feature_summary="Donors can search nonprofits.")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        checked = csc.check(Path(repo))
        self.assertTrue(checked["checked"])
        candidate = next(c for c in checked["candidates"] if c["spec_id"] == "001-example")
        self.assertEqual(candidate["title"], "Example Feature")

        verified = csc.verify(Path(repo), candidate["spec_id"])
        self.assertTrue(verified["confirmed"])
        self.assertEqual(verified["status"], "Implemented")
        self.assertEqual(verified["files"], ["src/feature.py"])


class TestCli(unittest.TestCase):
    def test_json_check_output_matches_check_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            _make_spec(tmp, "001-example")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = csc.main(["--repo", tmp, "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()), csc.check(Path(tmp)))

    def test_json_verify_output_matches_verify_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        repo = _init_repo()
        spec_dir = _make_spec(repo, "001-example", status="Implemented")
        target = Path(repo) / "src" / "feature.py"
        _write(target, "print('shipped')\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "ship feature")
        _make_task(spec_dir, "TASK-001", files=["src/feature.py"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = csc.main(["--repo", repo, "--verify", "001-example", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), csc.verify(Path(repo), "001-example"))


if __name__ == "__main__":
    unittest.main()

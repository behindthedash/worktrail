#!/usr/bin/env python3
"""Extra coverage for integrate.py: _write_group_journal, _mark_integrate_complete_if_terminal,
synthetic_fanout, integrate_one dep-on-quarantined path."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import integrate, progress


class WriteGroupJournalTests(unittest.TestCase):
    def test_writes_new_journal(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "journal.json")
            integrate._write_group_journal(
                path, "base", "https://github.com/o/r/pull/1", "run-001/base", "OPEN"
            )
            data = json.loads(Path(path).read_text())
            self.assertEqual(
                data["groups"]["base"]["pr_url"], "https://github.com/o/r/pull/1"
            )
            self.assertEqual(data["groups"]["base"]["state"], "OPEN")

    def test_updates_existing_journal(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "journal.json")
            Path(path).write_text(
                json.dumps(
                    {
                        "groups": {
                            "base": {"state": "OPEN", "pr_url": "", "head_branch": ""}
                        }
                    }
                )
            )
            integrate._write_group_journal(
                path, "base", "https://github.com/o/r/pull/1", "run-001/base", "MERGED"
            )
            data = json.loads(Path(path).read_text())
            self.assertEqual(data["groups"]["base"]["state"], "MERGED")

    def test_none_journal_path_is_noop(self):
        # Should not raise
        integrate._write_group_journal(None, "base", "", "", "OPEN")

    def test_exception_is_printed_not_raised(self):
        with patch.object(
            progress, "atomic_write_text", side_effect=OSError("disk full")
        ):
            with tempfile.TemporaryDirectory() as t:
                path = os.path.join(t, "journal.json")
                # Should not raise — exception is swallowed and printed
                integrate._write_group_journal(path, "base", "", "", "OPEN")


class MarkIntegrateCompleteTests(unittest.TestCase):
    def _groups(self, names):
        return [{"name": n, "tasks": [], "reqs": [], "depends_on": []} for n in names]

    def test_marks_complete_when_all_terminal(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "journal.json")
            Path(path).write_text(
                json.dumps(
                    {
                        "groups": {
                            "base": {
                                "state": "MERGED",
                                "pr_url": "",
                                "head_branch": "",
                            },
                            "feature-1": {
                                "state": "OPEN",
                                "pr_url": "",
                                "head_branch": "",
                            },
                        }
                    }
                )
            )
            complete = integrate._mark_integrate_complete_if_terminal(
                path, self._groups(["base", "feature-1"])
            )
            # "OPEN" is in TERMINAL_GROUP_STATES, so both are terminal
            self.assertTrue(complete)
            data = json.loads(Path(path).read_text())
            self.assertTrue(data.get("integrate_complete"))

    def test_no_mark_when_group_missing(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "journal.json")
            Path(path).write_text(
                json.dumps(
                    {
                        "groups": {
                            "base": {"state": "MERGED", "pr_url": "", "head_branch": ""}
                        }
                    }
                )
            )
            complete = integrate._mark_integrate_complete_if_terminal(
                path,
                self._groups(["base", "feature-1"]),  # feature-1 missing
            )
            self.assertFalse(complete)

    def test_none_journal_path_returns_false(self):
        result = integrate._mark_integrate_complete_if_terminal(
            None, self._groups(["base"])
        )
        self.assertFalse(result)

    def test_exception_returns_false(self):
        with patch.object(
            progress, "atomic_write_text", side_effect=OSError("disk full")
        ):
            with tempfile.TemporaryDirectory() as t:
                path = os.path.join(t, "journal.json")
                Path(path).write_text(
                    json.dumps(
                        {
                            "groups": {
                                "base": {
                                    "state": "MERGED",
                                    "pr_url": "",
                                    "head_branch": "",
                                }
                            }
                        }
                    )
                )
                result = integrate._mark_integrate_complete_if_terminal(
                    path, self._groups(["base"])
                )
                self.assertFalse(result)


class SyntheticFanoutTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        (repo / "README.md").write_text("init\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
            check=True,
            capture_output=True,
        )
        return repo

    def test_creates_branch_per_task(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            tasks = [
                {"id": "TASK-001", "kind": "impl", "files": ["src/a.txt"]},
                {"id": "TASK-002", "kind": "impl", "files": ["src/b.txt"]},
            ]
            integrate.synthetic_fanout(repo, "001-spec", tasks, "HEAD")
            result = subprocess.run(
                ["git", "-C", str(repo), "branch"],
                capture_output=True,
                text=True,
                check=True,
            )
            branches = result.stdout
            self.assertIn("001-spec/task-001", branches)
            self.assertIn("001-spec/task-002", branches)

    def test_tail_tasks_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            tasks = [
                {"id": "TASK-001", "kind": "impl", "files": ["src/a.txt"]},
                {"id": "TASK-002", "kind": "e2e", "files": ["test/e2e.spec.ts"]},
            ]
            integrate.synthetic_fanout(repo, "001-spec", tasks, "HEAD")
            result = subprocess.run(
                ["git", "-C", str(repo), "branch"],
                capture_output=True,
                text=True,
                check=True,
            )
            branches = result.stdout
            self.assertIn("001-spec/task-001", branches)
            self.assertNotIn("001-spec/task-002", branches)


class IntegrateOneDepOnQuarantinedTests(unittest.TestCase):
    def test_dep_on_quarantined_cascades(self):
        g = {
            "name": "feature-1",
            "tasks": ["TASK-002"],
            "reqs": [],
            "depends_on": ["base"],
        }
        quarantined = {"base": "some reason"}
        group_branch = {}
        status = {"TASK-002": "done"}
        tasks = [
            {
                "id": "TASK-002",
                "deps": ["TASK-001"],
                "files": ["src/b.ts"],
                "kind": "impl",
                "status": "done",
            }
        ]

        result = integrate.integrate_one(
            g=g,
            repo=Path("/fake/repo"),
            spec_id="001-spec",
            tasks=tasks,
            remote="origin",
            run_id="run-001",
            base="main",
            journal_path=None,
            status=status,
            group_branch=group_branch,
            quarantined=quarantined,
        )
        self.assertIsNone(result)
        self.assertIn("feature-1", quarantined)
        self.assertIn("base", quarantined["feature-1"])


class AutoResolveAddAddInitTests(unittest.TestCase):
    """Real-git tests for deterministic add/add package-init conflict resolution."""

    def _init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        (repo / "README.md").write_text("init\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
            check=True,
            capture_output=True,
        )
        return repo

    def _add_init_branch(self, repo: Path, branch: str, content: str) -> None:
        """Branch off main and ADD tools/__init__.py with `content`."""
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-B", branch, "main"],
            check=True,
            capture_output=True,
        )
        d = repo / "tools"
        d.mkdir(exist_ok=True)
        (d / "__init__.py").write_text(content)
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", f"add init via {branch}"],
            check=True,
            capture_output=True,
        )

    def _conflict_merge(self, repo: Path, into: str, other: str) -> None:
        """Check out `into`, merge `other`, asserting the merge actually conflicts."""
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", into],
            check=True,
            capture_output=True,
        )
        m = subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-edit", other],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(m.returncode, 0, "expected an add/add merge conflict")

    def test_addadd_init_union_merged(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            self._add_init_branch(repo, "a", '"""Tools package (from A)."""\n')
            self._add_init_branch(repo, "b", '"""Tools package (from B)."""\n')
            self._conflict_merge(repo, "a", "b")

            self.assertTrue(integrate._auto_resolve_addadd_inits(repo))
            # Tree is clean (merge committed, no unmerged paths).
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(status.strip(), "")
            # Both sides' content survives the union.
            merged = (repo / "tools" / "__init__.py").read_text()
            self.assertIn("from A", merged)
            self.assertIn("from B", merged)

    def test_union_init_helper(self):
        # Identical sides collapse to one copy (git auto-merges these, so this
        # branch is purely defensive); one-sided-empty keeps the non-empty side;
        # differing sides keep both, newline-separated.
        body = '"""Tools package."""\n'
        self.assertEqual(integrate._union_init(body, body), body)
        self.assertEqual(integrate._union_init("", body), body)
        self.assertEqual(integrate._union_init(body, "  \n"), body)
        merged = integrate._union_init("import a\n", "import b\n")
        self.assertIn("import a", merged)
        self.assertIn("import b", merged)

    def test_addadd_non_init_not_resolved(self):
        """add/add on a non-init file is left for the worker/quarantine path."""
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))

            def add_branch(branch, content):
                subprocess.run(
                    ["git", "-C", str(repo), "checkout", "-q", "-B", branch, "main"],
                    check=True,
                    capture_output=True,
                )
                (repo / "config.py").write_text(content)
                subprocess.run(
                    ["git", "-C", str(repo), "add", "-A"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-q", "-m", branch],
                    check=True,
                    capture_output=True,
                )

            add_branch("a", "X = 1\n")
            add_branch("b", "X = 2\n")
            self._conflict_merge(repo, "a", "b")

            self.assertFalse(integrate._auto_resolve_addadd_inits(repo))
            # Conflict state is preserved for the fallback path.
            status = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("config.py", status)

    def test_modify_modify_init_not_resolved(self):
        """A real modify/modify conflict on __init__.py is NOT auto-unioned."""
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            # Establish a shared base for tools/__init__.py first.
            d = repo / "tools"
            d.mkdir()
            (d / "__init__.py").write_text('"""base."""\nVERSION = "0"\n')
            subprocess.run(
                ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-q", "-m", "base init"],
                check=True,
                capture_output=True,
            )

            def edit_branch(branch, content):
                subprocess.run(
                    ["git", "-C", str(repo), "checkout", "-q", "-B", branch, "main"],
                    check=True,
                    capture_output=True,
                )
                (d / "__init__.py").write_text(content)
                subprocess.run(
                    ["git", "-C", str(repo), "add", "-A"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-q", "-m", branch],
                    check=True,
                    capture_output=True,
                )

            edit_branch("a", '"""base."""\nVERSION = "A"\n')
            edit_branch("b", '"""base."""\nVERSION = "B"\n')
            self._conflict_merge(repo, "a", "b")

            self.assertFalse(
                integrate._auto_resolve_addadd_inits(repo),
                "modify/modify (UU) must fall through, not union",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

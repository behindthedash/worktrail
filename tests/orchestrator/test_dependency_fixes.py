#!/usr/bin/env python3
"""Regression tests for the dependency-chain orchestrator fixes.

Reproduces the failure reported from a real `go` run on a 4-task spec
(helper -> consumer -> test-of-consumer -> cleanup):

  Defect A -- dependent tasks were fanned out off bare base, so an
              integration/e2e task ran against a tree WITHOUT the code it tests
              and was structurally guaranteed to fail in isolation.
  Defect B -- one failed task (often a test-only task) quarantined its WHOLE
              group, dropping an already-passed, self-contained sibling (the
              real fix), so the merged PR carried only dead helper code.
  Defect C -- cleanup ran a bare global formatter (`npx prettier --write`) on a
              repo with no formatter config, reflowing whole files.

The user's acceptance check: the consumer + its test should land in a passing
PR (not a quarantine), and a merged PR must never contain a symbol with no
caller (the helper must arrive together with its consumer).

Run: python3 scripts/test_dependency_fixes.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import dispatch  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")


def _branch_with_file(repo, branch, fname, content, start="HEAD"):
    """Create `branch` off `start` carrying a single new file, then return to base."""
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-B", branch, start)
    (Path(repo) / fname).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"add {fname}")
    _git(repo, "checkout", "-q", base)


def _write_sibling_task_file(repo, sibling_spec_id, sibling_task_id, status="completed"):
    """Materialize a sibling spec's task file so `resolve_external_dependency`
    reports the reference as satisfied (read-only filesystem check, no git)."""
    td = Path(repo) / "docs" / "specs" / sibling_spec_id / "tasks"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{sibling_task_id}.md").write_text(
        f"---\nid: {sibling_task_id}\nstatus: {status}\n---\nbody\n"
    )


# --------------------------------------------------------------------------- #
# Defect B -- deliverable_subset: ship healthy siblings, split out the failure
# --------------------------------------------------------------------------- #
class DeliverableSubset(unittest.TestCase):
    CHAIN = [
        {"id": "TASK-001", "deps": []},  # helper
        {"id": "TASK-002", "deps": ["TASK-001"]},  # consumer
        {"id": "TASK-003", "deps": ["TASK-002"]},  # test-of-consumer
    ]
    GROUP = ["TASK-001", "TASK-002", "TASK-003"]

    def test_all_done_ships_whole_chain_not_quarantine(self):
        # The headline regression: a clean dependency chain delivers as ONE PR
        # (no quarantine), so the consumer + its test land together.
        status = {t["id"]: "done" for t in self.CHAIN}
        deliverable, dropped = coordinator.deliverable_subset(self.GROUP, self.CHAIN, status)
        self.assertEqual(deliverable, self.GROUP)
        self.assertEqual(dropped, [])

    def test_failed_test_task_split_out_consumer_still_ships(self):
        status = {"TASK-001": "done", "TASK-002": "done", "TASK-003": "failed"}
        deliverable, dropped = coordinator.deliverable_subset(self.GROUP, self.CHAIN, status)
        self.assertEqual(deliverable, ["TASK-001", "TASK-002"])
        self.assertEqual(dropped, ["TASK-003"])

    def test_failed_root_drops_everything_downstream(self):
        # If the helper itself failed, nothing in the chain is shippable.
        status = {"TASK-001": "failed", "TASK-002": "done", "TASK-003": "done"}
        deliverable, dropped = coordinator.deliverable_subset(self.GROUP, self.CHAIN, status)
        self.assertEqual(deliverable, [])
        self.assertEqual(dropped, self.GROUP)

    def test_independent_sibling_survives_a_failed_task(self):
        tasks = self.CHAIN + [{"id": "TASK-009", "deps": []}]  # independent
        group = self.GROUP + ["TASK-009"]
        status = {"TASK-001": "done", "TASK-002": "done", "TASK-003": "failed", "TASK-009": "done"}
        deliverable, dropped = coordinator.deliverable_subset(group, tasks, status)
        self.assertEqual(deliverable, ["TASK-001", "TASK-002", "TASK-009"])
        self.assertEqual(dropped, ["TASK-003"])

    def test_escalated_treated_like_failed(self):
        status = {"TASK-001": "done", "TASK-002": "escalated", "TASK-003": "done"}
        deliverable, dropped = coordinator.deliverable_subset(self.GROUP, self.CHAIN, status)
        # consumer escalated -> consumer AND its dependent test both drop
        self.assertEqual(deliverable, ["TASK-001"])
        self.assertEqual(dropped, ["TASK-002", "TASK-003"])


# --------------------------------------------------------------------------- #
# Defect A -- worktrees stack on their dependencies' commits, not bare HEAD
# --------------------------------------------------------------------------- #
class DependencyStacking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _init_repo(self.repo)
        self.spec = "010-feature"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_root_task_starts_from_head(self):
        by_id = {"TASK-001": {"id": "TASK-001", "deps": []}}
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-001"], by_id)
        self.assertEqual((ref, extra), ("HEAD", []))

    def test_dependent_starts_from_dependency_branch(self):
        _branch_with_file(self.repo, f"{self.spec}/task-001", "helper.py", "x=1\n")
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-002"], by_id)
        self.assertEqual(ref, f"{self.spec}/task-001")
        self.assertEqual(extra, [])

    def test_missing_dep_branch_falls_back_to_head(self):
        # e.g. dep pre-marked done outside this run (--only): no branch exists.
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-002"], by_id)
        self.assertEqual((ref, extra), ("HEAD", []))

    def test_chain_worktree_carries_helper_and_consumer(self):
        """End-to-end: the test-of-consumer worktree must SEE helper + consumer
        (defect A) -- and a delivered branch never carries a symbol with no
        caller, because the consumer arrives together with its helper."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
            "TASK-003": {"id": "TASK-003", "deps": ["TASK-002"]},
        }
        wt_base = Path(self.tmp) / "wt"
        wt_base.mkdir()

        # TASK-001 (helper) fans out off HEAD.
        wt1 = wt_base / "task-001"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        # TASK-002 (consumer) must stack on TASK-001 -> helper.py is present.
        wt2 = wt_base / "task-002"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        self.assertTrue(
            (wt2 / "helper.py").exists(), "consumer worktree did not stack on its helper dependency"
        )
        (wt2 / "consumer.py").write_text("from helper import helper\nhelper()\n")
        _git(wt2, "add", "-A")
        _git(wt2, "commit", "-q", "-m", "TASK-002 consumer")

        # TASK-003 (test-of-consumer) must stack on TASK-002 -> both present.
        wt3 = wt_base / "task-003"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-003"], by_id, wt3)
        self.assertTrue((wt3 / "helper.py").exists())
        self.assertTrue(
            (wt3 / "consumer.py").exists(),
            "test worktree did not see the consumer it tests (defect A)",
        )

    def test_multiple_deps_are_all_merged_in(self):
        _branch_with_file(self.repo, f"{self.spec}/task-001", "a.py", "a=1\n")
        _branch_with_file(self.repo, f"{self.spec}/task-005", "b.py", "b=1\n")
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-005": {"id": "TASK-005", "deps": []},
            "TASK-006": {"id": "TASK-006", "deps": ["TASK-001", "TASK-005"]},
        }
        wt6 = Path(self.tmp) / "wt6"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-006"], by_id, wt6)
        self.assertTrue((wt6 / "a.py").exists())
        self.assertTrue(
            (wt6 / "b.py").exists(), "sibling dependency was not merged into the worktree"
        )

    # ----------------------------------------------------------------- #
    # TASK-006 -- cross-spec stacking on satisfied external-dependencies
    # ----------------------------------------------------------------- #
    def test_cross_spec_dep_stacks_on_sibling_branch(self):
        """AC-016: a satisfied external dep whose sibling branch (named with the
        SIBLING's own spec id) exists becomes the start ref."""
        sibling_spec = "098-x"
        # Materialize the branch FIRST: `_branch_with_file`'s `add -A` commit
        # would otherwise sweep up an untracked sibling task file created
        # beforehand and remove it again on checkout back to base.
        _branch_with_file(self.repo, f"{sibling_spec}/task-036", "sibling.py", "s=1\n")
        _write_sibling_task_file(self.repo, sibling_spec, "TASK-036")
        by_id = {
            "TASK-002": {
                "id": "TASK-002",
                "deps": [],
                "external_deps": [f"{sibling_spec}/TASK-036"],
            }
        }
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-002"], by_id)
        self.assertEqual(ref, f"{sibling_spec}/task-036")
        self.assertEqual(extra, [])

    def test_cross_spec_dep_never_uses_current_task_spec_id(self):
        """Regression against the documented gap: a branch named with the
        CURRENT task's own spec_id (instead of the sibling's) must NOT match."""
        sibling_spec = "098-x"
        # Materialize the branch under the WRONG (current task's) spec id only.
        _branch_with_file(self.repo, f"{self.spec}/task-036", "wrong.py", "w=1\n")
        _write_sibling_task_file(self.repo, sibling_spec, "TASK-036")
        by_id = {
            "TASK-002": {
                "id": "TASK-002",
                "deps": [],
                "external_deps": [f"{sibling_spec}/TASK-036"],
            }
        }
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-002"], by_id)
        self.assertEqual(
            (ref, extra),
            ("HEAD", []),
            "must not stack on a branch named with the current task's own spec_id",
        )

    def test_cross_spec_dep_falls_back_to_base_ref_when_branch_absent(self):
        """AC-017: satisfied external dep, but no materialized sibling branch
        (already merged to base) -- falls back exactly like an already-
        integrated same-spec dependency does today."""
        sibling_spec = "098-x"
        _write_sibling_task_file(self.repo, sibling_spec, "TASK-036")
        by_id = {
            "TASK-002": {
                "id": "TASK-002",
                "deps": [],
                "external_deps": [f"{sibling_spec}/TASK-036"],
            }
        }
        ref, extra = live.dependency_start_ref(self.repo, self.spec, by_id["TASK-002"], by_id)
        self.assertEqual((ref, extra), ("HEAD", []))

    def test_mixed_same_spec_and_cross_spec_deps_both_merged(self):
        """A same-spec dependency branch and a satisfied cross-spec dependency
        branch, both materialized, are both stacked (one as start ref, the
        other as an extra merge)."""
        sibling_spec = "098-x"
        _branch_with_file(self.repo, f"{self.spec}/task-001", "helper.py", "x=1\n")
        _branch_with_file(self.repo, f"{sibling_spec}/task-036", "sibling.py", "s=1\n")
        _write_sibling_task_file(self.repo, sibling_spec, "TASK-036")
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {
                "id": "TASK-002",
                "deps": ["TASK-001"],
                "external_deps": [f"{sibling_spec}/TASK-036"],
            },
        }
        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        self.assertTrue((wt2 / "helper.py").exists())
        self.assertTrue(
            (wt2 / "sibling.py").exists(),
            "cross-spec dependency branch was not merged into the worktree",
        )

    def test_require_dependency_files_raises_when_declared_file_missing(self):
        """Structural guard (PR #295's follow-up): a dependency's declared
        `files:` deliberately absent post-merge must quarantine the task
        instead of silently spawning a worker that has to notice the gap."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["helper.py"]},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        # Declare a file TASK-001 never actually committed -- simulates the
        # lineage gap (dependency's real commit didn't carry the declared file).
        by_id["TASK-001"]["files"] = ["helper.py", "missing_file.py"]

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        with self.assertRaises(live.WorktreeMissingDependencyFileError):
            live._require_dependency_files(wt2, by_id["TASK-002"], by_id)

    def test_require_dependency_files_passes_when_declared_file_present(self):
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["helper.py"]},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        live._require_dependency_files(wt2, by_id["TASK-002"], by_id)  # must not raise

    def test_require_dependency_files_warns_instead_of_raising_on_renamed_file(self):
        """A dependency that legitimately renames its declared output (e.g. an
        alembic migration dodging a revision-slot collision) must not crash the
        dependent dispatch: its commit IS in the stacked worktree's ancestry,
        so the declared-vs-actual mismatch downgrades to a WARN, not a raise."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["helper_v1.py"]},  # declared name
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        # Agent legitimately committed under a different name than declared.
        (wt1 / "helper_v2.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper (renamed)")
        by_id["TASK-001"]["head_sha"] = _git(wt1, "rev-parse", "HEAD").stdout.strip()

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        self.assertFalse(
            (wt2 / "helper_v1.py").exists(), "test setup should reproduce the declared path missing"
        )
        # must not raise, and must return the structured event so callers can
        # journal it for cross-run aggregation (safety_net_report.py)
        events = live._require_dependency_files(wt2, by_id["TASK-002"], by_id)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event"], "dependency_file_drift")
        self.assertEqual(event["task"], "TASK-002")
        self.assertEqual(event["dep_id"], "TASK-001")
        self.assertEqual(event["declared_path"], "helper_v1.py")
        self.assertEqual(event["dep_head_sha"], by_id["TASK-001"]["head_sha"])

    def test_require_dependency_files_still_raises_when_dep_commit_not_merged(self):
        """A head_sha that is NOT an ancestor of the stacked worktree (the
        genuine lineage-gap case) must still raise -- the WARN downgrade only
        applies when the dependency's actual commit did land."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["helper.py"]},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        # Declare a file TASK-001 never committed, AND point head_sha at a
        # nonexistent commit never merged into wt2 (simulates a stale/incorrect
        # journal head rather than genuine renamed-file drift).
        by_id["TASK-001"]["files"] = ["helper.py", "missing_file.py"]
        by_id["TASK-001"]["head_sha"] = "0" * 40

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        with self.assertRaises(live.WorktreeMissingDependencyFileError):
            live._require_dependency_files(wt2, by_id["TASK-002"], by_id)

    def test_require_dependency_files_glob_entry_passes_when_files_present(self):
        """Brief 20260808-132549: a dependency's `files:` legitimately uses a
        glob as shorthand for many files (e.g. `api/tests/**` for ~56 test
        files). A literal `Path.exists()` check can never match `**` -- it
        must expand the pattern against the worktree instead."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["api/tests/**"]},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "api" / "tests").mkdir(parents=True)
        (wt1 / "api" / "tests" / "test_foo.py").write_text("def test_foo(): pass\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 tests")

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        live._require_dependency_files(wt2, by_id["TASK-002"], by_id)  # must not raise

    def test_require_dependency_files_glob_entry_raises_when_nothing_matches(self):
        """A glob entry is a real check, not a bypass: if nothing under the
        pattern exists, it must still raise."""
        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": [], "files": ["api/tests/**"]},
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        with self.assertRaises(live.WorktreeMissingDependencyFileError):
            live._require_dependency_files(wt2, by_id["TASK-002"], by_id)

    def test_require_dependency_files_fresh_run_no_head_sha_warns_for_completed_dep(self):
        """Brief 20260808-132549: on a `--fresh` run (no journal), a
        pre-completed dependency never gets a `head_sha` populated (frontmatter
        carries no commit SHAs), so the ancestor-check WARN fallback could never
        engage. A dependency already in a DONE-like status was, by definition,
        merged before this run started (`dependency_start_ref` falls back to
        bare HEAD for it -- no branch to stack), so a still-missing declared
        file downgrades to WARN instead of hard-failing the dispatch."""
        by_id = {
            "TASK-001": {
                "id": "TASK-001",
                "deps": [],
                "files": ["helper.py", "missing_file.py"],
                "status": "completed",
            },
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        events = live._require_dependency_files(wt2, by_id["TASK-002"], by_id)  # must not raise
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event"], "dependency_file_drift")
        self.assertEqual(event["dep_id"], "TASK-001")
        self.assertEqual(event["declared_path"], "missing_file.py")
        self.assertIsNone(event["dep_head_sha"])

    def test_require_dependency_files_fresh_run_no_head_sha_still_raises_for_in_run_dep(self):
        """The WARN downgrade for a missing `head_sha` must stay scoped to
        already-completed dependencies. A dependency this run is actively
        driving (no DONE-like status yet) has no trusted "merged before this
        run" guarantee, so a missing file with no `head_sha` must still raise."""
        by_id = {
            "TASK-001": {
                "id": "TASK-001",
                "deps": [],
                "files": ["helper.py", "missing_file.py"],
                "status": "fixing",
            },
            "TASK-002": {"id": "TASK-002", "deps": ["TASK-001"]},
        }
        wt1 = Path(self.tmp) / "wt1"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-001"], by_id, wt1)
        (wt1 / "helper.py").write_text("def helper():\n    return 42\n")
        _git(wt1, "add", "-A")
        _git(wt1, "commit", "-q", "-m", "TASK-001 helper")

        wt2 = Path(self.tmp) / "wt2"
        live.add_stacked_worktree(self.repo, self.spec, by_id["TASK-002"], by_id, wt2)
        with self.assertRaises(live.WorktreeMissingDependencyFileError):
            live._require_dependency_files(wt2, by_id["TASK-002"], by_id)


# --------------------------------------------------------------------------- #
# Defect C -- cleanup prompt is config-gated, no bare global formatter
# --------------------------------------------------------------------------- #
class CleanupFormatGating(unittest.TestCase):
    def _cleanup_prompt(self):
        task = {"id": "TASK-002", "deps": [], "files": ["src/route.ts"], "status": "cleaning"}
        ctx = {
            "spec_id": "010-feature",
            "spec_folder": "docs/specs/010-feature/",
            "worktree_path": "/wt/010-feature-task-002",
            "branch": "010-feature/task-002",
        }
        return dispatch.build_worker_prompt(dispatch.ROLE_CLEANUP, task, ctx)

    def test_prompt_gates_formatting_on_repo_config(self):
        p = self._cleanup_prompt()
        self.assertIn("ONLY if the repo actually CONFIGURES a formatter", p)

    def test_prompt_forbids_bare_global_formatter(self):
        p = self._cleanup_prompt()
        self.assertIn("NEVER run a bare global formatter", p)
        self.assertIn("npx prettier --write", p)  # named as the thing to avoid

    def test_prompt_scopes_to_changed_files(self):
        p = self._cleanup_prompt()
        self.assertIn("ONLY the files this task changed", p)


# --------------------------------------------------------------------------- #
# tail-dispatch-noop-and-pr-discovery-guard 1.2 -- zero-file tail-task scope
# --------------------------------------------------------------------------- #
class TailTaskNoopScope(unittest.TestCase):
    def _ctx(self):
        return {
            "spec_id": "010-feature",
            "spec_folder": "docs/specs/010-feature/",
            "worktree_path": "/wt/010-feature-task-002",
            "branch": "010-feature/task-002",
        }

    def test_tail_kind_empty_files_gets_explicit_noop_scope(self):
        for kind in ("e2e", "cleanup"):
            task = {"id": "TASK-002", "deps": [], "files": [], "kind": kind, "status": "cleaning"}
            p = dispatch.build_worker_prompt(dispatch.ROLE_CLEANUP, task, self._ctx())
            self.assertNotIn("(see task file)", p)
            self.assertIn("verification-only", p)
            self.assertIn("zero", p)

    def test_tail_kind_nonempty_files_renders_files_unchanged(self):
        for kind in ("e2e", "cleanup"):
            task = {
                "id": "TASK-002",
                "deps": [],
                "files": ["src/route.ts", "src/other.ts"],
                "kind": kind,
                "status": "cleaning",
            }
            p = dispatch.build_worker_prompt(dispatch.ROLE_CLEANUP, task, self._ctx())
            self.assertIn("src/route.ts, src/other.ts", p)
            self.assertNotIn("verification-only", p)

    def test_non_tail_kind_empty_files_keeps_existing_fallback(self):
        task = {"id": "TASK-002", "deps": [], "files": [], "kind": "implement", "status": "cleaning"}
        p = dispatch.build_worker_prompt(dispatch.ROLE_CLEANUP, task, self._ctx())
        self.assertIn("(see task file)", p)
        self.assertNotIn("verification-only", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)

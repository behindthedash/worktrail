#!/usr/bin/env python3
"""End-to-end tests for the recurring spec-sync drift sweep (TASK-006).

Exercises the full composition of the five sweep modules --
`spec_sync_sweep_discovery`, `spec_sync_sweep_check`, `spec_sync_sweep_dedup`,
`spec_sync_sweep_brief`, and `spec_sync_sweep` itself -- through
`spec_sync_sweep.main()`, the same single entry point a real crontab
invocation calls (see `spec_sync_sweep.py`'s module docstring). Unlike each
module's own unit tests (`test_spec_sync_sweep_discovery.py`,
`test_spec_sync_sweep_check.py`, `test_spec_sync_sweep_dedup.py`,
`test_spec_sync_sweep_brief.py`, `test_spec_sync_sweep.py`), this file builds
one realistic multi-repo fixture tree per scenario -- a mix of clean,
drifted, non-spec, and non-git repos -- and asserts on the full Sweep Run
Record and on-disk queue state produced end-to-end.

Run: python3 -m pytest tests/router/test_spec_sync_sweep_e2e.py -q
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from collections.abc import Generator
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from worktrail.router import spec_sync_sweep as sss
from worktrail.router.spec_sync_sweep_checkbox_check import (
    check_repo_checkbox_drift as _real_check_repo_checkbox_drift,
)
from worktrail.shared.brief_frontmatter import read_frontmatter
from worktrail.workqueue import work_queue

# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, capture_output=True, text=True, check=True
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_task(spec_dir: Path, task_id: str, status: str) -> None:
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Fixture task"
spec: docs/specs/{spec_dir.name}/fixture.md
status: {status}
dependencies: []
---

## Definition of Done (DoD)
- [x] done
""",
    )


def _make_parent_spec(spec_dir: Path, status: str) -> None:
    _write(
        spec_dir / "2026-01-01--fixture.md",
        f"""# Functional Specification: Fixture

**Spec ID**: {spec_dir.name}
**Date**: 2026-01-01
**Status**: {status}
**Version**: 1.0
""",
    )


def _make_clean_repo(root: Path, name: str) -> Path:
    """A git repo with one spec whose parent Status is not stale -> no drift."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task(spec_dir, "TASK-001", "completed")
    _make_parent_spec(spec_dir, "Implemented")
    return repo


def _make_drifted_repo_single_spec(root: Path, name: str) -> Path:
    """A git repo with exactly one drifted spec (Check B: all tasks terminal,
    parent Status header still reads a pre-implementation value)."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task(spec_dir, "TASK-001", "completed")
    _make_parent_spec(spec_dir, "Draft")
    return repo


def _make_drifted_repo_two_specs(root: Path, name: str) -> Path:
    """A git repo drifted across two independent specs -> one repo, two
    findings, still exactly one filed brief (AC-008)."""
    repo = root / name
    _git_init(repo)
    for spec_name in ("001-fixture", "002-fixture"):
        spec_dir = repo / "docs" / "specs" / spec_name
        _make_task(spec_dir, "TASK-001", "completed")
        _make_parent_spec(spec_dir, "Draft")
    return repo


def _make_no_specs_repo(root: Path, name: str) -> Path:
    """A git repo with no docs/specs/ directory at all."""
    repo = root / name
    _git_init(repo)
    return repo


def _make_non_git_dir(root: Path, name: str) -> Path:
    """A plain directory (not a git repo) sitting under repos-root."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("not a repo\n", encoding="utf-8")
    return d


def _make_errored_repo(root: Path, name: str) -> Path:
    """A git repo whose docs/specs/ tree makes check_repo_drift() raise for
    real (no mocking): TASK-001.md is a directory, not a file, so
    find_task_statuses()'s `tf.read_text()` raises IsADirectoryError, which
    propagates out of check_spec() and is captured by check_repo_drift()'s
    try/except as this repo's `error`."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    (spec_dir / "tasks" / "TASK-001.md").mkdir(parents=True, exist_ok=True)
    return repo


def _make_task_with_dod_checkbox(
    spec_dir: Path, task_id: str, status: str, checkbox_ticked: bool
) -> None:
    """Like `_make_task`, but the Definition of Done (DoD) checkbox is ticked
    or left unticked depending on `checkbox_ticked` -- independently steering
    `audit_completed_task_checkboxes.audit_repo()`'s checkbox-drift detection
    (status: completed + an unticked box in a COMPLETION_AUDIT_SECTIONS
    section) while `status` still independently steers `check_spec()`'s "all
    tasks terminal" spec-sync-drift input the same way `_make_task` does."""
    box = "- [x] done" if checkbox_ticked else "- [ ] not done"
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Fixture task"
spec: docs/specs/{spec_dir.name}/fixture.md
status: {status}
dependencies: []
---

## Definition of Done (DoD)
{box}
""",
    )


def _make_spec_sync_only_repo(root: Path, name: str) -> Path:
    """Repo shape (a): spec-sync-drift only. Parent Status header stale
    (Draft) drives spec-sync drift; DoD fully ticked means no checkbox-drift
    finding."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task_with_dod_checkbox(
        spec_dir, "TASK-001", "completed", checkbox_ticked=True
    )
    _make_parent_spec(spec_dir, "Draft")
    return repo


def _make_checkbox_only_repo(root: Path, name: str) -> Path:
    """Repo shape (b): checkbox-drift only. Parent Status header is not
    stale (Implemented) so there is no spec-sync-drift finding; DoD left
    unticked on a status:completed task drives checkbox drift."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task_with_dod_checkbox(
        spec_dir, "TASK-001", "completed", checkbox_ticked=False
    )
    _make_parent_spec(spec_dir, "Implemented")
    return repo


def _make_both_drift_repo(root: Path, name: str) -> Path:
    """Repo shape (c): both spec-sync-drift and checkbox-drift findings,
    driven by the very same task file (stale parent Status + unticked DoD
    box), so a single run must file one brief of each type."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task_with_dod_checkbox(
        spec_dir, "TASK-001", "completed", checkbox_ticked=False
    )
    _make_parent_spec(spec_dir, "Draft")
    return repo


def _make_neither_drift_repo(root: Path, name: str) -> Path:
    """Repo shape (d): neither check finds drift. Parent Status header is
    not stale (Implemented) and the DoD is fully ticked."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task_with_dod_checkbox(
        spec_dir, "TASK-001", "completed", checkbox_ticked=True
    )
    _make_parent_spec(spec_dir, "Implemented")
    return repo


def _hash_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    """Snapshot every file under `root` (including inside `.git/`) as
    (size, mtime_ns, sha256) keyed by relative path, for byte-identical
    before/after comparison."""
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, digest)
    return snapshot


@contextmanager
def _work_queue_dir(path: Path) -> Generator[None, None, None]:
    """Point work_queue.py's base_dir() at a fixture queue base for the
    duration of the block, restoring the prior WORK_QUEUE_DIR afterward."""
    previous = os.environ.get("WORK_QUEUE_DIR")
    os.environ["WORK_QUEUE_DIR"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("WORK_QUEUE_DIR", None)
        else:
            os.environ["WORK_QUEUE_DIR"] = previous


class SpecSyncSweepE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repos_root = self.root / "repos"
        self.queue_base = self.root / "queue-base"
        self.lock_path = self.root / "sweep.lock"

    def _run_main_json(self) -> dict:
        buf = StringIO()
        with redirect_stdout(buf):
            rc = sss.main(
                [
                    "--repos-root",
                    str(self.repos_root),
                    "--queue-dir",
                    str(self.queue_base),
                    "--lock-file",
                    str(self.lock_path),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0, f"main() exited non-zero: {buf.getvalue()}")
        return json.loads(buf.getvalue())

    # -- Complete happy path (AC-001, AC-002, AC-004, AC-005, AC-008, AC-009,
    #    AC-010, AC-018) + CLI-level integration via main(argv) --------------

    def test_happy_path_classifies_every_repo_and_files_one_brief_for_multi_spec_drift(
        self,
    ) -> None:
        clean = _make_clean_repo(self.repos_root, "clean-repo")
        drifted = _make_drifted_repo_two_specs(self.repos_root, "drifted-repo")
        no_specs = _make_no_specs_repo(self.repos_root, "no-specs-repo")
        non_git = _make_non_git_dir(self.repos_root, "not-a-repo")

        record = self._run_main_json()

        self.assertFalse(record["skipped_overlap"])
        # Discovery: only git repos with a docs/specs/ tree are checked.
        self.assertIn(str(clean), record["checked"])
        self.assertIn(str(drifted), record["checked"])
        self.assertNotIn(str(no_specs), record["checked"])
        self.assertNotIn(str(non_git), record["checked"])
        self.assertEqual(len(record["checked"]), 2)

        # Per-repo classification.
        self.assertNotIn(str(clean), record["drifted"])
        self.assertIn(str(drifted), record["drifted"])
        self.assertIn(str(drifted), record["filed"])
        self.assertEqual(record["failed"], [])
        self.assertEqual(record["skipped_existing"], [])

        # Exactly one brief filed for the two-spec-drifted repo, not two.
        queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(queue_files), 1)
        fm = read_frontmatter(queue_files[0])
        self.assertEqual(fm.get("repo"), str(drifted))
        self.assertEqual(fm.get("drift-source"), "spec-sync-sweep")
        body = queue_files[0].read_text(encoding="utf-8")
        self.assertIn("001-fixture", body)
        self.assertIn("002-fixture", body)

    # -- Idempotency across two real runs (AC-012) ---------------------------

    def test_idempotency_across_two_real_runs_files_only_one_brief_total(self) -> None:
        drifted = _make_drifted_repo_single_spec(self.repos_root, "drifted-repo")

        first = self._run_main_json()
        self.assertIn(str(drifted), first["filed"])

        second = self._run_main_json()
        self.assertEqual(second["filed"], [])
        self.assertIn(str(drifted), second["skipped_existing"])

        queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(queue_files), 1)

    # -- Resolution re-arms filing (AC-013) ----------------------------------

    def test_resolving_the_filed_brief_re_arms_filing_on_the_next_run(self) -> None:
        drifted = _make_drifted_repo_single_spec(self.repos_root, "drifted-repo")

        first = self._run_main_json()
        self.assertIn(str(drifted), first["filed"])

        queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(queue_files), 1)
        original_stem = queue_files[0].stem

        # Simulate `work_queue.py claim` then `work_queue.py done` -- the
        # real lifecycle a brief goes through when a fix session resolves it.
        with _work_queue_dir(self.queue_base):
            claim_result = work_queue.claim(original_stem)
            self.assertEqual(claim_result["status"], "claimed")
            done_result = work_queue.done(original_stem)
            self.assertEqual(done_result["status"], "done")

        # queue/ is empty again; the brief now sits in picked/ as done.
        self.assertEqual(list((self.queue_base / "queue").glob("*.md")), [])
        picked_files = list((self.queue_base / "picked").glob("*.md"))
        self.assertEqual(len(picked_files), 1)
        self.assertEqual(read_frontmatter(picked_files[0]).get("status"), "done")

        # Repo is still drifted -> the next run must file a brand-new brief.
        # queue/ was fully empty immediately before this run (asserted above),
        # so the one file present afterward is unambiguously a fresh filing
        # from this run, not a leftover -- regardless of whether the
        # timestamp-based brief-id happens to collide with the resolved one
        # when both runs land within the same wall-clock second.
        second = self._run_main_json()
        self.assertIn(str(drifted), second["filed"])
        new_queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(new_queue_files), 1)
        self.assertEqual(read_frontmatter(new_queue_files[0]).get("status"), "queued")
        # The original (now-resolved) brief is untouched in picked/, still done.
        self.assertEqual(read_frontmatter(picked_files[0]).get("status"), "done")

    # -- Zero-drift, zero-pre-existing run (AC-014, [SEF]) -------------------

    def test_zero_drift_and_zero_pre_existing_files_nothing_and_succeeds(self) -> None:
        _make_clean_repo(self.repos_root, "clean-repo-1")
        _make_clean_repo(self.repos_root, "clean-repo-2")

        record = self._run_main_json()

        self.assertFalse(record["skipped_overlap"])
        self.assertEqual(record["drifted"], [])
        self.assertEqual(record["filed"], [])
        self.assertEqual(record["failed"], [])
        self.assertFalse(
            (self.queue_base / "queue").is_dir()
            and any((self.queue_base / "queue").glob("*.md"))
        )

    # -- Per-repo failure isolation (REQ-008, REQ-NR004) ---------------------

    def test_one_repo_erroring_does_not_block_other_repos_in_the_same_run(self) -> None:
        clean = _make_clean_repo(self.repos_root, "clean-repo")
        drifted = _make_drifted_repo_single_spec(self.repos_root, "drifted-repo")
        errored = _make_errored_repo(self.repos_root, "errored-repo")

        record = self._run_main_json()

        self.assertEqual(len(record["checked"]), 3)
        self.assertIn(str(errored), record["failed"])
        self.assertNotIn(str(errored), record["drifted"])
        self.assertNotIn(str(errored), record["filed"])
        # Every other repo in the same run is still fully processed.
        self.assertNotIn(str(clean), record["drifted"])
        self.assertIn(str(drifted), record["drifted"])
        self.assertIn(str(drifted), record["filed"])

    # -- Overlap skip (AC-017) -----------------------------------------------

    def test_overlap_skip_returns_immediately_with_zero_side_effects(self) -> None:
        _make_drifted_repo_single_spec(self.repos_root, "drifted-repo")

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(self.lock_path, "a+")
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.discover_repos_with_specs"
        ) as spy:
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)
            spy.assert_not_called()

        self.assertEqual(
            record,
            {
                "skipped_overlap": True,
                "checked": [],
                "drifted": [],
                "filed": [],
                "skipped_existing": [],
                "failed": [],
                "checkbox_drifted": [],
                "checkbox_filed": [],
                "checkbox_skipped_existing": [],
                "checkbox_failed": [],
            },
        )
        # No queue write ever happened -- the directory was never even created.
        self.assertFalse((self.queue_base / "queue").exists())

    # -- Non-mutation across the whole run (AC-007, AC-011) ------------------

    def test_no_fixture_repo_file_is_mutated_and_no_subprocess_is_invoked(self) -> None:
        clean = _make_clean_repo(self.repos_root, "clean-repo")
        drifted = _make_drifted_repo_two_specs(self.repos_root, "drifted-repo")
        no_specs = _make_no_specs_repo(self.repos_root, "no-specs-repo")
        non_git = _make_non_git_dir(self.repos_root, "not-a-repo")

        repos = [clean, drifted, no_specs, non_git]
        before = {repo: _hash_tree(repo) for repo in repos}

        with mock.patch("subprocess.run") as spy_subprocess:
            record = self._run_main_json()
            spy_subprocess.assert_not_called()

        self.assertIn(str(drifted), record["filed"])

        after = {repo: _hash_tree(repo) for repo in repos}
        for repo in repos:
            self.assertEqual(
                before[repo], after[repo], f"fixture repo mutated by the sweep: {repo}"
            )

    # -- CLI-level integration via main(argv) (also exercised by the happy
    #    path test above, which invokes main() with --json end-to-end) ------

    def test_cli_main_with_argv_reports_non_zero_on_unwritable_queue_dir(self) -> None:
        _make_drifted_repo_single_spec(self.repos_root, "drifted-repo")
        self.queue_base.mkdir(parents=True)
        self.queue_base.chmod(0o500)  # read + execute only, no write
        self.addCleanup(lambda: self.queue_base.chmod(0o700))

        buf = StringIO()
        with redirect_stdout(buf):
            rc = sss.main(
                [
                    "--repos-root",
                    str(self.repos_root),
                    "--queue-dir",
                    str(self.queue_base),
                    "--lock-file",
                    str(self.lock_path),
                    "--json",
                ]
            )
        self.assertNotEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("error", payload)


class SpecSyncSweepCheckboxDriftE2ETests(unittest.TestCase):
    """End-to-end tests for the checkbox-drift second check
    (TASK-CHG-001..004): the widened two-check `run_sweep()`/`main()`
    pipeline exercised through realistic multi-repo fixtures, the same way
    `SpecSyncSweepE2ETests` above exercises the original single-check
    pipeline.

    Regression coverage (REQ-CHG-019): `SpecSyncSweepE2ETests` above is left
    completely unmodified and continues to run in the same pytest session
    against the now-widened `run_sweep()`/`main()` -- its scenarios (happy
    path, idempotency, resolution re-arming, zero-drift, per-repo failure
    isolation, overlap skip, non-mutation, CLI integration) are the
    regression check this task's `TASK-CHG-005.md` calls for: identical
    outcomes for every pre-existing field, confirmed by their still passing
    unmodified.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repos_root = self.root / "repos"
        self.queue_base = self.root / "queue-base"
        self.lock_path = self.root / "sweep.lock"

    def _run_main_json(self) -> dict:
        buf = StringIO()
        with redirect_stdout(buf):
            rc = sss.main(
                [
                    "--repos-root",
                    str(self.repos_root),
                    "--queue-dir",
                    str(self.queue_base),
                    "--lock-file",
                    str(self.lock_path),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0, f"main() exited non-zero: {buf.getvalue()}")
        return json.loads(buf.getvalue())

    def _queue_briefs_by_drift_source(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        for f in (self.queue_base / "queue").glob("*.md"):
            fm = read_frontmatter(f)
            result[str(fm.get("drift-source"))] = f
        return result

    # -- Complete happy path: four repo shapes, both check families bucketed
    #    correctly, plus CLI-level integration via main(argv) --json --------

    def test_four_repo_shapes_happy_path_buckets_both_check_families_correctly(
        self,
    ) -> None:
        spec_sync_only = _make_spec_sync_only_repo(
            self.repos_root, "spec-sync-only-repo"
        )
        checkbox_only = _make_checkbox_only_repo(self.repos_root, "checkbox-only-repo")
        both = _make_both_drift_repo(self.repos_root, "both-drift-repo")
        neither = _make_neither_drift_repo(self.repos_root, "neither-drift-repo")

        record = self._run_main_json()

        # CLI-level integration: main(argv) with --json prints a record that
        # carries every pre-existing field alongside the new checkbox_* ones.
        self.assertEqual(
            set(record.keys()),
            {
                "skipped_overlap",
                "checked",
                "drifted",
                "filed",
                "skipped_existing",
                "failed",
                "checkbox_drifted",
                "checkbox_filed",
                "checkbox_skipped_existing",
                "checkbox_failed",
            },
        )
        self.assertEqual(len(record["checked"]), 4)

        # (a) spec-sync-drift only.
        self.assertIn(str(spec_sync_only), record["drifted"])
        self.assertIn(str(spec_sync_only), record["filed"])
        self.assertNotIn(str(spec_sync_only), record["checkbox_drifted"])
        self.assertNotIn(str(spec_sync_only), record["checkbox_filed"])

        # (b) checkbox-drift only.
        self.assertNotIn(str(checkbox_only), record["drifted"])
        self.assertNotIn(str(checkbox_only), record["filed"])
        self.assertIn(str(checkbox_only), record["checkbox_drifted"])
        self.assertIn(str(checkbox_only), record["checkbox_filed"])

        # (c) both simultaneously -- appears in filed AND checkbox_filed in
        # the same run.
        self.assertIn(str(both), record["drifted"])
        self.assertIn(str(both), record["filed"])
        self.assertIn(str(both), record["checkbox_drifted"])
        self.assertIn(str(both), record["checkbox_filed"])

        # (d) neither.
        self.assertNotIn(str(neither), record["drifted"])
        self.assertNotIn(str(neither), record["filed"])
        self.assertNotIn(str(neither), record["checkbox_drifted"])
        self.assertNotIn(str(neither), record["checkbox_filed"])

        self.assertEqual(record["failed"], [])
        self.assertEqual(record["checkbox_failed"], [])
        self.assertEqual(record["skipped_existing"], [])
        self.assertEqual(record["checkbox_skipped_existing"], [])

        # Exactly one brief per (repo, check-type): 4 briefs total (a:1,
        # b:1, c:2, d:0).
        queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(queue_files), 4)

        by_repo_and_source: dict[tuple[str, str], Path] = {}
        for f in queue_files:
            fm = read_frontmatter(f)
            by_repo_and_source[(str(fm.get("repo")), str(fm.get("drift-source")))] = f

        self.assertEqual(
            set(by_repo_and_source.keys()),
            {
                (str(spec_sync_only), "spec-sync-sweep"),
                (str(checkbox_only), "checkbox-drift-sweep"),
                (str(both), "spec-sync-sweep"),
                (str(both), "checkbox-drift-sweep"),
            },
        )

        checkbox_brief_body = by_repo_and_source[
            (str(checkbox_only), "checkbox-drift-sweep")
        ].read_text(encoding="utf-8")
        self.assertIn("TASK-001.md", checkbox_brief_body)

    # -- Idempotency across two real runs, per check-type independently
    #    (AC-CHG-009) -----------------------------------------------------

    def test_idempotency_per_check_type_independently_files_only_one_of_each(
        self,
    ) -> None:
        both = _make_both_drift_repo(self.repos_root, "both-drift-repo")

        first = self._run_main_json()
        self.assertIn(str(both), first["filed"])
        self.assertIn(str(both), first["checkbox_filed"])

        second = self._run_main_json()
        self.assertEqual(second["filed"], [])
        self.assertEqual(second["checkbox_filed"], [])
        self.assertIn(str(both), second["skipped_existing"])
        self.assertIn(str(both), second["checkbox_skipped_existing"])

        by_source = self._queue_briefs_by_drift_source()
        self.assertEqual(
            set(by_source.keys()), {"spec-sync-sweep", "checkbox-drift-sweep"}
        )
        queue_files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(queue_files), 2)

    # -- Resolution re-arms filing, per check-type independently
    #    (AC-CHG-010, AC-CHG-015) ------------------------------------------

    def test_resolution_rearms_filing_per_check_type_independently(self) -> None:
        both = _make_both_drift_repo(self.repos_root, "both-drift-repo")

        first = self._run_main_json()
        self.assertIn(str(both), first["filed"])
        self.assertIn(str(both), first["checkbox_filed"])

        by_source = self._queue_briefs_by_drift_source()
        self.assertEqual(
            set(by_source.keys()), {"spec-sync-sweep", "checkbox-drift-sweep"}
        )
        spec_sync_brief_stem = by_source["spec-sync-sweep"].stem
        checkbox_brief_stem = by_source["checkbox-drift-sweep"].stem

        # Resolve only the checkbox-drift brief (simulate `work_queue.py
        # claim` then `work_queue.py done`); leave the spec-sync-drift brief
        # untouched and unresolved in queue/.
        with _work_queue_dir(self.queue_base):
            claim_result = work_queue.claim(checkbox_brief_stem)
            self.assertEqual(claim_result["status"], "claimed")
            done_result = work_queue.done(checkbox_brief_stem)
            self.assertEqual(done_result["status"], "done")

        self.assertEqual(
            read_frontmatter(
                self.queue_base / "picked" / f"{checkbox_brief_stem}.md"
            ).get("status"),
            "done",
        )
        # The spec-sync-drift brief is still sitting in queue/, unresolved.
        self.assertTrue(
            (self.queue_base / "queue" / f"{spec_sync_brief_stem}.md").exists()
        )

        # Repo is still drifted on both checks -> the second run must file a
        # brand-new checkbox-drift brief while leaving the still-unresolved
        # spec-sync-drift brief alone (reported in skipped_existing, not
        # duplicated).
        second = self._run_main_json()
        self.assertEqual(second["filed"], [])
        self.assertIn(str(both), second["skipped_existing"])
        self.assertIn(str(both), second["checkbox_filed"])
        self.assertNotIn(str(both), second["checkbox_skipped_existing"])

        # queue/ now holds the original (still-unresolved) spec-sync-drift
        # brief plus exactly one new checkbox-drift brief -- never two of
        # the latter.
        queue_by_source: dict[str, list] = {
            "spec-sync-sweep": [],
            "checkbox-drift-sweep": [],
        }
        for f in (self.queue_base / "queue").glob("*.md"):
            fm = read_frontmatter(f)
            queue_by_source.setdefault(str(fm.get("drift-source")), []).append(f)
        self.assertEqual(len(queue_by_source["spec-sync-sweep"]), 1)
        self.assertEqual(len(queue_by_source["checkbox-drift-sweep"]), 1)
        self.assertEqual(
            queue_by_source["spec-sync-sweep"][0].stem, spec_sync_brief_stem
        )

        # The original (now-resolved) checkbox-drift brief is untouched in
        # picked/, still done.
        self.assertEqual(
            read_frontmatter(
                self.queue_base / "picked" / f"{checkbox_brief_stem}.md"
            ).get("status"),
            "done",
        )

    # -- Zero-checkbox-drift, zero-pre-existing run (AC-CHG-011, [SEF]) ------

    def test_zero_checkbox_drift_and_zero_pre_existing_files_nothing_for_checkbox(
        self,
    ) -> None:
        _make_spec_sync_only_repo(self.repos_root, "spec-sync-only-repo")
        _make_neither_drift_repo(self.repos_root, "neither-drift-repo")

        record = self._run_main_json()

        self.assertEqual(record["checkbox_drifted"], [])
        self.assertEqual(record["checkbox_filed"], [])
        self.assertEqual(record["checkbox_failed"], [])
        # Success (main() already asserted rc == 0 inside _run_main_json).
        by_source = self._queue_briefs_by_drift_source()
        self.assertNotIn("checkbox-drift-sweep", by_source)

    # -- Per-check, per-repo failure isolation (REQ-CHG-005) -----------------

    def test_checkbox_check_failure_in_one_repo_does_not_block_other_checks(
        self,
    ) -> None:
        failing = _make_both_drift_repo(self.repos_root, "failing-checkbox-repo")
        healthy_checkbox = _make_checkbox_only_repo(
            self.repos_root, "healthy-checkbox-repo"
        )

        def fake_checkbox_check(repo: Path) -> dict:
            if repo == failing:
                return {
                    "repo": str(repo),
                    "findings": [],
                    "error": "simulated checkbox failure",
                }
            return _real_check_repo_checkbox_drift(repo)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.check_repo_checkbox_drift",
            side_effect=fake_checkbox_check,
        ):
            record = self._run_main_json()

        # (a) The failing repo's checkbox-drift check is captured as a
        # failure, not silently dropped or misclassified as drift-free.
        self.assertIn(str(failing), record["checkbox_failed"])
        self.assertNotIn(str(failing), record["checkbox_drifted"])
        self.assertNotIn(str(failing), record["checkbox_filed"])

        # ...but that repo's independent spec-sync-drift check still ran and
        # was correctly bucketed (unaffected by the checkbox-check failure).
        self.assertIn(str(failing), record["drifted"])
        self.assertIn(str(failing), record["filed"])

        # (b) Every other fixture repo's checkbox-drift check still ran.
        self.assertIn(str(healthy_checkbox), record["checkbox_drifted"])
        self.assertIn(str(healthy_checkbox), record["checkbox_filed"])

        # (c) The failing repo appears in checkbox_failed (already asserted
        # above); the run itself still succeeds overall.
        self.assertEqual(record["failed"], [])

    # -- Non-mutation across the whole run (REQ-CHG-006) ---------------------

    def test_no_fixture_repo_file_is_mutated_across_the_full_two_check_run(
        self,
    ) -> None:
        spec_sync_only = _make_spec_sync_only_repo(
            self.repos_root, "spec-sync-only-repo"
        )
        checkbox_only = _make_checkbox_only_repo(self.repos_root, "checkbox-only-repo")
        both = _make_both_drift_repo(self.repos_root, "both-drift-repo")
        neither = _make_neither_drift_repo(self.repos_root, "neither-drift-repo")

        repos = [spec_sync_only, checkbox_only, both, neither]
        before = {repo: _hash_tree(repo) for repo in repos}

        with mock.patch("subprocess.run") as spy_subprocess:
            record = self._run_main_json()
            spy_subprocess.assert_not_called()

        self.assertIn(str(both), record["filed"])
        self.assertIn(str(both), record["checkbox_filed"])

        after = {repo: _hash_tree(repo) for repo in repos}
        for repo in repos:
            self.assertEqual(
                before[repo], after[repo], f"fixture repo mutated by the sweep: {repo}"
            )


if __name__ == "__main__":
    unittest.main()

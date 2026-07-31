#!/usr/bin/env python3
"""Regression test: `add_stacked_worktree` must not silently continue when a
sibling dependency branch cannot be merged into a stacked worktree.

Reproduces the incident from `~/.go/runs/datalena/go-20260730-133115.yaml`:
two sibling tasks (no dependency edge between them) both touched the same
file; a dependent task's stacked worktree tried to merge both sibling
branches in and hit a conflict on the second merge. The old behavior printed
a warning and proceeded on the first branch only, silently missing the second
sibling's commits -- the dependent then failed much later, far from the real
cause. It must now raise immediately so `_safe_drive` can isolate the failure
to just this task instead of letting it corrupt the worktree's content.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live, verify  # noqa: E402


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "shared.py").write_text("base\n")
    (path / "README.md").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")


def _branch_editing_shared_file(repo, branch, content, start="HEAD"):
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-B", branch, start)
    (Path(repo) / "shared.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"edit shared.py on {branch}")
    _git(repo, "checkout", "-q", base)


def _build_multi_sibling_conflict_repo(
    tmp, spec_id="102-x", sibling_count=3, dependent_id="TASK-004"
):
    """Multi-sibling extension of the 1.2/1.3 two-sibling repro: `sibling_count`
    independent tasks (no dep edge between any of them) all edit `shared.py`
    from the same base, and one dependent task depends on all of them. Building
    the dependent's stacked worktree must therefore walk `sibling_count - 1`
    SEQUENTIAL merge conflicts in a single `add_stacked_worktree` call (start on
    the first sibling's branch, then merge each remaining sibling in turn) --
    the two-sibling repro only ever exercises one such conflict per call.

    Returns (repo, by_id, wt, sibling_branches).
    """
    repo = Path(tmp) / "repo"
    repo.mkdir()
    _init_repo(repo)

    sibling_ids = [f"TASK-{i:03d}" for i in range(1, sibling_count + 1)]
    sibling_branches = []
    for tid in sibling_ids:
        branch = f"{spec_id}/{tid.lower()}"
        _branch_editing_shared_file(repo, branch, f"from {tid.lower()}\n")
        sibling_branches.append(branch)

    by_id = {tid: {"id": tid, "deps": []} for tid in sibling_ids}
    by_id[dependent_id] = {"id": dependent_id, "deps": list(sibling_ids)}

    wt = Path(tmp) / "wt" / f"{spec_id}-{dependent_id.lower()}"
    wt.parent.mkdir(parents=True)
    return repo, by_id, wt, sibling_branches


class AddStackedWorktreeSiblingConflict(unittest.TestCase):
    def test_raises_instead_of_silently_dropping_sibling_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            spec_id = "102-x"
            # Two independent siblings (no dep edge between them) both edit
            # shared.py from the same base -- exactly the 1.2/1.3 scenario.
            _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
            _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

            by_id = {
                "TASK-001": {"id": "TASK-001", "deps": []},
                "TASK-002": {"id": "TASK-002", "deps": []},
                "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
            }
            wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
            wt.parent.mkdir(parents=True)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(repo, spec_id, by_id["TASK-003"], by_id, wt)
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # No lingering merge state left behind for a human/resume to trip on.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")

    def test_unparseable_report_back_salvaged_when_git_state_clean(self):
        """Mirrors `integrate._assembly_resolve_salvage`: a resolve worker's
        report-back that fails to parse is not itself a failure -- if the git
        state proves the merge actually concluded clean, the resolution is
        salvaged as success rather than raised."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            spec_id = "102-x"
            _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
            _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

            by_id = {
                "TASK-001": {"id": "TASK-001", "deps": []},
                "TASK-002": {"id": "TASK-002", "deps": []},
                "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
            }
            wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
            wt.parent.mkdir(parents=True)

            def fake_spawn(prompt, worktree):
                # Worker actually resolves and concludes the merge on disk, but
                # its final message carries no parseable report-back JSON block.
                (Path(worktree) / "shared.py").write_text("merged\n")
                _git(worktree, "add", "-A")
                _git(worktree, "commit", "-q", "--no-edit")
                return "done -- merged it by hand, no report-back block here"

            live.add_stacked_worktree(
                repo,
                spec_id,
                by_id["TASK-003"],
                by_id,
                wt,
                assembly_resolve_spawn=fake_spawn,
            )

            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "salvaged merge must leave a clean tree")
            self.assertEqual((wt / "shared.py").read_text(), "merged\n")
            log = _git(wt, "log", "--format=%s").stdout
            self.assertIn("task-001", log)
            self.assertIn("task-002", log)

    def test_resolve_spawn_provided_worker_succeeds_verified_clean_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            spec_id = "102-x"
            _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
            _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

            by_id = {
                "TASK-001": {"id": "TASK-001", "deps": []},
                "TASK-002": {"id": "TASK-002", "deps": []},
                "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
            }
            wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
            wt.parent.mkdir(parents=True)

            def _resolving_spawn(prompt, worktree):
                (Path(worktree) / "shared.py").write_text(
                    "merged: task-001 + task-002\n"
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "add", "-A"], check=True
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "commit", "-q", "--no-edit"],
                    check=True,
                )
                return (
                    "```json\n"
                    '{"task": "TASK-003", "step": "resolve", "status": "success"}\n'
                    "```"
                )

            live.add_stacked_worktree(
                repo,
                spec_id,
                by_id["TASK-003"],
                by_id,
                wt,
                assembly_resolve_spawn=_resolving_spawn,
            )

            # No lingering merge state.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "resolved merge must leave a clean tree")

            # Worktree carries both siblings' commits.
            for branch in (f"{spec_id}/task-001", f"{spec_id}/task-002"):
                self.assertEqual(
                    _git(wt, "merge-base", "--is-ancestor", branch, "HEAD").returncode,
                    0,
                    f"{branch} commit must be an ancestor of the stacked worktree HEAD",
                )
            self.assertEqual(
                (wt / "shared.py").read_text(), "merged: task-001 + task-002\n"
            )

    def test_resolve_spawn_raises_aborts_merge_and_raises(self):
        """When a resolve spawn is provided but the worker itself raises/crashes,
        the crash must not be trusted as a resolution: the merge is aborted and
        `WorktreeStackConflictError` is raised with the same message format as
        the no-resolve-spawn path."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            spec_id = "102-x"
            _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
            _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

            by_id = {
                "TASK-001": {"id": "TASK-001", "deps": []},
                "TASK-002": {"id": "TASK-002", "deps": []},
                "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
            }
            wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
            wt.parent.mkdir(parents=True)

            calls = []

            def _spawn_raises(prompt, worktree):
                calls.append((prompt, worktree))
                raise RuntimeError("resolve worker crashed")

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=_spawn_raises,
                )
            self.assertEqual(len(calls), 1, "resolve spawn must have been invoked once")
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # No lingering merge state left behind -- `git merge --abort` ran despite
            # the spawn crash, exactly as it would with no resolve spawn at all.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")
            self.assertEqual((Path(wt) / "shared.py").read_text(), "from task-001\n")

    def test_resolve_spawn_none_or_omitted_existing_behavior_unchanged(self):
        """`assembly_resolve_spawn=None` (or simply omitted, the default) must
        behave identically to the pre-existing no-resolve-spawn path: raise
        immediately on a sibling merge conflict with no resolve attempt, and
        leave no lingering merge state. Covers both call styles explicitly so
        a future default change or callsite regression is caught here."""
        for pass_none_explicitly in (False, True):
            with self.subTest(pass_none_explicitly=pass_none_explicitly):
                with tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp) / "repo"
                    repo.mkdir()
                    _init_repo(repo)

                    spec_id = "102-x"
                    _branch_editing_shared_file(
                        repo, f"{spec_id}/task-001", "from task-001\n"
                    )
                    _branch_editing_shared_file(
                        repo, f"{spec_id}/task-002", "from task-002\n"
                    )

                    by_id = {
                        "TASK-001": {"id": "TASK-001", "deps": []},
                        "TASK-002": {"id": "TASK-002", "deps": []},
                        "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
                    }
                    wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
                    wt.parent.mkdir(parents=True)

                    kwargs = {"assembly_resolve_spawn": None} if pass_none_explicitly else {}

                    with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                        live.add_stacked_worktree(
                            repo, spec_id, by_id["TASK-003"], by_id, wt, **kwargs
                        )
                    self.assertIn("TASK-003", str(ctx.exception))
                    self.assertIn("task-002", str(ctx.exception).lower())

                    # No lingering merge state left behind, exactly as before.
                    status = _git(wt, "status", "--porcelain").stdout
                    self.assertEqual(
                        status.strip(), "", "merge must have been aborted cleanly"
                    )
                    self.assertEqual(
                        (wt / "shared.py").read_text(), "from task-001\n"
                    )


class AddStackedWorktreeResolveSpawnUnverified(unittest.TestCase):
    """4.3: resolve spawn provided, worker reports success, but the git state
    doesn't back it up -- must be treated as unverified and raise, exactly as
    if no resolve had been attempted at all."""

    def _setup_conflict(self, tmp):
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _init_repo(repo)

        spec_id = "102-x"
        _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
        _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {"id": "TASK-002", "deps": []},
            "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
        }
        wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
        wt.parent.mkdir(parents=True)
        return repo, spec_id, by_id, wt

    def test_reports_success_but_merge_head_still_present(self):
        # Worker claims success and even cleans up the conflict markers, but
        # never runs `git merge --continue` -- MERGE_HEAD is still there.
        def fake_spawn(prompt, wt):
            (Path(wt) / "shared.py").write_text("resolved, but never committed\n")
            _git(wt, "add", "shared.py")
            return '```json\n{"task": "TASK-003", "step": "assembly-resolve", "status": "success"}\n```'

        with tempfile.TemporaryDirectory() as tmp:
            repo, spec_id, by_id, wt = self._setup_conflict(tmp)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=fake_spawn,
                )
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # Rejected resolution must leave no lingering merge state behind.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")
            self.assertNotEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    capture_output=True,
                ).returncode,
                0,
                "MERGE_HEAD must not remain after a rejected resolution",
            )

    def test_reports_success_but_conflict_markers_remain(self):
        # Worker claims success and completes the merge, but never actually
        # removed the conflict markers from the file it staged/committed.
        def fake_spawn(prompt, wt):
            _git(wt, "add", "shared.py")  # stage as-is, markers still present
            _git(wt, "commit", "-q", "-m", "resolve (bogus, markers left in)")
            return '```json\n{"task": "TASK-003", "step": "assembly-resolve", "status": "success"}\n```'

        with tempfile.TemporaryDirectory() as tmp:
            repo, spec_id, by_id, wt = self._setup_conflict(tmp)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=fake_spawn,
                )
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # The worker's own (bogus) commit is not undone by our abort --
            # `merge --abort` only undoes an in-progress merge, and this
            # worker committed it. What matters is that we did NOT accept the
            # resolution and continue on as if the stack succeeded: the raise
            # above already confirms that.


class AddStackedWorktreeMultiSiblingConflict(unittest.TestCase):
    """5.1: extends the two-sibling repro to three independent siblings, so a
    single `add_stacked_worktree` call must resolve two SEQUENTIAL sibling-merge
    conflicts (start branch vs. sibling 2, then the result vs. sibling 3) rather
    than just one. Uses a scripted (non-live) spawn -- the real-worker run is
    `AddStackedWorktreeLiveValidation` below."""

    def test_resolves_two_sequential_sibling_conflicts_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, by_id, wt, sibling_branches = _build_multi_sibling_conflict_repo(
                tmp, sibling_count=3, dependent_id="TASK-004"
            )

            resolve_calls = []

            def _resolving_spawn(prompt, worktree):
                resolve_calls.append(worktree)
                # Actually clear the conflict markers -- a real worker's edit,
                # not just a textual append on top of the still-conflicted file.
                (Path(worktree) / "shared.py").write_text(
                    f"merged (resolve #{len(resolve_calls)})\n"
                )
                subprocess.run(["git", "-C", str(worktree), "add", "-A"], check=True)
                subprocess.run(
                    ["git", "-C", str(worktree), "commit", "-q", "--no-edit"],
                    check=True,
                )
                return (
                    "```json\n"
                    '{"task": "TASK-004", "step": "resolve", "status": "success"}\n'
                    "```"
                )

            live.add_stacked_worktree(
                repo,
                "102-x",
                by_id["TASK-004"],
                by_id,
                wt,
                assembly_resolve_spawn=_resolving_spawn,
            )

            self.assertEqual(
                len(resolve_calls), 2, "one resolve dispatch per sequential conflict"
            )

            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "resolved merge must leave a clean tree")

            for branch in sibling_branches:
                self.assertEqual(
                    _git(wt, "merge-base", "--is-ancestor", branch, "HEAD").returncode,
                    0,
                    f"{branch} commit must be an ancestor of the stacked worktree HEAD",
                )


class AddStackedWorktreeLiveValidation(unittest.TestCase):
    """5.1 real-world validation (per incident lesson: unit tests alone missed
    this failure class). Opt-in only -- set WORKTRAIL_LIVE_VALIDATION=1 to run
    it; by default it is skipped so the ordinary hermetic test run (and CI)
    never makes a real network/API call. Wires `assembly_resolve_spawn` to
    `verify._make_live_spawn`, the exact factory the production call sites
    (`live_run_real`, `full_real`'s `_ensure_wt`) use, so this exercises the
    real orchestrator seam end to end: a real headless resolve worker is
    spawned into the conflicted worktree and must resolve it unattended.
    """

    @unittest.skipUnless(
        os.environ.get("WORKTRAIL_LIVE_VALIDATION") == "1",
        "set WORKTRAIL_LIVE_VALIDATION=1 to run the real-headless-worker "
        "validation (makes a real API call via `claude -p`)",
    )
    def test_real_resolve_worker_clears_multi_sibling_conflict_unattended(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, by_id, wt, sibling_branches = _build_multi_sibling_conflict_repo(
                tmp, sibling_count=3, dependent_id="TASK-004"
            )

            live_spawn = verify._make_live_spawn(
                model=os.environ.get("WORKTRAIL_LIVE_VALIDATION_MODEL", "haiku"),
                timeout=int(os.environ.get("WORKTRAIL_LIVE_VALIDATION_TIMEOUT", "600")),
                agent="claude",
            )

            live.add_stacked_worktree(
                repo,
                "102-x",
                by_id["TASK-004"],
                by_id,
                wt,
                assembly_resolve_spawn=live_spawn,
            )

            # Unattended: no raise above means both sequential conflicts were
            # resolved by the real worker without any manual intervention here.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "resolved merge must leave a clean tree")
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    capture_output=True,
                ).returncode,
                1,
                "no lingering MERGE_HEAD after a real unattended resolution",
            )

            # Both siblings' commits (and therefore both siblings' intent) are
            # present in the stacked worktree's history.
            for branch in sibling_branches:
                self.assertEqual(
                    _git(wt, "merge-base", "--is-ancestor", branch, "HEAD").returncode,
                    0,
                    f"{branch} commit must be an ancestor of the stacked worktree HEAD",
                )

            content = (wt / "shared.py").read_text()
            self.assertNotIn("<<<<<<<", content)
            self.assertNotIn(">>>>>>>", content)


if __name__ == "__main__":
    unittest.main()

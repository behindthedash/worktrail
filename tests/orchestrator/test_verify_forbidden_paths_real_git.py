#!/usr/bin/env python3
"""Real-git regression tests for the deny-list guard's base-tip narrowing.

`Verifier._forbidden_paths_touched` intersects the worker's pre/post diff with
`<remote>/<base>..<group branch>` so that a resolve worker merging base into the
group branch is not struck out for denied paths it never authored (run
`go-20260920-124658`: `.github/workflows/gitleaks.yml` and the spec root's
`tasks.md` arrived unchanged from base).

`tests/orchestrator/test_verify.py` covers the guard's logic through a fake git
runner, which cannot see a wrong ref spelling, a stale remote-tracking ref, a
shallow clone, or the real diff-range semantics. These tests drive the default
subprocess runner against real repositories: a bare `origin`, a clone with a
group branch, a sibling commit landed on base, and a resolve-style merge of base
into the group branch.

Run: PYTHONPATH=src pytest -q tests/orchestrator/test_verify_forbidden_paths_real_git.py
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import verify

GROUP_BRANCH = "run/feature-1"
GROUP = {"name": "feature-1"}
WORKFLOW = ".github/workflows/gitleaks.yml"
SPEC_TASKS = "docs/specs/001-x/tasks.md"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit_files(repo: Path, message: str, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


class ForbiddenPathsRealGit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.logs: list[str] = []
        self.bare = self.tmp / "origin.git"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True
        )
        seed = self.tmp / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        _configure(seed)
        _commit_files(
            seed,
            "base",
            {
                "README.md": "base\n",
                ".github/workflows/ci.yml": "ci: 1\n",
                SPEC_TASKS: "- [ ] 1.1\n",
            },
        )
        _git(seed, "remote", "add", "origin", str(self.bare))
        _git(seed, "push", "-q", "origin", "main")
        self.seed = seed

    def _clone(self, *, shallow: bool = False) -> Path:
        """The worker's repo (the Verifier's `repo`), with a group branch carrying
        one authored file. `self.pre_sha` is the worker's pre-run HEAD."""
        repo = self.tmp / "repo"
        depth = ["--depth", "1"] if shallow else []
        # `file://` so `--depth` is honoured for a local clone.
        subprocess.run(
            ["git", "clone", "-q", *depth, f"file://{self.bare}", str(repo)],
            check=True,
        )
        _configure(repo)
        _git(repo, "checkout", "-q", "-b", GROUP_BRANCH)
        _commit_files(repo, "authored", {"src/feature.py": "x = 1\n"})
        self.pre_sha = _git(repo, "rev-parse", GROUP_BRANCH).stdout.strip()
        return repo

    def _land_on_base(self, files: dict[str, str]) -> str:
        """A sibling group's commit reaching `origin/main` after the fork."""
        _commit_files(self.seed, "sibling landed on base", files)
        _git(self.seed, "push", "-q", "origin", "main")
        return _git(self.seed, "rev-parse", "HEAD").stdout.strip()

    def _merge_base_in(self, repo: Path) -> None:
        """What a resolve worker is told to do: merge `origin/main` into the
        group branch (a real merge commit, not a fast-forward)."""
        _git(repo, "fetch", "-q", "origin", "main")
        _git(repo, "merge", "-q", "--no-edit", "origin/main")

    def _guard(self, repo: Path, *, remote="origin", base="main") -> list[str]:
        v = verify.Verifier(
            repo,
            remote,
            base,
            "001-x",
            log=self.logs.append,
            worktree_base=self.tmp / "worktrees",
        )
        return v._forbidden_paths_touched(self.pre_sha, GROUP_BRANCH, GROUP)

    def _premise_denied_paths_in_pre_post_diff(self, repo: Path, *paths: str) -> None:
        """The narrowing only matters if the un-narrowed diff really carries the
        merged-in paths; without this a broken merge setup would pass vacuously."""
        touched = _git(
            repo, "diff", "--name-only", f"{self.pre_sha}..{GROUP_BRANCH}"
        ).stdout.split()
        for path in paths:
            self.assertIn(path, touched)

    def _assert_narrowed(self):
        self.assertFalse(
            [m for m in self.logs if "narrowing unavailable" in m], self.logs
        )

    # -- narrowing ---------------------------------------------------------- #

    def test_paths_merged_in_from_base_are_not_a_violation(self):
        """The `go-20260920-124658` shape, against real git: base gained a
        workflow file and a spec-root edit, the resolve worker merged base in,
        and the branch's net scope is only the file it authored."""
        repo = self._clone()
        self._land_on_base(
            {
                WORKFLOW: "name: gitleaks\n",
                SPEC_TASKS: "- [x] 1.1\n",
                "src/other.py": "",
            }
        )
        self._merge_base_in(repo)
        self._premise_denied_paths_in_pre_post_diff(repo, WORKFLOW, SPEC_TASKS)

        self.assertEqual(self._guard(repo), [])
        self._assert_narrowed()

    def test_edited_forbidden_paths_are_still_reported_alongside_a_merge(self):
        """Narrowing must not disarm the guard: a denied path the worker really
        edited stays flagged while the merged-in ones drop out."""
        repo = self._clone()
        self._land_on_base({WORKFLOW: "name: gitleaks\n", SPEC_TASKS: "- [x] 1.1\n"})
        _commit_files(
            repo,
            "worker edits denied paths",
            {
                ".github/workflows/ci.yml": "ci: weakened\n",
                "docs/specs/001-x/notes.md": "worker notes\n",
            },
        )
        self._merge_base_in(repo)

        self.assertEqual(
            self._guard(repo),
            [".github/workflows/ci.yml", "docs/specs/001-x/notes.md"],
        )
        self._assert_narrowed()

    def test_edit_without_a_merge_is_reported(self):
        """No base movement at all: the narrowing is a no-op, not a filter."""
        repo = self._clone()
        _commit_files(repo, "worker edit", {".github/workflows/ci.yml": "ci: 2\n"})

        self.assertEqual(self._guard(repo), [".github/workflows/ci.yml"])
        self._assert_narrowed()

    def test_base_advancing_after_the_merge_is_not_attributed_to_the_worker(self):
        """Narrowing is an intersection, never a second source of violations: a
        denied path that landed on base *after* the merge differs from the
        branch, but the worker never touched it."""
        repo = self._clone()
        self._land_on_base({"src/other.py": ""})
        self._merge_base_in(repo)
        self._land_on_base({".github/workflows/later.yml": "name: later\n"})

        self.assertEqual(self._guard(repo), [])
        self._assert_narrowed()

    def test_stale_remote_tracking_ref_is_refreshed_by_the_guard(self):
        """The worker merged the base tip without updating `origin/main` (fetch
        by URL leaves it alone), so the guard's own `git fetch` is what makes
        the narrowing correct. Without it `origin/main..gb` would still carry
        every merged-in file."""
        repo = self._clone()
        tip = self._land_on_base({WORKFLOW: "name: gitleaks\n"})
        _git(repo, "fetch", "-q", str(self.bare), "main")
        _git(repo, "merge", "-q", "--no-edit", "FETCH_HEAD")
        self.assertNotEqual(_git(repo, "rev-parse", "origin/main").stdout.strip(), tip)
        self._premise_denied_paths_in_pre_post_diff(repo, WORKFLOW)

        self.assertEqual(self._guard(repo), [])
        self.assertEqual(_git(repo, "rev-parse", "origin/main").stdout.strip(), tip)
        self._assert_narrowed()

    def test_shallow_clone(self):
        """A `--depth 1` worker repo: the base diff must still resolve."""
        repo = self._clone(shallow=True)
        self._land_on_base({WORKFLOW: "name: gitleaks\n"})
        self._merge_base_in(repo)
        self._premise_denied_paths_in_pre_post_diff(repo, WORKFLOW)

        self.assertEqual(self._guard(repo), [])
        self._assert_narrowed()

    # -- fail-open ---------------------------------------------------------- #

    def test_unfetchable_base_falls_back_to_the_unnarrowed_diff_and_logs(self):
        """A stale-or-absent base tip must make the guard noisier, never
        quieter: the merged-in denied path is reported, with a log line."""
        repo = self._clone()
        self._land_on_base({WORKFLOW: "name: gitleaks\n"})
        self._merge_base_in(repo)

        self.bare.rename(self.tmp / "origin-gone.git")
        cases = {
            # `origin/main` still resolves locally, so a guard that diffed against
            # it after a failed fetch would narrow against a stale tip and
            # quietly report nothing.
            "unreachable origin, stale tracking ref": {},
            "missing base ref": {"base": "no-such-branch"},
            "missing remote": {"remote": "no-such-remote"},
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.logs.clear()
                self.assertEqual(self._guard(repo, **kwargs), [WORKFLOW])
                self.assertTrue(
                    [m for m in self.logs if "base-tip narrowing unavailable" in m],
                    self.logs,
                )


if __name__ == "__main__":
    unittest.main()

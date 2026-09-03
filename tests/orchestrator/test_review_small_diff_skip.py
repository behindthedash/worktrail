"""Integration tests for the design-D3 small-diff review skip (live.py).

A repo opts in via `.worktrail/policy.yaml`'s `review_skip_max_diff_lines`
(default 0 -- off). When set, a task's FIRST review is skipped entirely
(never spawned) if the implement step reported `status: success`,
`tests: passed`, and the resulting diff -- excluding test files -- has fewer
non-test added+removed lines than the threshold. The skip is journaled as
`review_status: skipped-small-diff` and is resumable.

Hermetic: an injected RecordingSpawn (no real claude -p) against a throwaway
git repo.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch, live, spawnlib


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "docs" / "specs" / "001-x" / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    fm = (
        "---\n"
        "id: TASK-001\n"
        "status: pending\n"
        "dependencies: []\n"
        "files: [src/foo.py]\n"
        "kind: impl\n"
        "---\nbody\n"
    )
    (repo / "docs" / "specs" / "001-x" / "tasks" / "TASK-001.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


def _set_policy(repo: Path, threshold: int) -> None:
    policy_dir = repo / ".worktrail"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.yaml").write_text(
        f"review_skip_max_diff_lines: {threshold}\n"
    )


def _commit(wt: Path, rel: str, content: str) -> str:
    f = Path(wt) / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", rel],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


class _ImplementThenReviewSpawn:
    """Implement makes a small (or large / test-heavy) diff, reporting a
    configurable `tests` verdict. Review, if ever spawned, always PASSES --
    the point of every test here is whether review gets spawned at all."""

    def __init__(self, impl_tests="passed", impl_status="success", diff="small"):
        self.impl_tests = impl_tests
        self.impl_status = impl_status
        self.diff = diff
        self.calls: list = []

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append(role)
        if role == dispatch.ROLE_IMPLEMENT:
            if self.diff == "small":
                sha = _commit(wt, "src/foo.py", "impl\n")
            elif self.diff == "test-heavy":
                _commit(wt, "src/foo.py", "impl\n")
                sha = _commit(
                    wt, "tests/test_foo.py", "\n".join(f"line{i}" for i in range(200))
                )
            else:  # "large"
                sha = _commit(
                    wt, "src/foo.py", "\n".join(f"line{i}" for i in range(200))
                )
            return spawnlib.SpawnResult(
                text=(
                    '```json\n{{"task":"{}","step":"implement",'
                    '"status":"{}","head_sha":"{}","tests":"{}"}}\n```'
                ).format(task["id"], self.impl_status, sha[:8], self.impl_tests),
                usage={},
            )
        if role == dispatch.ROLE_REVIEW:
            sha = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            return spawnlib.SpawnResult(
                text=(
                    '```json\n{{"task":"{}","step":"review","status":"success",'
                    '"head_sha":"{}","review_status":"PASSED"}}\n```'
                ).format(task["id"], sha[:8]),
                usage={},
            )
        raise AssertionError(f"unexpected role spawned: {role}")


class _FailOnceThenPassSpawn:
    """implement (tests:failed, so never skip-eligible) -> review 1 FAILS
    (forces a real fix cycle) -> fix -> review 2 (post-fix) must ALSO spawn,
    never skipped, even though every diff here is tiny."""

    def __init__(self):
        self.calls: list = []

    def _review_count(self) -> int:
        return sum(1 for c in self.calls if c == "review")

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append(role)
        if role == dispatch.ROLE_IMPLEMENT:
            sha = _commit(wt, "src/foo.py", "impl\n")
            return spawnlib.SpawnResult(
                text=(
                    '```json\n{{"task":"{}","step":"implement","status":"success",'
                    '"head_sha":"{}","tests":"failed"}}\n```'
                ).format(task["id"], sha[:8]),
                usage={},
            )
        if role == dispatch.ROLE_FIX:
            sha = _commit(wt, "src/foo.py", "fix\n")
            return spawnlib.SpawnResult(
                text=(
                    '```json\n{{"task":"{}","step":"fix","status":"success",'
                    '"head_sha":"{}","tests":"passed"}}\n```'
                ).format(task["id"], sha[:8]),
                usage={},
            )
        if role == dispatch.ROLE_REVIEW:
            sha = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            status = "FAILED" if self._review_count() == 1 else "PASSED"
            return spawnlib.SpawnResult(
                text=(
                    '```json\n{{"task":"{}","step":"review","status":"success",'
                    '"head_sha":"{}","review_status":"{}"}}\n```'
                ).format(task["id"], sha[:8], status),
                usage={},
            )
        raise AssertionError(f"unexpected role spawned: {role}")


class TestReviewSmallDiffSkip(unittest.TestCase):
    def _run(self, spawn, threshold=None, resume=False, journal=None):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            if threshold is not None:
                _set_policy(repo, threshold)
            journal_path = journal or str(Path(tmp) / "run-001-x.json")
            result = live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal_path,
                run_id="test-skip",
                spawn=spawn,
                resume=resume,
            )
            entries = json.loads(Path(journal_path).read_text())["entries"]
            return result, entries

    def test_threshold_zero_never_skips(self):
        spawn = _ImplementThenReviewSpawn()
        result, _ = self._run(spawn, threshold=0)
        self.assertIn("review", spawn.calls)
        task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["status"], "done")

    def test_no_policy_file_never_skips(self):
        spawn = _ImplementThenReviewSpawn()
        _result, _ = self._run(spawn, threshold=None)
        self.assertIn("review", spawn.calls)

    def test_small_passing_diff_skips_with_journal_verdict(self):
        spawn = _ImplementThenReviewSpawn()
        result, entries = self._run(spawn, threshold=40)
        self.assertNotIn("review", spawn.calls)
        task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["status"], "done")
        review_entries = [e for e in entries if e.get("role") == "review"]
        self.assertEqual(len(review_entries), 1)
        self.assertEqual(
            review_entries[0]["report"]["review_status"], "skipped-small-diff"
        )

    def test_tests_none_never_skips(self):
        spawn = _ImplementThenReviewSpawn(impl_tests="none")
        _result, _ = self._run(spawn, threshold=40)
        self.assertIn("review", spawn.calls)

    def test_large_diff_does_not_skip(self):
        spawn = _ImplementThenReviewSpawn(diff="large")
        _result, _ = self._run(spawn, threshold=40)
        self.assertIn("review", spawn.calls)

    def test_test_file_lines_excluded_from_count(self):
        """A large test-file diff alongside a small src diff still skips --
        only non-test lines count toward the threshold."""
        spawn = _ImplementThenReviewSpawn(diff="test-heavy")
        result, _entries = self._run(spawn, threshold=40)
        self.assertNotIn("review", spawn.calls)
        task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["status"], "done")

    def test_post_fix_review_always_spawns(self):
        spawn = _FailOnceThenPassSpawn()
        result, entries = self._run(spawn, threshold=1000)
        self.assertEqual(spawn.calls.count("review"), 2)
        task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["status"], "done")
        review_entries = [e for e in entries if e.get("role") == "review"]
        self.assertTrue(
            all(
                e["report"].get("review_status") != "skipped-small-diff"
                for e in review_entries
            )
        )

    def test_resume_from_skipped_entry_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _set_policy(repo, 40)
            journal_path = str(Path(tmp) / "run-001-x.json")
            phase1 = _ImplementThenReviewSpawn()
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal_path,
                run_id="test-skip",
                spawn=phase1,
                resume=True,
            )
            self.assertNotIn("review", phase1.calls)

            def _raise_on_review(role, task, wt):
                raise AssertionError(f"{role} must not spawn on resume")

            result = live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal_path,
                run_id="test-skip",
                spawn=_raise_on_review,
                resume=True,
            )
            task = next(t for t in result["tasks"] if t["id"] == "TASK-001")
            self.assertEqual(task["status"], "done")


if __name__ == "__main__":
    unittest.main()

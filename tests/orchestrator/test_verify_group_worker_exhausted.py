#!/usr/bin/env python3
"""A capacity block costs no group-worker strike (spawn-caller-exhausted-audit 2.2).

An exhausted spawn's text is the provider's capacity notice, not a worker
answer. `_make_live_spawn`'s inner `spawn()` now fails closed on it, and the
resolve / ci-fix loops report a capacity-blocked outcome instead of burning
strikes and quarantining the group with a worker-attributed reason.

Hermetic: reuses `test_verify`'s fake git/gh runner and Verifier factory.
"""

from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path

from worktrail.orchestrator import spawnlib, verify
from worktrail.runtime.selection import NoExecutionTarget

from .test_verify import FEATURE, GREEN, RED, FakeRun, mk, view


class _Result:
    """Minimal stand-in for spawnlib.SpawnResult."""

    def __init__(self, text="", exhausted=False, failure_class=""):
        self.text = text
        self.exhausted = exhausted
        self.failure_class = failure_class


class ExhaustedSpawn:
    """A group-worker spawn whose every cell was capacity-gated."""

    def __init__(self, failure_class="billing"):
        self.calls = 0
        self.failure_class = failure_class

    def __call__(self, _prompt, _worktree_path):
        self.calls += 1
        raise spawnlib.SpawnExhausted("group worker", self.failure_class)


class UnparseableSpawn:
    """A worker that answers, badly -- not exhausted, so it costs a strike."""

    def __init__(self):
        self.calls = 0

    def __call__(self, _prompt, _worktree_path):
        self.calls += 1
        return "I could not figure it out."


class OkSpawn:
    def __init__(self):
        self.calls = 0

    def __call__(self, _prompt, _worktree_path):
        self.calls += 1
        return (
            'done.\n```json\n{"task":"g","step":"verify",'
            '"status":"success","head_sha":"def"}\n```'
        )


def _no_mutations(test, run):
    test.assertEqual(run.find("git", "-C", "/repo", "push"), [])
    test.assertFalse(
        [c for c in run.calls if c[:2] == ["gh", "pr"] and c[2] != "view"],
        f"no gh pr mutation expected, got {run.calls}",
    )


class LiveSpawnFailsClosed(unittest.TestCase):
    def test_exhausted_result_raises_instead_of_returning_text(self):
        exhausted = _Result(
            text="Claude usage limit reached.", exhausted=True, failure_class="billing"
        )
        with (
            unittest.mock.patch.object(spawnlib, "spawn_agent", return_value=exhausted),
            self.assertRaises(spawnlib.SpawnExhausted) as cm,
        ):
            verify._make_live_spawn()("prompt", Path("/tmp"))
        self.assertIn("group worker", str(cm.exception))
        self.assertEqual(cm.exception.failure_class, "billing")
        # the shape the callers upstream already handle
        self.assertIsInstance(cm.exception, NoExecutionTarget)

    def test_non_exhausted_result_returns_its_text(self):
        ok = _Result(text="worker said this")
        with unittest.mock.patch.object(spawnlib, "spawn_agent", return_value=ok):
            self.assertEqual(
                verify._make_live_spawn()("prompt", Path("/tmp")), "worker said this"
            )


class ResolveLoopCapacityBlock(unittest.TestCase):
    def test_exhausted_resolve_spawns_once_and_reports_capacity(self):
        run = FakeRun({"run/feature-1": [view(mergeable="CONFLICTING")]})
        spawn = ExhaustedSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.ensure_mergeable(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertEqual(spawn.calls, 1)  # not max_strikes
        self.assertIn("capacity blocked", reason)
        self.assertIn("resolve group worker", reason)
        self.assertNotIn("worker failed", reason)
        self.assertNotIn("still CONFLICTING", reason)
        _no_mutations(self, run)

    def test_unparseable_resolve_still_fails_as_a_worker_failure(self):
        run = FakeRun({"run/feature-1": [view(mergeable="CONFLICTING")]})
        spawn = UnparseableSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.ensure_mergeable(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertEqual(spawn.calls, 1)
        self.assertEqual(reason, "resolve worker failed")

    def test_successful_resolve_still_returns_true(self):
        run = FakeRun(
            {
                "run/feature-1": [
                    view(mergeable="CONFLICTING"),
                    view(mergeable="MERGEABLE"),
                ]
            }
        )
        spawn = OkSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.ensure_mergeable(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(spawn.calls, 1)


class CiFixLoopCapacityBlock(unittest.TestCase):
    def test_exhausted_ci_fix_spawns_once_and_spends_no_strike(self):
        run = FakeRun({"run/feature-1": [view(rollup=RED)]})
        spawn = ExhaustedSpawn(failure_class="rate_limit")
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.wait_and_fix_ci(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertEqual(spawn.calls, 1)  # one attempt, then out -- no strikes
        self.assertIn("capacity blocked", reason)
        self.assertIn("ci-fix group worker", reason)
        self.assertIn("rate_limit", reason)
        self.assertNotIn("CI still failing after", reason)
        self.assertNotIn("CI fix loop exhausted", reason)
        _no_mutations(self, run)

    def test_unparseable_ci_fix_still_consumes_every_strike(self):
        run = FakeRun({"run/feature-1": [view(rollup=RED)]})
        spawn = UnparseableSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.wait_and_fix_ci(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertEqual(spawn.calls, v.max_strikes)
        self.assertNotIn("capacity", reason)

    def test_successful_ci_fix_still_returns_true(self):
        run = FakeRun({"run/feature-1": [view(rollup=RED), view(rollup=GREEN)]})
        spawn = OkSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, reason = v.wait_and_fix_ci(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertEqual(spawn.calls, 1)


if __name__ == "__main__":
    unittest.main()

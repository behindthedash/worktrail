#!/usr/bin/env python3
"""`spawn_agent` signals a give-up as `exhausted`, not as a result.

Every give-up return hands the caller the provider's own notice as `text`. A
consumer that cannot tell that apart from a worker's answer will parse a usage-cap
message as a verdict, so the SpawnResult now carries `exhausted` plus the failure
class the cell was gated on. Hermetic: subprocess.run and routing are both faked,
mirroring tests/orchestrator/test_spawnlib.py.
"""

import os
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import patch

from worktrail.orchestrator import spawnlib

Proc = namedtuple("Proc", "returncode stdout stderr")

# Single-target row: every give-up on it has no alternate cell to hop to.
SINGLE_CLAUDE_ROUTING = {
    "targets": {
        "claude-sub": {
            "harness": "claude",
            "pool": "subscription",
            "api_opt_in": False,
            "auth": None,
        }
    },
    "tiers": {"t2-build": {"claude-sub": {"model": "sonnet", "effort": None}}},
    "roles": {},
    "purposes": {},
    "default_tier": "t2-build",
    "drain": {},
}


class SpawnExhaustionSignal(unittest.TestCase):
    def setUp(self):
        self._orig_run = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._routing_patch = patch.object(
            spawnlib, "resolve_routing", return_value=SINGLE_CLAUDE_ROUTING
        )
        self._routing_patch.start()

    def tearDown(self):
        self._routing_patch.stop()
        spawnlib.subprocess.run = self._orig_run
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _run(self, outcomes, **kw):
        calls = []

        def fake_run(*a, **k):
            out = outcomes[min(len(calls), len(outcomes) - 1)]
            calls.append(k)
            return out

        spawnlib.subprocess.run = fake_run
        result = spawnlib.spawn_agent(
            "p", "/tmp", tier="t2-build", sleep=lambda _s: None, **kw
        )
        return result, calls

    def test_defaults_are_not_exhausted(self):
        result = spawnlib.SpawnResult(text="ok", usage={})
        self.assertFalse(result.exhausted)
        self.assertEqual(result.failure_class, "")

    def test_no_alternate_cell_gives_up_as_exhausted_billing(self):
        # A provider usage cap on the only cell in the row: retries exhaust, the
        # cell is gated `billing`, and re-selection has nothing left.
        cap = Proc(1, "", "You've hit your usage limit.")
        result, calls = self._run([cap], retries=1)
        self.assertEqual(len(calls), 2)  # first attempt + 1 retry
        self.assertTrue(result.exhausted)
        self.assertEqual(result.failure_class, "billing")

    def test_session_limit_wait_budget_exhaustion_is_exhausted(self):
        limit = Proc(0, "You've hit your session limit, resets 3:00pm", "")
        result, _calls = self._run([limit], retries=0, session_limit_waits=1)
        self.assertTrue(result.exhausted)
        self.assertEqual(result.failure_class, "rate_limit")
        self.assertIn("session limit", result.text)

    def test_retry_loop_fall_out_is_exhausted(self):
        # The trailing `return finish(...)` after the retry loop is defensive:
        # normal flow always returns or hops from inside the loop. Force the loop
        # to be skipped entirely (zero attempts) so the fall-out return is the one
        # under test -- it must still be signalled as a give-up, never a result.
        real_max = max

        def fake_max(*args):
            return 0 if args == (1, 1) else real_max(*args)

        with patch.object(spawnlib, "max", fake_max, create=True):
            result, calls = self._run([Proc(0, "unused", "")], retries=0)
        self.assertEqual(calls, [])  # loop body never ran
        self.assertTrue(result.exhausted)

    def test_success_is_not_exhausted_and_keeps_its_fields(self):
        result, calls = self._run([Proc(0, "good report", "")], retries=0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result.exhausted)
        self.assertEqual(result.failure_class, "")
        self.assertEqual(result.text, "good report")
        self.assertEqual(result.usage, {})
        self.assertEqual(result.session_id, "")
        self.assertEqual(result.served_target, "claude-sub")
        self.assertEqual(result.served_model, "sonnet")
        self.assertEqual(result.served_harness, "claude")


if __name__ == "__main__":
    unittest.main()

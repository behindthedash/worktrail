"""BLOCKED is classified only once every required context has reported.

GitHub reports `mergeStateStatus: BLOCKED` both for "a required check
failed" and for "a required check has not reported yet". Classifying the
second as `blocked_product_decision` strands a healthy PR, so the BLOCKED
branch re-polls the merge-state guard until the required contexts are
terminal (or a bounded budget is spent).
"""

from __future__ import annotations

import unittest
from unittest import mock

from worktrail.router import automerge_preflight, land_pr

from .test_land_pr import FakeRun, RunRecordSpy, _land_request

PENDING = {"name": "CI", "state": "PENDING"}
DONE = {"name": "CI", "conclusion": "SUCCESS"}
FAILED = {"name": "CI", "conclusion": "FAILURE"}
DONE_BY_CONTEXT = {"context": "CI", "state": "SUCCESS"}


def _blocked(rollup=None) -> dict:
    return {
        "state": "OPEN",
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": list(rollup or []),
    }


def _clean() -> dict:
    return {"state": "OPEN", "mergeStateStatus": "CLEAN", "statusCheckRollup": [DONE]}


class BlockedRequiredContextsTests(unittest.TestCase):
    def _run(self, guard_sequence, required_contexts=("CI",), run_record_spy=None):
        """`land_pr()` with every step patched at its seam, `_merge_state_guard`
        driven by a scripted sequence (its last entry repeats)."""
        spy = run_record_spy or RunRecordSpy()
        guard = mock.Mock(
            side_effect=lambda *a, **k: guard_sequence[
                min(guard.call_count - 1, len(guard_sequence) - 1)
            ]
        )
        defaults = {
            "_commit_pending": None,
            "_ensure_compile_markers": (None, None),
            "_run_preflight_and_labels": (None, ["go:risk-low"]),
            "_current_branch": "feature",
            "_push_target": ("origin", "o/r"),
            "_push": None,
            "open_or_update_pull_request": {
                "pr_url": "https://github.com/o/r/pull/1",
                "pr_number": 1,
                "refused_step": None,
                "detail": None,
            },
            "_watch_ci": {
                "settled": True,
                "failing_checks": [],
                "log_excerpt": "",
                "budget_exhausted": False,
            },
            "_review_thread_gate": {"checked": True, "blocking": False},
            "_pr_is_merged": False,
        }
        patchers = [
            mock.patch.object(land_pr, name, return_value=value)
            for name, value in defaults.items()
        ]
        patchers.append(mock.patch.object(land_pr, "_merge_state_guard", guard))
        patchers.append(mock.patch.object(land_pr.time, "sleep"))
        patchers.append(
            mock.patch.object(
                automerge_preflight,
                "required_status_check_contexts",
                return_value=list(required_contexts)
                if required_contexts is not None
                else None,
            )
        )
        request = _land_request(runner=FakeRun())
        with mock.patch.object(land_pr.run_record_module, "main", spy):
            for p in patchers:
                p.start()
            try:
                outcome = land_pr.land_pr(request)
            finally:
                for p in patchers:
                    p.stop()
        return outcome, spy, guard

    def _finish_statuses(self, spy: RunRecordSpy) -> list[str]:
        return [c[c.index("--status") + 1] for c in spy.finish_calls()]

    # -- helper unit coverage -------------------------------------------

    def test_helper_true_for_none_and_empty_contexts(self) -> None:
        blocked = _blocked([PENDING])
        self.assertTrue(land_pr._required_contexts_reported(blocked, None))
        self.assertTrue(land_pr._required_contexts_reported(blocked, []))
        self.assertEqual(land_pr._outstanding_required_contexts(blocked, None), [])

    def test_helper_matches_context_key_as_well_as_name(self) -> None:
        self.assertTrue(
            land_pr._required_contexts_reported(_blocked([DONE_BY_CONTEXT]), ["CI"])
        )

    def test_helper_false_for_pending_or_absent_context(self) -> None:
        self.assertFalse(
            land_pr._required_contexts_reported(_blocked([PENDING]), ["CI"])
        )
        self.assertEqual(
            land_pr._outstanding_required_contexts(_blocked([]), ["CI"]), ["CI"]
        )

    # -- orchestration coverage -----------------------------------------

    def test_pending_required_context_repolls_then_classifies(self) -> None:
        outcome, _spy, guard = self._run([_blocked([PENDING]), _blocked([DONE])])
        self.assertGreater(guard.call_count, 1)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "blocked_product_decision")

    def test_absent_required_context_repolls_then_classifies(self) -> None:
        outcome, _, guard = self._run([_blocked([]), _blocked([DONE])])
        self.assertGreater(guard.call_count, 1)
        self.assertEqual(outcome.final_status, "blocked_product_decision")

    def test_merge_state_clearing_on_a_later_poll_continues_normal_flow(self) -> None:
        outcome, spy, guard = self._run([_blocked([PENDING]), _clean()])
        self.assertGreater(guard.call_count, 1)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_pr_open")
        self.assertEqual(self._finish_statuses(spy), ["completed_pr_open"])

    def test_spent_budget_returns_ceiling_naming_outstanding_contexts(self) -> None:
        outcome, spy, guard = self._run(
            [_blocked([PENDING])], required_contexts=("CI", "lint")
        )
        self.assertEqual(
            guard.call_count, land_pr.BLOCKED_REQUIRED_CONTEXT_POLL_MAX + 1
        )
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.final_status, "failed_recoverable")
        self.assertIn("CI", outcome.merge_result)
        self.assertIn("lint", outcome.merge_result)
        self.assertEqual(self._finish_statuses(spy), ["failed_recoverable"])
        self.assertNotIn("blocked_product_decision", self._finish_statuses(spy))

    def test_failing_but_terminal_required_context_classifies_immediately(self) -> None:
        outcome, _, guard = self._run([_blocked([FAILED])])
        self.assertEqual(guard.call_count, 1)
        self.assertEqual(outcome.final_status, "blocked_product_decision")

    def test_no_required_contexts_classifies_immediately(self) -> None:
        for contexts in (None, ()):
            with self.subTest(contexts=contexts):
                outcome, _, guard = self._run(
                    [_blocked([PENDING])], required_contexts=contexts
                )
                self.assertEqual(guard.call_count, 1)
                self.assertEqual(outcome.final_status, "blocked_product_decision")


if __name__ == "__main__":
    unittest.main()

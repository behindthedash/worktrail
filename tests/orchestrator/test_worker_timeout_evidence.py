#!/usr/bin/env python3
"""A timed-out worker's evidence and token usage must survive the kill.

Brief 20260918-221038. Spec review-loop-repeated-finding-to-decision task 4.1
(run go-20260918-181212, 2026-09-18): a verification-only tail task ran its
full 1800s implement worker, made 52 turns / ~2.3M cache-read tokens, got
through two suites and a golden replay, and was killed mid-`pytest`. Its
journal entry read `status failed, head_sha '', tests 'none'` -- every passing
result gone -- and the end-of-run token table did not count the spawn at all.

Two separable halves are covered here:
  * spawnlib attaches the partial parse to the propagated `TimeoutExpired`
    (`worktrail_partial`), so usage/tools are recoverable.
  * `progress.summarize_usage` counts a failed entry's usage like any other,
    so a timed-out spawn reaches the token table.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from worktrail.orchestrator import progress, spawnlib


def _stream_json(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


_PARTIAL_STREAM = _stream_json(
    [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
                "usage": {"input_tokens": 120, "cache_read_input_tokens": 2_300_000},
            },
        },
    ]
)


class TimeoutCarriesPartialResultTests(unittest.TestCase):
    def test_timeout_expired_carries_the_partial_parse(self):
        def raising_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd=cmd, timeout=1800, output=_PARTIAL_STREAM
            )

        with (
            patch.object(spawnlib.subprocess, "run", raising_run),
            self.assertRaises(subprocess.TimeoutExpired) as ctx,
        ):
            spawnlib.spawn_claude_p(
                "do the thing", "/tmp", tier="t2-build", timeout=1800
            )

        partial = getattr(ctx.exception, "worktrail_partial", None)
        self.assertIsNotNone(
            partial, "TimeoutExpired must carry the killed worker's partial parse"
        )
        self.assertEqual(partial.failure_class, "timeout")
        self.assertIn("Bash", partial.tools_used)
        self.assertEqual(
            partial.usage.get("cache_read_input_tokens"),
            2_300_000,
            "the tokens the killed worker burned must be recoverable",
        )

    def test_unparseable_partial_output_never_masks_the_timeout(self):
        def raising_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30, output="not json{{{")

        with (
            patch.object(spawnlib.subprocess, "run", raising_run),
            self.assertRaises(subprocess.TimeoutExpired) as ctx,
        ):
            spawnlib.spawn_claude_p("do the thing", "/tmp", tier="t2-build", timeout=30)
        self.assertIsNotNone(getattr(ctx.exception, "worktrail_partial", None))

    def test_no_output_at_all_still_yields_a_partial(self):
        def raising_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)

        with (
            patch.object(spawnlib.subprocess, "run", raising_run),
            self.assertRaises(subprocess.TimeoutExpired) as ctx,
        ):
            spawnlib.spawn_claude_p("do the thing", "/tmp", tier="t2-build", timeout=30)
        partial = getattr(ctx.exception, "worktrail_partial", None)
        self.assertIsNotNone(partial)
        self.assertEqual(partial.tools_used, [])


class PartialUsageFromStreamTests(unittest.TestCase):
    """The per-turn fallback must never double-count a completed spawn."""

    def test_sums_per_turn_usage_and_counts_turns(self):
        stream = _stream_json(
            [
                {"type": "assistant", "message": {"usage": {"output_tokens": 10}}},
                {"type": "assistant", "message": {"usage": {"output_tokens": 7}}},
            ]
        )
        usage = spawnlib.partial_usage_from_stream(stream)
        self.assertEqual(usage["output_tokens"], 17)
        self.assertEqual(usage["num_turns"], 2)
        self.assertTrue(usage["partial"])

    def test_reports_no_cost(self):
        """Cost is only ever stated by the `result` event; estimating it would
        be a guess."""
        usage = spawnlib.partial_usage_from_stream(_PARTIAL_STREAM)
        self.assertNotIn("total_cost_usd", usage)

    def test_stream_with_no_assistant_events_yields_nothing(self):
        self.assertEqual(spawnlib.partial_usage_from_stream(""), {})
        self.assertEqual(
            spawnlib.partial_usage_from_stream(_stream_json([{"type": "system"}])), {}
        )

    def test_a_result_event_still_wins_on_a_completed_spawn(self):
        """Precondition for the fallback being safe: a stream that DOES carry
        a result event parses its authoritative total, and the fallback never
        runs."""
        stream = _stream_json(
            [
                {"type": "assistant", "message": {"usage": {"output_tokens": 10}}},
                {
                    "type": "result",
                    "result": "done",
                    "usage": {"output_tokens": 99},
                    "num_turns": 1,
                },
            ]
        )
        _text, usage, _tools, _skills, _sid = spawnlib._parse_stream_json(stream)
        self.assertEqual(usage["output_tokens"], 99)
        self.assertNotIn("partial", usage)


class TimedOutSpawnIsAccountedForTests(unittest.TestCase):
    """The end-of-run token table must not be short by a killed spawn."""

    def test_failed_entry_with_usage_is_counted(self):
        journal = {
            "entries": [
                {
                    "task": "4.1",
                    "role": "implement",
                    "report": {"status": "failed", "head_sha": "", "tests": "none"},
                    "usage": {
                        "input_tokens": 120,
                        "cache_read_input_tokens": 2_300_000,
                        "num_turns": 52,
                    },
                }
            ]
        }
        summary = progress.summarize_usage(journal)
        self.assertEqual(summary["total"]["spawns"], 1)
        self.assertEqual(summary["total"]["cache_read_input_tokens"], 2_300_000)
        self.assertEqual(summary["total"]["turns"], 52)
        self.assertIn("implement", progress.render_usage(journal))

    def test_entry_without_usage_is_still_uncounted(self):
        """Precondition, so the assertion above is about the usage field and
        not about failed entries in general."""
        journal = {
            "entries": [
                {
                    "task": "4.1",
                    "role": "implement",
                    "report": {"status": "failed", "head_sha": "", "tests": "none"},
                }
            ]
        }
        self.assertEqual(progress.summarize_usage(journal)["total"]["spawns"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

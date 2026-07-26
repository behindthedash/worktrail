#!/usr/bin/env python3
"""Tests for progress.py -- per-step timing journal + on-demand checklist render.

The live fan-out spawns a BLOCKING `claude -p` per step with captured output, so
the terminal is silent until each step finishes. progress.py closes that gap with
journal timing (auditable after the fact) + a heartbeat sidecar (the in-flight
step) that `render` turns into a checklist. These tests pin: duration formatting,
heartbeat round-trips, the rendered checklist (completed timings, live elapsed for
the active step, pending tasks, slowest-step summary), and that the timing keys
progress adds to entries never disturb journal replay.

Run: python3 scripts/test_progress.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import progress  # noqa: E402


class FmtDur(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(progress._fmt_dur(0), "0s")
        self.assertEqual(progress._fmt_dur(42), "42s")
        self.assertEqual(progress._fmt_dur(59.4), "59s")

    def test_minutes(self):
        self.assertEqual(progress._fmt_dur(63), "1m03s")
        self.assertEqual(progress._fmt_dur(125), "2m05s")

    def test_hours(self):
        self.assertEqual(progress._fmt_dur(3725), "1h02m")

    def test_none_is_unknown(self):
        self.assertEqual(progress._fmt_dur(None), "?")


class HeartbeatRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "run-008-foo.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_heartbeat_path_sibling_naming(self):
        hb = progress.heartbeat_path(self.journal)
        self.assertEqual(hb.name, "run-008-foo.status.json")
        self.assertEqual(hb.parent, self.journal.parent)

    def test_begin_step_then_set_phase(self):
        progress.begin_step(
            self.journal,
            run_id="full-123",
            spec_id="008-foo",
            task_id="TASK-002",
            role="review",
            started_at=1000.0,
        )
        hb = json.loads(progress.heartbeat_path(self.journal).read_text())
        self.assertEqual(hb["active"]["task"], "TASK-002")
        self.assertEqual(hb["active"]["role"], "review")
        self.assertEqual(hb["run_id"], "full-123")
        self.assertEqual(hb["phase"], "fanout")

        # phase change clears the in-flight worker but keeps identity
        progress.set_phase(self.journal, "verify")
        hb2 = json.loads(progress.heartbeat_path(self.journal).read_text())
        self.assertIsNone(hb2["active"])
        self.assertEqual(hb2["phase"], "verify")
        self.assertEqual(hb2["run_id"], "full-123")

    def test_set_phase_writes_optional_detail(self):
        progress.begin_step(
            self.journal,
            run_id="full-123",
            spec_id="008-foo",
            task_id="TASK-002",
            role="review",
            started_at=1000.0,
        )
        progress.set_phase(
            self.journal,
            "fanout_failed",
            detail={
                "failed_tasks": [{"id": "TASK-010", "status": "failed"}],
                "blocked_tasks": [
                    {"id": "TASK-011", "status": "pending", "blocked_by": ["TASK-010"]}
                ],
            },
        )
        hb = json.loads(progress.heartbeat_path(self.journal).read_text())
        self.assertEqual(hb["failed_tasks"], [{"id": "TASK-010", "status": "failed"}])
        self.assertEqual(
            hb["blocked_tasks"],
            [{"id": "TASK-011", "status": "pending", "blocked_by": ["TASK-010"]}],
        )

    def test_writes_never_raise_on_bad_path(self):
        # best-effort: a write to an impossible path is swallowed, not raised
        progress.begin_step(
            "/nonexistent-dir-xyz/deeper/run.json",
            run_id="x",
            spec_id="s",
            task_id="t",
            role="implement",
        )  # must not raise


def _entry(tid, role, dur, *, review_status=None):
    return {
        "task": tid,
        "role": role,
        "report": {"status": "success", "head_sha": "abc", "review_status": review_status},
        "started_at": 1000.0,
        "ended_at": 1000.0 + dur,
        "duration_s": dur,
    }


class Render(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "run-008-foo.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_journal(self, entries, **extra):
        data = {"spec_id": "008-foo", "run_id": "full-123", "entries": entries}
        data.update(extra)
        self.journal.write_text(json.dumps(data))

    def test_completed_and_pending_and_active(self):
        self._write_journal(
            [
                _entry("TASK-001", "implement", 63),
                _entry("TASK-001", "review", 41, review_status="PASSED"),
                _entry("TASK-001", "cleanup", 22),
                _entry("TASK-002", "implement", 371),
            ]
        )
        # TASK-002 currently reviewing, started 124s before "now"
        progress.begin_step(
            self.journal,
            run_id="full-123",
            spec_id="008-foo",
            task_id="TASK-002",
            role="review",
            started_at=2000.0,
        )
        tasks = [
            {"id": "TASK-001", "status": "done"},
            {"id": "TASK-002", "status": "reviewing"},
            {"id": "TASK-003", "status": "pending"},
        ]
        out = progress.render(self.journal, tasks=tasks, now=2124.0)

        self.assertIn("spec 008-foo", out)
        self.assertIn("run full-123", out)
        # completed step timings, formatted
        self.assertIn("implement 1m03s", out)
        self.assertIn("review 41s [PASSED]", out)
        # active step shows live elapsed
        self.assertIn("review … running 2m04s", out)
        # markers
        self.assertIn("[✓] TASK-001", out)
        self.assertIn("[▶] TASK-002", out)
        self.assertIn("[ ] TASK-003", out)
        # summary: 1 done, 1 in flight, slowest is TASK-002 implement 6m11s (371s)
        self.assertIn("1/3 tasks done", out)
        self.assertIn("1 in flight", out)
        self.assertIn("slowest step: TASK-002 implement 6m11s", out)

    def test_render_without_tasks_uses_journal_order(self):
        self._write_journal([_entry("TASK-009", "implement", 10)])
        out = progress.render(self.journal, now=1010.0)
        self.assertIn("TASK-009", out)
        self.assertIn("implement 10s", out)

    def test_missing_journal_is_graceful(self):
        out = progress.render(self.journal / "nope.json")
        self.assertIn("0/0 tasks done", out)

    def test_legacy_entry_without_timing_renders(self):
        # pre-timing journals (no duration_s) must still render, sans times
        self._write_journal(
            [{"task": "TASK-001", "role": "implement", "report": {"status": "success"}}]
        )
        out = progress.render(self.journal, now=9999.0)
        self.assertIn("TASK-001", out)
        self.assertIn("implement ?", out)  # unknown duration


class TimingKeysDoNotBreakReplay(unittest.TestCase):
    """The timing keys progress adds to entries are ignored by reconcile_from_journal."""

    def test_reconcile_ignores_timing_keys(self):
        tasks = [
            {"id": "TASK-001", "status": "pending", "retry_count": 0, "deps": [], "files": ["a.py"]}
        ]
        journal = {
            "entries": [
                _entry("TASK-001", "implement", 12),
                _entry("TASK-001", "review", 5, review_status="PASSED"),
                _entry("TASK-001", "cleanup", 3),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(tasks[0]["status"], "done")


class ConcurrentPhaseProgress(unittest.TestCase):
    """AC-016/AC-017/AC-018: concurrent-phase representation and render."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "run-009-foo.json"
        self.journal.write_text(
            json.dumps({"spec_id": "009-foo", "run_id": "full-99", "entries": []})
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_group_phases_stored_in_heartbeat(self):
        """AC-016: heartbeat carries more than one group phase at once."""
        progress.set_group_phases(
            self.journal, {"group-a": "fanout", "group-b": "verifying"}
        )
        hb = json.loads(progress.heartbeat_path(self.journal).read_text())
        self.assertEqual(hb["group_phases"]["group-a"], "fanout")
        self.assertEqual(hb["group_phases"]["group-b"], "verifying")

    def test_render_pipelined_shows_fanning_and_verifying(self):
        """AC-017: render with group_phases shows N fanning / M verifying summary."""
        progress.set_group_phases(
            self.journal,
            {"group-a": "fanout", "group-b": "fanout", "group-c": "verifying"},
        )
        out = progress.render(self.journal, now=9999.0)
        self.assertIn("2 fanning", out)
        self.assertIn("1 verifying", out)
        # Single phase: label must NOT appear (the summary replaces it)
        self.assertNotIn("phase: fanout", out)

    def test_render_all_three_pipeline_phases(self):
        """AC-017: summary distinguishes fanning / integrating / verifying."""
        progress.set_group_phases(
            self.journal,
            {
                "group-a": "fanout",
                "group-b": "integrating",
                "group-c": "verifying",
            },
        )
        out = progress.render(self.journal, now=9999.0)
        self.assertIn("1 fanning", out)
        self.assertIn("1 integrating", out)
        self.assertIn("1 verifying", out)

    def test_render_sequential_falls_back_to_phase(self):
        """AC-017: sequential heartbeat (no group_phases) still shows phase: line."""
        progress.set_phase(self.journal, "verify")
        out = progress.render(self.journal, now=9999.0)
        self.assertIn("phase: verify", out)
        self.assertNotIn("fanning", out)

    def test_set_group_phases_bad_path_is_swallowed(self):
        """AC-018: write failure to an impossible path never raises."""
        progress.set_group_phases(
            "/no-such-dir-xyz-pqr/deeper/run.json",
            {"group-a": "fanout"},
        )  # must not raise


class SummarizeUsage(unittest.TestCase):
    def _u(self, **kw):
        base = dict(
            input_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=0,
            total_cost_usd=0.0,
        )
        base.update(kw)
        return base

    def test_aggregates_per_role_and_total(self):
        journal = {
            "entries": [
                {"role": "implement", "usage": self._u(input_tokens=11000, cache_creation_input_tokens=24000, output_tokens=3000, total_cost_usd=0.21)},
                {"role": "implement", "usage": self._u(input_tokens=9000, cache_read_input_tokens=24000, output_tokens=2500, total_cost_usd=0.07)},
                {"role": "review", "usage": self._u(input_tokens=5000, cache_read_input_tokens=24000, output_tokens=800, total_cost_usd=0.04)},
            ]
        }
        s = progress.summarize_usage(journal)
        self.assertEqual(s["roles"]["implement"]["spawns"], 2)
        self.assertEqual(s["roles"]["implement"]["input_tokens"], 20000)
        self.assertEqual(s["roles"]["implement"]["output_tokens"], 5500)
        self.assertAlmostEqual(s["roles"]["implement"]["cost"], 0.28)
        self.assertEqual(s["total"]["spawns"], 3)
        self.assertEqual(s["total"]["input_tokens"], 25000)
        self.assertEqual(s["total"]["cache_read_input_tokens"], 48000)
        self.assertAlmostEqual(s["total"]["cost"], 0.32)
        self.assertEqual(s["entries_with_usage"], 3)

    def test_entries_without_usage_are_ignored(self):
        # A python-cleanup step (no spawn) carries no usage and must not be counted.
        journal = {"entries": [
            {"role": "implement", "usage": self._u(input_tokens=100, total_cost_usd=0.01)},
            {"role": "cleanup"},  # deterministic, no spawn
        ]}
        s = progress.summarize_usage(journal)
        self.assertEqual(s["total"]["spawns"], 1)
        self.assertEqual(s["entries_with_usage"], 1)
        self.assertEqual(s["entries_total"], 2)
        self.assertNotIn("cleanup", s["roles"])

    def test_cache_hit_ratio(self):
        # 24K read of 48K input-side -> 50%.
        self.assertAlmostEqual(
            progress._cache_hit_ratio(
                {"cache_read_input_tokens": 24000, "input_tokens": 16000, "cache_creation_input_tokens": 8000}
            ),
            0.5,
        )
        self.assertEqual(progress._cache_hit_ratio({}), 0.0)  # no tokens -> no div-by-zero


class RenderUsage(unittest.TestCase):
    def test_renders_table_with_total_and_cache_line(self):
        journal = {"entries": [
            {"role": "implement", "usage": {"input_tokens": 11000, "cache_read_input_tokens": 24000, "output_tokens": 3000, "total_cost_usd": 0.21}},
        ]}
        out = progress.render_usage(journal)
        self.assertIn("token usage (per role):", out)
        self.assertIn("implement", out)
        self.assertIn("TOTAL", out)
        self.assertIn("cache hit:", out)

    def test_empty_usage_explains_absence(self):
        out = progress.render_usage({"entries": [{"role": "implement"}, {"role": "review"}]})
        self.assertIn("no per-spawn usage recorded on 2 entries", out)

    def test_no_entries(self):
        out = progress.render_usage({})
        self.assertIn("no per-spawn usage recorded on 0 entries", out)

    def test_pre_spec_journal_without_agent_labels_has_no_pool_section(self):
        # AC-029: a journal missing pool-label data renders byte-compatibly
        # with today's report -- no pool section, no exception.
        journal = {"entries": [
            {"role": "implement", "usage": {"input_tokens": 11000, "cache_read_input_tokens": 24000, "output_tokens": 3000, "total_cost_usd": 0.21}},
        ]}
        out = progress.render_usage(journal)
        self.assertNotIn("usage by pool:", out)

    def test_pool_section_groups_agents_when_labeled(self):
        journal = {"entries": [
            {"role": "implement", "agent": "claude", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 100, "total_cost_usd": 0.05}},
            {"role": "implement", "agent": "opencode", "usage": {"input_tokens": 2000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 200, "total_cost_usd": 0.0}},
        ]}
        out = progress.render_usage(journal)
        self.assertIn("usage by pool:", out)
        self.assertIn("subscription", out)
        self.assertIn("free", out)
        self.assertIn("claude", out)
        self.assertIn("opencode", out)

    def test_mixed_labeled_unlabeled_still_renders_per_role_breakdown(self):
        journal = {"entries": [
            {"role": "implement", "agent": "claude", "usage": {"input_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 100, "total_cost_usd": 0.05}},
            {"role": "review", "usage": {"input_tokens": 500, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 50, "total_cost_usd": 0.02}},
        ]}
        out = progress.render_usage(journal)
        # Per-role breakdown (unchanged) still reports both roles, agent or not.
        self.assertIn("implement", out)
        self.assertIn("review", out)
        # Pool section only reflects the labeled entry.
        self.assertIn("usage by pool:", out)
        self.assertIn("subscription", out)
        self.assertNotIn("free", out)


class SummarizePoolUsage(unittest.TestCase):
    def _u(self, **kw):
        base = dict(
            input_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            output_tokens=0,
            total_cost_usd=0.0,
        )
        base.update(kw)
        return base

    def test_groups_claude_codex_opencode_by_pool(self):
        journal = {"entries": [
            {"role": "implement", "agent": "claude", "usage": self._u(input_tokens=1000, total_cost_usd=0.1)},
            {"role": "implement", "agent": "codex", "usage": self._u(input_tokens=2000, total_cost_usd=0.2)},
            {"role": "implement", "agent": "opencode", "usage": self._u(input_tokens=3000, total_cost_usd=0.3)},
        ]}
        s = progress.summarize_pool_usage(journal)
        self.assertEqual(s["entries_with_agent_label"], 3)
        self.assertEqual(set(s["pools"]["subscription"]), {"claude", "codex"})
        self.assertEqual(s["pools"]["subscription"]["claude"]["input_tokens"], 1000)
        self.assertEqual(s["pools"]["subscription"]["codex"]["input_tokens"], 2000)
        self.assertEqual(set(s["pools"]["free"]), {"opencode"})
        self.assertEqual(s["pools"]["free"]["opencode"]["input_tokens"], 3000)

    def test_unknown_agent_falls_back_to_api_pool(self):
        journal = {"entries": [
            {"role": "implement", "agent": "some-api-model", "usage": self._u(input_tokens=500, total_cost_usd=0.5)},
        ]}
        s = progress.summarize_pool_usage(journal)
        self.assertEqual(set(s["pools"]["api"]), {"some-api-model"})

    def test_entries_without_agent_label_are_omitted(self):
        # Pre-spec journal: no `agent` key on any entry -> no pool grouping.
        journal = {"entries": [
            {"role": "implement", "usage": self._u(input_tokens=100, total_cost_usd=0.01)},
        ]}
        s = progress.summarize_pool_usage(journal)
        self.assertEqual(s["pools"], {})
        self.assertEqual(s["entries_with_agent_label"], 0)

    def test_mixed_labeled_and_unlabeled_entries_only_count_labeled(self):
        journal = {"entries": [
            {"role": "implement", "agent": "claude", "usage": self._u(input_tokens=100, total_cost_usd=0.01)},
            {"role": "implement", "usage": self._u(input_tokens=999, total_cost_usd=9.99)},  # unlabeled, omitted
        ]}
        s = progress.summarize_pool_usage(journal)
        self.assertEqual(s["entries_with_agent_label"], 1)
        self.assertEqual(s["pools"]["subscription"]["claude"]["spawns"], 1)
        # The unlabeled entry's tokens/cost must not leak into any pool bucket.
        total_spawns = sum(
            d["spawns"] for agents in s["pools"].values() for d in agents.values()
        )
        self.assertEqual(total_spawns, 1)

    def test_entries_without_usage_are_ignored_even_if_labeled(self):
        journal = {"entries": [{"role": "cleanup", "agent": "claude"}]}
        s = progress.summarize_pool_usage(journal)
        self.assertEqual(s["pools"], {})
        self.assertEqual(s["entries_with_agent_label"], 0)


class RenderToolsUsed(unittest.TestCase):
    def test_aggregates_tools_and_skills_across_entries(self):
        journal = {
            "entries": [
                {"role": "implement", "tools_used": ["Bash", "Read", "Edit"]},
                {"role": "review", "tools_used": ["Read", "Grep"]},
                {"role": "implement", "skills_used": ["worktrail-sdd-workflow"]},
            ]
        }
        s = progress.summarize_tools(journal)
        self.assertEqual(s["tools_used"], ["Bash", "Edit", "Grep", "Read"])
        self.assertEqual(s["skills_used"], ["worktrail-sdd-workflow"])

    def test_deduplicates_across_entries(self):
        journal = {
            "entries": [
                {"role": "implement", "tools_used": ["Read", "Bash"]},
                {"role": "fix", "tools_used": ["Read", "Bash", "Write"]},
            ]
        }
        s = progress.summarize_tools(journal)
        self.assertEqual(s["tools_used"], ["Bash", "Read", "Write"])

    def test_render_lists_tools_and_skills(self):
        journal = {
            "entries": [
                {"tools_used": ["Bash", "Read"]},
                {"skills_used": ["foo:bar"]},
            ]
        }
        out = progress.render_tools_used(journal)
        self.assertIn("tools used (2): Bash, Read", out)
        self.assertIn("skills used (1): foo:bar", out)

    def test_render_none_recorded_message(self):
        out = progress.render_tools_used({"entries": []})
        self.assertIn("none recorded", out)

    def test_render_empty_journal(self):
        out = progress.render_tools_used({})
        self.assertIn("none recorded", out)

    def test_handles_missing_or_empty_fields(self):
        journal = {
            "entries": [
                {"role": "implement"},
                {"role": "review", "tools_used": [], "skills_used": None},
            ]
        }
        s = progress.summarize_tools(journal)
        self.assertEqual(s["tools_used"], [])
        self.assertEqual(s["skills_used"], [])


class RenderContextQuality(unittest.TestCase):
    def test_summarize_counts_and_collects_missing(self):
        journal = {
            "entries": [
                {"task": "TASK-001", "role": "implement", "report": {"context_quality": "sufficient"}},
                {"task": "TASK-002", "role": "review", "report": {"context_quality": "too_much"}},
                {
                    "task": "TASK-003",
                    "role": "implement",
                    "report": {
                        "context_quality": "insufficient",
                        "missing_context": ["data-model.md §Orders"],
                    },
                },
            ]
        }
        s = progress.summarize_context_quality(journal)
        self.assertEqual(s["counts"], {"sufficient": 1, "too_much": 1, "insufficient": 1})
        self.assertEqual(s["entries_with_signal"], 3)
        self.assertEqual(
            s["missing"],
            [{"task": "TASK-003", "role": "implement", "items": ["data-model.md §Orders"]}],
        )

    def test_render_lists_counts_too_much_hint_and_missing(self):
        journal = {
            "entries": [
                {"task": "TASK-001", "role": "implement", "report": {"context_quality": "sufficient"}},
                {"task": "TASK-002", "role": "review", "report": {"context_quality": "too_much"}},
                {
                    "task": "TASK-003",
                    "role": "implement",
                    "report": {
                        "context_quality": "insufficient",
                        "missing_context": ["data-model.md §Orders"],
                    },
                },
            ]
        }
        out = progress.render_context_quality(journal)
        self.assertIn("1 sufficient · 1 too_much · 1 insufficient (of 3 reports)", out)
        self.assertIn("too_much context", out)
        self.assertIn("missing context: TASK-003 implement → [data-model.md §Orders]", out)

    def test_render_none_recorded_message(self):
        out = progress.render_context_quality({"entries": []})
        self.assertIn("none recorded", out)

    def test_render_empty_journal(self):
        out = progress.render_context_quality({})
        self.assertIn("none recorded", out)

    def test_handles_missing_or_empty_report_fields(self):
        journal = {
            "entries": [
                {"role": "implement"},
                {"role": "review", "report": {"context_quality": None, "missing_context": []}},
                {"role": "fix", "report": {}},
            ]
        }
        s = progress.summarize_context_quality(journal)
        self.assertEqual(s["counts"], {})
        self.assertEqual(s["entries_with_signal"], 0)
        self.assertEqual(s["missing"], [])


class AppendSafetyNetEvents(unittest.TestCase):
    """`append_safety_net_events` merges group-level safety-net events (e.g. an
    `automerge_preflight_fallback`) into the run journal without disturbing the
    existing `entries`/`spec_id`/`run_id` -- the same journal
    `safety_net_report.py` scans for cross-run aggregation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal_path = Path(self.tmp) / "run-001-feature.json"

    def _write_journal(self, data):
        self.journal_path.write_text(json.dumps(data))

    def test_noop_on_empty_events(self):
        self._write_journal({"spec_id": "001-feature", "entries": []})
        before = self.journal_path.read_text()
        progress.append_safety_net_events(self.journal_path, [])
        self.assertEqual(self.journal_path.read_text(), before)

    def test_noop_when_journal_missing(self):
        # No journal on disk yet -- nothing to attach an event to; must not create one.
        progress.append_safety_net_events(self.journal_path, [{"event": "x"}])
        self.assertFalse(self.journal_path.exists())

    def test_appends_preserving_existing_fields(self):
        self._write_journal({
            "spec_id": "001-feature", "run_id": "run-abc",
            "entries": [{"task": "TASK-001", "role": "implement", "report": {}}],
        })
        event = {"event": "automerge_preflight_fallback", "group": "base",
                  "reason": "gh api failed", "outcome": "queued"}
        progress.append_safety_net_events(self.journal_path, [event])

        data = json.loads(self.journal_path.read_text())
        self.assertEqual(data["spec_id"], "001-feature")
        self.assertEqual(data["run_id"], "run-abc")
        self.assertEqual(len(data["entries"]), 1)  # untouched
        self.assertEqual(data["safety_net_events"], [event])

    def test_second_call_appends_not_overwrites(self):
        self._write_journal({"spec_id": "001-feature", "entries": []})
        progress.append_safety_net_events(self.journal_path, [{"event": "a"}])
        progress.append_safety_net_events(self.journal_path, [{"event": "b"}])

        data = json.loads(self.journal_path.read_text())
        self.assertEqual([e["event"] for e in data["safety_net_events"]], ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

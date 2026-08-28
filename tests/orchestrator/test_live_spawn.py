#!/usr/bin/env python3
"""Tests for LiveSpawn.__call__ (routing-target-selector 4.2).

LiveSpawn no longer resolves an agent/model pair itself: it calls
`dispatch.tier_for()` for the (tier, prefer, independent) row to walk, then
`spawnlib.spawn_agent(tier=, prefer=, exclude_harness=)` -- the single selector
(`runtime.selection.select_cell`) does the rest. Hermetic: `spawnlib.resolve_routing`
and `spawnlib.spawn_agent` are both patched so no real routing.yaml or subprocess
is touched; `dispatch.build_worker_prompt` is patched too so no real spec tree is
required. `GO_AGENT_CAPACITY_CACHE` points at a throwaway file per test so the
selector's capacity reads are hermetic (mirrors tests/orchestrator/test_spawnlib.py).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _target(harness, pool="subscription", api_opt_in=False, auth=None):
    return {"harness": harness, "pool": pool, "api_opt_in": api_opt_in, "auth": auth}


def _routing(targets, tiers, roles=None, purposes=None, default_tier=None):
    """A `resolve_routing()`-shaped dict, mirroring test_spawnlib.py's own
    `_routing()` helper but with `roles`/`purposes` populated too, since
    `LiveSpawn.__call__` (unlike `spawn_agent`) also consults them via
    `dispatch.tier_for()`."""
    return {
        "targets": targets, "tiers": tiers, "roles": roles or {}, "purposes": purposes or {},
        "default_tier": default_tier, "drain": {},
    }


TWO_TARGET_ROUTING = _routing(
    {
        "claude-sub": _target("claude"),
        "codex-sub": _target("codex"),
    },
    {
        "t2-build": {
            "claude-sub": {"model": "sonnet", "effort": None},
            "codex-sub": {"model": "gpt-5.3-codex", "effort": None},
        },
        "t1-deep": {
            "claude-sub": {"model": "opus", "effort": None},
            "codex-sub": {"model": "gpt-5.3-codex", "effort": "high"},
        },
    },
    default_tier="t2-build",
)


class _FakeResult:
    def __init__(self, text="ok"):
        self.text = text
        self.usage = {}
        self.tools_used = []
        self.skills_used = []
        self.paused_s = 0.0
        self.session_id = ""


class LiveSpawnCallTestCase(unittest.TestCase):
    """Shared hermetic fixture: routing/capacity/prompt/spawn_agent all faked."""

    def setUp(self):
        self._cache_dir = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache_dir.name, "capacity.json"
        )
        self._routing_patch = patch.object(
            spawnlib, "resolve_routing", return_value=self.routing()
        )
        self._routing_patch.start()
        self._prompt_patch = patch.object(
            dispatch, "build_worker_prompt", return_value="prompt"
        )
        self._prompt_patch.start()
        self.captured: dict = {}

        def _fake_spawn_agent(prompt, cwd, **kw):
            self.captured.update(kw)
            self.captured["prompt"] = prompt
            self.captured["cwd"] = cwd
            return _FakeResult()

        self._spawn_patch = patch.object(
            spawnlib, "spawn_agent", side_effect=_fake_spawn_agent
        )
        self._spawn_patch.start()

    def tearDown(self):
        self._spawn_patch.stop()
        self._prompt_patch.stop()
        self._routing_patch.stop()
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache_dir.cleanup()

    def routing(self):
        return TWO_TARGET_ROUTING

    def _task(self, **overrides):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        task.update(overrides)
        return task


class TierForWiringTests(LiveSpawnCallTestCase):
    """LiveSpawn.__call__ calls tier_for() then spawn_agent(tier=, prefer=,
    exclude_harness=) -- the AC this task exists for."""

    def test_default_tier_used_when_task_has_no_override(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["tier"], "t2-build")
        self.assertIsNone(self.captured["prefer"])
        self.assertIsNone(self.captured["exclude_harness"])

    def test_explicit_task_tier_overrides_default(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn(dispatch.ROLE_IMPLEMENT, self._task(tier="t1-deep"), Path("/tmp/wt"))
        self.assertEqual(self.captured["tier"], "t1-deep")

    def test_purpose_resolves_tier_ahead_of_complexity(self):
        routing = _routing(
            TWO_TARGET_ROUTING["targets"], TWO_TARGET_ROUTING["tiers"],
            purposes={"security-review": "t1-deep"}, default_tier="t2-build",
        )
        with patch.object(spawnlib, "resolve_routing", return_value=routing):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
            spawn(
                dispatch.ROLE_IMPLEMENT,
                self._task(purpose="security-review", complexity="t2-build"),
                Path("/tmp/wt"),
            )
        self.assertEqual(self.captured["tier"], "t1-deep")

    def test_review_defaults_to_independent_judgment_tier(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn(dispatch.ROLE_REVIEW, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["tier"], "t1-deep")

    def test_roles_config_can_pin_review_to_a_tier_and_prefer(self):
        routing = _routing(
            TWO_TARGET_ROUTING["targets"], TWO_TARGET_ROUTING["tiers"],
            roles={"review": {"tier": "t2-build", "prefer": "codex-sub", "independent": True}},
            default_tier="t2-build",
        )
        with patch.object(spawnlib, "resolve_routing", return_value=routing):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
            spawn(dispatch.ROLE_REVIEW, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["tier"], "t2-build")
        self.assertEqual(self.captured["prefer"], "codex-sub")


class OperatorPreferOverrideTests(LiveSpawnCallTestCase):
    """The constructor's `prefer` (the full-real CLI's --agent) is an
    operator-level override that wins entirely over whatever tier_for()
    resolves for a role -- it never gets silently shadowed by a role config."""

    def test_constructor_prefer_wins_over_role_resolution(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", prefer="codex-sub")
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["prefer"], "codex-sub")

    def test_constructor_prefer_wins_even_over_a_role_prefer(self):
        routing = _routing(
            TWO_TARGET_ROUTING["targets"], TWO_TARGET_ROUTING["tiers"],
            roles={"review": {"tier": "t2-build", "prefer": "claude-sub", "independent": True}},
            default_tier="t2-build",
        )
        with patch.object(spawnlib, "resolve_routing", return_value=routing):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", prefer="codex-sub")
            spawn(dispatch.ROLE_REVIEW, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["prefer"], "codex-sub")


class IndependentReviewExcludesImplementerHarnessTests(LiveSpawnCallTestCase):
    """Locked decision 13.3: an independent judgment role (review, the only one
    that reaches LiveSpawn.__call__) excludes the harness that implemented the
    task -- recorded onto the task dict by the earlier implement/fix spawn."""

    def test_implement_records_served_harness_on_the_task(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task()
        spawn(dispatch.ROLE_IMPLEMENT, task, Path("/tmp/wt"))
        self.assertEqual(task["_served_harness"], "claude")

    def test_fix_also_records_served_harness(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task()
        spawn(dispatch.ROLE_FIX, task, Path("/tmp/wt"))
        self.assertEqual(task["_served_harness"], "claude")

    def test_review_does_not_overwrite_served_harness(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task(_served_harness="claude")
        spawn(dispatch.ROLE_REVIEW, task, Path("/tmp/wt"))
        self.assertEqual(task["_served_harness"], "claude")

    def test_review_excludes_the_recorded_implementer_harness(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task(_served_harness="claude")
        spawn(dispatch.ROLE_REVIEW, task, Path("/tmp/wt"))
        self.assertEqual(self.captured["exclude_harness"], "claude")

    def test_implement_never_excludes_a_harness(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task(_served_harness="claude")
        spawn(dispatch.ROLE_IMPLEMENT, task, Path("/tmp/wt"))
        self.assertIsNone(self.captured["exclude_harness"])

    def test_non_independent_role_review_override_excludes_nothing(self):
        routing = _routing(
            TWO_TARGET_ROUTING["targets"], TWO_TARGET_ROUTING["tiers"],
            roles={"review": {"tier": "t2-build", "independent": False}},
            default_tier="t2-build",
        )
        with patch.object(spawnlib, "resolve_routing", return_value=routing):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
            task = self._task(_served_harness="claude")
            spawn(dispatch.ROLE_REVIEW, task, Path("/tmp/wt"))
        self.assertIsNone(self.captured["exclude_harness"])


class ServedCellJournalRecordTests(LiveSpawnCallTestCase):
    """The served cell (from the SAME authoritative select_cell() walk
    spawn_agent() performs internally, not a guess) is recorded on the
    LiveSpawn instance so the caller can stamp it onto the journal/run record."""

    def test_last_agent_and_last_target_reflect_the_served_cell(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        self.assertIsNone(spawn.last_agent)
        self.assertIsNone(spawn.last_target)
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertEqual(spawn.last_agent, "claude")
        self.assertEqual(spawn.last_target, "claude-sub")

    def test_last_target_updates_on_exclusion_hop(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        task = self._task(_served_harness="claude")
        spawn(dispatch.ROLE_REVIEW, task, Path("/tmp/wt"))
        self.assertEqual(spawn.last_agent, "codex")
        self.assertEqual(spawn.last_target, "codex-sub")

    def test_apply_step_commit_writes_target_alongside_agent(self):
        entries: list = []
        old, new = live._apply_step_commit(
            tasks=[{"id": "TASK-001", "status": "reviewing", "retry_count": 0}],
            entries=entries,
            actives={"TASK-001": {}},
            record_fn=lambda: None,
            task={"id": "TASK-001"},
            role=dispatch.ROLE_IMPLEMENT,
            rep={"task": "TASK-001", "step": "implement", "status": "success"},
            t0=0.0,
            t1=1.0,
            agent="claude",
            target="claude-sub",
        )
        self.assertEqual(entries[-1]["agent"], "claude")
        self.assertEqual(entries[-1]["target"], "claude-sub")

    def test_apply_step_commit_omits_target_when_not_given(self):
        entries: list = []
        live._apply_step_commit(
            tasks=[{"id": "TASK-001", "status": "reviewing", "retry_count": 0}],
            entries=entries,
            actives={},
            record_fn=lambda: None,
            task={"id": "TASK-001"},
            role=dispatch.ROLE_IMPLEMENT,
            rep={"task": "TASK-001", "step": "implement", "status": "success"},
            t0=0.0,
            t1=1.0,
        )
        self.assertNotIn("target", entries[-1])
        self.assertNotIn("agent", entries[-1])


class ReconcileFromJournalRestoresServedHarnessTests(unittest.TestCase):
    """A resumed run replays the journal, not LiveSpawn.__call__ -- the served
    harness recorded on an implement/fix entry must round-trip back onto the
    task dict so an independent review dispatched after resume still excludes
    it, same as within a single continuous process."""

    def test_implement_entry_restores_served_harness(self):
        tasks = [{"id": "TASK-001", "status": "pending", "retry_count": 0}]
        journal = {
            "entries": [
                {
                    "task": "TASK-001", "role": dispatch.ROLE_IMPLEMENT,
                    "report": {"status": "success", "head_sha": "abc123"},
                    "agent": "codex",
                }
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(tasks[0]["_served_harness"], "codex")

    def test_review_entry_does_not_set_served_harness(self):
        tasks = [{"id": "TASK-001", "status": "pending", "retry_count": 0}]
        journal = {
            "entries": [
                {
                    "task": "TASK-001", "role": dispatch.ROLE_IMPLEMENT,
                    "report": {"status": "success", "head_sha": "abc123"},
                    "agent": "codex",
                },
                {
                    "task": "TASK-001", "role": dispatch.ROLE_REVIEW,
                    "report": {"status": "success", "review_status": "PASSED"},
                    "agent": "claude",
                },
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        # The review's own served harness must never overwrite the recorded
        # implementer harness -- only implement/fix entries do.
        self.assertEqual(tasks[0]["_served_harness"], "codex")


class ClaudeVsNonClaudeSpawnShapeTests(LiveSpawnCallTestCase):
    """Lean flags / reviewer system prompt / resume_session_id all key off the
    SERVED cell's harness, resolved from routing -- never a bare `agent` field."""

    def test_claude_cell_gets_lean_flags(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertIn("--strict-mcp-config", self.captured["extra_args"])

    def test_claude_review_gets_appended_system_prompt(self):
        routing = _routing(
            TWO_TARGET_ROUTING["targets"], TWO_TARGET_ROUTING["tiers"],
            roles={"review": {"tier": "t2-build", "prefer": "claude-sub", "independent": True}},
            default_tier="t2-build",
        )
        with patch.object(spawnlib, "resolve_routing", return_value=routing):
            spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
            spawn(dispatch.ROLE_REVIEW, self._task(), Path("/tmp/wt"))
        self.assertIn("--append-system-prompt", self.captured["extra_args"])

    def test_non_claude_cell_gets_no_lean_flags(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", prefer="codex-sub")
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["extra_args"], [])

    def test_non_claude_review_prepends_reviewer_prompt_instead(self):
        task = self._task(_served_harness="claude")
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn(dispatch.ROLE_REVIEW, task, Path("/tmp/wt"))
        # claude excluded -> served cell is codex-sub
        self.assertEqual(self.captured["extra_args"], [])
        self.assertTrue(self.captured["prompt"].startswith(live._REVIEWER_SYSTEM_PROMPT))

    def test_non_claude_cell_drops_resume_session_id(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec", prefer="codex-sub")
        spawn.research_session_id = "sess-123"
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertIsNone(self.captured["resume_session_id"])

    def test_claude_cell_keeps_resume_session_id(self):
        spawn = live.LiveSpawn("spec-001", "docs/specs/001-spec")
        spawn.research_session_id = "sess-123"
        spawn(dispatch.ROLE_IMPLEMENT, self._task(), Path("/tmp/wt"))
        self.assertEqual(self.captured["resume_session_id"], "sess-123")


class DefaultTargetForHarnessTests(unittest.TestCase):
    """DEFAULT_AGENT is a default-TARGET hint now (not a bare harness name):
    `_default_target_for_harness()` resolves the first configured
    `routing.targets` entry whose `harness` matches, so it actually moves a
    cell to the front of a tier row via `select_cell()`'s `prefer=` (which
    only matches target names, never harness names)."""

    def test_resolves_first_target_declaring_the_harness(self):
        with patch.object(spawnlib, "resolve_routing", return_value=TWO_TARGET_ROUTING):
            self.assertEqual(live._default_target_for_harness("codex"), "codex-sub")

    def test_falls_back_to_bare_harness_when_no_target_matches(self):
        with patch.object(spawnlib, "resolve_routing", return_value=TWO_TARGET_ROUTING):
            self.assertEqual(live._default_target_for_harness("opencode"), "opencode")

    def test_falls_back_to_bare_harness_on_resolution_error(self):
        with patch.object(spawnlib, "resolve_routing", side_effect=RuntimeError("no policy")):
            self.assertEqual(live._default_target_for_harness("claude"), "claude")


class LegacyFallbackMachineryRemovedTests(unittest.TestCase):
    """The judgment_pinned/effective_fallback branch, _serving_agent_guess(),
    and the fallback_chain/fallback_agent constructor params are all deleted --
    LiveSpawn's tier/prefer/exclude_harness precedence is the only recovery
    path now (spawn_agent's own within-row re-select handles a served cell
    going unavailable)."""

    def test_serving_agent_guess_no_longer_exists(self):
        self.assertFalse(hasattr(live, "_serving_agent_guess"))

    def test_constructor_rejects_fallback_agent_kwarg(self):
        with self.assertRaises(TypeError):
            live.LiveSpawn("spec-001", "docs/specs/001-spec", fallback_agent="codex")

    def test_constructor_rejects_fallback_chain_kwarg(self):
        with self.assertRaises(TypeError):
            live.LiveSpawn("spec-001", "docs/specs/001-spec", fallback_chain=["codex"])

    def test_constructor_still_accepts_legacy_model_and_role_map_kwargs(self):
        # role_models/role_agents/tier_map/purpose_tier_map/effort/model are kept
        # on the constructor for caller signature compatibility (verify.py's
        # resolve/ci-fix/assembly-resolve spawns and CLI flags still thread them
        # through) even though __call__'s tier_for()/select_cell() resolution
        # never reads them.
        spawn = live.LiveSpawn(
            "spec-001", "docs/specs/001-spec",
            model="sonnet", role_models={"review": "opus"}, role_agents={"review": "claude"},
            tier_map={("hard", "backend"): {}}, purpose_tier_map={"security-review": "t1"},
            effort="high",
        )
        self.assertEqual(spawn.model, "sonnet")

    def test_dispatch_agent_for_no_longer_exists(self):
        self.assertFalse(hasattr(dispatch, "agent_for"))

    def test_default_model_for_agent_no_longer_used_by_live(self):
        with open(live.__file__) as f:
            source = f.read()
        self.assertNotIn("default_model_for_agent", source)

    def test_full_real_cli_no_longer_offers_fallback_chain_flag(self):
        with self.assertRaises(SystemExit):
            live.main(["full-real", "--repo", "/tmp", "--spec", "x", "--fallback-chain", "codex"])


if __name__ == "__main__":
    unittest.main()

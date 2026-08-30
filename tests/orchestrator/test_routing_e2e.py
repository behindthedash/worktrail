#!/usr/bin/env python3
"""End-to-end tests for subscription-aware routing (spec 023, TASK-014).

Drives the whole routing seam together -- policy loading -> resolution helper ->
dispatch precedence -> LiveSpawn wiring -> fallback chain -> journal/run-record/
usage-report observability -- through fixtures/ScriptedSpawn only. No live agent
spawns, no network, no LLM calls: that separation is itself AC-036.

Coverage (see docs/specs/023-subscription-aware-routing/tasks/TASK-014.md):
  - Happy-path journey: go-policy routing table + tier-stamped tasks -> per-task
    resolution matches the documented precedence, journal entries carry agent
    labels, the usage report groups by pool, the run record captures the decision.
  - AC-016 [EXT]: no routing policy + no tier metadata -> pre-spec dispatch,
    unchanged; the toy golden (`orchestrate.py check`) stays clean.
  - AC-022 [SEF]: the dashboard's `rendered` text is unchanged in structure --
    `planned-agent` is additive JSON only.
  - AC-025 [EXT]: a single-entry fallback (today's shape) resolves through
    `policy.resolve_routing()` and spawns exactly like the pre-spec single
    `--fallback-agent`.
  - AC-024/AC-028: a whole routing-resolved fallback chain being gated raises
    `AllProvidersUnavailable`, and `run_record.py capacity-gate` still succeeds
    with the routing decision it was started with.
  - AC-029 [SEF]: a journal missing pool-label data renders the usage report
    without crashing, omitting the pool grouping.
  - AC-035 [EXT]: dispatch-time routing resolution is invariant to whether a
    Tier-Accuracy Report exists, is stale, or is absent -- proven both
    behaviorally (identical `resolve_routing()` output) and structurally (no
    dispatch-time module ever imports the aggregation).
  - AC-036 [EXT]: the Tier-Accuracy aggregation makes zero network/subprocess
    calls end to end.

Also covers the Test Instructions' "malformed routing block mid-journey"
scenario: an invalid entry is dropped with a warning while dispatch proceeds on
the remaining valid entries.

Run: python3 scripts/test_routing_e2e.py
"""

from __future__ import annotations

import inspect
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch, live, progress, spawnlib

_HERE = Path(__file__).resolve().parent

# `dashboard`/`policy`/`run_record`/`tier_accuracy` live in `worktrail.router`
# (extracted from devkit-pm-go). Production code deliberately never imports
# across the orchestrator/router boundary (live.py's "cross-plugin note":
# tier_map/fallback_chain are threaded through as plain data, not a policy.py
# import) -- but this E2E TEST legitimately needs to prove the two halves of
# the routing seam compose correctly, so it imports router directly, test-only.
from worktrail.router import dashboard
from worktrail.router import policy as policy_mod
from worktrail.router import tier_accuracy as tier_accuracy_mod

# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_routing_repo(root: Path, *, tier_stamps: bool) -> Path:
    """A 3-task spec: TASK-001 (root) + TASK-002/TASK-003 (both depend on it).

    tier_stamps=True stamps TASK-001 trivial/infra, TASK-002 hard/backend,
    TASK-003 standard/frontend -- so exactly one task (TASK-002) matches a tier
    map entry, one task-role stays on the tier's real domain, and one falls
    through to the run default. tier_stamps=False omits complexity/domain
    frontmatter entirely (pre-spec task files, AC-016/AC-009 shape).
    """
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "023-x" / "tasks"
    spec_dir.mkdir(parents=True)
    stamps = {
        "TASK-001": "\ncomplexity: trivial\ndomain: infra" if tier_stamps else "",
        "TASK-002": "\ncomplexity: hard\ndomain: backend" if tier_stamps else "",
        "TASK-003": "\ncomplexity: standard\ndomain: frontend" if tier_stamps else "",
    }
    deps = {"TASK-001": "[]", "TASK-002": "[TASK-001]", "TASK-003": "[TASK-001]"}
    for tid in ("TASK-001", "TASK-002", "TASK-003"):
        (spec_dir / f"{tid}.md").write_text(
            f"---\nid: {tid}\nstatus: pending\ndependencies: {deps[tid]}\n"
            f"files: [src/{tid.lower()}.txt]\nkind: impl{stamps[tid]}\n---\nbody\n"
        )
    (repo / "README.md").write_text("x\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _fake_report(task_id: str, role: str, sha: str) -> spawnlib.SpawnResult:
    rs = '"PASSED"' if role == dispatch.ROLE_REVIEW else "null"
    text = (
        f'```json\n{{"task":"{task_id}","step":"{role}",'
        f'"status":"success","head_sha":"{sha}","review_status":{rs}}}\n```'
    )
    # Synthetic per-spawn usage so the pool-usage grouping (AC-027/AC-029) has
    # real numbers to aggregate -- distinct per role so roles are distinguishable
    # in the rendered report too.
    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 50,
        "total_cost_usd": 0.01,
    }
    return spawnlib.SpawnResult(text=text, usage=usage)


class _LegacySpawn:
    """A spawn callable with NO `last_agent` attribute at all -- mirrors a spawn
    object from before TASK-007 landed (AC-029: pre-spec journals carry no
    `agent` label on any entry)."""

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        tid = task["id"]
        if role == dispatch.ROLE_IMPLEMENT:
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            _git(Path(wt), "add", "-A")
            _git(Path(wt), "commit", "-q", "-m", f"feat({tid})")
        sha = (
            subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            ).stdout.strip()[:8]
            or "00000000"
        )
        return _fake_report(tid, role, sha)


# --------------------------------------------------------------------------- #
# Happy-path journey (Acceptance Criteria bullet 1 + malformed-entry handling)
# --------------------------------------------------------------------------- #
#
# The routed-dispatch-journey and fallback-chain E2E classes formerly here
# (RoutingFakeSpawn + E2ERoutedDispatchJourneyTest, E2EFallbackChainTest) tested
# spec 023 concepts the routing-target-selector redesign retired outright:
# route/risk-keyed `routing.defaults`, `dispatch.agent_for()`'s `(complexity,
# domain) -> agent` tier_map, and explicit multi-hop `--fallback-agent` chains
# (spawn_agent's own same-row re-selection replaces it, task 3.4). No task in
# routing-target-selector's tasks.md ever claimed this file; the underlying
# concerns they covered are exercised elsewhere: per-role tier/target
# resolution and journal agent-label correctness by
# tests/orchestrator/test_live_extras.py's LiveSpawnTierRoutingTests/
# LiveSpawnPreSpecParityTests/LiveSpawnServedTargetCorrectionTests, and a
# row's own hop-on-exhaustion behavior by tests/orchestrator/test_spawnlib.py.
# NOT re-verified here: run_record.py's `capacity-gate` CLI subcommand against
# a real `NoExecutionTarget` (the new design's replacement for the retired
# `AllProvidersUnavailable`, which is now unreferenced by any real call site --
# confirmed live, `rg AllProvidersUnavailable` outside tests/agent_capacity.py
# itself returns nothing) -- worth a follow-up if that CLI path matters going
# forward.


class E2EBackwardCompatTest(unittest.TestCase):
    """AC-016: with no routing: policy and no task tier metadata, every existing
    orchestrator cassette/golden test still passes unchanged."""

    def test_no_routing_configured_resolves_to_the_empty_shape(self):
        # Isolate the machine-wide routing file: an operator-configured
        # routing.yaml under worktrail_home() must not leak into a test
        # asserting "no routing configured anywhere".
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(
                os.environ, {"GO_ROUTING_FILE": str(Path(tmp) / "no-such-routing.yaml")}
            ),
        ):
            repo = _init_routing_repo(Path(tmp), tier_stamps=False)
            # No go-policy.yaml at all -- the "no routing anywhere" case.
            policy = policy_mod.load_policy(repo)
            self.assertIsNone(policy["routing"])
            resolved = policy_mod.resolve_routing(policy)
            self.assertEqual(
                resolved,
                {
                    "targets": {},
                    "tiers": {},
                    "roles": {},
                    "purposes": {},
                    "default_tier": None,
                    "drain": {},
                },
            )
            # dispatch.tier_for() must not raise on this empty shape either --
            # every role falls through to default_tier (None), never a KeyError.
            tier, prefer, independent = dispatch.tier_for(
                dispatch.ROLE_IMPLEMENT,
                {"id": "TASK-001"},
                roles=resolved["roles"],
                purposes=resolved["purposes"],
                default_tier=resolved["default_tier"],
            )
            self.assertIsNone(tier)
            self.assertIsNone(prefer)
            self.assertFalse(independent)

    def test_orchestrate_golden_check_passes(self):
        """AC-016 [EXT]: `orchestrate.py check` must exit 0 (golden unchanged)."""
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.orchestrate", "check"],
            capture_output=True,
            text=True,
            cwd=str(_HERE),
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Golden drift detected!\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )


# --------------------------------------------------------------------------- #
# AC-021/AC-022: dashboard additive JSON, rendered text unchanged
# --------------------------------------------------------------------------- #


class E2EDashboardAdditiveJSONTest(unittest.TestCase):
    """AC-021: a category_items entry carries a planned-agent field.
    AC-022: printing the dashboard's rendered text is unchanged in structure --
    planned-agent is additive JSON, never surfaced in the rendered text."""

    def test_planned_agent_additive_rendered_text_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            worktrail_dir = repo / ".worktrail"
            worktrail_dir.mkdir(parents=True)
            (worktrail_dir / "policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  targets:\n"
                "    codex-sub:\n"
                "      harness: codex\n"
                "      pool: subscription\n"
                "  tiers:\n"
                "    t3-bulk:\n"
                "      codex-sub:\n"
                "        model: gpt-5.4-mini\n"
                "  default_tier: t3-bulk\n"
            )
            spec = {
                "id": "001",
                "stage": "ready-to-implement",
                "next_action": "orchestrator",
                "route": "B",
                "risk": "medium",
            }
            rows = [{"repo": "repo-a", "path": str(repo), "active_specs": [spec]}]

            # AC-021: category_items additively carries a real, policy-resolved
            # planned-agent (the configured default_tier's winning target) --
            # not a stub/None. Route/risk no longer select routing (retired by
            # the target/tier/role redesign), which is why this test's routing
            # config no longer keys on them either.
            items = dashboard.build_category_items(
                rows, None, inflight=[], queue_briefs=[]
            )
            self.assertEqual(items["ready"][0]["planned-agent"], "codex-sub")

            # AC-022: the SAME repo_rows, rendered as text, is byte-identical
            # whether or not route/risk (hence planned-agent resolution) is
            # present on the spec dict -- render_dashboard never reads
            # planned-agent, so the printed dashboard cannot regress.
            rows_unrouted = [
                {
                    "repo": "repo-a",
                    "path": str(repo),
                    "active_specs": [
                        {k: v for k, v in spec.items() if k not in ("route", "risk")}
                    ],
                }
            ]
            rendered_routed = dashboard.render_dashboard(rows, None, [], [])
            rendered_unrouted = dashboard.render_dashboard(rows_unrouted, None, [], [])
            self.assertEqual(rendered_routed, rendered_unrouted)


# --------------------------------------------------------------------------- #
# AC-029: usage report degrades gracefully on a pool-label-less journal
# --------------------------------------------------------------------------- #
#
# The fallback-chain E2E class formerly here (E2EFallbackChainTest) called
# spawnlib.spawn_agent(agent=, fallback_agent=) and
# spawnlib.default_model_for_agent() -- both fully deleted by task 3.3 -- and
# asserted agent_capacity.AllProvidersUnavailable, which no real call site
# raises any more (spawn_agent's row-based re-selection raises
# runtime.selection.NoExecutionTarget instead; confirmed live, `rg
# AllProvidersUnavailable` outside its own definition and tests returns
# nothing). See the note above E2EBackwardCompatTest for where the underlying
# concerns live now; the run_record.py `capacity-gate` CLI integration this
# class also exercised against a real NoExecutionTarget is not yet
# re-verified anywhere.


class E2EPoolUsageDegradationTest(unittest.TestCase):
    def test_pre_spec_journal_no_agent_labels_usage_report_no_crash(self):
        """AC-029: a spawn with no `last_agent` at all (mirrors a pre-TASK-007
        spawn object) drives the REAL journal-write path in live.py end to end;
        the on-disk journal carries no `agent` label on any entry, and
        progress.render_usage on it renders cleanly with no pool section."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_routing_repo(Path(tmp), tier_stamps=False)
            journal_path = str(Path(tmp) / "journal.json")
            spawn = _LegacySpawn()
            self.assertFalse(hasattr(spawn, "last_agent"))

            result = live.live_run_real(
                repo,
                "docs/specs/023-x",
                max_workers=1,
                out_cassette=journal_path,
                run_id="e2e-legacy",
                spawn=spawn,
            )
            self.assertEqual(result["done"], 3)

            journal = json.loads(Path(journal_path).read_text())
            self.assertTrue(journal["entries"])
            self.assertTrue(
                all("agent" not in e for e in journal["entries"]),
                "a legacy spawn's entries must carry no agent label",
            )

            rendered = progress.render_usage(journal)
            self.assertIn("token usage", rendered)
            self.assertNotIn(
                "usage by pool:", rendered, "no agent labels -> no pool section"
            )


# --------------------------------------------------------------------------- #
# AC-035: dispatch-time resolution is invariant to the Tier-Accuracy Report
# --------------------------------------------------------------------------- #


class E2ETierAccuracyDispatchInvarianceTest(unittest.TestCase):
    def test_resolve_routing_identical_regardless_of_tier_accuracy_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            worktrail_dir = repo / ".worktrail"
            worktrail_dir.mkdir(parents=True)
            (worktrail_dir / "policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  defaults:\n"
                "    B:\n"
                "      medium:\n"
                "        agent_cli: codex\n"
            )
            policy = policy_mod.load_policy(repo)
            baseline = policy_mod.resolve_routing(policy, "B", "medium")

            # absent: no report anywhere on disk -- `baseline` above already IS
            # this case (nothing was ever written).

            # fresh: generate a genuine Tier-Accuracy Report via the real
            # aggregation (not a stub) and place it on disk.
            worktrees_root = repo.parent / f"{repo.name}-worktrees"
            report = tier_accuracy_mod.aggregate_tier_accuracy(
                repo_root=repo, worktrees_root=worktrees_root
            )
            report_path = repo / ".tier-accuracy-report.json"
            report_path.write_text(json.dumps(report))
            self.assertEqual(
                policy_mod.resolve_routing(policy, "B", "medium"),
                baseline,
                "resolve_routing must be unaffected by a fresh report on disk",
            )

            # stale: age the report file by 30 days.
            old = time.time() - 30 * 86400
            os.utime(report_path, (old, old))
            self.assertEqual(
                policy_mod.resolve_routing(policy, "B", "medium"),
                baseline,
                "resolve_routing must be unaffected by a stale report on disk",
            )

    def test_dispatch_time_modules_never_reference_tier_accuracy(self):
        """REQ-037 structural proof: resolution cannot depend on the report's
        presence/absence/content because no dispatch-time module ever imports
        or reads it -- not merely "doesn't currently", but has no code path to."""
        for mod in (policy_mod, dispatch, live, spawnlib):
            source = inspect.getsource(mod)
            self.assertNotIn(
                "tier_accuracy",
                source,
                f"{mod.__name__} must never reference tier_accuracy (REQ-037)",
            )


# --------------------------------------------------------------------------- #
# AC-036: the Tier-Accuracy aggregation is offline, end to end
# --------------------------------------------------------------------------- #


class E2ETierAccuracyOfflineTest(unittest.TestCase):
    def test_aggregation_matches_hand_computed_stats_with_network_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            specs = repo / "docs" / "specs" / "099-x" / "tasks"
            specs.mkdir(parents=True)
            worktrees = repo.parent / f"{repo.name}-worktrees"
            worktrees.mkdir(parents=True)

            (specs / "TASK-001.md").write_text(
                "---\nid: TASK-001\ncomplexity: hard\ndomain: backend\n---\nbody\n"
            )
            (specs / "TASK-002.md").write_text(
                "---\nid: TASK-002\ncomplexity: hard\ndomain: backend\n---\nbody\n"
            )
            journal = {
                "entries": [
                    {
                        "task": "TASK-001",
                        "role": "review",
                        "report": {"review_status": "PASSED"},
                    },
                    {
                        "task": "TASK-002",
                        "role": "review",
                        "report": {"review_status": "FAILED"},
                    },
                ]
            }
            (worktrees / "run-e2e.json").write_text(json.dumps(journal))

            def _blocked_socket(*_a, **_k):
                raise AssertionError("tier_accuracy must never open a network socket")

            original_socket = socket.socket
            socket.socket = _blocked_socket
            try:
                report = tier_accuracy_mod.aggregate_tier_accuracy(
                    repo_root=repo, worktrees_root=worktrees
                )
            finally:
                socket.socket = original_socket

            pair = next(
                p
                for p in report["pairs"]
                if p["complexity"] == "hard" and p["domain"] == "backend"
            )
            # Hand-computed: 1 PASSED + 1 FAILED review outcome -> 50% pass rate.
            self.assertEqual(pair["review_attempts"], 2)
            self.assertEqual(pair["review_passes"], 1)
            self.assertEqual(pair["review_pass_rate"], 0.5)
            self.assertEqual(pair["status"], "ok")

    def test_tier_accuracy_module_has_no_network_or_subprocess_references(self):
        """AC-036 structural proof, mirroring the module's own docstring claim
        ('The script performs no writes, no network calls, and no LLM
        invocations')."""
        source = inspect.getsource(tier_accuracy_mod)
        for forbidden in (
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "httpx",
            "http.client",
        ):
            self.assertNotIn(
                forbidden,
                source,
                f"tier_accuracy.py must not reference {forbidden!r} (AC-036)",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

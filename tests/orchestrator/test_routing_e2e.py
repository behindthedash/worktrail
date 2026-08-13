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

import datetime
import inspect
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import agent_capacity  # noqa: E402
from worktrail.orchestrator import dispatch  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import progress  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402

_HERE = Path(__file__).resolve().parent

# `dashboard`/`policy`/`run_record`/`tier_accuracy` live in `worktrail.router`
# (extracted from devkit-pm-go). Production code deliberately never imports
# across the orchestrator/router boundary (live.py's "cross-plugin note":
# tier_map/fallback_chain are threaded through as plain data, not a policy.py
# import) -- but this E2E TEST legitimately needs to prove the two halves of
# the routing seam compose correctly, so it imports router directly, test-only.
from worktrail.router import dashboard
from worktrail.router import policy as policy_mod
from worktrail.router import run_record as run_record_mod
from worktrail.router import tier_accuracy as tier_accuracy_mod

_Proc = namedtuple("_Proc", "returncode stdout stderr")


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
        "input_tokens": 100, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0, "output_tokens": 50,
        "total_cost_usd": 0.01,
    }
    return spawnlib.SpawnResult(text=text, usage=usage)


class RoutingFakeSpawn:
    """Resolves every spawn through the REAL `dispatch.agent_for()` precedence
    function -- the exact function `LiveSpawn.__call__` calls (TASK-007) -- then
    makes a real git commit and returns a valid report-back, without a real
    subprocess. `last_agent` is set every call, mirroring `LiveSpawn.last_agent`
    (read by live.py's journal-entry write sites, REQ-027) so the journal's
    `agent` label reflects genuine precedence resolution, not a hardcoded value.
    """

    def __init__(self, default_agent="claude", role_agents=None, tier_map=None, fail_task=None):
        self.agent = default_agent
        self.role_agents = role_agents or {}
        self.tier_map = tier_map or {}
        self.fail_task = fail_task
        self.calls: list[tuple[str, str, str]] = []  # (task_id, role, resolved_agent)
        self.last_agent: str | None = None

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        tid = task["id"]
        resolved = dispatch.agent_for(
            role, task,
            reviewer_agent=self.agent, default_agent=self.agent,
            role_agent_map=self.role_agents, tier_map=self.tier_map,
        )
        agent = resolved["agent_cli"] or self.agent
        self.last_agent = agent
        self.calls.append((tid, role, agent))

        if role in (dispatch.ROLE_IMPLEMENT, dispatch.ROLE_FIX):
            if tid == self.fail_task:
                return spawnlib.SpawnResult(
                    text=f'```json\n{{"task":"{tid}","step":"{role}","status":"failed"}}\n```',
                    usage={},
                )
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid}\n")
            _git(Path(wt), "add", "-A")
            _git(Path(wt), "commit", "-q", "-m", f"feat({tid})")
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()[:8] or "00000000"
        return _fake_report(tid, role, sha)


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
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()[:8] or "00000000"
        return _fake_report(tid, role, sha)


def _gate_capacity_cache(test: unittest.TestCase) -> None:
    """Point GO_AGENT_CAPACITY_CACHE at a fresh tempdir for the duration of one
    test, restoring the prior value afterward (mirrors test_spawnlib.py's
    FallbackChain fixture)."""
    cache_dir = tempfile.TemporaryDirectory()
    old = os.environ.get("GO_AGENT_CAPACITY_CACHE")
    os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(cache_dir.name, "capacity.json")

    def _restore():
        if old is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = old
        cache_dir.cleanup()

    test.addCleanup(_restore)


# --------------------------------------------------------------------------- #
# Happy-path journey (Acceptance Criteria bullet 1 + malformed-entry handling)
# --------------------------------------------------------------------------- #

class E2ERoutedDispatchJourneyTest(unittest.TestCase):
    """A go-policy routing table + tier-stamped tasks -> per-task resolution
    matches the documented precedence, journal entries carry agent labels, the
    usage report groups by pool, the run record captures the decision."""

    def test_full_journey_resolution_journal_usage_run_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_routing_repo(Path(tmp), tier_stamps=True)
            (repo / "docs" / "specs" / "go-policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  roles:\n"
                "    review: opencode\n"
                "  tiers:\n"
                "    hard/backend:\n"
                "      agent: codex\n"
            )
            policy = policy_mod.load_policy(repo)
            resolved = policy_mod.resolve_routing(policy, "B", "medium")
            self.assertEqual(resolved["agent_cli"], "claude")
            self.assertEqual(resolved["roles"], {"review": {"agent_cli": "opencode", "agent_model": None, "effort": None}})

            # `routing.tiers` now validates to the `(complexity, domain) -> agent`
            # shape documented in
            # docs/specs/023-subscription-aware-routing/contracts/routing-policy-schema.md
            # (fixed by TASK-CHG-001, wired to dispatch by TASK-CHG-002); drive it
            # from the real `go-policy.yaml` block above via `resolve_tier_map()`
            # rather than hand-building the dict.
            tier_map = policy_mod.resolve_tier_map(policy)
            self.assertEqual(tier_map, {("hard", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": None}})

            spawn = RoutingFakeSpawn(
                default_agent=resolved["agent_cli"],
                role_agents=resolved["roles"],
                tier_map=tier_map,
            )
            journal_path = str(Path(tmp) / "journal.json")
            result = live.live_run_real(
                repo, "docs/specs/023-x", max_workers=1,
                out_cassette=journal_path, run_id="e2e-routing", spawn=spawn,
            )

            self.assertEqual(result["done"], 3, f"all 3 tasks should reach done; tasks={result['tasks']}")

            # Per-task resolution matches the documented precedence: tier match
            # wins for implement on the ONE task whose (complexity, domain)
            # matches (TASK-002); the role override always wins for review
            # regardless of tier; no-match tasks fall through to the run
            # default. `cleanup` is deliberately absent from both `spawn.calls`
            # and the journal's `agent` label: live.py's fan-out loop runs
            # cleanup entirely in Python ("Deterministic cleanup (#14): status
            # write-back + commit, no spawn" -- live.py ~line 1901, pre-dating
            # spec 023) and never calls `spawn()`/dispatch.agent_for for it, so
            # dispatch.agent_for's own "implement/fix/cleanup" precedence
            # table (correct for the pure function's contract) is exercised by
            # this fixture only for implement/review, matching what the real
            # fan-out loop actually dispatches.
            expected = {
                ("TASK-001", "implement"): "claude", ("TASK-001", "review"): "opencode",
                ("TASK-002", "implement"): "codex", ("TASK-002", "review"): "opencode",
                ("TASK-003", "implement"): "claude", ("TASK-003", "review"): "opencode",
            }
            actual = {(tid, role): agent for tid, role, agent in spawn.calls}
            self.assertEqual(actual, expected)

            # Journal entries carry the resolved agent label on spawned roles
            # (REQ-027/AC-026); the deterministic cleanup entries carry none.
            journal = json.loads(Path(journal_path).read_text())
            self.assertTrue(journal["entries"])
            for entry in journal["entries"]:
                key = (entry["task"], entry["role"])
                if entry["role"] == dispatch.ROLE_CLEANUP:
                    self.assertNotIn("agent", entry, f"cleanup entry {key} must carry no agent label")
                    continue
                self.assertEqual(
                    entry.get("agent"), expected[key],
                    f"journal entry for {key} should carry agent label {expected[key]!r}",
                )

            # Usage report groups by pool (AC-027): claude/codex -> subscription,
            # opencode -> free.
            rendered = progress.render_usage(journal)
            self.assertIn("usage by pool:", rendered)
            pool_usage = progress.summarize_pool_usage(journal)
            self.assertEqual(set(pool_usage["pools"]["subscription"]), {"claude", "codex"})
            self.assertEqual(set(pool_usage["pools"]["free"]), {"opencode"})

            # Run record captures the resolved routing decision (AC-020),
            # composing TASK-001's resolve_routing() output directly with
            # TASK-008's run_record.py consumer.
            with tempfile.TemporaryDirectory() as run_dir:
                out = io.StringIO()
                with mock.patch("sys.stdout", out):
                    rc = run_record_mod.main([
                        "start", "--repo", str(repo), "--request", "routed dispatch",
                        "--route", "B", "--risk", "medium", "--dir", run_dir,
                        "--routing-decision", json.dumps(resolved),
                    ])
                self.assertEqual(rc, 0)
                start_res = json.loads(out.getvalue())
                record = run_record_mod._load(Path(start_res["path"]))
                self.assertEqual(record["routing_decision"], resolved)

    def test_malformed_routing_entry_dropped_dispatch_proceeds(self):
        """Test Instructions #2: a malformed entry mid-journey -> warning,
        dispatch proceeds on the remaining valid entries."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_routing_repo(Path(tmp), tier_stamps=False)
            (repo / "docs" / "specs" / "go-policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  defaults:\n"
                "    B:\n"
                "      medium:\n"
                "        agent_cli: not-a-real-agent\n"  # malformed
                "  roles:\n"
                "    review: opencode\n"  # valid, sits alongside the malformed entry
            )
            policy = policy_mod.load_policy(repo)
            self.assertTrue(
                any("routing.defaults" in w for w in policy["_meta"]["warnings"]),
                f"expected a routing.defaults warning; got {policy['_meta']['warnings']}",
            )
            resolved = policy_mod.resolve_routing(policy, "B", "medium")
            # The malformed (route, risk) entry is dropped -> falls back to the
            # flat agent_cli default; the valid roles entry still applies.
            self.assertEqual(resolved["agent_cli"], "claude")
            self.assertEqual(resolved["roles"]["review"]["agent_cli"], "opencode")

            spawn = RoutingFakeSpawn(default_agent=resolved["agent_cli"], role_agents=resolved["roles"])
            journal_path = str(Path(tmp) / "journal.json")
            result = live.live_run_real(
                repo, "docs/specs/023-x", max_workers=1,
                out_cassette=journal_path, run_id="e2e-malformed", spawn=spawn,
            )
            self.assertEqual(result["done"], 3, "dispatch must proceed on the valid entries")


# --------------------------------------------------------------------------- #
# AC-016: backward compatibility
# --------------------------------------------------------------------------- #

class E2EBackwardCompatTest(unittest.TestCase):
    """AC-016: with no routing: policy and no task tier metadata, every existing
    orchestrator cassette/golden test still passes unchanged."""

    def test_no_routing_no_tier_matches_pre_spec_dispatch(self):
        # Isolate the machine-wide routing file: an operator-configured
        # routing.yaml under worktrail_home() (e.g. a claude/codex/opencode fallback chain) must
        # not leak into a test asserting "no routing configured anywhere".
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ,
                                {"GO_ROUTING_FILE": str(Path(tmp) / "no-such-routing.yaml")}):
            repo = _init_routing_repo(Path(tmp), tier_stamps=False)
            # No go-policy.yaml at all -- the "no routing anywhere" case.
            policy = policy_mod.load_policy(repo)
            self.assertIsNone(policy["routing"])
            resolved = policy_mod.resolve_routing(policy, "B", "medium")
            self.assertEqual(resolved, {
                "agent_cli": None, "agent_model": None, "roles": {}, "fallback": [],
                "purpose_tiers": {},
            })

            spawn = RoutingFakeSpawn(default_agent="claude")  # no role_agents/tier_map at all
            journal_path = str(Path(tmp) / "journal.json")
            result = live.live_run_real(
                repo, "docs/specs/023-x", max_workers=1,
                out_cassette=journal_path, run_id="e2e-backcompat", spawn=spawn,
            )
            self.assertEqual(result["done"], 3)
            # Every role of every task resolves to exactly the run default --
            # byte-identical to pre-spec dispatch (REQ-016).
            self.assertTrue(all(agent == "claude" for _, _, agent in spawn.calls), spawn.calls)

    def test_orchestrate_golden_check_passes(self):
        """AC-016 [EXT]: `orchestrate.py check` must exit 0 (golden unchanged)."""
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.orchestrate", "check"],
            capture_output=True, text=True, cwd=str(_HERE), timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
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
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  defaults:\n"
                "    B:\n"
                "      medium:\n"
                "        agent_cli: codex\n"
            )
            spec = {
                "id": "001", "stage": "ready-to-implement", "next_action": "orchestrator",
                "route": "B", "risk": "medium",
            }
            rows = [{"repo": "repo-a", "path": str(repo), "active_specs": [spec]}]

            # AC-021: category_items additively carries a real, policy-resolved
            # planned-agent -- not a stub/None.
            items = dashboard.build_category_items(rows, None, inflight=[], queue_briefs=[])
            self.assertEqual(items["ready"][0]["planned-agent"], "codex")

            # AC-022: the SAME repo_rows, rendered as text, is byte-identical
            # whether or not route/risk (hence planned-agent resolution) is
            # present on the spec dict -- render_dashboard never reads
            # planned-agent, so the printed dashboard cannot regress.
            rows_unrouted = [{
                "repo": "repo-a", "path": str(repo),
                "active_specs": [{k: v for k, v in spec.items() if k not in ("route", "risk")}],
            }]
            rendered_routed = dashboard.render_dashboard(rows, None, [], [])
            rendered_unrouted = dashboard.render_dashboard(rows_unrouted, None, [], [])
            self.assertEqual(rendered_routed, rendered_unrouted)


# --------------------------------------------------------------------------- #
# AC-025/AC-024/AC-028: fallback chain (legacy single entry + full exhaustion)
# --------------------------------------------------------------------------- #

class E2EFallbackChainTest(unittest.TestCase):
    def setUp(self):
        _gate_capacity_cache(self)

    def test_single_entry_fallback_from_policy_resolves_and_spawns(self):
        """AC-025: a repo with only the legacy flat fallback_agent_cli key (no
        routing.fallback list) resolves through policy.resolve_routing() to a
        single-entry chain -- today's shape -- and spawn_agent walks exactly
        that one hop when the primary is gated, unchanged from pre-spec."""
        # Isolate the machine-wide routing file: this test asserts the LEGACY
        # single-entry shape specifically, which only holds when no machine-wide
        # fallback chain (e.g. an operator's real machine-wide routing.yaml) is in play.
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ,
                                {"GO_ROUTING_FILE": str(Path(tmp) / "no-such-routing.yaml")}):
            repo = Path(tmp) / "repo"
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
                "agent_cli: claude\nfallback_agent_cli: opencode\n"
            )
            policy = policy_mod.load_policy(repo)
            resolved = policy_mod.resolve_routing(policy, "B", "medium")
            self.assertEqual(resolved["fallback"], [{"agent_cli": "opencode", "agent_model": None}])

            agent_capacity.record(
                "claude", spawnlib.default_model_for_agent("claude"),
                outcome="unavailable", failure_class="transport",
                retry_after=datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(seconds=60),
            )
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                return _Proc(0, json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n", "")

            original_run = spawnlib.subprocess.run
            spawnlib.subprocess.run = fake_run
            try:
                out = spawnlib.spawn_agent(
                    "prompt", "/tmp", agent="claude",
                    fallback_agent=[e["agent_cli"] for e in resolved["fallback"]],
                )
            finally:
                spawnlib.subprocess.run = original_run

            self.assertEqual(out.text, "done")
            self.assertEqual(len(calls), 1)
            self.assertIn("opencode", calls[0])

    def test_whole_chain_gated_raises_and_capacity_gate_recorded(self):
        """AC-024/AC-028 re-verified end to end: exhausting every hop of a
        routing-resolved fallback chain raises AllProvidersUnavailable naming
        every provider, and `run_record.py capacity-gate` still succeeds
        against the run record the routing decision was started with."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
                "agent_cli: claude\n"
                "routing:\n"
                "  fallback:\n"
                "    - codex\n"
                "    - opencode\n"
            )
            policy = policy_mod.load_policy(repo)
            resolved = policy_mod.resolve_routing(policy, "B", "medium")
            self.assertEqual([e["agent_cli"] for e in resolved["fallback"]], ["codex", "opencode"])

            now = datetime.datetime.now(datetime.timezone.utc)
            for agent in ("claude", "codex", "opencode"):
                agent_capacity.record(
                    agent, spawnlib.default_model_for_agent(agent),
                    outcome="unavailable", failure_class="transport",
                    retry_after=now + datetime.timedelta(seconds=60),
                )

            with self.assertRaises(agent_capacity.AllProvidersUnavailable) as ctx:
                spawnlib.spawn_agent(
                    "prompt", "/tmp", agent="claude",
                    fallback_agent=[e["agent_cli"] for e in resolved["fallback"]],
                )

            with tempfile.TemporaryDirectory() as run_dir:
                out = io.StringIO()
                with mock.patch("sys.stdout", out):
                    rc = run_record_mod.main([
                        "start", "--repo", str(repo), "--request", "routed dispatch",
                        "--route", "B", "--risk", "medium", "--dir", run_dir,
                        "--routing-decision", json.dumps(resolved),
                    ])
                self.assertEqual(rc, 0)
                start_res = json.loads(out.getvalue())

                out2 = io.StringIO()
                with mock.patch("sys.stdout", out2):
                    rc2 = run_record_mod.main([
                        "capacity-gate", start_res["path"],
                        *[f"--provider={p}" for p in ctx.exception.providers],
                        "--failure-class", "transport",
                    ])
                self.assertEqual(rc2, 0)

                record = run_record_mod._load(Path(start_res["path"]))
                self.assertEqual(record["capacity_gate"]["status"], "blocked")
                self.assertEqual(set(record["capacity_gate"]["providers"]), set(ctx.exception.providers))
                self.assertEqual(record["routing_decision"], resolved)


# --------------------------------------------------------------------------- #
# AC-029: usage report degrades gracefully on a pool-label-less journal
# --------------------------------------------------------------------------- #

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
                repo, "docs/specs/023-x", max_workers=1,
                out_cassette=journal_path, run_id="e2e-legacy", spawn=spawn,
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
            self.assertNotIn("usage by pool:", rendered, "no agent labels -> no pool section")


# --------------------------------------------------------------------------- #
# AC-035: dispatch-time resolution is invariant to the Tier-Accuracy Report
# --------------------------------------------------------------------------- #

class E2ETierAccuracyDispatchInvarianceTest(unittest.TestCase):
    def test_resolve_routing_identical_regardless_of_tier_accuracy_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
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
                repo_root=repo, worktrees_root=worktrees_root)
            report_path = repo / ".tier-accuracy-report.json"
            report_path.write_text(json.dumps(report))
            self.assertEqual(
                policy_mod.resolve_routing(policy, "B", "medium"), baseline,
                "resolve_routing must be unaffected by a fresh report on disk",
            )

            # stale: age the report file by 30 days.
            old = time.time() - 30 * 86400
            os.utime(report_path, (old, old))
            self.assertEqual(
                policy_mod.resolve_routing(policy, "B", "medium"), baseline,
                "resolve_routing must be unaffected by a stale report on disk",
            )

    def test_dispatch_time_modules_never_reference_tier_accuracy(self):
        """REQ-037 structural proof: resolution cannot depend on the report's
        presence/absence/content because no dispatch-time module ever imports
        or reads it -- not merely "doesn't currently", but has no code path to."""
        for mod in (policy_mod, dispatch, live, spawnlib):
            source = inspect.getsource(mod)
            self.assertNotIn(
                "tier_accuracy", source,
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
                    {"task": "TASK-001", "role": "review", "report": {"review_status": "PASSED"}},
                    {"task": "TASK-002", "role": "review", "report": {"review_status": "FAILED"}},
                ]
            }
            (worktrees / "run-e2e.json").write_text(json.dumps(journal))

            def _blocked_socket(*_a, **_k):
                raise AssertionError("tier_accuracy must never open a network socket")

            original_socket = socket.socket
            socket.socket = _blocked_socket
            try:
                report = tier_accuracy_mod.aggregate_tier_accuracy(
                    repo_root=repo, worktrees_root=worktrees)
            finally:
                socket.socket = original_socket

            pair = next(
                p for p in report["pairs"]
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
        for forbidden in ("subprocess", "socket", "urllib", "requests", "httpx", "http.client"):
            self.assertNotIn(
                forbidden, source,
                f"tier_accuracy.py must not reference {forbidden!r} (AC-036)",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for policy.py. Run: python3 test_policy.py"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.policy import (
    DEFAULTS, _json_safe, _validate_routing_purpose_tiers,
    _validate_routing_tiers, automerge_eligible,
    automerge_labels, detect_external_automerge, load_policy,
    merge_method_for_branch, parse_policy_yaml,
    resolve_routing, resolve_tier_map,
)


def _repo_with(policy_text):
    tmp = tempfile.mkdtemp()
    d = Path(tmp) / "docs" / "specs"
    d.mkdir(parents=True)
    (d / "go-policy.yaml").write_text(policy_text)
    return Path(tmp)


def _repo_with_workflow(files, policy_text=None):
    """files: dict of workflow filename -> file content, written under .github/workflows/."""
    tmp = tempfile.mkdtemp()
    wf_dir = Path(tmp) / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for name, content in files.items():
        (wf_dir / name).write_text(content)
    if policy_text is not None:
        specs = Path(tmp) / "docs" / "specs"
        specs.mkdir(parents=True)
        (specs / "go-policy.yaml").write_text(policy_text)
    return Path(tmp)


class TestDefaults(unittest.TestCase):
    def test_missing_policy_file_yields_safe_defaults(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        self.assertFalse(pol["automerge"]["enabled"])
        self.assertEqual(pol["automerge"]["max_risk"], "low")
        self.assertIsNone(pol["_meta"]["source"])
        self.assertEqual(pol["docs_only_paths"], [])

    def test_docs_only_paths_configured(self):
        repo = _repo_with(
            "docs_only_paths:\n"
            "  - docs/**\n"
            "  - '**/*.md'\n"
            "  - .gitignore\n")
        pol = load_policy(repo)
        self.assertEqual(pol["docs_only_paths"],
                         ["docs/**", "**/*.md", ".gitignore"])

    def test_defaults_not_mutated_between_loads(self):
        repo = _repo_with("automerge:\n  enabled: true\n")
        load_policy(repo)
        self.assertFalse(DEFAULTS["automerge"]["enabled"])

    def test_repo_agent_override_is_loaded(self):
        pol = load_policy(_repo_with(
            "agent_cli: codex\n"
            "agent_model: gpt-5.4-mini\n"
            "fallback_agent_cli: opencode\n"
        ))
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertEqual(pol["agent_model"], "gpt-5.4-mini")
        self.assertEqual(pol["fallback_agent_cli"], "opencode")


class TestYamlSubset(unittest.TestCase):
    def test_scalars_nesting_and_lists(self):
        parsed = parse_policy_yaml(
            "base_branch: dev\n"
            "automerge:\n"
            "  enabled: true\n"
            "  max_risk: medium\n"
            "  target_branches:\n"
            "    - dev\n"
            "protected_paths:\n"
            "  - migrations/\n"
            "  - 'src/billing/'\n"
            "# a comment\n"
            "auth_testing: \"Playwright storage state; creds in .env.local\"\n")
        self.assertEqual(parsed["base_branch"], "dev")
        self.assertIs(parsed["automerge"]["enabled"], True)
        self.assertEqual(parsed["automerge"]["max_risk"], "medium")
        self.assertEqual(parsed["automerge"]["target_branches"], ["dev"])
        self.assertEqual(parsed["protected_paths"], ["migrations/", "src/billing/"])
        self.assertIn("Playwright", parsed["auth_testing"])

    def test_inline_comment_stripped(self):
        parsed = parse_policy_yaml("base_branch: dev  # the integration base\n")
        self.assertEqual(parsed["base_branch"], "dev")


class TestValidation(unittest.TestCase):
    def test_invalid_max_risk_clamped_to_low(self):
        pol = load_policy(_repo_with("automerge:\n  enabled: true\n  max_risk: critical\n"))
        self.assertEqual(pol["automerge"]["max_risk"], "low")
        self.assertTrue(pol["_meta"]["warnings"])

    def test_non_bool_enabled_forced_false(self):
        pol = load_policy(_repo_with("automerge:\n  enabled: definitely\n"))
        self.assertFalse(pol["automerge"]["enabled"])

    def test_unknown_keys_surfaced(self):
        pol = load_policy(_repo_with("automrege: true\n"))
        self.assertIn("automrege", pol["_meta"]["unknown_keys"])

    def test_scalar_automerge_warns_and_keeps_defaults(self):
        """A hand-edited `automerge: true` must not crash Phase 4."""
        pol = load_policy(_repo_with("automerge: true\n"))
        self.assertFalse(pol["automerge"]["enabled"])
        self.assertEqual(pol["automerge"]["max_risk"], "low")
        self.assertTrue(any("automerge" in w for w in pol["_meta"]["warnings"]))

    def test_invalid_agent_override_is_dropped(self):
        pol = load_policy(_repo_with("agent_cli: other\nfallback_agent_cli: invalid\n"))
        self.assertIsNone(pol["agent_cli"])
        self.assertIsNone(pol["fallback_agent_cli"])
        self.assertTrue(any("agent_cli" in w for w in pol["_meta"]["warnings"]))

    def test_max_workers_loaded(self):
        pol = load_policy(_repo_with("max_workers: 5\n"))
        self.assertEqual(pol["max_workers"], 5)
        self.assertFalse(any("max_workers" in w for w in pol["_meta"]["warnings"]))

    def test_max_workers_invalid_values_dropped(self):
        for bad in ("max_workers: lots\n", "max_workers: 0\n", "max_workers: true\n"):
            pol = load_policy(_repo_with(bad))
            self.assertIsNone(pol["max_workers"], msg=bad)
            self.assertTrue(
                any("max_workers" in w for w in pol["_meta"]["warnings"]), msg=bad
            )

    def test_pr_pacing_wait_invalid_dropped_to_default(self):
        pol = load_policy(_repo_with("pr_pacing_wait_s: -5\n"))
        self.assertEqual(pol["pr_pacing_wait_s"], 0)
        self.assertTrue(
            any("pr_pacing_wait_s" in w for w in pol["_meta"]["warnings"])
        )

    def test_pr_pacing_wait_valid_loaded(self):
        pol = load_policy(_repo_with("pr_pacing_wait_s: 1800\n"))
        self.assertEqual(pol["pr_pacing_wait_s"], 1800)


class TestAutomergeEligibility(unittest.TestCase):
    def setUp(self):
        self.pol = load_policy(_repo_with(
            "automerge:\n  enabled: true\n  max_risk: medium\n"
            "  target_branches:\n    - dev\n"))

    def test_eligible_low_risk_on_dev(self):
        ok, _ = automerge_eligible(self.pol, "low", [], "dev")
        self.assertTrue(ok)

    def test_gate_blocks_regardless_of_policy(self):
        ok, why = automerge_eligible(self.pol, "low", ["never_automerge"], "dev")
        self.assertFalse(ok)
        self.assertIn("protected", why)

    def test_risk_above_max_blocks(self):
        ok, _ = automerge_eligible(self.pol, "high", [], "dev")
        self.assertFalse(ok)

    def test_wrong_target_branch_blocks(self):
        ok, _ = automerge_eligible(self.pol, "low", [], "main")
        self.assertFalse(ok)

    def test_disabled_policy_blocks(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        ok, why = automerge_eligible(pol, "low", [], "dev")
        self.assertFalse(ok)
        self.assertIn("disabled", why)

    def test_external_automerge_detected_changes_reason(self):
        repo = _repo_with_workflow(
            {"auto-merge.yml": "jobs:\n  merge:\n    run: gh pr merge --auto --squash \"$PR\"\n"})
        pol = load_policy(repo)
        ok, why = automerge_eligible(pol, "low", [], "dev")
        self.assertTrue(ok)
        self.assertIn("own CI automation", why)
        self.assertIn(".github/workflows/auto-merge.yml", why)

    def test_external_automerge_detected_omits_no_automerge_label(self):
        """Regression guard: eligible=True here must not yield go:no-automerge, since a
        repo's own auto-merge.yml reads that label as a signal to skip/disarm itself."""
        repo = _repo_with_workflow(
            {"auto-merge.yml": "jobs:\n  merge:\n    run: gh pr merge --auto --squash \"$PR\"\n"})
        pol = load_policy(repo)
        ok, _why = automerge_eligible(pol, "low", [], "dev")
        self.assertNotIn("go:no-automerge", automerge_labels(ok, "low"))


class TestExternalAutomergeDetection(unittest.TestCase):
    def test_gh_pr_merge_auto_detected(self):
        repo = _repo_with_workflow(
            {"auto-merge.yml": "jobs:\n  merge:\n    run: gh pr merge --auto --squash \"$PR\"\n"})
        result = detect_external_automerge(repo)
        self.assertEqual(result, {
            "detected": True,
            "workflow_file": ".github/workflows/auto-merge.yml",
        })

    def test_no_workflows_dir(self):
        result = detect_external_automerge(Path(tempfile.mkdtemp()))
        self.assertEqual(result, {"detected": False, "workflow_file": None})

    def test_unrelated_workflow_not_detected(self):
        repo = _repo_with_workflow(
            {"ci.yml": "jobs:\n  test:\n    run: pytest -q\n"})
        result = detect_external_automerge(repo)
        self.assertFalse(result["detected"])

    def test_enable_auto_merge_action_detected(self):
        repo = _repo_with_workflow(
            {"auto-merge.yml": "- uses: peter-evans/enable-pull-request-automerge@v3\n"})
        result = detect_external_automerge(repo)
        self.assertTrue(result["detected"])

    def test_independent_of_go_policy_automerge_enabled(self):
        repo = _repo_with_workflow(
            {"ci.yml": "jobs:\n  test:\n    run: pytest -q\n"},
            policy_text="automerge:\n  enabled: true\n")
        result = detect_external_automerge(repo)
        self.assertFalse(result["detected"])

    def test_empty_workflows_dir(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".github" / "workflows").mkdir(parents=True)
        result = detect_external_automerge(Path(tmp))
        self.assertEqual(result, {"detected": False, "workflow_file": None})

    def test_first_match_wins_sorted_order(self):
        repo = _repo_with_workflow({
            "b-second.yml": "run: gh pr merge --auto\n",
            "a-first.yml": "run: gh pr merge --auto\n",
        })
        result = detect_external_automerge(repo)
        self.assertEqual(result["workflow_file"], ".github/workflows/a-first.yml")


class TestIntegrateSmokeNudge(unittest.TestCase):
    """The unset-smoke-cmd warning fires only for a repo that actually has specs."""

    def _repo_with_spec(self, policy_text=None, spec_dir="001-feature"):
        tmp = tempfile.mkdtemp()
        specs = Path(tmp) / "docs" / "specs"
        (specs / spec_dir).mkdir(parents=True)
        if policy_text is not None:
            (specs / "go-policy.yaml").write_text(policy_text)
        return Path(tmp)

    def _has_nudge(self, pol):
        return any("integrate_smoke_cmd unset" in w for w in pol["_meta"]["warnings"])

    def test_warns_when_specs_exist_and_smoke_unset(self):
        self.assertTrue(self._has_nudge(load_policy(self._repo_with_spec())))

    def test_no_warn_when_smoke_cmd_set(self):
        pol = load_policy(self._repo_with_spec('integrate_smoke_cmd: "pytest -q"\n'))
        self.assertFalse(self._has_nudge(pol))
        self.assertEqual(pol["integrate_smoke_cmd"], "pytest -q")

    def test_no_warn_when_no_specs(self):
        # bare repo (no docs/specs) — nothing to smoke-test, so no nudge
        self.assertFalse(self._has_nudge(load_policy(Path(tempfile.mkdtemp()))))


class TestProtectedPathsEnforcement(unittest.TestCase):
    """protected_paths is enforced by automerge_eligible() via its
    changed_paths param — a matching changed path denies eligibility
    independent of risk/gates."""

    def _policy(self, protected_paths):
        return {
            "automerge": {"enabled": True, "max_risk": "critical", "target_branches": []},
            "protected_paths": protected_paths,
            "_meta": {"external_automerge": {"detected": False}},
        }

    def test_directory_prefix_pattern_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["migrations/"]), "low", [], "main",
            changed_paths=["migrations/0001_init.sql"])
        self.assertFalse(eligible)
        self.assertIn("migrations/", reason)

    def test_glob_pattern_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["src/billing/*.py"]), "low", [], "main",
            changed_paths=["src/billing/invoices.py"])
        self.assertFalse(eligible)

    def test_non_matching_path_eligible(self):
        eligible, _ = automerge_eligible(
            self._policy(["migrations/"]), "low", [], "main",
            changed_paths=["README.md"])
        self.assertTrue(eligible)

    def test_omitted_changed_paths_skips_check(self):
        # No changed_paths supplied at all -- the check does not fail closed.
        eligible, _ = automerge_eligible(
            self._policy(["migrations/"]), "low", [], "main")
        self.assertTrue(eligible)

    def test_unset_protected_paths_never_denies(self):
        eligible, _ = automerge_eligible(
            self._policy([]), "low", [], "main",
            changed_paths=["migrations/0001_init.sql"])
        self.assertTrue(eligible)


class TestRequireHumanRoutesEnforcement(unittest.TestCase):
    """require_human_routes is enforced by automerge_eligible() via its
    route param — a listed route denies eligibility independent of
    risk/gates."""

    def _policy(self, require_human_routes):
        return {
            "automerge": {"enabled": True, "max_risk": "critical", "target_branches": []},
            "require_human_routes": require_human_routes,
            "_meta": {"external_automerge": {"detected": False}},
        }

    def test_listed_route_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["D"]), "low", [], "main", route="D")
        self.assertFalse(eligible)
        self.assertIn("D", reason)

    def test_unlisted_route_eligible(self):
        eligible, _ = automerge_eligible(
            self._policy(["D"]), "low", [], "main", route="F")
        self.assertTrue(eligible)

    def test_omitted_route_skips_check(self):
        eligible, _ = automerge_eligible(self._policy(["D"]), "low", [], "main")
        self.assertTrue(eligible)

    def test_unset_require_human_routes_never_denies(self):
        eligible, _ = automerge_eligible(
            self._policy([]), "low", [], "main", route="D")
        self.assertTrue(eligible)


class TestNoStaleUnenforcedWarning(unittest.TestCase):
    """Configuring protected_paths/require_human_routes must not print the
    old 'not yet enforced' nudge now that both are real gates."""

    def test_no_warn_when_configured(self):
        repo = _repo_with('protected_paths:\n  - "migrations/"\n'
                           'require_human_routes:\n  - "D"\n')
        pol = load_policy(repo)
        warnings = pol["_meta"]["warnings"]
        self.assertFalse(any("not yet enforced" in w for w in warnings))

    def test_no_warn_when_unset(self):
        pol = load_policy(_repo_with(""))
        warnings = pol["_meta"]["warnings"]
        self.assertFalse(any("protected_paths" in w for w in warnings))
        self.assertFalse(any("require_human_routes" in w for w in warnings))


class TestMergeMethodByBase(unittest.TestCase):
    """merge_method_by_base — handoff 20260714-120011-go-automerge-coordination:
    verify.py's own repo-wide merge-method detection can't distinguish "this repo
    allows merge commits for stg/prd promotions" from "dev-target feature PRs
    should still squash." This key lets a repo declare the branch-aware split."""

    def test_default_is_empty_mapping(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        self.assertEqual(pol["merge_method_by_base"], {})

    def test_configured_mapping_parsed(self):
        repo = _repo_with(
            "merge_method_by_base:\n"
            "  dev: squash\n"
            "  stg: merge\n"
            "  prd: merge\n")
        pol = load_policy(repo)
        self.assertEqual(pol["merge_method_by_base"],
                         {"dev": "squash", "stg": "merge", "prd": "merge"})

    def test_invalid_method_dropped_with_warning(self):
        repo = _repo_with(
            "merge_method_by_base:\n"
            "  dev: squash\n"
            "  stg: rocket\n")
        pol = load_policy(repo)
        self.assertEqual(pol["merge_method_by_base"], {"dev": "squash"})
        self.assertTrue(any("merge_method_by_base.stg" in w
                            for w in pol["_meta"]["warnings"]))

    def test_scalar_value_ignored_with_warning(self):
        repo = _repo_with("merge_method_by_base: squash\n")
        pol = load_policy(repo)
        self.assertEqual(pol["merge_method_by_base"], {})
        self.assertTrue(any("merge_method_by_base must be a mapping" in w
                            for w in pol["_meta"]["warnings"]))

    def test_merge_method_for_branch_hit_and_miss(self):
        policy = {"merge_method_by_base": {"stg": "merge", "dev": "squash"}}
        self.assertEqual(merge_method_for_branch(policy, "stg"), "merge")
        self.assertEqual(merge_method_for_branch(policy, "dev"), "squash")
        self.assertIsNone(merge_method_for_branch(policy, "feature/x"))

    def test_merge_method_for_branch_unset_key(self):
        self.assertIsNone(merge_method_for_branch({}, "main"))


class TestCheckAutomergeCli(unittest.TestCase):
    """--check-automerge gives automerge_eligible() a real CLI invocation path
    instead of relying on an agent hand-writing `python3 -c` per sdd-workflow
    SKILL.md's documented (but previously untested end-to-end) usage."""

    def _run(self, repo, *extra):
        import json
        import subprocess
        import sys
        cmd = [sys.executable, "-m", "worktrail.router.policy",
               "--repo", str(repo), "--check-automerge", *extra]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_defaults_to_ineligible(self):
        result = self._run(_repo_with(""))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["reason"], "automerge disabled by policy")

    def test_gates_flag_forces_ineligible(self):
        repo = _repo_with("automerge:\n  enabled: true\n  max_risk: medium\n")
        result = self._run(repo, "--gates", "never_automerge", "--risk", "low")
        self.assertFalse(result["eligible"])

    def test_eligible_when_enabled_and_risk_within_bounds(self):
        repo = _repo_with("automerge:\n  enabled: true\n  max_risk: medium\n")
        result = self._run(repo, "--risk", "low", "--target-branch", "main")
        self.assertTrue(result["eligible"])

    def test_eligible_labels_omit_no_automerge(self):
        repo = _repo_with("automerge:\n  enabled: true\n  max_risk: medium\n")
        result = self._run(repo, "--risk", "low")
        self.assertEqual(result["labels"], ["go:risk-low"])

    def test_ineligible_labels_include_no_automerge(self):
        repo = _repo_with("automerge:\n  enabled: true\n  max_risk: medium\n")
        result = self._run(repo, "--risk", "high")
        self.assertEqual(result["labels"], ["go:risk-high", "go:no-automerge"])


class TestAutomergeLabels(unittest.TestCase):
    """automerge_labels(): the deterministic go:risk-*/go:no-automerge mapping
    a repo's own CI (auto-merge.yml) reads as PR metadata, since it cannot
    call automerge_eligible() directly — see policy.py's docstring on the
    function."""

    def test_eligible_gets_only_risk_label(self):
        self.assertEqual(automerge_labels(True, "low"), ["go:risk-low"])

    def test_ineligible_gets_risk_and_no_automerge_labels(self):
        self.assertEqual(
            automerge_labels(False, "high"), ["go:risk-high", "go:no-automerge"])


class TestMergeMethodForBranchCli(unittest.TestCase):
    """--merge-method-for-branch: the sdd-workflow conductor's real invocation path
    for resolving `--merge-method` before it hands the flag to the orchestrator."""

    def _run(self, repo, branch):
        import json
        import subprocess
        import sys
        cmd = [sys.executable, "-m", "worktrail.router.policy", "--repo", str(repo),
              "--merge-method-for-branch", branch]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_configured_branch_returns_method(self):
        repo = _repo_with("merge_method_by_base:\n  stg: merge\n")
        result = self._run(repo, "stg")
        self.assertEqual(result["merge_method"], "merge")

    def test_unconfigured_branch_returns_null(self):
        repo = _repo_with("merge_method_by_base:\n  stg: merge\n")
        result = self._run(repo, "dev")
        self.assertIsNone(result["merge_method"])


class TestPolicyJsonCli(unittest.TestCase):
    """--json on the raw policy dict: `routing.tiers` is stored with
    `(complexity, domain)` tuple keys (see `Routing.test_validate_routing_tiers_*`
    below) for the in-memory `resolve_tier_map()` contract, but `json.dumps`
    cannot serialize tuple keys. Any repo with a `routing.tiers` block used to
    crash `worktrail-policy --repo . --json` (Phase 4 of every `/go` invocation
    against it) with `TypeError: keys must be str, int, float, bool or None, not
    tuple`."""

    def _run(self, repo):
        import json
        import subprocess
        import sys
        cmd = [sys.executable, "-m", "worktrail.router.policy",
               "--repo", str(repo), "--json"]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_json_with_tiers_domain_does_not_crash(self):
        repo = _repo_with("routing:\n  tiers:\n    hard/backend:\n      agent: codex\n")
        result = self._run(repo)
        self.assertEqual(
            result["routing"]["tiers"],
            {"hard/backend": {"agent_cli": "codex", "agent_model": None, "effort": None}})

    def test_json_with_tiers_no_domain_does_not_crash(self):
        repo = _repo_with("routing:\n  tiers:\n    easy:\n      agent: claude\n")
        result = self._run(repo)
        self.assertEqual(
            result["routing"]["tiers"],
            {"easy": {"agent_cli": "claude", "agent_model": None, "effort": None}})

    def test_json_without_tiers_unaffected(self):
        repo = _repo_with("routing:\n  roles:\n    reviewer:\n      agent_cli: codex\n")
        result = self._run(repo)
        self.assertEqual(
            result["routing"]["roles"],
            {"reviewer": {"agent_cli": "codex", "agent_model": None, "effort": None}})


class TestPolicyJsonSafetyMatrix(unittest.TestCase):
    """Regression coverage for the risk pattern behind PR #122 (not just the
    exact tuple-key shape it fixed): `json.dumps(_json_safe(load_policy(repo)))`
    must never raise for any valid `routing.*` configuration, so a future
    validator that stores another non-JSON-safe key (following the
    `routing.tiers` precedent) is caught here instead of live during a `/go`
    Phase 4 policy load. Covers `defaults`/`roles`/`tiers`/`fallback`, each
    with and without a domain/model/effort segment present."""

    POLICY_YAMLS = {
        "empty_routing_block": "routing: {}\n",
        "defaults_only": (
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: claude\n"
            "        agent_model: sonnet\n"
            "        effort: high\n"),
        "roles_only": (
            "routing:\n"
            "  roles:\n"
            "    review:\n"
            "      agent_cli: codex\n"),
        "tiers_with_domain": (
            "routing:\n"
            "  tiers:\n"
            "    hard/backend:\n"
            "      agent: codex\n"
            "      effort: xhigh\n"),
        "tiers_without_domain": (
            "routing:\n"
            "  tiers:\n"
            "    easy:\n"
            "      agent: claude\n"),
        "fallback_only": (
            "routing:\n"
            "  fallback:\n"
            "    - codex\n"
            "    - agent_cli: opencode\n"
            "      agent_model: deepseek-v4-flash\n"),
        "all_four_combined": (
            "routing:\n"
            "  defaults:\n"
            "    F:\n"
            "      high:\n"
            "        agent_cli: claude\n"
            "  roles:\n"
            "    review:\n"
            "      agent_cli: claude\n"
            "      agent_model: opus\n"
            "  tiers:\n"
            "    t1-deep/frontend:\n"
            "      agent: codex\n"
            "      agent_model: gpt-5.6-sol\n"
            "    t4-trivia:\n"
            "      agent: claude\n"
            "  fallback:\n"
            "    - claude\n"
            "    - codex\n"
            "    - opencode\n"),
        "multiple_tiers_mixed_domains": (
            "routing:\n"
            "  tiers:\n"
            "    trivial:\n"
            "      agent: claude\n"
            "    trivial/frontend:\n"
            "      agent: codex\n"
            "    standard/backend:\n"
            "      agent: opencode\n"),
    }

    def test_json_dumps_never_raises_and_round_trips(self):
        import json
        for name, policy_text in self.POLICY_YAMLS.items():
            with self.subTest(policy=name):
                repo = _repo_with(policy_text)
                pol = load_policy(repo)
                safe = _json_safe(pol)
                try:
                    dumped = json.dumps(safe)
                except TypeError as exc:
                    self.fail(f"{name}: json.dumps raised on _json_safe(load_policy(...)): {exc}")
                # Round-trip: re-loading the dump must not raise either, and
                # every routing.tiers key comes back as its "complexity[/domain]"
                # string form (the tuple keys are gone after _json_safe).
                reloaded = json.loads(dumped)
                for key in (reloaded.get("routing") or {}).get("tiers", {}):
                    self.assertIsInstance(key, str)

    def test_json_safe_is_idempotent_when_no_tuple_keys_present(self):
        # A policy with no routing.tiers has nothing for _json_safe to convert;
        # it must still return a plain, json.dumps-able structure unchanged.
        import json
        repo = _repo_with("routing:\n  roles:\n    review:\n      agent_cli: codex\n")
        pol = load_policy(repo)
        self.assertEqual(_json_safe(pol), pol)
        json.dumps(_json_safe(pol))  # must not raise


class Routing(unittest.TestCase):
    """routing: schema, machine-wide fallback file, and resolve_routing() —
    TASK-001 (023-subscription-aware-routing)."""

    def _mw_env(self, path):
        return mock.patch.dict(os.environ, {"GO_ROUTING_FILE": str(path)})

    def _no_mw_env(self):
        """Point GO_ROUTING_FILE at a path that doesn't exist, so a real
        ~/.go/routing.yaml on the test-running machine can't leak in."""
        return mock.patch.dict(
            os.environ, {"GO_ROUTING_FILE": "/nonexistent/go-routing-test/routing.yaml"})

    def test_full_routing_block_returns_all_four_sub_keys(self):
        # AC-001
        repo = _repo_with(
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: claude\n"
            "        agent_model: sonnet\n"
            "  roles:\n"
            "    reviewer:\n"
            "      agent_cli: codex\n"
            "  tiers:\n"
            "    hard/backend:\n"
            "      agent: codex\n"
            "  fallback:\n"
            "    - codex\n"
            "    - opencode\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"],
                         {"A": {"low": {"agent_cli": "claude", "agent_model": "sonnet", "effort": None}}})
        self.assertEqual(pol["routing"]["roles"],
                         {"reviewer": {"agent_cli": "codex", "agent_model": None, "effort": None}})
        self.assertEqual(pol["routing"]["tiers"],
                         {("hard", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": None}})
        self.assertEqual(pol["routing"]["fallback"],
                         [{"agent_cli": "codex", "agent_model": None, "effort": None},
                          {"agent_cli": "opencode", "agent_model": None, "effort": None}])

    def test_effort_field_validates_and_resolves(self):
        # AC-CHG-003: an agent entry with a string `effort` validates and
        # carries the value through to the resolved dict.
        repo = _repo_with(
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: codex\n"
            "        effort: high\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"],
                         {"A": {"low": {"agent_cli": "codex", "agent_model": None, "effort": "high"}}})
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_effort_field_absent_resolves_to_none(self):
        # AC-CHG-003: an agent entry with no `effort` key resolves with
        # `effort: None`, not a missing key.
        repo = _repo_with("routing:\n  roles:\n    reviewer:\n      agent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"],
                         {"reviewer": {"agent_cli": "codex", "agent_model": None, "effort": None}})

    def test_effort_field_invalid_type_dropped_with_warning(self):
        # AC-CHG-003: a non-string `effort` is dropped (resolves to None) with
        # a warning, matching the existing agent_model validation pattern.
        repo = _repo_with(
            "routing:\n"
            "  tiers:\n"
            "    hard/backend:\n"
            "      agent: codex\n"
            "      effort: 123\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["tiers"],
                         {("hard", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": None}})
        self.assertTrue(any("effort must be a string" in w for w in pol["_meta"]["warnings"]))

    def test_invalid_agent_literal_in_defaults_dropped(self):
        # AC-002
        repo = _repo_with(
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: bogus\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"], {})
        self.assertTrue(any("routing.defaults.A.low" in w for w in pol["_meta"]["warnings"]))

    def test_invalid_agent_literal_in_roles_dropped(self):
        # AC-002
        repo = _repo_with("routing:\n  roles:\n    reviewer: bogus\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"], {})
        self.assertTrue(any("routing.roles.reviewer" in w for w in pol["_meta"]["warnings"]))

    def test_invalid_agent_literal_in_tiers_dropped(self):
        # AC-002, AC-CHG-006
        repo = _repo_with("routing:\n  tiers:\n    hard/backend:\n      agent: bogus\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["tiers"], {})
        self.assertTrue(any("routing.tiers" in w for w in pol["_meta"]["warnings"]))

    def test_invalid_agent_literal_in_fallback_dropped(self):
        # AC-002
        repo = _repo_with("routing:\n  fallback:\n    - bogus\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["fallback"], [])
        self.assertTrue(any("routing.fallback" in w and "bogus" in w
                            for w in pol["_meta"]["warnings"]))

    def test_no_repo_routing_but_machine_wide_file_present(self):
        # AC-003
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text("defaults:\n  A:\n    low:\n      agent_cli: codex\n")
        repo = _repo_with("")
        with self._mw_env(mw):
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"],
                         {"A": {"low": {"agent_cli": "codex", "agent_model": None, "effort": None}}})

    def test_repo_routing_wins_over_machine_wide_file(self):
        # AC-003 precedence
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text("defaults:\n  A:\n    low:\n      agent_cli: codex\n")
        repo = _repo_with("routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n")
        with self._mw_env(mw):
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"]["A"]["low"]["agent_cli"], "claude")

    def test_no_repo_routing_no_machine_wide_file_matches_flat_baseline(self):
        # AC-004
        repo = _repo_with("agent_cli: codex\nagent_model: gpt-5.4-mini\nfallback_agent_cli: opencode\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertEqual(pol["agent_model"], "gpt-5.4-mini")
        self.assertEqual(pol["fallback_agent_cli"], "opencode")

    def test_fallback_api_literal_excluded_without_opt_in(self):
        # AC-005
        repo = _repo_with("routing:\n  fallback:\n    - codex\n    - openrouter\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["fallback"], [{"agent_cli": "codex", "agent_model": None, "effort": None}])
        self.assertTrue(any("openrouter" in w and "excluded" in w
                            for w in pol["_meta"]["warnings"]))

    def test_fallback_api_literal_included_with_opt_in(self):
        # AC-005
        repo = _repo_with(
            "routing:\n"
            "  fallback:\n"
            "    - codex\n"
            "    - agent_cli: openrouter\n"
            "      api_opt_in: true\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["fallback"],
                         [{"agent_cli": "codex", "agent_model": None, "effort": None},
                          {"agent_cli": "openrouter", "agent_model": None, "effort": None, "api": True}])

    def test_validate_routing_tiers_complexity_and_domain(self):
        # AC-CHG-004
        meta = {"warnings": []}
        resolved = _validate_routing_tiers(
            {"hard/backend": {"agent": "codex", "model": "gpt-5"}}, meta)
        self.assertEqual(resolved,
                         {("hard", "backend"): {"agent_cli": "codex", "agent_model": "gpt-5", "effort": None}})
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_tiers_no_domain_yields_none(self):
        # AC-CHG-005
        meta = {"warnings": []}
        resolved = _validate_routing_tiers({"easy": {"agent": "claude"}}, meta)
        self.assertEqual(resolved, {("easy", None): {"agent_cli": "claude", "agent_model": None, "effort": None}})

    def test_validate_routing_tiers_invalid_agent_dropped_others_kept(self):
        # AC-CHG-006
        meta = {"warnings": []}
        resolved = _validate_routing_tiers(
            {"hard/backend": {"agent": "bogus"},
             "easy": {"agent": "claude"}}, meta)
        self.assertEqual(resolved, {("easy", None): {"agent_cli": "claude", "agent_model": None, "effort": None}})
        self.assertTrue(any("routing.tiers.hard/backend" in w for w in meta["warnings"]))

    def test_purpose_tiers_configured_resolves_and_validates(self):
        # 3.3: a configured routing.purpose_tiers table resolves and validates.
        repo = _repo_with(
            "routing:\n"
            "  purpose_tiers:\n"
            "    scaffolding: t3\n"
            "    security-review: t1-deep\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["purpose_tiers"],
                         {"scaffolding": "t3", "security-review": "t1-deep"})
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_purpose_tiers_unconfigured_resolves_to_empty(self):
        # 3.3: an unconfigured/empty table resolves to {}.
        repo = _repo_with("routing:\n  roles:\n    reviewer:\n      agent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["purpose_tiers"], {})

    def test_purpose_tiers_explicit_empty_mapping_resolves_to_empty(self):
        # 3.3: an unconfigured/empty table resolves to {}.
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_purpose_tiers({}, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_purpose_tiers_malformed_non_string_value_dropped_with_warning(self):
        # 3.3: a malformed entry (non-string value) is dropped with a warning.
        repo = _repo_with(
            "routing:\n"
            "  purpose_tiers:\n"
            "    scaffolding: 3\n"
            "    security-review: t1-deep\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["purpose_tiers"], {"security-review": "t1-deep"})
        self.assertTrue(any("routing.purpose_tiers" in w and "scaffolding" in w
                            for w in pol["_meta"]["warnings"]))

    def test_validate_routing_purpose_tiers_non_string_value_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_purpose_tiers(
            {"scaffolding": 3, "security-review": "t1-deep"}, meta)
        self.assertEqual(resolved, {"security-review": "t1-deep"})
        self.assertTrue(any("purpose_tiers" in w for w in meta["warnings"]))

    def test_validate_routing_purpose_tiers_non_mapping_ignored(self):
        meta = {"warnings": []}
        resolved = _validate_routing_purpose_tiers(["scaffolding"], meta)
        self.assertEqual(resolved, {})
        self.assertTrue(any("routing.purpose_tiers must be a mapping" in w for w in meta["warnings"]))

    def test_resolve_tier_map_populated(self):
        # AC-CHG-001
        repo = _repo_with("routing:\n  tiers:\n    hard/backend:\n      agent: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(resolve_tier_map(pol),
                         {("hard", "backend"): {"agent_cli": "codex", "agent_model": None, "effort": None}})

    def test_resolve_tier_map_no_routing_returns_empty(self):
        # AC-CHG-002
        repo = _repo_with("agent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(resolve_tier_map(pol), {})

    def test_resolve_tier_map_routing_none_returns_empty(self):
        # AC-CHG-002
        self.assertEqual(resolve_tier_map({"routing": None}), {})

    def test_resolve_tier_map_tiers_absent_returns_empty(self):
        # AC-CHG-002
        repo = _repo_with("routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(resolve_tier_map(pol), {})

    def test_resolve_routing_deterministic_match(self):
        # REQ-002, REQ-NR002
        repo = _repo_with(
            "routing:\n"
            "  defaults:\n"
            "    B:\n"
            "      medium:\n"
            "        agent_cli: claude\n"
            "        agent_model: opus\n"
            "  roles:\n"
            "    reviewer: codex\n"
            "  fallback:\n"
            "    - opencode\n"
            "  purpose_tiers:\n"
            "    scaffolding: t3\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        first = resolve_routing(pol, "B", "medium")
        second = resolve_routing(pol, "B", "medium")
        self.assertEqual(first, second)
        self.assertEqual(first["agent_cli"], "claude")
        self.assertEqual(first["agent_model"], "opus")
        self.assertEqual(first["roles"], {"reviewer": {"agent_cli": "codex", "agent_model": None, "effort": None}})
        self.assertEqual(first["fallback"], [{"agent_cli": "opencode", "agent_model": None, "effort": None}])
        self.assertEqual(first["purpose_tiers"], {"scaffolding": "t3"})

    def test_resolve_routing_purpose_tiers_empty_when_unconfigured(self):
        repo = _repo_with(
            "routing:\n  defaults:\n    B:\n      medium:\n        agent_cli: claude\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(resolve_routing(pol, "B", "medium")["purpose_tiers"], {})

    def test_resolve_routing_unmatched_falls_back_to_flat_agent_cli(self):
        # REQ-002
        repo = _repo_with(
            "agent_cli: opencode\n"
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol, "Z", "critical")
        self.assertEqual(result["agent_cli"], "opencode")

    def test_resolve_routing_no_routing_configured_matches_flat_behavior(self):
        repo = _repo_with("agent_cli: claude\nagent_model: sonnet\nfallback_agent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol, "A", "low")
        self.assertEqual(result["agent_cli"], "claude")
        self.assertEqual(result["agent_model"], "sonnet")
        self.assertEqual(result["roles"], {})
        self.assertEqual(result["fallback"], [{"agent_cli": "codex", "agent_model": None}])
        self.assertEqual(result["purpose_tiers"], {})

    def test_malformed_scalar_routing_value_warns_and_ignored(self):
        # REQ-001, REQ-NR004
        repo = _repo_with("routing: true\nagent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertTrue(any("routing must be a mapping" in w for w in pol["_meta"]["warnings"]))

    def test_machine_wide_file_malformed_yaml_falls_through_to_flat_keys(self):
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text("defaults:\n  A: [unterminated\n")
        repo = _repo_with("agent_cli: codex\n")
        with self._mw_env(mw):
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertTrue(any("malformed YAML" in w for w in pol["_meta"]["warnings"]))

    def test_empty_routing_mapping_treated_as_absent(self):
        repo = _repo_with("routing:\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

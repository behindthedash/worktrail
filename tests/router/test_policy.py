#!/usr/bin/env python3
"""Tests for policy.py. Run: python3 test_policy.py"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from worktrail.router.policy import (
    DEFAULTS,
    EFFORT_VOCABULARY,
    OperatorConfigError,
    _json_safe,
    _reject_legacy_routing_keys,
    _validate_routing_agents,
    _validate_routing_default_tier,
    _validate_routing_drain,
    _validate_routing_purpose_tiers,
    _validate_routing_targets,
    _validate_routing_tiers,
    automerge_eligible,
    automerge_labels,
    detect_external_automerge,
    load_policy,
    merge_method_for_branch,
    parse_policy_yaml,
    resolve_post_merge_smoke_cmd,
    resolve_routing,
    resolve_tier_map,
)


@pytest.fixture(autouse=True)
def _no_legacy_machine_wide_routing_file_by_default(monkeypatch):
    """`tests/conftest.py`'s suite-wide `GO_ROUTING_FILE` fixture seeds a
    legacy `agents:`-shaped routing file (kept for `default_model_for_agent()`,
    retired in a later task) -- 1.4 makes that shape a hard
    `OperatorConfigError`. Point the env var at a nonexistent path by
    default for every test in this module; a test that wants the
    machine-wide-file fallback path overrides the same env var itself inside
    its own body (`_mw_env()`/`_no_mw_env()` below), winning since it patches
    later than this module-level fixture's setup."""
    monkeypatch.setenv(
        "GO_ROUTING_FILE", "/nonexistent/worktrail-routing-test/routing.yaml"
    )


def _repo_with(policy_text):
    tmp = tempfile.mkdtemp()
    d = Path(tmp) / ".worktrail"
    d.mkdir(parents=True)
    (d / "policy.yaml").write_text(policy_text)
    return Path(tmp)


def _repo_with_workflow(files, policy_text=None):
    """files: dict of workflow filename -> file content, written under .github/workflows/."""
    tmp = tempfile.mkdtemp()
    wf_dir = Path(tmp) / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for name, content in files.items():
        (wf_dir / name).write_text(content)
    if policy_text is not None:
        worktrail_dir = Path(tmp) / ".worktrail"
        worktrail_dir.mkdir(parents=True)
        (worktrail_dir / "policy.yaml").write_text(policy_text)
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
            "docs_only_paths:\n  - docs/**\n  - '**/*.md'\n  - .gitignore\n"
        )
        pol = load_policy(repo)
        self.assertEqual(pol["docs_only_paths"], ["docs/**", "**/*.md", ".gitignore"])

    def test_defaults_not_mutated_between_loads(self):
        repo = _repo_with("automerge:\n  enabled: true\n")
        load_policy(repo)
        self.assertFalse(DEFAULTS["automerge"]["enabled"])

    def test_repo_agent_override_is_loaded(self):
        pol = load_policy(
            _repo_with(
                "agent_cli: codex\n"
                "agent_model: gpt-5.4-mini\n"
                "fallback_agent_cli: opencode\n"
            )
        )
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertEqual(pol["agent_model"], "gpt-5.4-mini")
        self.assertEqual(pol["fallback_agent_cli"], "opencode")

    def test_allow_seeded_implementation_defaults_false(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        self.assertFalse(pol["allow_seeded_implementation"])

    def test_allow_seeded_implementation_true_when_set(self):
        pol = load_policy(_repo_with("allow_seeded_implementation: true\n"))
        self.assertTrue(pol["allow_seeded_implementation"])


class TestAddOns(unittest.TestCase):
    """add_ons: opt-in map of add-on name -> config, consumed by
    addons/runner.py (post-task-cmd-addon). Empty by default so a repo with
    no add_ons: key sees zero behavior change."""

    def test_add_ons_defaults_to_empty_dict(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        self.assertEqual(pol["add_ons"], {})

    def test_configured_add_ons_round_trips(self):
        repo = _repo_with("add_ons:\n  aspens: true\n")
        pol = load_policy(repo)
        self.assertEqual(pol["add_ons"], {"aspens": True})

    def test_add_ons_not_reported_as_unknown_key(self):
        repo = _repo_with("add_ons:\n  aspens: true\n")
        pol = load_policy(repo)
        self.assertNotIn("add_ons", pol["_meta"]["unknown_keys"])

    def test_nested_add_on_config_round_trips(self):
        # Design D3's real shape (`Dict[str, Dict[str, Any]]`) — the flat
        # `aspens: true` case above doesn't exercise nesting, and
        # `parse_policy_yaml`'s one-level-nesting subset used to flatten a
        # nested add-on's own keys up into `add_ons` as siblings of the
        # add-on name instead of nesting them under it.
        repo = _repo_with(
            "add_ons:\n"
            "  aspens:\n"
            "    enabled: true\n"
            "    target: claude\n"
            "    required: false\n"
        )
        pol = load_policy(repo)
        self.assertEqual(
            pol["add_ons"],
            {"aspens": {"enabled": True, "target": "claude", "required": False}},
        )

    def test_malformed_add_ons_falls_back_to_empty_dict(self):
        repo = _repo_with("add_ons: true\n")
        pol = load_policy(repo)
        self.assertEqual(pol["add_ons"], {})
        self.assertTrue(
            any("add_ons must be a mapping" in w for w in pol["_meta"]["warnings"])
        )


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
            'auth_testing: "Playwright storage state; creds in .env.local"\n'
        )
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
        pol = load_policy(
            _repo_with("automerge:\n  enabled: true\n  max_risk: critical\n")
        )
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

    def test_allow_seeded_implementation_non_bool_clamped_false(self):
        pol = load_policy(_repo_with("allow_seeded_implementation: yesplease\n"))
        self.assertFalse(pol["allow_seeded_implementation"])
        self.assertTrue(
            any("allow_seeded_implementation" in w for w in pol["_meta"]["warnings"])
        )

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
        self.assertTrue(any("pr_pacing_wait_s" in w for w in pol["_meta"]["warnings"]))

    def test_pr_pacing_wait_valid_loaded(self):
        pol = load_policy(_repo_with("pr_pacing_wait_s: 1800\n"))
        self.assertEqual(pol["pr_pacing_wait_s"], 1800)


class TestAutomergeEligibility(unittest.TestCase):
    def setUp(self):
        self.pol = load_policy(
            _repo_with(
                "automerge:\n  enabled: true\n  max_risk: medium\n"
                "  target_branches:\n    - dev\n"
            )
        )

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
            {
                "auto-merge.yml": 'jobs:\n  merge:\n    run: gh pr merge --auto --squash "$PR"\n'
            }
        )
        pol = load_policy(repo)
        ok, why = automerge_eligible(pol, "low", [], "dev")
        self.assertTrue(ok)
        self.assertIn("own CI automation", why)
        self.assertIn(".github/workflows/auto-merge.yml", why)

    def test_external_automerge_detected_omits_no_automerge_label(self):
        """Regression guard: eligible=True here must not yield go:no-automerge, since a
        repo's own auto-merge.yml reads that label as a signal to skip/disarm itself."""
        repo = _repo_with_workflow(
            {
                "auto-merge.yml": 'jobs:\n  merge:\n    run: gh pr merge --auto --squash "$PR"\n'
            }
        )
        pol = load_policy(repo)
        ok, _why = automerge_eligible(pol, "low", [], "dev")
        self.assertNotIn("go:no-automerge", automerge_labels(ok, "low"))


class TestExternalAutomergeDetection(unittest.TestCase):
    def test_gh_pr_merge_auto_detected(self):
        repo = _repo_with_workflow(
            {
                "auto-merge.yml": 'jobs:\n  merge:\n    run: gh pr merge --auto --squash "$PR"\n'
            }
        )
        result = detect_external_automerge(repo)
        self.assertEqual(
            result,
            {
                "detected": True,
                "workflow_file": ".github/workflows/auto-merge.yml",
            },
        )

    def test_no_workflows_dir(self):
        result = detect_external_automerge(Path(tempfile.mkdtemp()))
        self.assertEqual(result, {"detected": False, "workflow_file": None})

    def test_unrelated_workflow_not_detected(self):
        repo = _repo_with_workflow({"ci.yml": "jobs:\n  test:\n    run: pytest -q\n"})
        result = detect_external_automerge(repo)
        self.assertFalse(result["detected"])

    def test_enable_auto_merge_action_detected(self):
        repo = _repo_with_workflow(
            {"auto-merge.yml": "- uses: peter-evans/enable-pull-request-automerge@v3\n"}
        )
        result = detect_external_automerge(repo)
        self.assertTrue(result["detected"])

    def test_independent_of_go_policy_automerge_enabled(self):
        repo = _repo_with_workflow(
            {"ci.yml": "jobs:\n  test:\n    run: pytest -q\n"},
            policy_text="automerge:\n  enabled: true\n",
        )
        result = detect_external_automerge(repo)
        self.assertFalse(result["detected"])

    def test_empty_workflows_dir(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / ".github" / "workflows").mkdir(parents=True)
        result = detect_external_automerge(Path(tmp))
        self.assertEqual(result, {"detected": False, "workflow_file": None})

    def test_first_match_wins_sorted_order(self):
        repo = _repo_with_workflow(
            {
                "b-second.yml": "run: gh pr merge --auto\n",
                "a-first.yml": "run: gh pr merge --auto\n",
            }
        )
        result = detect_external_automerge(repo)
        self.assertEqual(result["workflow_file"], ".github/workflows/a-first.yml")


class TestIntegrateSmokeNudge(unittest.TestCase):
    """The unset-smoke-cmd warning fires only for a repo that actually has specs."""

    def _repo_with_spec(self, policy_text=None, spec_dir="001-feature"):
        tmp = tempfile.mkdtemp()
        specs = Path(tmp) / "docs" / "specs"
        (specs / spec_dir).mkdir(parents=True)
        if policy_text is not None:
            worktrail_dir = Path(tmp) / ".worktrail"
            worktrail_dir.mkdir(parents=True, exist_ok=True)
            (worktrail_dir / "policy.yaml").write_text(policy_text)
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
            "automerge": {
                "enabled": True,
                "max_risk": "critical",
                "target_branches": [],
            },
            "protected_paths": protected_paths,
            "_meta": {"external_automerge": {"detected": False}},
        }

    def test_directory_prefix_pattern_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["migrations/"]),
            "low",
            [],
            "main",
            changed_paths=["migrations/0001_init.sql"],
        )
        self.assertFalse(eligible)
        self.assertIn("migrations/", reason)

    def test_glob_pattern_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["src/billing/*.py"]),
            "low",
            [],
            "main",
            changed_paths=["src/billing/invoices.py"],
        )
        self.assertFalse(eligible)

    def test_non_matching_path_eligible(self):
        eligible, _ = automerge_eligible(
            self._policy(["migrations/"]),
            "low",
            [],
            "main",
            changed_paths=["README.md"],
        )
        self.assertTrue(eligible)

    def test_omitted_changed_paths_skips_check(self):
        # No changed_paths supplied at all -- the check does not fail closed.
        eligible, _ = automerge_eligible(
            self._policy(["migrations/"]), "low", [], "main"
        )
        self.assertTrue(eligible)

    def test_unset_protected_paths_never_denies(self):
        eligible, _ = automerge_eligible(
            self._policy([]),
            "low",
            [],
            "main",
            changed_paths=["migrations/0001_init.sql"],
        )
        self.assertTrue(eligible)


class TestRequireHumanRoutesEnforcement(unittest.TestCase):
    """require_human_routes is enforced by automerge_eligible() via its
    route param — a listed route denies eligibility independent of
    risk/gates."""

    def _policy(self, require_human_routes):
        return {
            "automerge": {
                "enabled": True,
                "max_risk": "critical",
                "target_branches": [],
            },
            "require_human_routes": require_human_routes,
            "_meta": {"external_automerge": {"detected": False}},
        }

    def test_listed_route_denies(self):
        eligible, reason = automerge_eligible(
            self._policy(["D"]), "low", [], "main", route="D"
        )
        self.assertFalse(eligible)
        self.assertIn("D", reason)

    def test_unlisted_route_eligible(self):
        eligible, _ = automerge_eligible(
            self._policy(["D"]), "low", [], "main", route="F"
        )
        self.assertTrue(eligible)

    def test_omitted_route_skips_check(self):
        eligible, _ = automerge_eligible(self._policy(["D"]), "low", [], "main")
        self.assertTrue(eligible)

    def test_unset_require_human_routes_never_denies(self):
        eligible, _ = automerge_eligible(self._policy([]), "low", [], "main", route="D")
        self.assertTrue(eligible)


class TestNoStaleUnenforcedWarning(unittest.TestCase):
    """Configuring protected_paths/require_human_routes must not print the
    old 'not yet enforced' nudge now that both are real gates."""

    def test_no_warn_when_configured(self):
        repo = _repo_with(
            'protected_paths:\n  - "migrations/"\nrequire_human_routes:\n  - "D"\n'
        )
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
            "merge_method_by_base:\n  dev: squash\n  stg: merge\n  prd: merge\n"
        )
        pol = load_policy(repo)
        self.assertEqual(
            pol["merge_method_by_base"],
            {"dev": "squash", "stg": "merge", "prd": "merge"},
        )

    def test_invalid_method_dropped_with_warning(self):
        repo = _repo_with("merge_method_by_base:\n  dev: squash\n  stg: rocket\n")
        pol = load_policy(repo)
        self.assertEqual(pol["merge_method_by_base"], {"dev": "squash"})
        self.assertTrue(
            any("merge_method_by_base.stg" in w for w in pol["_meta"]["warnings"])
        )

    def test_scalar_value_ignored_with_warning(self):
        repo = _repo_with("merge_method_by_base: squash\n")
        pol = load_policy(repo)
        self.assertEqual(pol["merge_method_by_base"], {})
        self.assertTrue(
            any(
                "merge_method_by_base must be a mapping" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_merge_method_for_branch_hit_and_miss(self):
        policy = {"merge_method_by_base": {"stg": "merge", "dev": "squash"}}
        self.assertEqual(merge_method_for_branch(policy, "stg"), "merge")
        self.assertEqual(merge_method_for_branch(policy, "dev"), "squash")
        self.assertIsNone(merge_method_for_branch(policy, "feature/x"))

    def test_merge_method_for_branch_unset_key(self):
        self.assertIsNone(merge_method_for_branch({}, "main"))


class TestResolvePostMergeSmokeCmd(unittest.TestCase):
    """resolve_post_merge_smoke_cmd() -- verify.py's cumulative post-merge gate
    command (worktrail PR #167 follow-up). post_merge_smoke_cmd wins;
    integrate_smoke_cmd is the fallback; neither set = gate skipped."""

    def test_default_is_none(self):
        pol = load_policy(Path(tempfile.mkdtemp()))
        self.assertIsNone(resolve_post_merge_smoke_cmd(pol))

    def test_post_merge_smoke_cmd_used_when_set(self):
        repo = _repo_with('post_merge_smoke_cmd: "pytest -q -k smoke"\n')
        pol = load_policy(repo)
        self.assertEqual(resolve_post_merge_smoke_cmd(pol), "pytest -q -k smoke")

    def test_falls_back_to_integrate_smoke_cmd(self):
        repo = _repo_with('integrate_smoke_cmd: "make check"\n')
        pol = load_policy(repo)
        self.assertEqual(resolve_post_merge_smoke_cmd(pol), "make check")

    def test_post_merge_smoke_cmd_wins_over_integrate_smoke_cmd(self):
        repo = _repo_with(
            'post_merge_smoke_cmd: "pytest -q -k smoke"\n'
            'integrate_smoke_cmd: "make check"\n'
        )
        pol = load_policy(repo)
        self.assertEqual(resolve_post_merge_smoke_cmd(pol), "pytest -q -k smoke")

    def test_blank_value_treated_as_unset(self):
        self.assertIsNone(
            resolve_post_merge_smoke_cmd(
                {"post_merge_smoke_cmd": "   ", "integrate_smoke_cmd": None}
            )
        )


class TestCheckAutomergeCli(unittest.TestCase):
    """--check-automerge gives automerge_eligible() a real CLI invocation path
    instead of relying on an agent hand-writing `python3 -c` per sdd-workflow
    SKILL.md's documented (but previously untested end-to-end) usage."""

    def _run(self, repo, *extra):
        import json
        import subprocess
        import sys

        cmd = [
            sys.executable,
            "-m",
            "worktrail.router.policy",
            "--repo",
            str(repo),
            "--check-automerge",
            *extra,
        ]
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
            automerge_labels(False, "high"), ["go:risk-high", "go:no-automerge"]
        )


class TestMergeMethodForBranchCli(unittest.TestCase):
    """--merge-method-for-branch: the sdd-workflow conductor's real invocation path
    for resolving `--merge-method` before it hands the flag to the orchestrator."""

    def _run(self, repo, branch):
        import json
        import subprocess
        import sys

        cmd = [
            sys.executable,
            "-m",
            "worktrail.router.policy",
            "--repo",
            str(repo),
            "--merge-method-for-branch",
            branch,
        ]
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
    """--json on the raw policy dict: `routing.tiers` used to be stored with
    `(complexity, domain)` tuple keys, which `json.dumps` cannot serialize.
    `_validate_routing_tiers()`'s 1.2 rewrite made `routing.tiers` string-keyed
    (`{row: {target: cell}}`), but the CLI round-trip is still worth guarding
    against a future non-JSON-safe key (`_json_safe()` stays generic for that
    reason) -- `worktrail-policy --repo . --json` (Phase 4 of every `/go`
    invocation) must never crash on a repo's `routing.tiers` block."""

    def _run(self, repo):
        import json
        import subprocess
        import sys

        cmd = [
            sys.executable,
            "-m",
            "worktrail.router.policy",
            "--repo",
            str(repo),
            "--json",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_json_with_tiers_does_not_crash(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        result = self._run(repo)
        self.assertEqual(
            result["routing"]["tiers"],
            {"hard": {"codex-main": {"model": "gpt-5", "effort": None}}},
        )

    def test_json_without_tiers_unaffected(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t1-deep:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: t1-deep\n"
        )
        result = self._run(repo)
        self.assertEqual(
            result["routing"]["roles"],
            {"reviewer": {"tier": "t1-deep", "prefer": None, "independent": False}},
        )


class TestPolicyJsonSafetyMatrix(unittest.TestCase):
    """Regression coverage for the risk pattern behind PR #122 (not just the
    exact tuple-key shape it fixed): `json.dumps(_json_safe(load_policy(repo)))`
    must never raise for any valid `routing.*` configuration, so a future
    validator that stores another non-JSON-safe key (following the
    `routing.tiers` precedent) is caught here instead of live during a `/go`
    Phase 4 policy load. Covers `defaults`/`roles`/`tiers`, each with and
    without a model/effort segment present."""

    POLICY_YAMLS = {
        "empty_routing_block": "routing: {}\n",
        "defaults_only": (
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: claude\n"
            "        agent_model: sonnet\n"
            "        effort: high\n"
        ),
        "roles_only": (
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t1-deep:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    review:\n"
            "      tier: t1-deep\n"
            "      prefer: codex-main\n"
            "      independent: true\n"
        ),
        "tiers_one_target": (
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "        effort: xhigh\n"
        ),
        "tiers_no_effort": (
            "routing:\n"
            "  targets:\n"
            "    claude-main:\n"
            "      harness: claude\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    easy:\n"
            "      claude-main:\n"
            "        model: sonnet\n"
        ),
        "all_four_combined": (
            "routing:\n"
            "  defaults:\n"
            "    F:\n"
            "      high:\n"
            "        agent_cli: claude\n"
            "  roles:\n"
            "    review:\n"
            "      tier: t1-deep\n"
            "      prefer: claude-main\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "    claude-main:\n"
            "      harness: claude\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t1-deep:\n"
            "      codex-main:\n"
            "        model: gpt-5.6-sol\n"
            "    t4-trivia:\n"
            "      claude-main:\n"
            "        model: haiku\n"
        ),
        "multiple_tiers_multi_target": (
            "routing:\n"
            "  targets:\n"
            "    claude-main:\n"
            "      harness: claude\n"
            "      pool: subscription\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    trivial:\n"
            "      claude-main:\n"
            "        model: haiku\n"
            "      codex-main:\n"
            "        model: gpt-5-mini\n"
            "    standard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        ),
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
                    self.fail(
                        f"{name}: json.dumps raised on _json_safe(load_policy(...)): {exc}"
                    )
                # Round-trip: re-loading the dump must not raise either, and
                # every routing.tiers key comes back as a plain string.
                reloaded = json.loads(dumped)
                for key in (reloaded.get("routing") or {}).get("tiers", {}):
                    self.assertIsInstance(key, str)

    def test_json_safe_is_idempotent_when_no_tuple_keys_present(self):
        # A policy with no routing.tiers has nothing for _json_safe to convert;
        # it must still return a plain, json.dumps-able structure unchanged.
        import json

        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: codex\n"
        )
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
        machine-wide routing.yaml (under worktrail_home()) can't leak in."""
        return mock.patch.dict(
            os.environ, {"GO_ROUTING_FILE": "/nonexistent/go-routing-test/routing.yaml"}
        )

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
            "      tier: hard\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["defaults"],
            {
                "A": {
                    "low": {
                        "agent_cli": "claude",
                        "agent_model": "sonnet",
                        "effort": None,
                    }
                }
            },
        )
        self.assertEqual(
            pol["routing"]["roles"],
            {"reviewer": {"tier": "hard", "prefer": None, "independent": False}},
        )
        self.assertEqual(
            pol["routing"]["tiers"],
            {"hard": {"codex-main": {"model": "gpt-5", "effort": None}}},
        )

    def test_effort_field_validates_and_resolves(self):
        # AC-CHG-003: an agent entry with a string `effort` validates and
        # carries the value through to the resolved dict.
        repo = _repo_with(
            "routing:\n"
            "  defaults:\n"
            "    A:\n"
            "      low:\n"
            "        agent_cli: codex\n"
            "        effort: high\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["defaults"],
            {
                "A": {
                    "low": {"agent_cli": "codex", "agent_model": None, "effort": "high"}
                }
            },
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_effort_field_absent_resolves_to_none(self):
        # AC-CHG-003: an agent entry with no `effort` key resolves with
        # `effort: None`, not a missing key.
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: codex\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["defaults"],
            {"A": {"low": {"agent_cli": "codex", "agent_model": None, "effort": None}}},
        )

    def test_effort_field_invalid_type_dropped_with_warning(self):
        # AC-CHG-003: a non-string `effort` is dropped (resolves to None) with
        # a warning, matching the existing agent_model validation pattern.
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "        effort: 123\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["tiers"],
            {"hard": {"codex-main": {"model": "gpt-5", "effort": None}}},
        )
        self.assertTrue(
            any("effort must be a string" in w for w in pol["_meta"]["warnings"])
        )

    def test_invalid_agent_literal_in_defaults_dropped(self):
        # AC-002
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: bogus\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"], {})
        self.assertTrue(
            any("routing.defaults.A.low" in w for w in pol["_meta"]["warnings"])
        )

    def test_non_mapping_role_entry_dropped(self):
        # AC-002 (1.3: roles are now {tier, prefer?, independent?} mappings)
        repo = _repo_with("routing:\n  roles:\n    reviewer: bogus\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"], {})
        self.assertTrue(
            any("routing.roles.reviewer" in w for w in pol["_meta"]["warnings"])
        )

    def test_role_undeclared_tier_dropped(self):
        # 1.3: tier must name a declared routing.tiers row.
        repo = _repo_with("routing:\n  roles:\n    reviewer:\n      tier: bogus-tier\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"], {})
        self.assertTrue(
            any(
                "routing.roles.reviewer.tier" in w and "bogus-tier" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_role_undeclared_prefer_dropped(self):
        # 1.3: prefer must name a declared routing.targets entry.
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: hard\n"
            "      prefer: bogus-target\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"], {})
        self.assertTrue(
            any(
                "routing.roles.reviewer.prefer" in w and "bogus-target" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_role_valid_tier_and_prefer_resolves(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: hard\n"
            "      prefer: codex-main\n"
            "      independent: true\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["roles"],
            {"reviewer": {"tier": "hard", "prefer": "codex-main", "independent": True}},
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_role_independent_defaults_to_false(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: hard\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["roles"],
            {"reviewer": {"tier": "hard", "prefer": None, "independent": False}},
        )

    def test_role_non_bool_independent_dropped_to_false(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: hard\n"
            "      independent: yes-please\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["roles"],
            {"reviewer": {"tier": "hard", "prefer": None, "independent": False}},
        )
        self.assertTrue(
            any(
                "routing.roles.reviewer.independent" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_role_missing_tier_dropped(self):
        repo = _repo_with(
            "routing:\n  roles:\n    reviewer:\n      prefer: codex-main\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["roles"], {})
        self.assertTrue(
            any("routing.roles.reviewer.tier" in w for w in pol["_meta"]["warnings"])
        )

    def test_undeclared_target_in_tiers_dropped(self):
        # AC-002, AC-CHG-006 (1.2: cells are now keyed by declared target)
        repo = _repo_with(
            "routing:\n  tiers:\n    hard:\n      bogus-target:\n        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["tiers"], {})
        self.assertTrue(
            any(
                "routing.tiers" in w and "undeclared target" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_no_repo_routing_but_machine_wide_file_present(self):
        # AC-003
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text("defaults:\n  A:\n    low:\n      agent_cli: codex\n")
        repo = _repo_with("")
        with self._mw_env(mw):
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["defaults"],
            {"A": {"low": {"agent_cli": "codex", "agent_model": None, "effort": None}}},
        )

    def test_repo_routing_wins_over_machine_wide_file(self):
        # AC-003 precedence
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text("defaults:\n  A:\n    low:\n      agent_cli: codex\n")
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n"
        )
        with self._mw_env(mw):
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["defaults"]["A"]["low"]["agent_cli"], "claude")

    def test_no_repo_routing_no_machine_wide_file_matches_flat_baseline(self):
        # AC-004
        repo = _repo_with(
            "agent_cli: codex\nagent_model: gpt-5.4-mini\nfallback_agent_cli: opencode\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertEqual(pol["agent_model"], "gpt-5.4-mini")
        self.assertEqual(pol["fallback_agent_cli"], "opencode")

    def test_validate_routing_tiers_cell_keyed_by_declared_target(self):
        # 1.2: a row's cell is keyed by declared target name.
        meta = {"warnings": []}
        targets = {
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"codex-main": {"model": "gpt-5"}}}, targets, meta
        )
        self.assertEqual(
            resolved, {"hard": {"codex-main": {"model": "gpt-5", "effort": None}}}
        )
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_tiers_undeclared_target_dropped_with_warning(self):
        meta = {"warnings": []}
        targets = {
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"bogus-target": {"model": "gpt-5"}}}, targets, meta
        )
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.tiers.hard" in w
                and "bogus-target" in w
                and "undeclared target" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_tiers_undeclared_target_dropped_others_kept(self):
        meta = {"warnings": []}
        targets = {
            "claude-main": {
                "harness": "claude",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {
                "hard": {
                    "bogus-target": {"model": "gpt-5"},
                    "claude-main": {"model": "sonnet"},
                }
            },
            targets,
            meta,
        )
        self.assertEqual(
            resolved, {"hard": {"claude-main": {"model": "sonnet", "effort": None}}}
        )
        self.assertTrue(any("bogus-target" in w for w in meta["warnings"]))

    def test_validate_routing_tiers_missing_cell_is_not_an_error(self):
        # A target simply absent from a row's cells means it can't serve that
        # tier -- not a warning-worthy condition (the requirement this task
        # implements).
        meta = {"warnings": []}
        targets = {
            "claude-main": {
                "harness": "claude",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            },
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            },
        }
        resolved = _validate_routing_tiers(
            {"hard": {"codex-main": {"model": "gpt-5"}}}, targets, meta
        )
        self.assertNotIn("claude-main", resolved["hard"])
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_tiers_missing_model_dropped_with_warning(self):
        meta = {"warnings": []}
        targets = {
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers({"hard": {"codex-main": {}}}, targets, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.tiers.hard.codex-main.model" in w for w in meta["warnings"])
        )

    def test_validate_routing_tiers_non_mapping_cell_dropped(self):
        meta = {"warnings": []}
        targets = {
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"codex-main": "gpt-5"}}, targets, meta
        )
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.tiers.hard.codex-main must be a mapping" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_tiers_non_mapping_row_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_tiers({"hard": "codex-main"}, {}, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.tiers.hard must be a mapping" in w for w in meta["warnings"])
        )

    def test_validate_routing_tiers_absent_resolves_to_empty(self):
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_tiers(None, {}, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_tiers_non_mapping_top_level_warns_and_ignored(self):
        meta = {"warnings": []}
        resolved = _validate_routing_tiers(["hard"], {}, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.tiers must be a mapping" in w for w in meta["warnings"])
        )

    def test_effort_vocabulary_shape(self):
        # 1.5: EFFORT_VOCABULARY covers every SUPPORTED_AGENTS harness;
        # claude/codex declare their reasoning-effort literals, opencode
        # declares none (its `effort` maps to a model-variant flag, not a
        # reasoning-effort level).
        self.assertEqual(set(EFFORT_VOCABULARY), {"claude", "codex", "opencode"})
        self.assertIn("high", EFFORT_VOCABULARY["claude"])
        self.assertIn("high", EFFORT_VOCABULARY["codex"])
        self.assertIsNone(EFFORT_VOCABULARY["opencode"])

    def test_validate_routing_tiers_effort_in_vocabulary_no_warning(self):
        meta = {"warnings": []}
        targets = {
            "claude-main": {
                "harness": "claude",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"claude-main": {"model": "opus", "effort": "high"}}},
            targets,
            meta,
        )
        self.assertEqual(
            resolved, {"hard": {"claude-main": {"model": "opus", "effort": "high"}}}
        )
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_tiers_effort_outside_vocabulary_warns_but_kept(self):
        meta = {"warnings": []}
        targets = {
            "claude-main": {
                "harness": "claude",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"claude-main": {"model": "opus", "effort": "xhigh"}}},
            targets,
            meta,
        )
        # kept, not dropped -- an unsupported effort literal doesn't make the
        # target unusable for the tier.
        self.assertEqual(
            resolved, {"hard": {"claude-main": {"model": "opus", "effort": "xhigh"}}}
        )
        self.assertTrue(
            any(
                "routing.tiers.hard.claude-main.effort" in w
                and "xhigh" in w
                and "claude" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_tiers_codex_effort_outside_vocabulary_warns(self):
        meta = {"warnings": []}
        targets = {
            "codex-main": {
                "harness": "codex",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"codex-main": {"model": "gpt-5", "effort": "extreme"}}},
            targets,
            meta,
        )
        self.assertEqual(
            resolved, {"hard": {"codex-main": {"model": "gpt-5", "effort": "extreme"}}}
        )
        self.assertTrue(
            any(
                "routing.tiers.hard.codex-main.effort" in w
                and "extreme" in w
                and "codex" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_tiers_opencode_effort_always_ignored_by_harness(self):
        # opencode has no effort vocabulary at all -- any declared effort
        # warns "ignored by harness", even a literal that's valid elsewhere.
        meta = {"warnings": []}
        targets = {
            "opencode-main": {
                "harness": "opencode",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"opencode-main": {"model": "big-model", "effort": "high"}}},
            targets,
            meta,
        )
        self.assertEqual(
            resolved,
            {"hard": {"opencode-main": {"model": "big-model", "effort": "high"}}},
        )
        self.assertTrue(
            any(
                "routing.tiers.hard.opencode-main.effort" in w
                and "ignored by harness" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_tiers_no_effort_no_vocabulary_warning(self):
        meta = {"warnings": []}
        targets = {
            "opencode-main": {
                "harness": "opencode",
                "pool": "subscription",
                "api_opt_in": False,
                "auth": None,
            }
        }
        resolved = _validate_routing_tiers(
            {"hard": {"opencode-main": {"model": "big-model"}}}, targets, meta
        )
        self.assertEqual(
            resolved,
            {"hard": {"opencode-main": {"model": "big-model", "effort": None}}},
        )
        self.assertEqual(meta["warnings"], [])

    def test_load_policy_effort_outside_vocabulary_warns(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    opencode-main:\n"
            "      harness: opencode\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      opencode-main:\n"
            "        model: big-model\n"
            "        effort: high\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["tiers"],
            {"hard": {"opencode-main": {"model": "big-model", "effort": "high"}}},
        )
        self.assertTrue(
            any("ignored by harness" in w for w in pol["_meta"]["warnings"])
        )

    def test_load_policy_undeclared_target_in_tiers_reports_which_row(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      claude-main:\n"
            "        model: opus\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["tiers"], {})
        self.assertTrue(
            any(
                "routing.tiers.hard" in w and "claude-main" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_load_policy_multiple_targets_per_row_resolve(self):
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    claude-main:\n"
            "      harness: claude\n"
            "      pool: subscription\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t1-deep:\n"
            "      claude-main:\n"
            "        model: opus\n"
            "        effort: high\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["tiers"],
            {
                "t1-deep": {
                    "claude-main": {"model": "opus", "effort": "high"},
                    "codex-main": {"model": "gpt-5", "effort": None},
                }
            },
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_validate_routing_default_tier_names_declared_row(self):
        meta = {"warnings": []}
        tiers = {"t2-build": {"codex-main": {"model": "gpt-5", "effort": None}}}
        self.assertEqual(
            _validate_routing_default_tier("t2-build", tiers, meta), "t2-build"
        )
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_default_tier_absent_resolves_to_none(self):
        meta = {"warnings": []}
        self.assertIsNone(_validate_routing_default_tier(None, {}, meta))
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_default_tier_undeclared_row_dropped_with_warning(self):
        meta = {"warnings": []}
        self.assertIsNone(
            _validate_routing_default_tier("bogus-row", {"t2-build": {}}, meta)
        )
        self.assertTrue(
            any(
                "routing.default_tier" in w and "bogus-row" in w
                for w in meta["warnings"]
            )
        )

    def test_validate_routing_default_tier_non_string_dropped_with_warning(self):
        meta = {"warnings": []}
        self.assertIsNone(_validate_routing_default_tier(3, {"t2-build": {}}, meta))
        self.assertTrue(
            any("routing.default_tier must be a string" in w for w in meta["warnings"])
        )

    def test_load_policy_default_tier_names_declared_row_resolves(self):
        repo = _repo_with(
            "routing:\n"
            "  default_tier: t2-build\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t2-build:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["default_tier"], "t2-build")
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_load_policy_default_tier_undeclared_row_dropped_with_warning(self):
        repo = _repo_with(
            "routing:\n"
            "  default_tier: bogus-row\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t2-build:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"]["default_tier"])
        self.assertTrue(
            any(
                "routing.default_tier" in w and "bogus-row" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_load_policy_default_tier_absent_resolves_to_none(self):
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: codex\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"]["default_tier"])

    def test_purpose_tiers_configured_resolves_and_validates(self):
        # 1.3: a configured routing.purposes table resolves and validates
        # (renamed from routing.purpose_tiers).
        repo = _repo_with(
            "routing:\n  purposes:\n    scaffolding: t3\n    security-review: t1-deep\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            pol["routing"]["purposes"],
            {"scaffolding": "t3", "security-review": "t1-deep"},
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_purpose_tiers_unconfigured_resolves_to_empty(self):
        # 3.3: an unconfigured/empty table resolves to {}.
        repo = _repo_with("routing:\n  targets: {}\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["purposes"], {})

    def test_purpose_tiers_explicit_empty_mapping_resolves_to_empty(self):
        # 3.3: an unconfigured/empty table resolves to {}.
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_purpose_tiers({}, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_purpose_tiers_malformed_non_string_value_dropped_with_warning(self):
        # 3.3: a malformed entry (non-string value) is dropped with a warning.
        repo = _repo_with(
            "routing:\n  purposes:\n    scaffolding: 3\n    security-review: t1-deep\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["purposes"], {"security-review": "t1-deep"})
        self.assertTrue(
            any(
                "routing.purposes" in w and "scaffolding" in w
                for w in pol["_meta"]["warnings"]
            )
        )

    def test_validate_routing_purpose_tiers_non_string_value_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_purpose_tiers(
            {"scaffolding": 3, "security-review": "t1-deep"}, meta
        )
        self.assertEqual(resolved, {"security-review": "t1-deep"})
        self.assertTrue(any("purposes" in w for w in meta["warnings"]))

    def test_validate_routing_purpose_tiers_non_mapping_ignored(self):
        meta = {"warnings": []}
        resolved = _validate_routing_purpose_tiers(["scaffolding"], meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.purposes must be a mapping" in w for w in meta["warnings"])
        )

    def test_resolve_tier_map_populated(self):
        # AC-CHG-001
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    hard:\n"
            "      codex-main:\n"
            "        model: gpt-5\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(
            resolve_tier_map(pol),
            {"hard": {"codex-main": {"model": "gpt-5", "effort": None}}},
        )

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
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(resolve_tier_map(pol), {})

    def test_resolve_routing_deterministic_match(self):
        # REQ-002, REQ-NR002 (1.3: resolve_routing() exposes
        # targets/tiers/roles/purposes/default_tier/drain, not
        # route/risk-keyed agent_cli/agent_model).
        repo = _repo_with(
            "routing:\n"
            "  targets:\n"
            "    codex-main:\n"
            "      harness: codex\n"
            "      pool: subscription\n"
            "  tiers:\n"
            "    t3:\n"
            "      codex-main:\n"
            "        model: gpt-5-mini\n"
            "  default_tier: t3\n"
            "  roles:\n"
            "    reviewer:\n"
            "      tier: t3\n"
            "  purposes:\n"
            "    scaffolding: t3\n"
            "  drain:\n"
            "    max_workers: 3\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        first = resolve_routing(pol)
        second = resolve_routing(pol)
        self.assertEqual(first, second)
        self.assertEqual(
            first["targets"],
            {
                "codex-main": {
                    "harness": "codex",
                    "pool": "subscription",
                    "api_opt_in": False,
                    "auth": None,
                }
            },
        )
        self.assertEqual(
            first["tiers"],
            {"t3": {"codex-main": {"model": "gpt-5-mini", "effort": None}}},
        )
        self.assertEqual(
            first["roles"],
            {"reviewer": {"tier": "t3", "prefer": None, "independent": False}},
        )
        self.assertEqual(first["purposes"], {"scaffolding": "t3"})
        self.assertEqual(first["default_tier"], "t3")
        self.assertEqual(first["drain"], {"max_workers": 3})
        self.assertNotIn("agents", first)
        self.assertNotIn("fallback", first)

    def test_resolve_routing_purposes_empty_when_unconfigured(self):
        repo = _repo_with("routing:\n  targets: {}\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(resolve_routing(pol)["purposes"], {})

    def test_resolve_routing_no_routing_configured_returns_empty_shape(self):
        repo = _repo_with(
            "agent_cli: claude\nagent_model: sonnet\nfallback_agent_cli: codex\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol)
        self.assertEqual(
            result,
            {
                "targets": {},
                "tiers": {},
                "roles": {},
                "purposes": {},
                "default_tier": None,
                "drain": {},
            },
        )

    def test_malformed_scalar_routing_value_warns_and_ignored(self):
        # REQ-001, REQ-NR004
        repo = _repo_with("routing: true\nagent_cli: codex\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertIsNone(pol["routing"])
        self.assertEqual(pol["agent_cli"], "codex")
        self.assertTrue(
            any("routing must be a mapping" in w for w in pol["_meta"]["warnings"])
        )

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


class TestValidateRoutingTargets(unittest.TestCase):
    """`_validate_routing_targets()` (1.1): the ordered `routing.targets`
    mapping into `{name: {harness, pool, api_opt_in, auth}}`."""

    def test_absent_resolves_to_empty(self):
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_targets(None, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_non_mapping_warns_and_ignored(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(["claude-sub"], meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.targets must be a mapping" in w for w in meta["warnings"])
        )

    def test_valid_entry_resolves(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"claude-sub": {"harness": "claude", "pool": "subscription"}}, meta
        )
        self.assertEqual(
            resolved,
            {
                "claude-sub": {
                    "harness": "claude",
                    "pool": "subscription",
                    "api_opt_in": False,
                    "auth": None,
                }
            },
        )
        self.assertEqual(meta["warnings"], [])

    def test_auth_passed_through(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {
                "claude-api": {
                    "harness": "claude",
                    "pool": "api",
                    "api_opt_in": True,
                    "auth": {"env": "ANTHROPIC_API_KEY"},
                }
            },
            meta,
        )
        self.assertEqual(resolved["claude-api"]["auth"], {"env": "ANTHROPIC_API_KEY"})
        self.assertEqual(meta["warnings"], [])

    def test_file_order_preserved(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {
                "codex-sub": {"harness": "codex", "pool": "subscription"},
                "claude-sub": {"harness": "claude", "pool": "subscription"},
                "opencode-free": {"harness": "opencode", "pool": "free"},
            },
            meta,
        )
        self.assertEqual(list(resolved), ["codex-sub", "claude-sub", "opencode-free"])

    def test_entry_not_mapping_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets({"claude-sub": "claude"}, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.targets.claude-sub must be a mapping" in w
                for w in meta["warnings"]
            )
        )

    def test_invalid_harness_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"bogus": {"harness": "gemini", "pool": "subscription"}}, meta
        )
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.targets.bogus.harness" in w and "gemini" in w
                for w in meta["warnings"]
            )
        )

    def test_missing_harness_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"claude-sub": {"pool": "subscription"}}, meta
        )
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.targets.claude-sub.harness" in w for w in meta["warnings"])
        )

    def test_invalid_pool_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"claude-sub": {"harness": "claude", "pool": "enterprise"}}, meta
        )
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.targets.claude-sub.pool" in w and "enterprise" in w
                for w in meta["warnings"]
            )
        )

    def test_api_pool_without_opt_in_kept_but_warns_ineligible(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"claude-api": {"harness": "claude", "pool": "api"}}, meta
        )
        self.assertEqual(
            resolved,
            {
                "claude-api": {
                    "harness": "claude",
                    "pool": "api",
                    "api_opt_in": False,
                    "auth": None,
                }
            },
        )
        self.assertTrue(
            any("claude-api" in w and "api_opt_in" in w for w in meta["warnings"])
        )

    def test_api_pool_with_opt_in_resolves_no_warning(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {"claude-api": {"harness": "claude", "pool": "api", "api_opt_in": True}},
            meta,
        )
        self.assertEqual(resolved["claude-api"]["api_opt_in"], True)
        self.assertEqual(meta["warnings"], [])

    def test_mixed_valid_and_invalid_entries(self):
        meta = {"warnings": []}
        resolved = _validate_routing_targets(
            {
                "claude-sub": {"harness": "claude", "pool": "subscription"},
                "bogus": {"harness": "gemini", "pool": "subscription"},
            },
            meta,
        )
        self.assertEqual(list(resolved), ["claude-sub"])
        self.assertTrue(any("bogus" in w for w in meta["warnings"]))


class RoutingAgentsAndDrain(unittest.TestCase):
    """routing.agents / routing.drain schema (1.1-1.3): unit-level validators,
    load_policy() integration, and resolve_routing() exposure."""

    def _no_mw_env(self):
        return mock.patch.dict(
            os.environ, {"GO_ROUTING_FILE": "/nonexistent/go-routing-test/routing.yaml"}
        )

    # -- _validate_routing_agents() -----------------------------------

    def test_validate_routing_agents_valid_entry_resolves(self):
        meta = {"warnings": []}
        resolved = _validate_routing_agents(
            {"claude": {"default_model": "sonnet"}}, meta
        )
        self.assertEqual(resolved, {"claude": {"default_model": "sonnet"}})
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_agents_absent_resolves_to_empty(self):
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_agents(None, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_agents_non_mapping_warns_and_ignored(self):
        meta = {"warnings": []}
        resolved = _validate_routing_agents(["claude"], meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.agents must be a mapping" in w for w in meta["warnings"])
        )

    def test_validate_routing_agents_invalid_agent_literal_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_agents(
            {"bogus": {"default_model": "sonnet"}, "codex": {"default_model": "gpt-5"}},
            meta,
        )
        self.assertEqual(resolved, {"codex": {"default_model": "gpt-5"}})
        self.assertTrue(
            any("routing.agents" in w and "bogus" in w for w in meta["warnings"])
        )

    def test_validate_routing_agents_entry_not_mapping_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_agents({"claude": "sonnet"}, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any(
                "routing.agents.claude must be a mapping" in w for w in meta["warnings"]
            )
        )

    def test_validate_routing_agents_non_string_default_model_dropped(self):
        meta = {"warnings": []}
        resolved = _validate_routing_agents({"claude": {"default_model": 5}}, meta)
        self.assertEqual(resolved, {})
        self.assertTrue(
            any("routing.agents.claude.default_model" in w for w in meta["warnings"])
        )

    # -- _validate_routing_drain() -------------------------------------

    def test_validate_routing_drain_valid_entry_resolves(self):
        meta = {"warnings": []}
        resolved = _validate_routing_drain(
            {"agent": "codex", "fallback_agents": ["opencode"], "max_workers": 4}, meta
        )
        self.assertEqual(
            resolved,
            {"agent": "codex", "fallback_agents": ["opencode"], "max_workers": 4},
        )
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_drain_absent_resolves_to_empty(self):
        meta = {"warnings": []}
        self.assertEqual(_validate_routing_drain(None, meta), {})
        self.assertEqual(meta["warnings"], [])

    def test_validate_routing_drain_missing_max_workers_defaults_to_two(self):
        meta = {"warnings": []}
        resolved = _validate_routing_drain({"agent": "codex"}, meta)
        self.assertEqual(
            resolved, {"agent": "codex", "fallback_agents": [], "max_workers": 2}
        )

    def test_validate_routing_drain_non_mapping_raises(self):
        meta = {"warnings": []}
        with self.assertRaises(OperatorConfigError):
            _validate_routing_drain(["codex"], meta)

    def test_validate_routing_drain_non_string_agent_raises(self):
        meta = {"warnings": []}
        with self.assertRaises(OperatorConfigError):
            _validate_routing_drain({"agent": 5}, meta)

    def test_validate_routing_drain_non_string_fallback_agents_raises(self):
        meta = {"warnings": []}
        with self.assertRaises(OperatorConfigError):
            _validate_routing_drain({"fallback_agents": ["codex", 5]}, meta)

    def test_validate_routing_drain_invalid_max_workers_raises(self):
        meta = {"warnings": []}
        with self.assertRaises(OperatorConfigError):
            _validate_routing_drain({"max_workers": 0}, meta)

    # -- load_policy() integration --------------------------------------

    def test_load_policy_valid_drain_max_workers_resolves(self):
        # 1.4: routing.agents and routing.drain.agent/fallback_agents are now
        # retired keys (see LegacyRoutingKeys below) -- only max_workers
        # remains a valid routing.drain field.
        repo = _repo_with("routing:\n  drain:\n    max_workers: 3\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["agents"], {})
        self.assertEqual(
            pol["routing"]["drain"],
            {"agent": None, "fallback_agents": [], "max_workers": 3},
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    def test_load_policy_malformed_drain_raises(self):
        # 1.2's docstring: routing.drain is stated operator intent, so a
        # malformed section raises rather than warning-and-dropping, unlike
        # the sibling _validate_routing_* tables.
        repo = _repo_with("routing:\n  drain:\n    max_workers: 0\n")
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_absent_agents_and_drain_resolve_to_empty_and_change_nothing(
        self,
    ):
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        self.assertEqual(pol["routing"]["agents"], {})
        self.assertEqual(pol["routing"]["drain"], {})
        self.assertEqual(
            pol["routing"]["defaults"],
            {
                "A": {
                    "low": {"agent_cli": "claude", "agent_model": None, "effort": None}
                }
            },
        )
        self.assertEqual(pol["_meta"]["warnings"], [])

    # -- resolve_routing() exposure --------------------------------------
    # 1.3: resolve_routing() drops `agents`/`fallback` from its returned
    # shape entirely (routing.agents/routing.fallback stay internal to
    # `policy["routing"]`, resolved by `_validate_routing()` above, but are
    # no longer part of the selector-facing contract).

    def test_resolve_routing_exposes_drain_but_not_agents(self):
        repo = _repo_with("routing:\n  drain:\n    max_workers: 5\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol)
        self.assertNotIn("agents", result)
        self.assertEqual(result["drain"], {"max_workers": 5})

    def test_resolve_routing_drain_empty_when_absent(self):
        repo = _repo_with(
            "routing:\n  defaults:\n    A:\n      low:\n        agent_cli: claude\n"
        )
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol)
        self.assertNotIn("agents", result)
        self.assertEqual(result["drain"], {})

    def test_resolve_routing_drain_empty_when_no_routing_configured(self):
        repo = _repo_with("agent_cli: claude\n")
        with self._no_mw_env():
            pol = load_policy(repo)
        result = resolve_routing(pol)
        self.assertNotIn("agents", result)
        self.assertEqual(result["drain"], {})


class LegacyRoutingKeys(unittest.TestCase):
    """`_reject_legacy_routing_keys()` (task 1.4): a pre-target-selector
    `routing:` shape fails loud with `OperatorConfigError` naming the
    offending key and `worktrail-routing --migrate`, rather than being
    silently warned-and-dropped."""

    def _no_mw_env(self):
        return mock.patch.dict(
            os.environ, {"GO_ROUTING_FILE": "/nonexistent/go-routing-test/routing.yaml"}
        )

    # -- _reject_legacy_routing_keys() directly --------------------------

    def test_agents_key_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys(
                {"agents": {"claude": {"default_model": "sonnet"}}}
            )
        self.assertIn("routing.agents", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_fallback_key_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys({"fallback": ["codex"]})
        self.assertIn("routing.fallback", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_purpose_tiers_key_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys({"purpose_tiers": {"scaffolding": "t3"}})
        self.assertIn("routing.purpose_tiers", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_drain_agent_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys({"drain": {"agent": "codex"}})
        self.assertIn("routing.drain.agent", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_drain_fallback_agents_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys({"drain": {"fallback_agents": ["opencode"]}})
        self.assertIn("routing.drain.fallback_agents", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_drain_max_workers_only_does_not_raise(self):
        _reject_legacy_routing_keys({"drain": {"max_workers": 3}})  # must not raise

    def test_harness_keyed_tier_cell_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys(
                {"tiers": {"hard": {"codex": {"model": "gpt-5"}}}}
            )
        self.assertIn("routing.tiers.hard", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_target_keyed_tier_cell_does_not_raise(self):
        # A declared-target name that happens to differ from any harness
        # literal is the current (non-legacy) shape.
        _reject_legacy_routing_keys(
            {"tiers": {"hard": {"codex-main": {"model": "gpt-5"}}}}
        )  # must not raise

    def test_target_named_after_harness_literal_does_not_raise(self):
        # A target literally named `codex` is legal (target names are
        # free-form; only `harness` is constrained to SUPPORTED_AGENTS) and
        # must not be mistaken for the retired harness-keyed tier form.
        _reject_legacy_routing_keys(
            {
                "targets": {"codex": {"harness": "codex", "pool": "subscription"}},
                "tiers": {"hard": {"codex": {"model": "gpt-5"}}},
            }
        )  # must not raise

    def test_undeclared_harness_keyed_tier_cell_still_raises(self):
        # Without a matching routing.targets declaration, a cell keyed by a
        # harness literal is still read as the retired form even when other
        # unrelated targets are declared.
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys(
                {
                    "targets": {
                        "codex-main": {"harness": "codex", "pool": "subscription"}
                    },
                    "tiers": {"hard": {"codex": {"model": "gpt-5"}}},
                }
            )
        self.assertIn("routing.tiers.hard", str(ctx.exception))

    def test_role_with_agent_cli_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys({"roles": {"reviewer": {"agent_cli": "codex"}}})
        self.assertIn("routing.roles.reviewer", str(ctx.exception))
        self.assertIn("worktrail-routing --migrate", str(ctx.exception))

    def test_role_with_agent_model_raises(self):
        with self.assertRaises(OperatorConfigError) as ctx:
            _reject_legacy_routing_keys(
                {"roles": {"reviewer": {"agent_model": "opus"}}}
            )
        self.assertIn("routing.roles.reviewer", str(ctx.exception))

    def test_role_with_tier_prefer_independent_does_not_raise(self):
        _reject_legacy_routing_keys(
            {
                "roles": {
                    "reviewer": {
                        "tier": "hard",
                        "prefer": "codex-main",
                        "independent": True,
                    }
                }
            }
        )  # must not raise

    def test_no_legacy_keys_does_not_raise(self):
        _reject_legacy_routing_keys(
            {
                "targets": {"codex-main": {"harness": "codex", "pool": "subscription"}},
                "tiers": {"hard": {"codex-main": {"model": "gpt-5"}}},
                "default_tier": "hard",
                "roles": {"reviewer": {"tier": "hard"}},
                "purposes": {"scaffolding": "hard"},
                "drain": {"max_workers": 2},
            }
        )  # must not raise

    # -- called first in _validate_routing() / load_policy() ------------

    def test_load_policy_agents_key_raises(self):
        repo = _repo_with(
            "routing:\n  agents:\n    claude:\n      default_model: sonnet\n"
        )
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_fallback_key_raises(self):
        repo = _repo_with("routing:\n  fallback:\n    - codex\n")
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_purpose_tiers_key_raises(self):
        repo = _repo_with("routing:\n  purpose_tiers:\n    scaffolding: t3\n")
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_drain_agent_raises(self):
        repo = _repo_with("routing:\n  drain:\n    agent: codex\n")
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_drain_fallback_agents_raises(self):
        repo = _repo_with(
            "routing:\n  drain:\n    fallback_agents:\n      - opencode\n"
        )
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_harness_keyed_tier_cell_raises(self):
        repo = _repo_with(
            "routing:\n  tiers:\n    hard:\n      codex:\n        model: gpt-5\n"
        )
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_role_agent_cli_raises(self):
        repo = _repo_with("routing:\n  roles:\n    reviewer:\n      agent_cli: codex\n")
        with self._no_mw_env(), self.assertRaises(OperatorConfigError):
            load_policy(repo)

    def test_load_policy_legacy_key_raises_before_reaching_machine_wide_fallback(self):
        # _reject_legacy_routing_keys() runs on the repo-local block before
        # _resolve_routing() would otherwise fall through to a machine-wide
        # routing file -- a legacy repo-local block must not be silently
        # bypassed by a valid machine-wide file underneath it.
        tmp = tempfile.mkdtemp()
        mw = Path(tmp) / "routing.yaml"
        mw.write_text(
            "targets:\n  codex-main:\n    harness: codex\n    pool: subscription\n"
        )
        repo = _repo_with("routing:\n  fallback:\n    - codex\n")
        with mock.patch.dict(os.environ, {"GO_ROUTING_FILE": str(mw)}):
            with self.assertRaises(OperatorConfigError):
                load_policy(repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)

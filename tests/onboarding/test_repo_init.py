"""Tests for onboarding/repo_init.py -- the worktrail-repo-init CLI."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from worktrail.onboarding import repo_init


def _tmp_repo() -> Path:
    return Path(tempfile.mkdtemp())


class SplitClaudeMdTests(unittest.TestCase):
    def test_moves_content_and_stamps_import_line(self):
        repo = _tmp_repo()
        (repo / "CLAUDE.md").write_text("# my repo\nsome real content\n")
        changed, warning = repo_init.split_claude_md(repo)
        self.assertTrue(changed)
        self.assertIsNone(warning)
        self.assertEqual((repo / "CLAUDE.md").read_text(), "@AGENTS.md\n")
        self.assertEqual((repo / "AGENTS.md").read_text(), "# my repo\nsome real content\n")

    def test_no_claude_md_creates_stub_agents_md(self):
        repo = _tmp_repo()
        changed, warning = repo_init.split_claude_md(repo)
        self.assertTrue(changed)
        self.assertIsNone(warning)
        self.assertIn(repo.name, (repo / "AGENTS.md").read_text())
        self.assertEqual((repo / "CLAUDE.md").read_text(), "@AGENTS.md\n")

    def test_already_split_is_a_noop(self):
        repo = _tmp_repo()
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n")
        (repo / "AGENTS.md").write_text("# my repo\n")
        changed, warning = repo_init.split_claude_md(repo)
        self.assertFalse(changed)
        self.assertIsNone(warning)

    def test_existing_agents_md_with_unsplit_claude_md_warns_and_skips(self):
        repo = _tmp_repo()
        (repo / "CLAUDE.md").write_text("hand-authored content\n")
        (repo / "AGENTS.md").write_text("separate pre-existing content\n")
        changed, warning = repo_init.split_claude_md(repo)
        self.assertFalse(changed)
        self.assertIsNotNone(warning)
        # Neither file touched.
        self.assertEqual((repo / "CLAUDE.md").read_text(), "hand-authored content\n")
        self.assertEqual((repo / "AGENTS.md").read_text(), "separate pre-existing content\n")


class DiscoverCiChecksTests(unittest.TestCase):
    def test_no_workflows_dir_returns_empty(self):
        self.assertEqual(repo_init.discover_ci_checks(_tmp_repo()), [])

    def test_uses_job_name_falling_back_to_job_id(self):
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "on: pull_request\n"
            "jobs:\n"
            "  lint_test_build:\n"
            "    name: Lint, Test & Build\n"
            "    runs-on: ubuntu-latest\n"
            "  no_display_name:\n"
            "    runs-on: ubuntu-latest\n"
        )
        checks = repo_init.discover_ci_checks(repo)
        self.assertIn("Lint, Test & Build", checks)
        self.assertIn("no_display_name", checks)

    def test_malformed_yaml_is_skipped_not_raised(self):
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "broken.yml").write_text("jobs: [this is not: valid: yaml\n")
        self.assertEqual(repo_init.discover_ci_checks(repo), [])


class BuildRulesetTests(unittest.TestCase):
    def test_two_branch_dev_has_no_linear_history_and_squash(self):
        rs = repo_init.build_ruleset_for_branch("dev", "2")
        self.assertEqual(rs["name"], "protect-dev")
        self.assertEqual(rs["conditions"]["ref_name"]["include"], ["refs/heads/dev"])
        types = [r["type"] for r in rs["rules"]]
        self.assertNotIn("required_linear_history", types)
        pr_rule = next(r for r in rs["rules"] if r["type"] == "pull_request")
        self.assertEqual(pr_rule["parameters"]["allowed_merge_methods"], ["squash"])
        self.assertTrue(pr_rule["parameters"]["required_review_thread_resolution"])

    def test_three_branch_dev_has_linear_history(self):
        rs = repo_init.build_ruleset_for_branch("dev", "3")
        types = [r["type"] for r in rs["rules"]]
        self.assertIn("required_linear_history", types)

    def test_stg_and_prd_use_merge_and_no_linear_history(self):
        for branch in ("stg", "prd"):
            rs = repo_init.build_ruleset_for_branch(branch, "3")
            pr_rule = next(r for r in rs["rules"] if r["type"] == "pull_request")
            self.assertEqual(pr_rule["parameters"]["allowed_merge_methods"], ["merge"])
            types = [r["type"] for r in rs["rules"]]
            self.assertNotIn("required_linear_history", types)

    def test_no_required_status_checks_rule_when_checks_empty(self):
        rs = repo_init.build_ruleset("protect-dev", "dev", ["squash"], [])
        types = [r["type"] for r in rs["rules"]]
        self.assertNotIn("required_status_checks", types)

    def test_required_status_checks_rule_present_when_checks_given(self):
        rs = repo_init.build_ruleset("protect-dev", "dev", ["squash"], ["Lint, Test & Build"])
        check_rule = next(r for r in rs["rules"] if r["type"] == "required_status_checks")
        self.assertEqual(
            check_rule["parameters"]["required_status_checks"],
            [{"context": "Lint, Test & Build"}],
        )

    def test_unknown_branch_raises(self):
        with self.assertRaises(ValueError):
            repo_init.build_ruleset_for_branch("staging", "2")


class BuildAutomergeWorkflowTests(unittest.TestCase):
    def test_is_valid_yaml_with_expected_shape(self):
        doc = yaml.safe_load(repo_init.build_automerge_workflow())
        self.assertEqual(doc["name"], "CI: Auto-merge on open")
        self.assertIn("auto-merge", doc["jobs"])
        # PyYAML's SafeLoader resolves the bare `on:` GHA trigger key to the
        # boolean True (YAML 1.1), not the string "on" -- a well-known gotcha.
        self.assertIn("pull_request", doc[True])

    def test_gates_on_risk_labels_not_bare_arm(self):
        text = repo_init.build_automerge_workflow()
        self.assertIn("go:risk-(low|medium)", text)
        self.assertIn("go:no-automerge", text)

    def test_picks_squash_for_dev_merge_otherwise(self):
        text = repo_init.build_automerge_workflow()
        self.assertIn('base.ref }}" = "dev"', text)
        self.assertIn("--auto --squash", text)
        self.assertIn("--auto --merge", text)


class BuildRulesetsDriftGuardWorkflowTests(unittest.TestCase):
    def test_two_branch_model_produces_expected_branches_list(self):
        text = repo_init.build_rulesets_drift_guard_workflow(["dev", "prd"])
        doc = yaml.safe_load(text)
        self.assertEqual(doc[True]["pull_request"]["branches"], ["dev", "prd"])
        self.assertEqual(doc[True]["push"]["branches"], ["dev", "prd"])

    def test_three_branch_model_produces_expected_branches_list(self):
        text = repo_init.build_rulesets_drift_guard_workflow(["dev", "stg", "prd"])
        doc = yaml.safe_load(text)
        self.assertEqual(doc[True]["pull_request"]["branches"], ["dev", "stg", "prd"])
        self.assertEqual(doc[True]["push"]["branches"], ["dev", "stg", "prd"])

    def test_has_app_token_step_and_no_secrets_github_token_for_rulesets_api(self):
        text = repo_init.build_rulesets_drift_guard_workflow(["dev", "prd"])
        doc = yaml.safe_load(text)
        steps = doc["jobs"]["rulesets-check"]["steps"]
        app_token_step = next(s for s in steps if s.get("id") == "app-token")
        self.assertEqual(app_token_step["uses"], "actions/create-github-app-token@v3")
        rulesets_steps = [
            s for s in steps
            if "run" in s and "rulesets_sync.py" in s["run"]
        ]
        self.assertTrue(rulesets_steps)
        for step in rulesets_steps:
            self.assertNotIn("secrets.GITHUB_TOKEN", str(step))

    def test_credential_guard_if_conditions_present(self):
        text = repo_init.build_rulesets_drift_guard_workflow(["dev", "prd"])
        doc = yaml.safe_load(text)
        steps = doc["jobs"]["rulesets-check"]["steps"]

        app_token_step = next(s for s in steps if s.get("id") == "app-token")
        self.assertEqual(
            app_token_step["if"],
            "${{ env.RULESETS_APP_ID != '' && env.RULESETS_APP_PRIVATE_KEY != '' }}")

        check_step = next(
            s for s in steps if s.get("name") == "Check committed rulesets against live GitHub rulesets")
        self.assertEqual(
            check_step["if"],
            "${{ github.event_name != 'push' && steps.app-token.outputs.token != '' }}")

        apply_step = next(
            s for s in steps if s.get("name") == "Apply committed rulesets to live GitHub rulesets")
        self.assertEqual(
            apply_step["if"],
            "${{ github.event_name == 'push' && steps.app-token.outputs.token != '' }}")


class RulesetStructuralViewTests(unittest.TestCase):
    def test_strips_required_status_checks_rule_entirely(self):
        with_checks = repo_init.build_ruleset_for_branch("dev", "2", "some-check")
        without_checks = repo_init.build_ruleset_for_branch("dev", "2")
        self.assertEqual(
            repo_init._ruleset_structural_view(with_checks),
            repo_init._ruleset_structural_view(without_checks))

    def test_structural_change_still_detected(self):
        two_branch = repo_init.build_ruleset_for_branch("dev", "2")
        three_branch = repo_init.build_ruleset_for_branch("dev", "3")
        self.assertNotEqual(
            repo_init._ruleset_structural_view(two_branch),
            repo_init._ruleset_structural_view(three_branch))


class ComputeDriftTests(unittest.TestCase):
    def _state(self, repo: Path, **overrides):
        state = repo_init.detect_state(repo)
        state.update(overrides)
        return state

    def test_no_drift_when_files_match_current_templates(self):
        repo = _tmp_repo()
        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        for branch in ("dev", "prd"):
            ruleset = repo_init.build_ruleset_for_branch(branch, "2")
            (rulesets_dir / f"protect-{branch}.json").write_text(json.dumps(ruleset) + "\n")
        automerge_path = repo / repo_init.AUTOMERGE_WORKFLOW_RELPATH
        automerge_path.parent.mkdir(parents=True, exist_ok=True)
        automerge_path.write_text(repo_init.build_automerge_workflow())
        state = self._state(repo)
        drift = repo_init.compute_drift(repo, state, ["dev", "prd"], "2")
        self.assertEqual(drift, [])

    def test_ruleset_structural_drift_detected_but_extra_required_check_is_not(self):
        repo = _tmp_repo()
        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        # Operator-grown required_status_checks: not drift on its own.
        ruleset = repo_init.build_ruleset_for_branch("dev", "2", "some-other-check")
        (rulesets_dir / "protect-dev.json").write_text(json.dumps(ruleset) + "\n")
        state = self._state(repo)
        self.assertEqual(repo_init.compute_drift(repo, state, ["dev"], "2"), [])

        # Now introduce a genuine structural change (branch_model "3" adds
        # required_linear_history to dev) and confirm it IS flagged.
        ruleset3 = repo_init.build_ruleset_for_branch("dev", "3", "some-other-check")
        (rulesets_dir / "protect-dev.json").write_text(json.dumps(ruleset3) + "\n")
        drift = repo_init.compute_drift(repo, state, ["dev"], "2")
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["path"], ".github/rulesets/protect-dev.json")
        self.assertIn("required_status_checks is intentionally excluded", drift[0]["detail"])

    def test_automerge_workflow_content_drift_detected(self):
        repo = _tmp_repo()
        automerge_path = repo / repo_init.AUTOMERGE_WORKFLOW_RELPATH
        automerge_path.parent.mkdir(parents=True, exist_ok=True)
        automerge_path.write_text("name: stale hand-edited workflow\n")
        state = self._state(repo, automerge_workflow_exists=True)
        drift = repo_init.compute_drift(repo, state, ["dev", "prd"], "2")
        paths = [d["path"] for d in drift]
        self.assertIn(repo_init.AUTOMERGE_WORKFLOW_RELPATH, paths)

    def test_openspec_validate_workflow_content_drift_detected(self):
        repo = _tmp_repo()
        wf_path = repo / repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text("name: stale\n")
        state = self._state(repo, openspec_validate_workflow_exists=True)
        drift = repo_init.compute_drift(repo, state, ["dev", "prd"], "2")
        paths = [d["path"] for d in drift]
        self.assertIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, paths)

    def test_policy_yaml_and_agents_md_never_flagged_as_drift(self):
        repo = _tmp_repo()
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text("pre_pr_cmd: pytest -q\n")
        (repo / "AGENTS.md").write_text("# hand-authored content\n")
        state = self._state(repo)
        drift = repo_init.compute_drift(repo, state, ["dev", "prd"], "2")
        paths = [d["path"] for d in drift]
        self.assertNotIn(".worktrail/policy.yaml", paths)
        self.assertNotIn("AGENTS.md", paths)


def _fake_init_openspec(repo: Path):
    """Mirrors repo_init.init_openspec's file-existence idempotency without
    actually shelling out to npx."""
    marker = repo / "openspec" / "config.yaml"
    if marker.is_file():
        return False, None
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("schema: spec-driven\n")
    return True, None


class ProposeTests(unittest.TestCase):
    def _run_propose(self, repo: Path, **overrides):
        args = mock.Mock(
            repo=str(repo), branch_model="2", check=False, with_aspens=False,
            with_gitnexus=False, as_json=True)
        for key, value in overrides.items():
            setattr(args, key, value)
        with mock.patch.object(repo_init, "init_openspec", side_effect=_fake_init_openspec):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_propose(args)
        return rc, json.loads(printed.call_args[0][0])

    def test_fresh_repo_writes_everything(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertIn("AGENTS.md", result["written"])
        self.assertIn("CLAUDE.md", result["written"])
        self.assertIn(".github/rulesets/protect-dev.json", result["written"])
        self.assertIn(".github/rulesets/protect-prd.json", result["written"])
        self.assertIn(".worktrail/policy.yaml", result["written"])
        self.assertIn(".github/workflows/worktrail-auto-merge.yml", result["written"])
        self.assertTrue((repo / ".github" / "rulesets" / "protect-dev.json").is_file())
        self.assertTrue((repo / ".worktrail" / "policy.yaml").is_file())
        self.assertTrue((repo / ".github" / "workflows" / "worktrail-auto-merge.yml").is_file())
        # This run itself configured required_status_checks (openspec-validate), so the
        # ungated-automerge warning must not fire -- it would be false.
        self.assertFalse(any("nothing else to gate" in w for w in result["warnings"]))

    def test_fresh_repo_writes_openspec_validate_workflow_and_gates_rulesets(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, result["written"])
        workflow_path = repo / repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH
        self.assertTrue(workflow_path.is_file())
        self.assertEqual(workflow_path.read_text(), repo_init.build_openspec_validate_workflow())
        for branch_file in ("protect-dev.json", "protect-prd.json"):
            ruleset = json.loads(
                (repo / ".github" / "rulesets" / branch_file).read_text())
            rsc_rule = next(
                r for r in ruleset["rules"] if r["type"] == "required_status_checks")
            self.assertEqual(
                rsc_rule["parameters"]["required_status_checks"],
                [{"context": repo_init.OPENSPEC_VALIDATE_JOB_NAME}])

    def test_rerun_after_workflow_written_is_noop_on_workflow_and_required_checks(self):
        # Task 5.3: once the openspec-validate workflow exists from a prior
        # propose run, a second propose run must not touch the workflow file
        # or required_status_checks -- gating is keyed on
        # OPENSPEC_VALIDATE_WORKFLOW_RELPATH's presence, not on
        # openspec_initialized or on any other state. The hand-authored
        # variant is covered by
        # test_hand_authored_workflow_and_ruleset_missing_check_is_full_noop
        # below.
        repo = _tmp_repo()
        self._run_propose(repo)
        workflow_path = repo / repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH
        dev_ruleset_path = repo / ".github" / "rulesets" / "protect-dev.json"
        prd_ruleset_path = repo / ".github" / "rulesets" / "protect-prd.json"
        workflow_before = workflow_path.read_text()
        dev_ruleset_before = dev_ruleset_path.read_text()
        prd_ruleset_before = prd_ruleset_path.read_text()

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        self.assertNotIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, result["written"])
        self.assertTrue(
            any(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH in s for s in result["skipped"]))
        self.assertFalse(any("patched" in s for s in result["written"]))
        self.assertEqual(workflow_path.read_text(), workflow_before)
        self.assertEqual(dev_ruleset_path.read_text(), dev_ruleset_before)
        self.assertEqual(prd_ruleset_path.read_text(), prd_ruleset_before)

    def test_hand_authored_workflow_and_ruleset_missing_check_is_full_noop(self):
        # Task 5.3, "workflow already present, not newly written": unlike the
        # rerun-after-propose case above, this ruleset does NOT already
        # contain the check, so this pins the openspec_validate_newly_written
        # gate itself -- patch_ruleset_required_check's already-present
        # short-circuit (task 3.2) cannot account for a no-op here.
        repo = _tmp_repo()
        workflow_path = repo / repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH
        workflow_path.parent.mkdir(parents=True)
        hand_authored_workflow = "# hand-authored, not the generated workflow\n"
        workflow_path.write_text(hand_authored_workflow)

        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        dev_ruleset = repo_init.build_ruleset_for_branch("dev", "2")
        prd_ruleset = repo_init.build_ruleset_for_branch("prd", "2")
        (rulesets_dir / "protect-dev.json").write_text(json.dumps(dev_ruleset, indent=2) + "\n")
        (rulesets_dir / "protect-prd.json").write_text(json.dumps(prd_ruleset, indent=2) + "\n")
        dev_ruleset_before = (rulesets_dir / "protect-dev.json").read_text()
        prd_ruleset_before = (rulesets_dir / "protect-prd.json").read_text()

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        self.assertNotIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, result["written"])
        self.assertFalse(any("patched" in s for s in result["written"]))
        self.assertEqual(workflow_path.read_text(), hand_authored_workflow)
        self.assertEqual((rulesets_dir / "protect-dev.json").read_text(), dev_ruleset_before)
        self.assertEqual((rulesets_dir / "protect-prd.json").read_text(), prd_ruleset_before)

    def test_already_onboarded_repo_patches_existing_rulesets_without_altering_other_rules(self):
        repo = _tmp_repo()
        (repo / "openspec").mkdir()
        (repo / "openspec" / "config.yaml").write_text("schema: spec-driven\n")
        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        original_rulesets = {}

        # protect-dev.json: hand-customized -- non-empty bypass_actors, a
        # non-default enforcement, and an extra rule type -- so that a
        # wholesale-regenerate implementation (which would discard all of
        # this and re-emit exactly what build_ruleset_for_branch produces)
        # is distinguishable from a genuine in-place patch. No existing
        # required_status_checks entries here, so this file also exercises
        # the create-the-rule branch of patch_ruleset_required_check.
        dev_ruleset = repo_init.build_ruleset_for_branch("dev", "2")
        dev_ruleset["bypass_actors"] = [
            {"actor_id": 1, "actor_type": "Team", "bypass_mode": "always"}]
        dev_ruleset["enforcement"] = "evaluate"
        dev_ruleset["rules"].append({"type": "creation"})
        original_rulesets["protect-dev.json"] = dev_ruleset
        (rulesets_dir / "protect-dev.json").write_text(
            json.dumps(dev_ruleset, indent=2) + "\n")

        # protect-prd.json: already has an unrelated required_status_checks
        # entry, so this file exercises the append-onto-an-existing-list
        # branch of patch_ruleset_required_check (task 5.4 covers the
        # already-contains-this-job-name no-op, not this append case).
        prd_ruleset = repo_init.build_ruleset(
            "protect-prd", "prd", ["merge"], ["Lint, Test & Build"])
        original_rulesets["protect-prd.json"] = prd_ruleset
        (rulesets_dir / "protect-prd.json").write_text(
            json.dumps(prd_ruleset, indent=2) + "\n")

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        # The workflow was absent, so this run writes it.
        self.assertIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, result["written"])
        self.assertTrue(
            (repo / repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH).is_file())

        for file_name, original in original_rulesets.items():
            path = rulesets_dir / file_name
            patched = json.loads(path.read_text())
            # Every rule other than required_status_checks is untouched.
            original_other_rules = [
                r for r in original["rules"] if r["type"] != "required_status_checks"]
            patched_other_rules = [
                r for r in patched["rules"] if r["type"] != "required_status_checks"]
            self.assertEqual(patched_other_rules, original_other_rules)
            self.assertEqual(patched["name"], original["name"])
            self.assertEqual(patched["conditions"], original["conditions"])
            self.assertEqual(patched["bypass_actors"], original["bypass_actors"])
            self.assertEqual(patched["enforcement"], original["enforcement"])
            self.assertTrue(
                any(f"{file_name} (patched" in w for w in result["written"]))

        # protect-dev.json had no pre-existing required_status_checks rule:
        # the new check is the sole entry.
        dev_rsc = next(
            r for r in json.loads((rulesets_dir / "protect-dev.json").read_text())["rules"]
            if r["type"] == "required_status_checks")
        self.assertEqual(
            dev_rsc["parameters"]["required_status_checks"],
            [{"context": repo_init.OPENSPEC_VALIDATE_JOB_NAME}])

        # protect-prd.json had a pre-existing "Lint, Test & Build" check:
        # the new check is appended, not clobbering it.
        prd_rsc = next(
            r for r in json.loads((rulesets_dir / "protect-prd.json").read_text())["rules"]
            if r["type"] == "required_status_checks")
        self.assertEqual(
            prd_rsc["parameters"]["required_status_checks"],
            [{"context": "Lint, Test & Build"}, {"context": repo_init.OPENSPEC_VALIDATE_JOB_NAME}])

    def test_unrelated_discovered_ci_job_is_not_added_to_required_status_checks(self):
        # Task 5.5: discover_ci_checks() finding an unrelated CI job must
        # not leak it into required_status_checks -- only
        # OPENSPEC_VALIDATE_JOB_NAME is ever caller-supplied to
        # build_ruleset_for_branch (task 2.2). This exercises the
        # fresh-ruleset-generation path.
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "on: pull_request\n"
            "jobs:\n"
            "  test:\n"
            "    name: Lint, Test & Build\n"
            "    runs-on: ubuntu-latest\n")

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        self.assertIn("Lint, Test & Build", result["ci_jobs_discovered"])
        for branch_file in ("protect-dev.json", "protect-prd.json"):
            ruleset = json.loads(
                (repo / ".github" / "rulesets" / branch_file).read_text())
            rsc_rule = next(
                r for r in ruleset["rules"] if r["type"] == "required_status_checks")
            self.assertEqual(
                rsc_rule["parameters"]["required_status_checks"],
                [{"context": repo_init.OPENSPEC_VALIDATE_JOB_NAME}])

    def test_unrelated_discovered_ci_job_is_not_patched_into_existing_ruleset(self):
        # Same scenario as above, but exercising the patch-an-existing-file
        # path (task 3.1) rather than fresh generation.
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "on: pull_request\n"
            "jobs:\n"
            "  test:\n"
            "    name: Lint, Test & Build\n"
            "    runs-on: ubuntu-latest\n")

        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        dev_ruleset = repo_init.build_ruleset_for_branch("dev", "2")
        (rulesets_dir / "protect-dev.json").write_text(
            json.dumps(dev_ruleset, indent=2) + "\n")

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        self.assertIn("Lint, Test & Build", result["ci_jobs_discovered"])
        patched = json.loads((rulesets_dir / "protect-dev.json").read_text())
        rsc_rule = next(
            r for r in patched["rules"] if r["type"] == "required_status_checks")
        self.assertEqual(
            rsc_rule["parameters"]["required_status_checks"],
            [{"context": repo_init.OPENSPEC_VALIDATE_JOB_NAME}])

    def test_preexisting_ruleset_already_containing_check_is_byte_for_byte_unchanged(self):
        # Task 5.4: the workflow is newly written this run, and one ruleset
        # file already exists AND already has the job's exact display name
        # in required_status_checks -- patch_ruleset_required_check's
        # already-present short-circuit (task 3.2) must leave the file
        # byte-for-byte untouched, not merely semantically equivalent.
        repo = _tmp_repo()
        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        dev_ruleset = repo_init.build_ruleset(
            "protect-dev", "dev", ["squash"], [repo_init.OPENSPEC_VALIDATE_JOB_NAME])
        dev_path = rulesets_dir / "protect-dev.json"
        dev_text_before = json.dumps(dev_ruleset, indent=2) + "\n"
        dev_path.write_text(dev_text_before)
        dev_mtime_before = dev_path.stat().st_mtime_ns

        rc, result = self._run_propose(repo)

        self.assertEqual(rc, 0)
        # The workflow was absent, so this run still writes it.
        self.assertIn(repo_init.OPENSPEC_VALIDATE_WORKFLOW_RELPATH, result["written"])
        # protect-dev.json is untouched: not reported as patched or written,
        # its bytes are identical, and it was never rewritten to disk.
        self.assertFalse(any("patched" in w for w in result["written"]))
        self.assertNotIn(str(dev_path.relative_to(repo)), result["written"])
        self.assertTrue(
            any(str(dev_path.relative_to(repo)) in s for s in result["skipped"]))
        self.assertEqual(dev_path.read_text(), dev_text_before)
        self.assertEqual(dev_path.stat().st_mtime_ns, dev_mtime_before)

    def test_three_branch_model_writes_stg_too(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo, branch_model="3")
        self.assertEqual(rc, 0)
        self.assertIn(".github/rulesets/protect-stg.json", result["written"])

    def test_rerun_skips_already_written_files(self):
        repo = _tmp_repo()
        self._run_propose(repo)
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(result["written"], [])
        self.assertTrue(any("already split" in s for s in result["skipped"]))
        self.assertTrue(any("protect-dev.json" in s for s in result["skipped"]))

    def test_legacy_policy_filename_is_respected_not_overwritten(self):
        repo = _tmp_repo()
        (repo / "docs" / "specs").mkdir(parents=True)
        (repo / "docs" / "specs" / "go-policy.yaml").write_text("agent_cli: codex\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertFalse((repo / ".worktrail" / "policy.yaml").is_file())
        self.assertTrue(any("legacy policy filename" in s for s in result["skipped"]))

    def test_interim_policy_filename_is_respected_not_overwritten(self):
        repo = _tmp_repo()
        (repo / "docs" / "specs").mkdir(parents=True)
        (repo / "docs" / "specs" / "worktrail-go-policy.yaml").write_text("agent_cli: codex\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertFalse((repo / ".worktrail" / "policy.yaml").is_file())
        self.assertTrue(any("legacy policy filename" in s for s in result["skipped"]))

    def test_automerge_workflow_skipped_when_already_present(self):
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "worktrail-auto-merge.yml").write_text("# hand-customized\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            (wf_dir / "worktrail-auto-merge.yml").read_text(), "# hand-customized\n")
        self.assertTrue(any("worktrail-auto-merge.yml" in s for s in result["skipped"]))

    def test_fresh_repo_writes_rulesets_drift_guard_workflow_and_script(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertIn(".github/workflows/rulesets_drift_guard.yml", result["written"])
        self.assertIn("scripts/ci/rulesets/rulesets_sync.py", result["written"])
        self.assertIn("scripts/ci/rulesets/requirements.txt", result["written"])
        self.assertTrue(
            (repo / ".github" / "workflows" / "rulesets_drift_guard.yml").is_file())
        self.assertTrue((repo / "scripts" / "ci" / "rulesets" / "rulesets_sync.py").is_file())
        self.assertTrue(
            (repo / "scripts" / "ci" / "rulesets" / "requirements.txt").is_file())

    def test_rerun_skips_already_present_drift_guard_workflow(self):
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "rulesets_drift_guard.yml").write_text("# hand-customized\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(
            (wf_dir / "rulesets_drift_guard.yml").read_text(), "# hand-customized\n")
        self.assertTrue(any("rulesets_drift_guard.yml" in s for s in result["skipped"]))

    def test_no_ungated_automerge_warning_when_ci_jobs_exist(self):
        repo = _tmp_repo()
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "on: pull_request\njobs:\n  test:\n    name: Lint, Test & Build\n    runs-on: ubuntu-latest\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertFalse(any("nothing else to gate" in w for w in result["warnings"]))

    def test_check_mode_writes_nothing(self):
        repo = _tmp_repo()
        args = mock.Mock(repo=str(repo), branch_model="2", check=True, as_json=True)
        with mock.patch("builtins.print") as printed:
            rc = repo_init.cmd_propose(args)
        self.assertEqual(rc, 0)
        self.assertFalse((repo / "AGENTS.md").is_file())
        self.assertFalse((repo / ".github").exists())
        state = json.loads(printed.call_args[0][0])
        self.assertFalse(state["claude_md_already_split"])

    def test_nonexistent_repo_errors(self):
        args = mock.Mock(repo="/nonexistent/path/xyz", branch_model="2", check=False, as_json=True)
        rc = repo_init.cmd_propose(args)
        self.assertEqual(rc, 1)

    def test_with_aspens_writes_add_ons_block_and_runs_configure(self):
        repo = _tmp_repo()
        with mock.patch.object(repo_init, "enable_aspens", return_value=(True, None)) as ea:
            rc, result = self._run_propose(repo, with_aspens=True)
        self.assertEqual(rc, 0)
        ea.assert_called_once()
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertIn("add_ons:", policy_text)
        self.assertIn("aspens:", policy_text)
        self.assertIn(".aspens.json (aspens doc init)", result["written"])

    def test_without_aspens_flag_leaves_policy_file_bare(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo)
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertNotIn("add_ons:", policy_text)

    def test_aspens_warning_surfaces_without_failing_propose(self):
        repo = _tmp_repo()
        with mock.patch.object(
            repo_init, "enable_aspens", return_value=(False, "aspens doc init did not produce ...")
        ):
            rc, result = self._run_propose(repo, with_aspens=True)
        self.assertEqual(rc, 0)
        self.assertTrue(any("aspens doc init" in w for w in result["warnings"]))

    def test_with_gitnexus_runs_indexing_and_leaves_policy_bare(self):
        repo = _tmp_repo()
        with mock.patch.object(repo_init, "enable_gitnexus", return_value=(True, None)) as eg:
            rc, result = self._run_propose(repo, with_gitnexus=True)
        self.assertEqual(rc, 0)
        eg.assert_called_once()
        self.assertIn(".gitnexus/ (gitnexus analyze)", result["written"])
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertNotIn("gitnexus", policy_text)

    def test_without_gitnexus_flag_invokes_no_gitnexus_behavior(self):
        repo = _tmp_repo()
        with mock.patch.object(repo_init, "enable_gitnexus") as eg:
            rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        eg.assert_not_called()
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertNotIn("gitnexus", policy_text)

    def test_gitnexus_warning_surfaces_without_failing_propose(self):
        repo = _tmp_repo()
        with mock.patch.object(
            repo_init, "enable_gitnexus", return_value=(False, "gitnexus analyze did not produce ...")
        ):
            rc, result = self._run_propose(repo, with_gitnexus=True)
        self.assertEqual(rc, 0)
        self.assertTrue(any("gitnexus analyze" in w for w in result["warnings"]))
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertNotIn("gitnexus", policy_text)

    def test_with_gitnexus_already_indexed_wires_through_skipped(self):
        repo = _tmp_repo()
        with mock.patch.object(repo_init, "enable_gitnexus", return_value=(False, None)) as eg:
            rc, result = self._run_propose(repo, with_gitnexus=True)
        self.assertEqual(rc, 0)
        eg.assert_called_once()
        self.assertIn(".gitnexus/ (already indexed)", result["skipped"])
        policy_text = (repo / ".worktrail" / "policy.yaml").read_text()
        self.assertNotIn("gitnexus", policy_text)

    def test_fresh_repo_reports_no_drift(self):
        repo = _tmp_repo()
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(result["drift"], [])

    def test_second_run_on_already_onboarded_repo_reports_no_drift(self):
        repo = _tmp_repo()
        self._run_propose(repo)
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(result["drift"], [])

    def test_stale_automerge_workflow_surfaces_in_drift_and_is_not_rewritten(self):
        repo = _tmp_repo()
        self._run_propose(repo)
        automerge_path = repo / repo_init.AUTOMERGE_WORKFLOW_RELPATH
        automerge_path.write_text("name: hand-edited stale content\n")
        rc, result = self._run_propose(repo)
        self.assertEqual(rc, 0)
        drift_paths = [d["path"] for d in result["drift"]]
        self.assertIn(repo_init.AUTOMERGE_WORKFLOW_RELPATH, drift_paths)
        # Report-only: propose must never overwrite a drifted file on its own.
        self.assertEqual(automerge_path.read_text(), "name: hand-edited stale content\n")
        self.assertIn(f"{repo_init.AUTOMERGE_WORKFLOW_RELPATH} (already exists)", result["skipped"])

    def test_check_mode_includes_drift(self):
        repo = _tmp_repo()
        self._run_propose(repo)
        automerge_path = repo / repo_init.AUTOMERGE_WORKFLOW_RELPATH
        automerge_path.write_text("name: hand-edited stale content\n")
        rc, result = self._run_propose(repo, check=True)
        self.assertEqual(rc, 0)
        drift_paths = [d["path"] for d in result["drift"]]
        self.assertIn(repo_init.AUTOMERGE_WORKFLOW_RELPATH, drift_paths)


class EnableAspensTests(unittest.TestCase):
    def test_noop_when_already_configured(self):
        repo = _tmp_repo()
        (repo / ".aspens.json").write_text("{}\n")
        with mock.patch("worktrail.addons.aspens.AspensAddOn") as addon_cls:
            configured, warning = repo_init.enable_aspens(repo)
        self.assertFalse(configured)
        self.assertIsNone(warning)
        addon_cls.assert_not_called()

    def test_successful_configure_reports_configured(self):
        repo = _tmp_repo()

        def fake_configure(ctx):
            (Path(ctx.worktree) / ".aspens.json").write_text("{}\n")

        with mock.patch("worktrail.addons.aspens.AspensAddOn") as addon_cls:
            instance = addon_cls.return_value
            instance.configure.side_effect = fake_configure
            configured, warning = repo_init.enable_aspens(repo)
        self.assertTrue(configured)
        self.assertIsNone(warning)
        instance.install.assert_called_once()
        instance.configure.assert_called_once()

    def test_failed_configure_reports_warning_not_raised(self):
        repo = _tmp_repo()
        with mock.patch("worktrail.addons.aspens.AspensAddOn"):
            configured, warning = repo_init.enable_aspens(repo)
        self.assertFalse(configured)
        self.assertIn("aspens doc init did not produce", warning)


class EnableGitnexusTests(unittest.TestCase):
    def test_noop_when_already_indexed(self):
        repo = _tmp_repo()
        (repo / ".gitnexus").mkdir()
        with mock.patch("worktrail.onboarding.repo_init._run") as run:
            configured, warning = repo_init.enable_gitnexus(repo)
        self.assertFalse(configured)
        self.assertIsNone(warning)
        run.assert_not_called()

    def test_successful_indexing_reports_configured(self):
        repo = _tmp_repo()

        def fake_run(cmd, **kw):
            (repo / ".gitnexus").mkdir()
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch("worktrail.onboarding.repo_init._run", side_effect=fake_run) as run:
            configured, warning = repo_init.enable_gitnexus(repo)
        self.assertTrue(configured)
        self.assertIsNone(warning)
        run.assert_called_once()

    def test_failed_or_timeout_reports_warning_not_raised(self):
        repo = _tmp_repo()
        with mock.patch(
            "worktrail.onboarding.repo_init._run",
            side_effect=subprocess.TimeoutExpired(cmd="gitnexus", timeout=1),
        ):
            configured, warning = repo_init.enable_gitnexus(repo)
        self.assertFalse(configured)
        self.assertIn("gitnexus analyze did not produce", warning)


class ApplyTests(unittest.TestCase):
    def _repo_with_rulesets(self, branches=("dev", "prd")) -> Path:
        repo = _tmp_repo()
        rulesets_dir = repo / ".github" / "rulesets"
        rulesets_dir.mkdir(parents=True)
        for branch in branches:
            ruleset = repo_init.build_ruleset_for_branch(branch, "3" if "stg" in branches else "2")
            (rulesets_dir / f"protect-{branch}.json").write_text(json.dumps(ruleset))
        return repo

    def test_missing_rulesets_errors_without_touching_github(self):
        repo = _tmp_repo()
        args = mock.Mock(repo=str(repo), as_json=True)
        rc = repo_init.cmd_apply(args)
        self.assertEqual(rc, 1)

    def test_full_two_branch_apply_sequence(self):
        """A stateful fake: created branches/rulesets actually change what
        subsequent 'live' lookups return, so verification is exercised for
        real rather than assumed."""
        repo = self._repo_with_rulesets(("dev", "prd"))
        args = mock.Mock(repo=str(repo), as_json=True)
        state = {
            "default_branch": "master", "branches": {"master"}, "rulesets": [],
            "delete_branch_on_merge": False,
        }

        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if cmd[:3] == ["git", "-C", str(repo)]:
                return subprocess.CompletedProcess(cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr="")
            if joined == "gh api repos/acme/widget -q .default_branch":
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{state['default_branch']}\n", stderr="")
            if joined == "gh api repos/acme/widget -q .delete_branch_on_merge":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=f"{str(state['delete_branch_on_merge']).lower()}\n", stderr="")
            if joined == "gh api repos/acme/widget/branches/master -q .commit.sha":
                return subprocess.CompletedProcess(cmd, 0, stdout="deadbeef\n", stderr="")
            if joined == "gh api repos/acme/widget/branches/dev":
                rc = 0 if "dev" in state["branches"] else 1
                return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="" if rc == 0 else "404")
            if joined.startswith("gh api --method POST repos/acme/widget/git/refs"):
                state["branches"].add("dev")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh api --method POST repos/acme/widget/branches/master/rename -f new_name=prd":
                state["branches"].discard("master")
                state["branches"].add("prd")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh api --method PATCH repos/acme/widget -f default_branch=dev":
                state["default_branch"] = "dev"
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh api --method PATCH repos/acme/widget -f delete_branch_on_merge=true":
                state["delete_branch_on_merge"] = True
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(state["rulesets"]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "POST"] and joined.endswith("rulesets --input -"):
                payload = json.loads(kw["input"])
                state["rulesets"].append({"id": len(state["rulesets"]) + 1, "name": payload["name"]})
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(result["repo"], "acme/widget")
        self.assertEqual(result["branches"]["dev"], "created")
        self.assertEqual(result["branches"]["master"], "renamed to prd")
        self.assertEqual(result["default_branch"], "set to dev")
        self.assertEqual(result["delete_branch_on_merge"], "enabled")
        self.assertEqual(result["rulesets"]["protect-dev.json"], "created and verified live")
        self.assertEqual(result["rulesets"]["protect-prd.json"], "created and verified live")
        self.assertNotIn("labels", result)
        self.assertEqual(rc, 0)

    def test_labels_created_when_automerge_workflow_was_scaffolded(self):
        """Regression: worktrail-repo-init used to generate the auto-merge
        workflow (build_automerge_workflow()) without ever creating the
        go:risk-*/go:no-automerge labels it depends on, so PR labeling
        silently no-opped on every freshly onboarded repo."""
        repo = self._repo_with_rulesets(("dev", "prd"))
        workflow_path = repo / repo_init.AUTOMERGE_WORKFLOW_RELPATH
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(repo_init.build_automerge_workflow(), encoding="utf-8")
        args = mock.Mock(repo=str(repo), as_json=True)
        label_calls = []

        def fake_run(cmd, **kw):
            if cmd[:3] == ["git", "-C", str(repo)]:
                return subprocess.CompletedProcess(cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr="")
            if cmd[:3] == ["gh", "label", "create"]:
                label_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if " ".join(cmd) == "gh api repos/acme/widget -q .default_branch":
                return subprocess.CompletedProcess(cmd, 0, stdout="dev\n", stderr="")
            if " ".join(cmd) == "gh api repos/acme/widget -q .delete_branch_on_merge":
                return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
            if " ".join(cmd) == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                    [{"id": 1, "name": "protect-dev"}, {"id": 2, "name": "protect-prd"}]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "PUT"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(len(label_calls), 5)
        self.assertEqual(set(result["labels"].keys()), {
            "go:risk-low", "go:risk-medium", "go:risk-high",
            "go:risk-critical", "go:no-automerge"})
        self.assertTrue(all(status == "ok" for status in result["labels"].values()))
        self.assertEqual(rc, 0)

    def test_rerun_after_success_does_not_rename_dev(self):
        """Idempotency guard: once `apply` has succeeded, the default branch
        is 'dev' -- a naive re-run must not try to rename 'dev' to 'prd'."""
        repo = self._repo_with_rulesets(("dev", "prd"))
        args = mock.Mock(repo=str(repo), as_json=True)

        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if cmd[:3] == ["git", "-C", str(repo)]:
                return subprocess.CompletedProcess(cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr="")
            if joined == "gh api repos/acme/widget -q .default_branch":
                return subprocess.CompletedProcess(cmd, 0, stdout="dev\n", stderr="")
            if joined == "gh api repos/acme/widget -q .delete_branch_on_merge":
                return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                    [{"id": 1, "name": "protect-dev"}, {"id": 2, "name": "protect-prd"}]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "PUT"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh variable list --json name -R acme/widget":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([{"name": "RELEASE_NOTES_APP_ID"}]), stderr="")
            if joined == "gh secret list --json name -R acme/widget":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([{"name": "RELEASE_NOTES_APP_PRIVATE_KEY"}]), stderr="")
            # Any attempt to touch branches (create/rename) or re-enable an
            # already-enabled setting is a bug on a re-run.
            self.fail(f"unexpected branch/setting-mutating call on an already-applied repo: {cmd}")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(result["branches"], {})
        self.assertIn("already", " ".join(result["warnings"]))
        self.assertEqual(result["default_branch"], "already dev")
        self.assertEqual(result["delete_branch_on_merge"], "already enabled")
        self.assertEqual(rc, 0)

    def test_gh_repo_unresolvable_errors(self):
        repo = self._repo_with_rulesets(("dev", "prd"))
        args = mock.Mock(repo=str(repo), as_json=True)
        with mock.patch.object(
            repo_init, "_run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="not a repo"),
        ):
            rc = repo_init.cmd_apply(args)
        self.assertEqual(rc, 1)

    def _fake_run_already_applied(self, repo, variable_names, secret_names):
        """An 'already applied' repo (default is dev) so branch/ruleset calls
        are all no-ops, isolating the credential-reminder check under test."""
        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if cmd[:3] == ["git", "-C", str(repo)]:
                return subprocess.CompletedProcess(cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr="")
            if joined == "gh api repos/acme/widget -q .default_branch":
                return subprocess.CompletedProcess(cmd, 0, stdout="dev\n", stderr="")
            if joined == "gh api repos/acme/widget -q .delete_branch_on_merge":
                return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                    [{"id": 1, "name": "protect-dev"}, {"id": 2, "name": "protect-prd"}]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "PUT"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if joined == "gh variable list --json name -R acme/widget":
                data = [{"name": name} for name in variable_names]
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data), stderr="")
            if joined == "gh secret list --json name -R acme/widget":
                data = [{"name": name} for name in secret_names]
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data), stderr="")
            self.fail(f"unexpected call in credential-reminder test: {cmd}")
        return fake_run

    def test_reminder_printed_when_app_credentials_missing(self):
        repo = self._repo_with_rulesets(("dev", "prd"))
        args = mock.Mock(repo=str(repo), as_json=True)
        fake_run = self._fake_run_already_applied(repo, variable_names=[], secret_names=[])

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(rc, 0)
        self.assertTrue(
            any("RELEASE_NOTES_APP_ID" in w and "RELEASE_NOTES_APP_PRIVATE_KEY" in w
                for w in result["warnings"]),
            result["warnings"],
        )

    def test_no_reminder_when_app_credentials_present(self):
        repo = self._repo_with_rulesets(("dev", "prd"))
        args = mock.Mock(repo=str(repo), as_json=True)
        fake_run = self._fake_run_already_applied(
            repo,
            variable_names=["RELEASE_NOTES_APP_ID"],
            secret_names=["RELEASE_NOTES_APP_PRIVATE_KEY"],
        )

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(rc, 0)
        self.assertFalse(
            any("RELEASE_NOTES_APP_ID" in w for w in result["warnings"]),
            result["warnings"],
        )


class ResolveRepoDisplayNameTests(unittest.TestCase):
    def test_no_git_remote_falls_back_to_directory_name(self):
        repo = _tmp_repo()
        with mock.patch.object(repo_init, "resolve_gh_repo", return_value=None):
            self.assertEqual(repo_init.resolve_repo_display_name(repo), repo.name)

    def test_worktree_directory_name_does_not_leak_into_repo_name(self):
        # Regression: a worktree checkout conventionally lives at
        # <repo>-worktrees/<branch>/, so repo.name alone would resolve to
        # the branch name ("repo-standards"), not the actual repo ("hearsay").
        worktree_path = Path("/home/user/projects/hearsay-worktrees/repo-standards")
        with mock.patch.object(repo_init, "resolve_gh_repo", return_value="behindthedash/hearsay"):
            self.assertEqual(repo_init.resolve_repo_display_name(worktree_path), "hearsay")


class ApplyRulesetTests(unittest.TestCase):
    def test_creates_when_no_live_ruleset_shares_name(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            joined = " ".join(cmd)
            if joined == "gh api repos/acme/widget/rulesets":
                already_created = any(c[:4] == ["gh", "api", "--method", "POST"] for c in calls)
                data = [{"id": 9, "name": "protect-dev"}] if already_created else []
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            ok, detail = repo_init.apply_ruleset("acme/widget", {"name": "protect-dev"})
        self.assertTrue(ok)
        self.assertIn("created", detail)

    def test_updates_when_live_ruleset_shares_name(self):
        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([{"id": 9, "name": "protect-dev"}]), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            ok, detail = repo_init.apply_ruleset("acme/widget", {"name": "protect-dev"})
        self.assertTrue(ok)
        self.assertIn("updated", detail)

    def test_put_failure_reports_failed(self):
        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps([{"id": 9, "name": "protect-dev"}]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "PUT"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            ok, detail = repo_init.apply_ruleset("acme/widget", {"name": "protect-dev"})
        self.assertFalse(ok)
        self.assertIn("FAILED", detail)


class EnsureAutomergeLabelsTests(unittest.TestCase):
    def test_creates_or_updates_all_five_labels_via_force(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            result = repo_init.ensure_automerge_labels("acme/widget")

        expected_names = {"go:risk-low", "go:risk-medium", "go:risk-high",
                           "go:risk-critical", "go:no-automerge"}
        self.assertEqual(set(result.keys()), expected_names)
        self.assertTrue(all(status == "ok" for status in result.values()))
        self.assertEqual(len(calls), 5)
        for cmd in calls:
            self.assertEqual(cmd[:3], ["gh", "label", "create"])
            self.assertIn("--force", cmd)
            self.assertIn("--repo", cmd)
            self.assertEqual(cmd[cmd.index("--repo") + 1], "acme/widget")

    def test_one_label_failure_is_reported_without_aborting_the_rest(self):
        def fake_run(cmd, **kw):
            if cmd[3] == "go:risk-high":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            result = repo_init.ensure_automerge_labels("acme/widget")

        self.assertIn("FAILED", result["go:risk-high"])
        self.assertEqual(result["go:risk-low"], "ok")
        self.assertEqual(result["go:no-automerge"], "ok")


class InitOpenspecTests(unittest.TestCase):
    def test_already_initialized_is_a_noop(self):
        repo = _tmp_repo()
        (repo / "openspec").mkdir()
        (repo / "openspec" / "config.yaml").write_text("schema: spec-driven\n")
        with mock.patch.object(repo_init, "_run") as run_mock:
            changed, warning = repo_init.init_openspec(repo)
        self.assertFalse(changed)
        self.assertIsNone(warning)
        run_mock.assert_not_called()

    def test_failure_is_reported_as_warning_not_raised(self):
        repo = _tmp_repo()
        with mock.patch.object(
            repo_init, "_run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="npx exploded"),
        ):
            changed, warning = repo_init.init_openspec(repo)
        self.assertFalse(changed)
        self.assertIn("npx exploded", warning)


if __name__ == "__main__":
    unittest.main()

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
        # No CI discovered -> the safety warning about an ungated auto-merge must fire.
        self.assertTrue(any("nothing else to gate" in w for w in result["warnings"]))

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

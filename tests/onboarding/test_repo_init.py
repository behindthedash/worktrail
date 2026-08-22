"""Tests for onboarding/repo_init.py -- the worktrail-repo-init CLI."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        args = mock.Mock(repo=str(repo), branch_model="2", check=False, as_json=True)
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
        self.assertIn("docs/specs/worktrail-go-policy.yaml", result["written"])
        self.assertTrue((repo / ".github" / "rulesets" / "protect-dev.json").is_file())
        self.assertTrue((repo / "docs" / "specs" / "worktrail-go-policy.yaml").is_file())

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
        self.assertFalse((repo / "docs" / "specs" / "worktrail-go-policy.yaml").is_file())
        self.assertTrue(any("go-policy.yaml" in s for s in result["skipped"]))

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
        state = {"default_branch": "master", "branches": {"master"}, "rulesets": []}

        def fake_run(cmd, **kw):
            joined = " ".join(cmd)
            if cmd[:3] == ["git", "-C", str(repo)]:
                return subprocess.CompletedProcess(cmd, 0, stdout="git@github.com:acme/widget.git\n", stderr="")
            if joined == "gh api repos/acme/widget -q .default_branch":
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{state['default_branch']}\n", stderr="")
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
            if cmd[:4] == ["gh", "api", "--method", "PATCH"]:
                state["default_branch"] = "dev"
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
        self.assertEqual(result["rulesets"]["protect-dev.json"], "created and verified live")
        self.assertEqual(result["rulesets"]["protect-prd.json"], "created and verified live")
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
            if joined == "gh api repos/acme/widget/rulesets":
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                    [{"id": 1, "name": "protect-dev"}, {"id": 2, "name": "protect-prd"}]), stderr="")
            if cmd[:4] == ["gh", "api", "--method", "PUT"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            # Any attempt to touch branches (create/rename) is a bug on a re-run.
            self.fail(f"unexpected branch-mutating call on an already-applied repo: {cmd}")

        with mock.patch.object(repo_init, "_run", side_effect=fake_run):
            with mock.patch("builtins.print") as printed:
                rc = repo_init.cmd_apply(args)

        result = json.loads(printed.call_args[0][0])
        self.assertEqual(result["branches"], {})
        self.assertIn("already", " ".join(result["warnings"]))
        self.assertEqual(result["default_branch"], "already dev")
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

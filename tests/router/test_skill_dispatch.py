import json
import os
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from worktrail.router import skill_dispatch


class SkillDispatchTests(unittest.TestCase):
    def test_claude_uses_native_style_prompt_and_provider(self):
        command = skill_dispatch.build_command("claude", "worktrail-sdd-workflow", "route:E 002-bootstrap")
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertEqual(command[2], "/worktrail-sdd-workflow route:E 002-bootstrap")

    def test_codex_preserves_codex_binary(self):
        command = skill_dispatch.build_command("codex", "worktrail-sdd-workflow", "route:E")
        self.assertEqual(command[:5], ["codex", "exec", "--json", "-s", "workspace-write"])
        self.assertIn("Use the installed skill 'worktrail-sdd-workflow'", command[-1])
        self.assertNotIn("claude", command)

    def test_codex_receives_explicit_additional_writable_dirs(self):
        command = skill_dispatch.build_command(
            "codex",
            "worktrail-sdd-workflow",
            "route:C",
            cwd="/repo",
            add_dirs=("/runs", "/repo-worktrees"),
        )
        self.assertEqual(
            command[
                command.index("--add-dir") : command.index("--model")
                if "--model" in command else -1
            ],
            ["--add-dir", "/runs", "--add-dir", "/repo-worktrees"],
        )

    def test_additional_writable_dirs_are_not_added_to_other_providers(self):
        for agent in ("claude", "opencode"):
            with self.subTest(agent=agent):
                command = skill_dispatch.build_command(
                    agent, "worktrail-sdd-workflow", add_dirs=("/runs",)
                )
                self.assertNotIn("--add-dir", command)

    def test_opencode_preserves_opencode_binary(self):
        command = skill_dispatch.build_command("opencode", "worktrail-sdd-workflow", "route:E")
        self.assertEqual(command[:4], ["opencode", "run", "--format", "json"])
        self.assertEqual(command[-1], "/worktrail-sdd-workflow route:E")

    def test_args_are_one_argument_and_extra_args_are_not_shell_parsed(self):
        command = skill_dispatch.build_command("codex", "worktrail-sdd-workflow", "route:E; do-not-execute", extra_args=("--flag", "value"))
        self.assertIn("route:E; do-not-execute", command[-3])
        self.assertEqual(command[-2:], ["--flag", "value"])

    def test_cli_json_is_parseable(self):
        output = StringIO()
        with redirect_stdout(output):
            skill_dispatch.main(["--agent", "opencode", "--skill", "x:y", "--json", "--dry-run"])
        self.assertEqual(json.loads(output.getvalue())[0], "opencode")

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_default_cli_executes_the_selected_provider(self, run):
        run.return_value.returncode = 0
        with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
            self.assertEqual(
                skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y", "--args", "route:E",
                    "--codex-home", "/tmp/worktrail-codex-test",
                ]), 0
            )
        self.assertEqual(run.call_args.args[0][0], "codex")
        self.assertTrue(run.call_args.kwargs["check"] is False)

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/worktrail-codex"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_codex_home_environment_override_is_passed_to_child(self, run):
        run.return_value.returncode = 0

        with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
            self.assertEqual(
                skill_dispatch.main(["--agent", "codex", "--skill", "x:y"]), 0
            )

        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], "/tmp/worktrail-codex")

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/from-env"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_explicit_codex_home_takes_precedence(self, run):
        run.return_value.returncode = 0

        with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
            skill_dispatch.main([
                "--agent", "codex", "--skill", "x:y", "--codex-home", "/tmp/explicit"
            ])

        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], "/tmp/explicit")

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/should-not-leak"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_codex_home_override_is_not_applied_to_other_providers(self, run):
        run.return_value.returncode = 0

        skill_dispatch.main(["--agent", "opencode", "--skill", "x:y"])

        self.assertNotIn("env", run.call_args.kwargs)

    def test_invalid_skill_name_is_rejected(self):
        with self.assertRaises(ValueError):
            skill_dispatch.build_command("codex", "../../not-a-skill")


class WorkingDirectoryTargetingTests(unittest.TestCase):
    """`--cwd` targets a worktree without relocating the calling session.

    This replaces the `EnterWorktree`/`ExitWorktree` relocation the propose step
    used to need, which always prompted for approval and so could not run
    unattended.
    """

    def test_codex_receives_its_native_working_root_flag(self):
        command = skill_dispatch.build_command("codex", "openspec-propose", cwd="/wt")
        self.assertEqual(command[command.index("-C") + 1], "/wt")

    def test_opencode_receives_its_native_working_root_flag(self):
        command = skill_dispatch.build_command("opencode", "openspec-propose", cwd="/wt")
        self.assertEqual(command[command.index("--dir") + 1], "/wt")

    def test_claude_has_no_working_root_flag_so_argv_is_unchanged(self):
        # claude exposes no equivalent flag; process cwd is the only lever, so
        # `cwd` must not invent one here.
        self.assertEqual(
            skill_dispatch.build_command("claude", "openspec-propose", cwd="/wt"),
            skill_dispatch.build_command("claude", "openspec-propose"),
        )

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_every_agent_is_launched_with_the_target_as_process_cwd(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            for agent in skill_dispatch.SUPPORTED_AGENTS:
                with self.subTest(agent=agent):
                    skill_dispatch.main(
                        ["--agent", agent, "--skill", "openspec-propose", "--cwd", tmp]
                    )
                    self.assertEqual(run.call_args.kwargs["cwd"], tmp)

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_a_missing_target_fails_loudly_instead_of_spawning(self, run):
        stderr = StringIO()
        with redirect_stderr(stderr):
            result = skill_dispatch.main(
                ["--agent", "claude", "--skill", "openspec-propose", "--cwd", "/no/such/wt"]
            )
        self.assertEqual(result, 1)
        self.assertIn("/no/such/wt", stderr.getvalue())
        run.assert_not_called()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_cwd_is_omitted_entirely_when_not_requested(self, run):
        run.return_value.returncode = 0
        skill_dispatch.main(["--agent", "claude", "--skill", "openspec-propose"])
        self.assertNotIn("cwd", run.call_args.kwargs)


class HeadlessWritePermissionTests(unittest.TestCase):
    """`--write` is opt-in so it never silently widens an existing dispatch."""

    def test_claude_gets_a_permission_mode_that_survives_a_headless_run(self):
        command = skill_dispatch.build_command("claude", "openspec-propose", write=True)
        self.assertEqual(
            command[command.index("--permission-mode") + 1], "bypassPermissions"
        )

    def test_opencode_auto_approves_permissions(self):
        self.assertIn(
            "--auto", skill_dispatch.build_command("opencode", "openspec-propose", write=True)
        )

    def test_codex_needs_nothing_extra_because_it_already_has_workspace_write(self):
        self.assertEqual(
            skill_dispatch.build_command("codex", "openspec-propose", write=True),
            skill_dispatch.build_command("codex", "openspec-propose"),
        )

    def test_no_agent_gains_write_permission_by_default(self):
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                command = skill_dispatch.build_command(agent, "openspec-propose")
                self.assertNotIn("--permission-mode", command)
                self.assertNotIn("--auto", command)
                self.assertNotIn("bypassPermissions", command)


class CodexHomePreflightTests(unittest.TestCase):
    def test_resolve_codex_home_prefers_explicit_override(self):
        self.assertEqual(skill_dispatch.resolve_codex_home("/tmp/explicit"), "/tmp/explicit")

    @patch.dict(os.environ, {"CODEX_HOME": "/tmp/inherited"}, clear=False)
    def test_resolve_codex_home_falls_back_to_inherited_env(self):
        self.assertEqual(skill_dispatch.resolve_codex_home(None), "/tmp/inherited")

    @patch.dict(os.environ, {}, clear=True)
    def test_resolve_codex_home_defaults_to_dot_codex_under_home(self):
        self.assertEqual(
            skill_dispatch.resolve_codex_home(None),
            os.path.join(os.path.expanduser("~"), ".codex"),
        )

    def test_write_remediation_is_none_for_a_writable_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(skill_dispatch.codex_home_write_remediation(tmp))

    def test_write_remediation_is_none_for_a_writable_nonexistent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "not-yet-created", "codex-home")
            self.assertIsNone(skill_dispatch.codex_home_write_remediation(target))

    def test_select_codex_home_falls_back_when_parent_home_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o555)
            try:
                inherited = os.path.join(tmp, "codex-home")
                with patch.dict(os.environ, {"CODEX_HOME": inherited}, clear=True):
                    with patch.object(skill_dispatch, "default_worktrail_codex_home", return_value="/tmp/worktrail-default"):
                        self.assertEqual(
                            skill_dispatch.select_codex_home(None),
                            ("/tmp/worktrail-default", True),
                        )
            finally:
                os.chmod(tmp, 0o755)

    def test_automatic_home_is_created_and_skills_are_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "skills"
            (source / "worktrail-sdd-workflow").mkdir(parents=True)
            (source / "worktrail-go").mkdir()
            child = Path(tmp) / "child-home"
            with patch.object(skill_dispatch, "_codex_skill_roots", return_value=[source]):
                skill_dispatch.ensure_codex_home(str(child))
                self.assertTrue(skill_dispatch.bootstrap_codex_skills(str(child), "worktrail-go"))
            self.assertTrue((child / "skills/worktrail-go").is_symlink())
            self.assertTrue((child / "skills/worktrail-sdd-workflow").is_symlink())

    def test_write_remediation_flags_a_read_only_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o555)
            try:
                target = os.path.join(tmp, "nested", "codex-home")
                message = skill_dispatch.codex_home_write_remediation(target)
                self.assertIsNotNone(message)
                self.assertIn(target, message)
                self.assertIn("WORKTRAIL_CODEX_HOME", message)
                self.assertIn("--codex-home", message)
            finally:
                os.chmod(tmp, 0o755)

    def test_write_remediation_never_opens_files_under_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "auth.json"
            secret.write_text('{"token": "should-never-be-read"}')
            with patch("builtins.open", side_effect=AssertionError("must not open files")):
                self.assertIsNone(skill_dispatch.codex_home_write_remediation(tmp))

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_preflight_blocks_launch_when_codex_home_not_writable(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o555)
            try:
                target = os.path.join(tmp, "nested", "codex-home")
                stderr = StringIO()
                with redirect_stderr(stderr):
                    result = skill_dispatch.main([
                        "--agent", "codex", "--skill", "x:y", "--codex-home", target,
                    ])
                self.assertEqual(result, 1)
                self.assertIn(target, stderr.getvalue())
                run.assert_not_called()
            finally:
                os.chmod(tmp, 0o755)

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_preflight_allows_launch_when_codex_home_writable(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "codex-home")
            with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
                self.assertEqual(
                    skill_dispatch.main(["--agent", "codex", "--skill", "x:y", "--codex-home", target]),
                    0,
                )
            run.assert_called_once()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_preflight_does_not_run_for_dry_run(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o555)
            try:
                target = os.path.join(tmp, "nested", "codex-home")
                output = StringIO()
                with redirect_stdout(output):
                    result = skill_dispatch.main([
                        "--agent", "codex", "--skill", "x:y", "--codex-home", target,
                        "--dry-run",
                    ])
                self.assertEqual(result, 0)
                run.assert_not_called()
            finally:
                os.chmod(tmp, 0o755)

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_preflight_does_not_run_for_non_codex_agents(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o555)
            try:
                with patch.dict(os.environ, {"CODEX_HOME": os.path.join(tmp, "nested")}, clear=False):
                    self.assertEqual(
                        skill_dispatch.main(["--agent", "opencode", "--skill", "x:y"]),
                        0,
                    )
                run.assert_called_once()
            finally:
                os.chmod(tmp, 0o755)


if __name__ == "__main__":
    unittest.main()

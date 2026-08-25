import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from worktrail.router import skill_dispatch
from worktrail.workqueue import decisions as decisions_mod


class SkillDispatchTests(unittest.TestCase):
    def test_claude_uses_native_style_prompt_and_provider(self):
        command = skill_dispatch.build_command("claude", "worktrail-sdd-workflow", "route:E 002-bootstrap")
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertIn("[WORKTRAIL INTERNAL DISPATCH]", command[2])
        self.assertIn("Invocation: /worktrail-sdd-workflow route:E 002-bootstrap", command[2])
        self.assertIn("Do not invoke worktrail-go again", command[2])

    def test_codex_preserves_codex_binary(self):
        command = skill_dispatch.build_command("codex", "worktrail-sdd-workflow", "route:E")
        self.assertEqual(
            command[:5],
            ["codex", "exec", "--json", "-s", "danger-full-access"],
        )
        self.assertNotIn("-a", command)
        self.assertNotIn("on-request", command)
        self.assertIn("[WORKTRAIL INTERNAL DISPATCH]", command[-1])
        self.assertIn("handoff", skill_dispatch.build_command(
            "codex", "worktrail-sdd-workflow", "handoff:123 route:F"
        )[-1])
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
        self.assertIn("Invocation: /worktrail-sdd-workflow route:E", command[-1])

    def test_args_are_one_argument_and_extra_args_are_not_shell_parsed(self):
        command = skill_dispatch.build_command("codex", "worktrail-sdd-workflow", "route:E; do-not-execute", extra_args=("--flag", "value"))
        self.assertIn("route:E; do-not-execute", command[-3])
        self.assertEqual(command[-2:], ["--flag", "value"])

    def test_cli_json_is_parseable(self):
        output = StringIO()
        with redirect_stdout(output):
            skill_dispatch.main(["--agent", "opencode", "--skill", "x:y", "--json", "--dry-run"])
        self.assertEqual(json.loads(output.getvalue())[0], "opencode")


class OpsxCommandNamespacingTests(unittest.TestCase):
    """The bundled OpenSpec integration ships as Claude Code *commands*
    (commands/opsx/*.md), not Skills -- claude/opencode resolve a bare
    `/opsx:propose` as `Unknown command` (exit 0, no artifacts written).
    Live-verified 2026-08-24 against the namespaced form."""

    def test_claude_namespaces_a_bare_opsx_command(self):
        command = skill_dispatch.build_command("claude", "opsx:propose", "the request")
        self.assertIn("/worktrail:opsx:propose the request", command)

    def test_opencode_namespaces_a_bare_opsx_command(self):
        command = skill_dispatch.build_command("opencode", "opsx:sync", "spec-a")
        self.assertIn("/worktrail:opsx:sync spec-a", command)

    def test_an_already_namespaced_opsx_command_is_left_alone(self):
        command = skill_dispatch.build_command("claude", "worktrail:opsx:propose", "req")
        self.assertIn("/worktrail:opsx:propose req", command)

    def test_a_non_opsx_skill_is_never_namespaced(self):
        # openspec-propose is a real bundled Skill and resolves bare by its
        # own frontmatter `name:`, unlike the opsx:* commands above.
        command = skill_dispatch.build_command("claude", "openspec-propose", "req")
        self.assertIn("/openspec-propose req", command)

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_default_cli_executes_the_selected_provider(self, run):
        run.return_value.returncode = 0
        with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
            self.assertEqual(
                skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y", "--args", "route:E",
                    "--codex-home", "/tmp/worktrail-codex-test",
                    "--no-inherit-codex-auth",
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
                skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y",
                    "--no-inherit-codex-auth",
                ]), 0
            )

        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], "/tmp/worktrail-codex")

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/from-env"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_explicit_codex_home_takes_precedence(self, run):
        run.return_value.returncode = 0

        with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
            skill_dispatch.main([
                "--agent", "codex", "--skill", "x:y", "--codex-home", "/tmp/explicit",
                "--no-inherit-codex-auth",
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

    @patch.dict(os.environ, {"WORKTRAIL_SKILL_DISPATCH_DEPTH": "1"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_internal_executor_recursion_is_bounded_before_spawn(self, run):
        stderr = StringIO()
        with redirect_stderr(stderr):
            result = skill_dispatch.main([
                "--agent", "claude", "--skill", "worktrail-sdd-workflow",
                "--args", "handoff:20260812-083302 route:F",
            ])
        self.assertEqual(result, 2)
        self.assertIn("blocked_internal_dispatch_recursion", stderr.getvalue())
        self.assertIn("handoff:<id> route:<X>", stderr.getvalue())
        run.assert_not_called()

    @patch.dict(os.environ, {"WORKTRAIL_SKILL_DISPATCH_DEPTH": "0"})
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_internal_executor_child_receives_depth_marker_for_every_provider(self, run):
        run.return_value.returncode = 0
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                arguments = [
                    "--agent", agent, "--skill", "worktrail-sdd-workflow",
                    "--args", "handoff:20260812-083302 route:F",
                ]
                if agent == "codex":
                    arguments += ["--codex-home", "/tmp/worktrail-codex-test",
                                  "--no-inherit-codex-auth"]
                with patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
                    self.assertEqual(skill_dispatch.main(arguments), 0)
                self.assertEqual(
                    run.call_args.kwargs["env"]["WORKTRAIL_SKILL_DISPATCH_DEPTH"], "1"
                )


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
                    arguments = [
                        "--agent", agent, "--skill", "openspec-propose", "--cwd", tmp
                    ]
                    if agent == "codex":
                        arguments.append("--no-inherit-codex-auth")
                    skill_dispatch.main(arguments)
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

    def test_codex_worker_always_uses_socket_enabled_sandbox(self):
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
    def test_chatgpt_status_accepts_exact_line_on_stderr_after_warning(self):
        status = subprocess.CompletedProcess(
            [], 0, "", "warning without secrets\nLogged in using ChatGPT\n"
        )
        self.assertTrue(skill_dispatch._is_chatgpt_login_status(status))

    def test_chatgpt_status_rejects_similar_or_failed_output(self):
        self.assertFalse(skill_dispatch._is_chatgpt_login_status(
            subprocess.CompletedProcess([], 0, "Not logged in using ChatGPT\n", "")
        ))
        self.assertFalse(skill_dispatch._is_chatgpt_login_status(
            subprocess.CompletedProcess([], 1, "Logged in using ChatGPT\n", "")
        ))

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

    def test_stale_skill_symlinks_are_refreshed_to_the_current_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "current-skills"
            (source / "worktrail-sdd-workflow").mkdir(parents=True)
            child = root / "child-home"
            destination = child / "skills/worktrail-sdd-workflow"
            destination.parent.mkdir(parents=True)
            destination.symlink_to(root / "deleted-plugin-cache/worktrail-sdd-workflow")

            with patch.object(skill_dispatch, "_codex_skill_roots", return_value=[source]):
                self.assertTrue(
                    skill_dispatch.bootstrap_codex_skills(
                        str(child), "worktrail-sdd-workflow"
                    )
                )

            self.assertEqual(destination.readlink(), source / "worktrail-sdd-workflow")

    def test_real_child_skill_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "current-skills"
            (source / "worktrail-go").mkdir(parents=True)
            child = root / "child-home"
            destination = child / "skills/worktrail-go"
            destination.mkdir(parents=True)
            marker = destination / "user-owned"
            marker.write_text("keep")

            with patch.object(skill_dispatch, "_codex_skill_roots", return_value=[source]):
                self.assertTrue(skill_dispatch.bootstrap_codex_skills(str(child), "worktrail-go"))

            self.assertEqual(marker.read_text(), "keep")

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
    def test_auth_inheritance_is_default_and_copies_only_private_allowlist(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            child = Path(tmp) / "child"
            parent.mkdir(mode=0o700)
            secret = b'{"tokens":{"access_token":"do-not-log"}}'
            (parent / "auth.json").write_bytes(secret)
            (parent / "auth.json").chmod(0o600)
            (parent / "config.toml").write_text('model = "private-model"\n')
            run.side_effect = [
                subprocess.CompletedProcess([], 0, "Logged in using ChatGPT\n", ""),
                subprocess.CompletedProcess([], 0),
            ]
            with patch.dict(os.environ, {"CODEX_HOME": str(parent)}, clear=True), \
                    patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
                self.assertEqual(skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y",
                    "--codex-home", str(child),
                ]), 0)

            self.assertEqual((child / "auth.json").read_bytes(), secret)
            self.assertEqual(
                (child / "config.toml").read_text(),
                'cli_auth_credentials_store = "file"\n',
            )
            self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((child / "auth.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((child / "config.toml").stat().st_mode), 0o600)
            self.assertNotIn("private-model", (child / "config.toml").read_text())

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_explicit_isolated_mode_does_not_read_or_copy_parent_auth(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            child = Path(tmp) / "child"
            parent.mkdir()
            (parent / "auth.json").write_text("must-not-be-read")
            with patch.dict(os.environ, {"CODEX_HOME": str(parent)}, clear=True), \
                    patch.object(skill_dispatch, "bootstrap_codex_skills", return_value=True):
                self.assertEqual(skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y", "--codex-home", str(child),
                    "--no-inherit-codex-auth",
                ]), 0)
            self.assertFalse((child / "auth.json").exists())

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_auth_inheritance_rejects_symlink_without_leaking_secret(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            child = Path(tmp) / "child"
            parent.mkdir()
            target = Path(tmp) / "real-auth"
            target.write_text("unique-secret-value")
            target.chmod(0o600)
            (parent / "auth.json").symlink_to(target)
            run.return_value = subprocess.CompletedProcess(
                [], 0, "Logged in using ChatGPT\n", ""
            )
            stderr = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(parent)}, clear=True), \
                    redirect_stderr(stderr):
                result = skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y",
                    "--codex-home", str(child),
                ])
            self.assertEqual(result, 1)
            self.assertIn("blocked_external_dependency", stderr.getvalue())
            self.assertNotIn("unique-secret-value", stderr.getvalue())
            self.assertFalse((child / "auth.json").exists())

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_auth_inheritance_requires_chatgpt_login(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Logged in using an API key\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            parent.mkdir()
            stderr = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(parent)}, clear=True), \
                    redirect_stderr(stderr):
                result = skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y",
                    "--codex-home", str(Path(tmp) / "child"),
                ])
            self.assertEqual(result, 1)
            self.assertIn("not authenticated with ChatGPT", stderr.getvalue())

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_auth_inheritance_rejects_group_readable_auth_file(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Logged in using ChatGPT\n", "")
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "parent"
            parent.mkdir()
            (parent / "auth.json").write_text("secret")
            (parent / "auth.json").chmod(0o640)
            stderr = StringIO()
            with patch.dict(os.environ, {"CODEX_HOME": str(parent)}, clear=True), \
                    redirect_stderr(stderr):
                result = skill_dispatch.main([
                    "--agent", "codex", "--skill", "x:y",
                    "--codex-home", str(Path(tmp) / "child"),
                ])
            self.assertEqual(result, 1)
            self.assertIn("permissions must be 0600", stderr.getvalue())

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
                    skill_dispatch.main([
                        "--agent", "codex", "--skill", "x:y", "--codex-home", target,
                        "--no-inherit-codex-auth",
                    ]),
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


class PendingDecisionBoundaryTests(unittest.TestCase):
    """Attended presentation and exact decision-ID resume at the go/adapter
    boundary: every host gets the same versioned envelope to present, and a
    resume dispatch launches only through the exact answered record."""

    DECISION_ID = "dec-boundary-test-0001"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue_base = Path(self.tmp.name) / "queue"
        env = {"WORK_QUEUE_DIR": str(self.queue_base)}
        patcher = patch.dict(os.environ, env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _ask(self, decision_id=DECISION_ID):
        result = decisions_mod.ask(
            "Which scope should this request take?",
            background="The shipped spec already covers the requested scope.",
            why="Scope direction is a product call.",
            context="verify() confirmed Implemented status and git-tracked files.",
            options=[
                "extend: continue the existing spec",
                "proceed-anyway: dispatch despite the collision",
            ],
            source="check_spec_collision",
            repo="/tmp/some-repo",
            subject="spec-a",
            decision_id=decision_id,
            queue_base=self.queue_base,
        )
        self.assertEqual(result["status"], "created")
        return result

    def _answer(self):
        self._ask()
        answered = decisions_mod.answer(
            self.DECISION_ID, "extend: continue the existing spec",
            queue_base=self.queue_base,
        )
        self.assertEqual(answered["status"], "answered")

    def _dispatch_args(self, agent, extra=()):
        arguments = [
            "--agent", agent, "--skill", "worktrail-sdd-workflow",
            "--args", "route:F spec:demo",
            *extra,
        ]
        if agent == "codex":
            arguments += ["--codex-home", os.path.join(self.tmp.name, "codex-home"),
                          "--no-inherit-codex-auth"]
        return arguments

    def _run_main(self, agent, extra=(), run_mock=None):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with patch.object(skill_dispatch, "bootstrap_codex_skills",
                              return_value=True):
                rc = skill_dispatch.main(self._dispatch_args(agent, extra))
        if run_mock is not None:
            return rc, stdout.getvalue(), stderr.getvalue(), run_mock
        return rc, stdout.getvalue(), stderr.getvalue()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_present_decision_prints_the_versioned_envelope_for_every_host(self, run):
        self._ask()
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                out = StringIO()
                with redirect_stdout(out), redirect_stderr(StringIO()):
                    rc = skill_dispatch.main([
                        "--present-decision", self.DECISION_ID,
                    ])
                self.assertEqual(rc, 0)
                envelope = json.loads(out.getvalue())
                self.assertEqual(envelope["schema"], "worktrail.pending-decision")
                self.assertEqual(envelope["version"], 1)
                self.assertEqual(envelope["decision_id"], self.DECISION_ID)
                self.assertEqual(envelope["status"], "open")
                self.assertEqual(envelope["provenance"]["source"],
                                 "check_spec_collision")
        run.assert_not_called()

    def test_present_decision_includes_the_answer_once_answered(self):
        self._answer()
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            rc = skill_dispatch.main(["--present-decision", self.DECISION_ID])
        self.assertEqual(rc, 0)
        envelope = json.loads(out.getvalue())
        self.assertEqual(envelope["status"], "answered")
        self.assertIn("extend: continue the existing spec", envelope["answer"])

    def test_present_decision_unknown_id_fails_closed_without_spawning(self):
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            rc = skill_dispatch.main(["--present-decision", "dec-nope"])
        self.assertEqual(rc, 2)
        self.assertIn("blocked_pending_decision", stderr.getvalue())
        self.assertIn("dec-nope", stderr.getvalue())

    def test_present_decision_stamps_one_idempotent_presented_hop(self):
        self._ask()
        run_path = Path(self.tmp.name) / "run.yaml"
        run_path.write_text("run_id: boundary\nfinal_status: null\n")
        for _ in range(2):
            out = StringIO()
            with redirect_stdout(out), redirect_stderr(StringIO()):
                rc = skill_dispatch.main(
                    ["--present-decision", self.DECISION_ID, "--run", str(run_path)]
                )
            self.assertEqual(rc, 0)
        text = run_path.read_text()
        self.assertEqual(text.count("[presented]"), 1)
        self.assertIn(f"[presented] {self.DECISION_ID}", text)

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_resume_decision_on_open_record_is_refused_before_spawn(self, run):
        self._ask()
        rc, stdout, stderr, run = self._run_main(
            "claude", ["--resume-decision", self.DECISION_ID], run)
        self.assertEqual(rc, 2)
        envelope = json.loads(stdout)
        self.assertEqual(envelope["status"], "open")
        self.assertEqual(envelope["decision_id"], self.DECISION_ID)
        self.assertIn("blocked_pending_decision", stderr)
        run.assert_not_called()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_resume_decision_after_supersession_is_refused_before_spawn(self, run):
        self._answer()
        replacement = decisions_mod.ask(
            "Replacement question?",
            background="Facts changed after the original ask.",
            why="Still a product call.",
            context="The target moved between ask and answer.",
            options=["option one", "option two"],
            source="check_spec_collision",
            repo="/tmp/some-repo",
            subject="spec-a-moved",
            decision_id="dec-replacement-0001",
            queue_base=self.queue_base,
        )
        self.assertEqual(replacement["status"], "created")
        superseded = decisions_mod.supersede(
            self.DECISION_ID, "dec-replacement-0001",
            queue_base=self.queue_base,
        )
        self.assertEqual(superseded["status"], "superseded")
        rc, stdout, stderr, run = self._run_main(
            "claude", ["--resume-decision", self.DECISION_ID], run)
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(stdout)["superseded_by"],
                         "dec-replacement-0001")
        self.assertIn("superseded", stderr)
        run.assert_not_called()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_resume_decision_refuses_a_prefix_match(self, run):
        self._answer()
        prefix = self.DECISION_ID[:-1]
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            rc = skill_dispatch.main(
                self._dispatch_args("claude", ["--resume-decision", prefix]))
        self.assertEqual(rc, 2)
        self.assertIn("exactly", stderr.getvalue())
        run.assert_not_called()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_resume_decision_unknown_id_is_refused_before_spawn(self, run):
        self._answer()
        stderr = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(stderr):
            rc = skill_dispatch.main(
                self._dispatch_args("claude", ["--resume-decision", "dec-unknown"]))
        self.assertEqual(rc, 2)
        self.assertIn("dec-unknown", stderr.getvalue())
        run.assert_not_called()

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_resume_decision_threads_the_exact_id_into_every_provider(self, run):
        run.return_value.returncode = 0
        self._answer()
        token = f"decision:{self.DECISION_ID}"
        for agent in skill_dispatch.SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                rc, _stdout, _stderr = self._run_main(
                    agent, ["--resume-decision", self.DECISION_ID])
                self.assertEqual(rc, 0)
                argv = run.call_args.args[0]
                prompt = next(arg for arg in argv if "worktrail-sdd-workflow" in arg)
                self.assertIn(token, prompt)
                self.assertNotIn(token + "-", prompt)

    def test_resume_decision_token_lands_at_the_end_of_native_invocations(self):
        self.assertEqual(
            skill_dispatch.append_decision_token("", self.DECISION_ID),
            f"decision:{self.DECISION_ID}",
        )
        command = skill_dispatch.build_command(
            "opencode", "worktrail-sdd-workflow",
            skill_dispatch.append_decision_token("route:F", self.DECISION_ID),
        )
        self.assertTrue(command[-1].endswith(f"decision:{self.DECISION_ID}"))
        self.assertIn("Invocation: /worktrail-sdd-workflow route:F "
                      f"decision:{self.DECISION_ID}", command[-1])

    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_dry_run_with_valid_resume_prints_command_and_spawns_nothing(self, run):
        run.return_value.returncode = 0
        self._answer()
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            with patch.object(skill_dispatch, "bootstrap_codex_skills",
                              return_value=True):
                rc = skill_dispatch.main(
                    self._dispatch_args("codex", ["--resume-decision",
                                                  self.DECISION_ID, "--dry-run"]))
        self.assertEqual(rc, 0)
        self.assertIn(f"decision:{self.DECISION_ID}", out.getvalue())
        run.assert_not_called()

    def test_missing_agent_or_skill_is_rejected_unless_presenting(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as ctx:
                skill_dispatch.main([])
        self.assertEqual(ctx.exception.code, 2)
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            self._ask()
            rc = skill_dispatch.main(["--present-decision", self.DECISION_ID])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()


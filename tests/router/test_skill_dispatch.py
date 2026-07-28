import json
import os
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
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
        self.assertEqual(
            skill_dispatch.main(["--agent", "codex", "--skill", "x:y", "--args", "route:E"]), 0
        )
        self.assertEqual(run.call_args.args[0][0], "codex")
        self.assertTrue(run.call_args.kwargs["check"] is False)

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/worktrail-codex"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_codex_home_environment_override_is_passed_to_child(self, run):
        run.return_value.returncode = 0

        self.assertEqual(
            skill_dispatch.main(["--agent", "codex", "--skill", "x:y"]), 0
        )

        self.assertEqual(run.call_args.kwargs["env"]["CODEX_HOME"], "/tmp/worktrail-codex")

    @patch.dict(os.environ, {"WORKTRAIL_CODEX_HOME": "/tmp/from-env"}, clear=False)
    @patch("worktrail.router.skill_dispatch.subprocess.run")
    def test_explicit_codex_home_takes_precedence(self, run):
        run.return_value.returncode = 0

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


if __name__ == "__main__":
    unittest.main()

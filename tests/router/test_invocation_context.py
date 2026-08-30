import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from worktrail.router import invocation_context


def _resolve(**kwargs):
    kwargs.setdefault("environ", {})
    kwargs.setdefault("which", lambda _name: "/usr/bin/stub")
    return invocation_context.resolve(**kwargs)


class ProviderResolutionTests(unittest.TestCase):
    """Provider identity is resolved on its own axis, unchanged from go_seed."""

    def test_explicit_model_is_carried_for_subcall_inheritance(self):
        resolved = _resolve(agent="codex", model="gpt-5")
        self.assertEqual(resolved.model, "gpt-5")

    def test_explicit_agent_wins_over_every_environment_marker(self):
        resolved = _resolve(
            agent="opencode",
            environ={"GO_AGENT_CLI": "claude", "CODEX_THREAD_ID": "t"},
        )
        self.assertEqual(resolved.agent_cli, "opencode")

    def test_policy_agent_outranks_machine_wide_environment(self):
        resolved = _resolve(policy_agent="codex", environ={"GO_AGENT_CLI": "opencode"})
        self.assertEqual(resolved.agent_cli, "codex")

    def test_host_markers_are_the_last_resort_before_claude(self):
        self.assertEqual(
            _resolve(environ={"OPENCODE_PARENT": "1"}).agent_cli, "opencode"
        )
        self.assertEqual(_resolve(environ={"CODEX_THREAD_ID": "t"}).agent_cli, "codex")
        self.assertEqual(_resolve(environ={}).agent_cli, "claude")

    def test_unsupported_agent_is_rejected_rather_than_silently_replaced(self):
        with self.assertRaises(ValueError):
            _resolve(agent="gpt-cli")


class CapabilityIndependenceTests(unittest.TestCase):
    """`native_skill_available` is independent of which provider was resolved.

    This is the defect the brief names: SKILL.md used provider identity
    (`codex`) as a proxy for native-Skill availability, but an embedded Codex
    host resolves to `codex` and exposes no `Skill(...)` at all.
    """

    def test_codex_provider_does_not_imply_native_skill(self):
        embedded = _resolve(agent="codex", native_skill=False)
        self.assertEqual(embedded.agent_cli, "codex")
        self.assertFalse(embedded.native_skill_available)
        self.assertEqual(embedded.dispatch_mode, "adapter")

    def test_codex_provider_in_a_host_that_does_expose_skill_uses_it(self):
        hosted = _resolve(agent="codex", native_skill=True)
        self.assertEqual(hosted.agent_cli, "codex")
        self.assertTrue(hosted.native_skill_available)
        self.assertEqual(hosted.dispatch_mode, "native-skill")

    def test_claude_provider_without_the_capability_still_uses_the_adapter(self):
        headless = _resolve(agent="claude", native_skill=False)
        self.assertEqual(headless.agent_cli, "claude")
        self.assertEqual(headless.dispatch_mode, "adapter")

    def test_every_provider_resolves_both_capability_values_independently(self):
        for agent in invocation_context.SUPPORTED_AGENTS:
            for capability in (True, False):
                with self.subTest(agent=agent, native_skill=capability):
                    resolved = _resolve(agent=agent, native_skill=capability)
                    self.assertEqual(resolved.agent_cli, agent)
                    self.assertIs(resolved.native_skill_available, capability)


class CapabilitySignalPrecedenceTests(unittest.TestCase):
    def test_explicit_parameter_outranks_the_environment_override(self):
        resolved = _resolve(native_skill=False, environ={"WORKTRAIL_NATIVE_SKILL": "1"})
        self.assertFalse(resolved.native_skill_available)
        self.assertEqual(resolved.capability_source, "explicit")

    def test_environment_override_is_honored_when_no_parameter_is_given(self):
        resolved = _resolve(environ={"WORKTRAIL_NATIVE_SKILL": "1"})
        self.assertTrue(resolved.native_skill_available)
        self.assertEqual(resolved.capability_source, "environment")

    def test_environment_override_accepts_documented_falsey_spellings(self):
        for raw in ("0", "false", "FALSE", "no", "off", ""):
            with self.subTest(raw=raw):
                resolved = _resolve(environ={"WORKTRAIL_NATIVE_SKILL": raw})
                self.assertFalse(resolved.native_skill_available)

    def test_unparseable_environment_override_fails_closed_and_warns(self):
        resolved = _resolve(environ={"WORKTRAIL_NATIVE_SKILL": "maybe"})
        self.assertFalse(resolved.native_skill_available)
        self.assertEqual(resolved.capability_source, "unset")
        self.assertIn("WORKTRAIL_NATIVE_SKILL", resolved.warning)

    def test_absent_signal_resolves_to_adapter_never_a_speculative_skill_call(self):
        resolved = _resolve(environ={})
        self.assertFalse(resolved.native_skill_available)
        self.assertEqual(resolved.capability_source, "unset")
        self.assertEqual(resolved.dispatch_mode, "adapter")


class FailClosedTests(unittest.TestCase):
    """Neither capability available means an actionable stop, not a guess."""

    def test_no_native_skill_and_no_installed_provider_is_blocked(self):
        resolved = _resolve(agent="codex", native_skill=False, which=lambda _n: None)
        self.assertEqual(resolved.dispatch_mode, "blocked")
        self.assertIn("codex", resolved.blocked_reason)
        self.assertIn("WORKTRAIL_NATIVE_SKILL", resolved.blocked_reason)

    def test_native_skill_does_not_require_an_installed_provider_cli(self):
        # The in-session host executes the skill itself; no child is spawned,
        # so a missing provider binary is irrelevant on this branch.
        resolved = _resolve(agent="codex", native_skill=True, which=lambda _n: None)
        self.assertEqual(resolved.dispatch_mode, "native-skill")
        self.assertIsNone(resolved.blocked_reason)

    def test_a_healthy_resolution_carries_no_blocked_reason(self):
        self.assertIsNone(_resolve(agent="claude", native_skill=False).blocked_reason)


class ActiveResumeTests(unittest.TestCase):
    """An already-active Route E resume never spawns a nested worker.

    The adapter would start a fresh worker against a run/worktree the active
    parent already owns, which self-polls or duplicates execution
    (datalena run go-20260811-132806).
    """

    def test_active_resume_stays_in_session_even_without_native_skill(self):
        resolved = _resolve(agent="codex", native_skill=False, active_resume=True)
        self.assertEqual(resolved.dispatch_mode, "in-session-resume")

    def test_active_resume_outranks_the_blocked_branch(self):
        resolved = _resolve(
            agent="codex",
            native_skill=False,
            active_resume=True,
            which=lambda _n: None,
        )
        self.assertEqual(resolved.dispatch_mode, "in-session-resume")
        self.assertIsNone(resolved.blocked_reason)

    def test_active_resume_with_native_skill_is_still_in_session(self):
        resolved = _resolve(agent="claude", native_skill=True, active_resume=True)
        self.assertEqual(resolved.dispatch_mode, "in-session-resume")


class ProviderStabilityTests(unittest.TestCase):
    """The resolved provider is never silently swapped by the capability axis."""

    def test_resolved_provider_is_identical_across_every_dispatch_mode(self):
        modes = {}
        for kwargs in (
            {"native_skill": True},
            {"native_skill": False},
            {"native_skill": False, "active_resume": True},
            {"native_skill": False, "which": lambda _n: None},
        ):
            resolved = _resolve(agent="opencode", **kwargs)
            modes[resolved.dispatch_mode] = resolved.agent_cli
        self.assertEqual(set(modes.values()), {"opencode"})
        self.assertEqual(len(modes), 4)


class DispatchIdTests(unittest.TestCase):
    """dispatch_id is the stable identity threaded through both claim() calls
    of one /go dispatch -- see docs/specs/research/
    concurrent-go-dispatch-brief-claim-race.md."""

    def test_dispatch_id_is_present_and_nonempty(self):
        resolved = _resolve()
        self.assertTrue(resolved.dispatch_id)

    def test_two_independent_resolutions_get_distinct_dispatch_ids(self):
        first = _resolve()
        second = _resolve()
        self.assertNotEqual(first.dispatch_id, second.dispatch_id)

    def test_explicit_dispatch_id_is_passed_through_unchanged(self):
        resolved = _resolve(dispatch_id="go-fixed-for-test")
        self.assertEqual(resolved.dispatch_id, "go-fixed-for-test")


class CliTests(unittest.TestCase):
    def _run(self, argv, environ=None, installed=True):
        out, err = StringIO(), StringIO()
        located = "/usr/bin/stub" if installed else None
        with (
            patch.dict(os.environ, environ or {}, clear=True),
            patch.object(invocation_context, "_which", return_value=located),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = invocation_context.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_output_carries_both_axes_and_the_dispatch_mode(self):
        import json

        code, stdout, _ = self._run(["--agent", "codex", "--no-native-skill", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["agent_cli"], "codex")
        self.assertFalse(payload["native_skill_available"])
        self.assertEqual(payload["dispatch_mode"], "adapter")
        self.assertTrue(payload["dispatch_id"])

    def test_dispatch_id_override_is_honored(self):
        import json

        code, stdout, _ = self._run(
            [
                "--agent",
                "codex",
                "--no-native-skill",
                "--dispatch-id",
                "go-abc123",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["dispatch_id"], "go-abc123")

    def test_conflicting_capability_flags_are_rejected(self):
        with self.assertRaises(SystemExit):
            self._run(["--native-skill", "--no-native-skill", "--json"])

    def test_blocked_resolution_exits_non_zero_with_an_actionable_message(self):
        code, _, stderr = self._run(
            ["--agent", "codex", "--no-native-skill"], installed=False
        )
        self.assertEqual(code, 2)
        self.assertIn("blocked_missing_dispatch_capability", stderr)


if __name__ == "__main__":
    unittest.main()

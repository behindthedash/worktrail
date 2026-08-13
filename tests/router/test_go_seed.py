"""go_seed's default-agent resolution must agree with invocation_context.

PR #338 made `invocation_context.resolve()` the single resolver for provider
identity, but `go_seed.detect_default_agent()` kept an independent copy of the
precedence chain (GO_AGENT_CLI > ORCH_AGENT > OPENCODE_PARENT > CODEX_CI /
CODEX_THREAD_ID > claude) — the exact drift class #338 eliminated elsewhere.
go_seed now delegates; these tests pin both the agreement (against a future
re-divergence) and the chain itself.
"""

import itertools
import unittest

from worktrail.router import go_seed, invocation_context

# One representative value per host marker. GO_AGENT_CLI and ORCH_AGENT carry
# distinct providers so a precedence swap between them is visible.
MARKER_VALUES = {
    "GO_AGENT_CLI": "opencode",
    "ORCH_AGENT": "codex",
    "OPENCODE_PARENT": "1",
    "CODEX_CI": "1",
    "CODEX_THREAD_ID": "thread-1",
}


def _chain_expected(env: dict) -> str:
    """The documented precedence chain, restated independently so the parity
    test cannot be satisfied by both implementations drifting together."""
    return (
        env.get("GO_AGENT_CLI")
        or env.get("ORCH_AGENT")
        or ("opencode" if env.get("OPENCODE_PARENT") else None)
        or ("codex" if env.get("CODEX_CI") or env.get("CODEX_THREAD_ID") else None)
        or "claude"
    )


class DetectDefaultAgentParityTests(unittest.TestCase):
    def test_every_marker_combination_resolves_identically(self):
        """All 2^5 set/unset combinations of the host markers — including every
        precedence-conflict combination — must resolve to the same provider in
        go_seed and invocation_context, and to the documented chain's answer."""
        markers = sorted(MARKER_VALUES)
        for r in range(len(markers) + 1):
            for combo in itertools.combinations(markers, r):
                env = {name: MARKER_VALUES[name] for name in combo}
                with self.subTest(env=env):
                    expected = _chain_expected(env)
                    self.assertEqual(go_seed.detect_default_agent(env), expected)
                    self.assertEqual(
                        invocation_context.resolve(environ=env).agent_cli,
                        expected,
                    )

    def test_singleton_markers_pin_the_documented_providers(self):
        self.assertEqual(go_seed.detect_default_agent({}), "claude")
        self.assertEqual(
            go_seed.detect_default_agent({"GO_AGENT_CLI": "opencode"}), "opencode"
        )
        self.assertEqual(go_seed.detect_default_agent({"ORCH_AGENT": "codex"}), "codex")
        self.assertEqual(
            go_seed.detect_default_agent({"OPENCODE_PARENT": "1"}), "opencode"
        )
        self.assertEqual(go_seed.detect_default_agent({"CODEX_CI": "1"}), "codex")
        self.assertEqual(
            go_seed.detect_default_agent({"CODEX_THREAD_ID": "t"}), "codex"
        )

    def test_precedence_conflicts_resolve_top_down(self):
        # GO_AGENT_CLI outranks everything.
        self.assertEqual(
            go_seed.detect_default_agent(
                {
                    "GO_AGENT_CLI": "claude",
                    "ORCH_AGENT": "codex",
                    "OPENCODE_PARENT": "1",
                    "CODEX_CI": "1",
                    "CODEX_THREAD_ID": "t",
                }
            ),
            "claude",
        )
        # ORCH_AGENT outranks the detected-host markers.
        self.assertEqual(
            go_seed.detect_default_agent(
                {"ORCH_AGENT": "claude", "OPENCODE_PARENT": "1", "CODEX_CI": "1"}
            ),
            "claude",
        )
        # OPENCODE_PARENT outranks the codex markers.
        self.assertEqual(
            go_seed.detect_default_agent(
                {"OPENCODE_PARENT": "1", "CODEX_CI": "1", "CODEX_THREAD_ID": "t"}
            ),
            "opencode",
        )

    def test_supported_agents_is_the_resolvers_tuple_not_a_copy(self):
        self.assertIs(go_seed.SUPPORTED_AGENTS, invocation_context.SUPPORTED_AGENTS)

    def test_unsupported_env_provider_is_rejected_like_invocation_context(self):
        """The pre-consolidation chain leaked arbitrary env values (e.g.
        GO_AGENT_CLI=gpt-cli) straight into the seed prompt while --agent
        rejected the same value — the silent-divergence hazard itself. Both
        resolution paths now fail identically."""
        for env in ({"GO_AGENT_CLI": "gpt-cli"}, {"ORCH_AGENT": "gpt-cli"}):
            with self.subTest(env=env):
                with self.assertRaises(ValueError):
                    go_seed.detect_default_agent(env)
                with self.assertRaises(ValueError):
                    invocation_context.resolve(environ=env)


if __name__ == "__main__":
    unittest.main()

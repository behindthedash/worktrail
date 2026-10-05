#!/usr/bin/env python3
"""Tests for `router.spawn_readiness.readiness_problems` -- the spawn-readiness
probe `worktrail-routing --check` and the drain's preflight share.

Every case here is behavioural: the probe is fed a `resolve_routing()`-shaped
table (never a file path, never a repo) and asserted on the `(row, target)`
problems it reports. The two invariants that make it worth having are pinned
explicitly -- an unready cell records NO `agent_capacity` gate (a gate is
skipped silently by the next select, hiding the config error), and the probe
creates none of the per-spawn state `_prepare_child_env` would (a home it made
would make the spawn it predicted run against state it changed).
"""

import ast
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.orchestrator import agent_capacity, spawnlib
from worktrail.router import spawn_readiness
from worktrail.router.spawn_readiness import readiness_problems

API_VAR = "WORKTRAIL_TEST_READINESS_API_KEY"


def _target(harness, pool="subscription", api_opt_in=False, auth=None):
    return {"harness": harness, "pool": pool, "api_opt_in": api_opt_in, "auth": auth}


def _routing(targets, tiers, env_profiles=None):
    """A `resolve_routing()`-shaped table: the probe's only accepted input."""
    return {
        "targets": targets,
        "tiers": tiers,
        "roles": {},
        "purposes": {},
        "default_tier": None,
        "env_profiles": env_profiles if env_profiles is not None else {},
        "drain": {},
    }


def _write_profile(directory, name="settings.json", env=None):
    path = directory / name
    path.write_text(json.dumps({"env": env or {}}), encoding="utf-8")
    return path


class ReadinessTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = Path(self._dir.name)

    def _problems(self, routing, base_env):
        return readiness_problems(routing, base_env=base_env)

    def _only_problem(self, routing, *, target, base_env, row="t2-build"):
        """Assert the probe reports exactly one cell unready and return its
        message, so a case cannot pass by reporting an unrelated cell."""
        problems = self._problems(routing, base_env)
        self.assertEqual(list(problems), [(row, target)])
        return problems[(row, target)]


class TestReadyTables(ReadinessTestCase):
    def test_a_table_with_no_cells_has_no_problems(self):
        self.assertEqual(self._problems({"targets": {}, "tiers": {}}, {}), {})

    def test_a_servable_table_has_no_problems(self):
        routing = _routing(
            {
                "claude-sub": _target("claude"),
                "codex-sub": _target("codex"),
                "opencode-free": _target("opencode", pool="free"),
            },
            {
                "t2-build": {
                    "claude-sub": {"model": "sonnet", "effort": "medium"},
                    "codex-sub": {"model": "gpt-5.6-terra", "effort": None},
                    "opencode-free": {
                        "model": "opencode/x-preview-f-free",
                        "effort": None,
                    },
                }
            },
        )
        self.assertEqual(self._problems(routing, {}), {})

    def test_claude_api_cell_is_ready_when_its_variable_is_in_base_env(self):
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"env": API_VAR}
                )
            },
            {"t2-build": {"claude-api": {"model": "deepseek-flash[1m]"}}},
        )
        self.assertEqual(self._problems(routing, {API_VAR: "sk-not-printed"}), {})

    def test_env_profile_cell_is_ready_when_the_profile_resolves(self):
        profile = _write_profile(
            self.dir, env={"ANTHROPIC_AUTH_TOKEN": "sk-profile-secret"}
        )
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"profile": "deepseek"}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
            env_profiles={
                "deepseek": {
                    "from": str(profile),
                    "keys": ["ANTHROPIC_AUTH_TOKEN"],
                }
            },
        )
        self.assertEqual(self._problems(routing, {}), {})

    def test_codex_api_cell_is_ready_with_a_provisioned_home(self):
        home = self.dir / "codex-home"
        home.mkdir()
        (home / "auth.json").write_text("{}", encoding="utf-8")
        routing = _routing(
            {
                "codex-api": _target(
                    "codex", pool="api", api_opt_in=True, auth={"codex_home": str(home)}
                )
            },
            {"t2-build": {"codex-api": {"model": "gpt-5.3-codex"}}},
        )
        self.assertEqual(self._problems(routing, {}), {})


class TestUnreadyClasses(ReadinessTestCase):
    def test_api_pool_without_opt_in_names_the_fix(self):
        routing = _routing(
            {"claude-api": _target("claude", pool="api", auth={"env": API_VAR})},
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        message = self._only_problem(
            routing, target="claude-api", base_env={API_VAR: "set-but-ineligible"}
        )
        self.assertIn("claude-api", message)
        self.assertIn("api_opt_in", message)

    def test_harness_outside_the_supported_set(self):
        routing = _routing(
            {"aider-sub": _target("aider")},
            {"t2-build": {"aider-sub": {"model": "gpt-5"}}},
        )
        message = self._only_problem(routing, target="aider-sub", base_env={})
        self.assertIn("aider", message)
        self.assertIn("claude", message)  # names the supported set

    def test_tier_cell_naming_a_target_with_no_routing_targets_entry(self):
        routing = _routing(
            {"claude-sub": _target("claude")},
            {"t2-build": {"gone-sub": {"model": "sonnet"}}},
        )
        message = self._only_problem(routing, target="gone-sub", base_env={})
        self.assertIn("gone-sub", message)
        self.assertIn("routing.targets", message)

    def test_claude_api_variable_unset_in_the_checking_environment(self):
        """The environment-sensitive class: the spawn path builds a worker's
        env from the spawning process, so `--check` asks whether THIS shell can
        launch the cell."""
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"env": API_VAR}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        message = self._only_problem(routing, target="claude-api", base_env={})
        self.assertIn("claude-api", message)
        self.assertIn(API_VAR, message)
        self.assertIn("export it before spawning", message)

    def test_claude_api_with_no_auth_env_and_no_profile(self):
        routing = _routing(
            {"claude-api": _target("claude", pool="api", api_opt_in=True)},
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        message = self._only_problem(routing, target="claude-api", base_env={})
        self.assertIn("auth.env", message)
        self.assertIn("auth.profile", message)

    def test_profile_the_resolved_table_does_not_declare(self):
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"profile": "deepseek"}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        message = self._only_problem(routing, target="claude-api", base_env={})
        self.assertIn("deepseek", message)
        self.assertIn("env_profiles", message)
        # An empty/absent resolved table is the resolver/caller's fault, not
        # the operator's: the probe judges the table it was handed and never
        # claims the file does not declare the entry.
        self.assertIn("resolver/caller", message)
        self.assertNotIn("-- add an", message)
        self.assertNotIn("not declared in routing.env_profiles", message)

    def test_profile_key_dropped_by_the_resolver_is_reported(self):
        """The #1380 shape: the routing file declares `env_profiles` and the
        cell's profile resolves against it, but the *resolved* table carries no
        `env_profiles` at all. The probe must fail on the table it was handed,
        not on the file it cannot see."""
        profile = _write_profile(
            self.dir, env={"ANTHROPIC_AUTH_TOKEN": "sk-profile-secret"}
        )
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"profile": "deepseek"}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
            env_profiles={
                "deepseek": {"from": str(profile), "keys": ["ANTHROPIC_AUTH_TOKEN"]}
            },
        )
        del routing["env_profiles"]
        message = self._only_problem(routing, target="claude-api", base_env={})
        self.assertIn("deepseek", message)
        self.assertIn("env_profiles", message)
        # Provenance markers: the failure is attributed to the resolved
        # table's emptiness, and the probe -- which opens no file and is
        # never given the loader's table -- must not claim to know whether
        # the file declares the profile.
        self.assertIn("resolver/caller", message)
        self.assertNotIn("-- add an", message)
        self.assertNotIn("not declared in routing.env_profiles", message)

    def test_profile_source_file_missing(self):
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"profile": "deepseek"}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
            env_profiles={
                "deepseek": {
                    "from": str(self.dir / "absent.json"),
                    "keys": ["ANTHROPIC_AUTH_TOKEN"],
                }
            },
        )
        message = self._only_problem(routing, target="claude-api", base_env={})
        self.assertIn("absent.json", message)
        self.assertIn("does not exist", message)

    def test_codex_api_cell_without_a_declared_home(self):
        routing = _routing(
            {"codex-api": _target("codex", pool="api", api_opt_in=True)},
            {"t2-build": {"codex-api": {"model": "gpt-5.3-codex"}}},
        )
        message = self._only_problem(routing, target="codex-api", base_env={})
        self.assertIn("codex-api", message)
        self.assertIn("auth.codex_home", message)

    def test_codex_api_cell_with_an_unprovisioned_home(self):
        home = self.dir / "codex-home"
        home.mkdir()
        routing = _routing(
            {
                "codex-api": _target(
                    "codex", pool="api", api_opt_in=True, auth={"codex_home": str(home)}
                )
            },
            {"t2-build": {"codex-api": {"model": "gpt-5.3-codex"}}},
        )
        message = self._only_problem(routing, target="codex-api", base_env={})
        self.assertIn("auth.json", message)
        self.assertIn("--with-api-key", message)
        # Validation only -- the probe never creates the home it checked.
        self.assertFalse((home / "auth.json").exists())


class TestEnumeration(ReadinessTestCase):
    def test_every_declared_cell_is_enumerated(self):
        """Not only the cell `select_cell()` would pick first: readiness is a
        property of the whole table, including the cells a run falls back to."""
        routing = _routing(
            {
                "claude-sub": _target("claude"),
                "opencode-api": _target("opencode", pool="api"),
            },
            {
                "t1-deep": {
                    "claude-sub": {"model": "opus", "effort": "high"},
                    "opencode-api": {"model": "opencode/claude-opus-5"},
                },
                "t2-build": {
                    "claude-sub": {"model": "sonnet", "effort": "medium"},
                    "opencode-api": {"model": "opencode/claude-opus-5"},
                },
            },
        )
        self.assertEqual(
            list(self._problems(routing, {})),
            [("t1-deep", "opencode-api"), ("t2-build", "opencode-api")],
        )

    def test_base_env_defaults_to_the_process_environment(self):
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"env": API_VAR}
                )
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        self.assertNotIn(API_VAR, os.environ)
        self.assertEqual(
            list(self._problems(routing, None)), [("t2-build", "claude-api")]
        )
        with mock.patch.dict(os.environ, {API_VAR: "sk-exported"}):
            self.assertEqual(readiness_problems(routing), {})


class TestNoSideEffects(ReadinessTestCase):
    def test_an_unready_cell_records_no_capacity_gate(self):
        """A readiness failure is operator configuration, not a provider
        condition: recording a gate would make the next select skip the cell
        silently -- exactly the invisible-fallback failure class the probe
        exists to remove."""
        routing = _routing(
            {
                "claude-api": _target(
                    "claude", pool="api", api_opt_in=True, auth={"env": API_VAR}
                ),
                "opencode-api": _target("opencode", pool="api"),
            },
            {"t2-build": {"claude-api": {"model": "sonnet"}}},
        )
        problems = self._problems(routing, {})
        self.assertTrue(problems, "the case must actually report something unready")
        self.assertEqual(agent_capacity.load()["providers"], {})

    def test_probe_never_creates_per_spawn_state(self):
        """`_prepare_child_env()` runs only at spawn: its isolated opencode data
        dir / codex home are side effects a check must not produce, or the next
        spawn would differ from the one the check predicted."""
        routing = _routing(
            {
                "codex-sub": _target("codex"),
                "opencode-free": _target("opencode", pool="free"),
            },
            {
                "t2-build": {
                    "codex-sub": {"model": "gpt-5.6-terra"},
                    "opencode-free": {
                        "model": "opencode/x-preview-f-free",
                        "effort": None,
                    },
                }
            },
        )
        with (
            mock.patch.object(
                spawnlib,
                "prepare_codex_child_environment",
                side_effect=AssertionError("the probe must not create a codex home"),
            ),
            mock.patch.object(
                spawnlib,
                "prepare_opencode_child_environment",
                side_effect=AssertionError("the probe must not create opencode state"),
            ),
        ):
            self.assertEqual(self._problems(routing, {}), {})


class TestProbeContract(unittest.TestCase):
    """The module's whole value is that it can only be fed the *resolved* table:
    no path parameter, and no way to read the routing file itself. A behavior
    test cannot see a file that happened to exist, so the shape is pinned
    structurally."""

    # Anything that turns the probe back into a file reader, or lets it resolve
    # the table it is supposed to be handed.
    BANNED_NAMES = frozenset(
        {
            "open",
            "load_policy",
            "resolve_routing",
            "resolved_routing_file_path",
            "default_routing_file",
        }
    )
    BANNED_ATTRS = frozenset(
        {"read_text", "read_bytes", "write_text", "write_bytes", "is_file", "glob"}
    )

    def test_the_signature_is_the_resolved_table_and_an_optional_base_env(self):
        parameters = inspect.signature(readiness_problems).parameters
        self.assertEqual(list(parameters), ["routing", "base_env"])
        self.assertEqual(parameters["base_env"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertTrue(
            all(name not in {"path", "repo", "routing_file"} for name in parameters)
        )

    def test_no_path_parameter_and_no_file_access_anywhere_in_the_module(self):
        tree = ast.parse(Path(spawn_readiness.__file__).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in self.BANNED_NAMES:
                found.add(node.id)
            elif isinstance(node, ast.Attribute) and node.attr in self.BANNED_ATTRS:
                found.add(node.attr)
            elif isinstance(node, ast.arg) and node.arg in {"path", "repo"}:
                found.add(f"parameter:{node.arg}")
        self.assertEqual(found, set())


if __name__ == "__main__":
    unittest.main()

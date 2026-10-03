#!/usr/bin/env python3
"""Tests for routing_cli.py. Run: python3 test_routing_cli.py"""

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from worktrail.orchestrator import agent_capacity
from worktrail.router import routing_cli
from worktrail.router.policy import (
    _load_yaml_mapping,
    _validate_routing,
    resolve_routing,
)
from worktrail.router.routing_cli import (
    STARTER_ROUTING_YAML,
    _check,
    _init,
    _migrate,
    list_opencode_models,
)

# The routing.yaml shipped 2026-08-26 (docs/config/routing.yaml.example, "claude-first
# per the 2026-08-26 operator decision") -- the legacy shape design D9's `--migrate`
# exists to convert. This is the file's routing.* content verbatim, `effort: xhigh`
# included -- `_migrate_tiers`'s `_normalize_effort` clamps it to that harness's
# highest `EFFORT_VOCABULARY` (task 1.5) entry, so the migrated output still loads
# with zero warnings without the fixture itself deviating from the shipped source.
SHIPPED_2026_08_FIXTURE = """\
agents:
  claude:
    default_model: sonnet
  codex:
    default_model: gpt-5.6-terra
  opencode:
    default_model: opencode/x-preview-f-free

defaults:
  B:
    low:
      agent_cli: codex
      agent_model: gpt-5.6-terra
    medium:
      agent_cli: claude
      agent_model: sonnet

roles:
  review:
    agent_cli: claude
    agent_model: opus

purpose_tiers:
  architecture-design: t1-deep
  security-review: t1-deep
  agentic-automation: t2-build
  scaffolding: t2-build
  bulk-mechanical: t3-bulk
  trivial: t4-trivia

tiers:
  t1-deep:
    claude:
      model: opus
      effort: xhigh
    codex:
      model: gpt-5.6-sol
      effort: xhigh
    opencode:
      model: opencode/claude-opus-5

  t2-build:
    claude:
      model: sonnet
      effort: medium
    codex:
      model: gpt-5.6-terra
      effort: medium
    opencode:
      model: opencode/x-preview-f-free

  t3-bulk:
    claude:
      model: haiku
      effort: medium
    codex:
      model: gpt-5.6-terra
      effort: low
    opencode:
      model: opencode/x-preview-f-free

  t4-trivia:
    claude:
      model: haiku
      effort: low
    codex:
      model: gpt-5.6-luna
      effort: minimal
    opencode:
      model: opencode/x-preview-f-free

fallback:
  - claude
  - codex
  - opencode

drain:
  agent: claude
  fallback_agents:
    - codex
    - opencode
  max_workers: 2
"""


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ListOpencodeModelsTests(unittest.TestCase):
    def test_parses_one_model_id_per_line(self):
        def fake_runner(cmd, **kwargs):
            self.assertEqual(cmd, ["opencode", "models"])
            return _FakeResult(
                stdout="opencode/deepseek-v4-flash-free\nopenrouter/gpt-5.4\ngoogle/gemini-3\n"
            )

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(
            models,
            {
                "opencode/deepseek-v4-flash-free",
                "openrouter/gpt-5.4",
                "google/gemini-3",
            },
        )

    def test_ignores_blank_lines_and_strips_whitespace(self):
        def fake_runner(cmd, **kwargs):
            return _FakeResult(stdout="  opencode/foo  \n\n\nopenrouter/bar\n")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, {"opencode/foo", "openrouter/bar"})

    def test_nonzero_exit_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            return _FakeResult(returncode=1, stdout="", stderr="boom")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_missing_binary_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            raise FileNotFoundError("opencode: command not found")

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_timeout_returns_empty_set_and_warns(self):
        def fake_runner(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["opencode", "models"], timeout=30)

        models = list_opencode_models(runner=fake_runner)
        self.assertEqual(models, set())

    def test_default_runner_is_subprocess_run(self):
        import inspect

        self.assertIs(
            inspect.signature(list_opencode_models).parameters["runner"].default,
            subprocess.run,
        )


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.routing_path = Path(self._tmp.name) / "routing.yaml"
        self.capacity_path = Path(self._tmp.name) / "agent-capacity.json"
        self.now = datetime(2026, 8, 27, tzinfo=UTC)

    def _write(self, text: str) -> None:
        self.routing_path.write_text(text, encoding="utf-8")

    def _run(self, path: Path | None = None, **kwargs):
        """Run `_check` against `path` (default: this fixture).

        `_check` resolves the table the way a spawn does --
        `resolve_routing(load_policy(worktrail_home()))` -- so the file under
        test is selected through the documented `WORKTRAIL_ROUTING_FILE`
        override, the same seam an operator points at a routing file.
        """
        with mock.patch.dict(
            "os.environ",
            {"WORKTRAIL_ROUTING_FILE": str(path or self.routing_path)},
        ):
            return _check(**kwargs)

    def _fake_runner(self, *model_ids):
        def runner(cmd, **kwargs):
            return _FakeResult(stdout="\n".join(model_ids))

        return runner

    def _write_settings(self, name: str, payload: str) -> Path:
        path = Path(self._tmp.name) / name
        path.write_text(payload, encoding="utf-8")
        return path

    def _profile_routing(self, source: Path, *, expect: str = "") -> str:
        # Block style, not `{model: deepseek-flash[1m]}`: inside a YAML FLOW
        # mapping `[` opens a flow sequence, so an unquoted bracketed model id
        # there is a parse error. (Same hazard `explicit_cell_override` avoids
        # by writing its temp file with safe_dump.)
        return f"""
env_profiles:
  deepseek:
    from: {source}
    keys: [ANTHROPIC_BASE_URL]
{expect}targets:
  claude-deepseek:
    harness: claude
    pool: api
    api_opt_in: true
    auth:
      profile: deepseek
tiers:
  t1-deep:
    claude-deepseek:
      model: deepseek-flash[1m]
"""

    def test_resolvable_env_profile_notes_ok_and_exits_zero(self):
        source = self._write_settings(
            "settings.json", '{"env": {"ANTHROPIC_BASE_URL": "https://ds.test"}}'
        )
        self._write(self._profile_routing(source))
        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._run(
                path=self.routing_path, capacity_path=self.capacity_path, now=self.now
            )
        self.assertEqual(rc, 0)
        self.assertIn("env profile deepseek ok", out.getvalue())

    def test_missing_profile_file_fails_without_recording_a_capacity_gate(self):
        """It flips the exit code but must NOT record an `agent_capacity` gate:
        a gate is skipped silently by `select_cell` on the next spawn, which
        would turn a loud config error into an invisible fallback -- the exact
        failure class env profiles exist to eliminate. The FAIL carries the
        probe's own message, which names the cell, the profile, and the file."""
        source = Path(self._tmp.name) / "absent.json"
        self._write(self._profile_routing(source))
        err = io.StringIO()
        with (
            mock.patch.object(agent_capacity, "record") as record,
            redirect_stderr(err),
        ):
            rc = self._run(
                path=self.routing_path, capacity_path=self.capacity_path, now=self.now
            )
        self.assertEqual(rc, 1)
        record.assert_not_called()
        self.assertIn("does not exist", err.getvalue())
        self.assertIn("env profile 'deepseek'", err.getvalue())

    def test_expect_mismatch_names_the_key_and_fails(self):
        source = self._write_settings(
            "settings.json",
            '{"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}}',
        )
        self._write(
            self._profile_routing(
                source,
                expect=(
                    "    expect:\n"
                    "      ANTHROPIC_BASE_URL: https://api.deepseek.com/anthropic\n"
                ),
            )
        )
        err = io.StringIO()
        with redirect_stderr(err):
            rc = self._run(
                path=self.routing_path, capacity_path=self.capacity_path, now=self.now
            )
        self.assertEqual(rc, 1)
        self.assertIn("ANTHROPIC_BASE_URL", err.getvalue())
        self.assertIn("api.deepseek.com", err.getvalue())

    def test_dangling_profile_reference_does_not_crash_check(self):
        """A target naming an undeclared profile warns at validation time and
        FAILs at check time; neither may raise."""
        self._write("""
targets:
  claude-deepseek:
    harness: claude
    pool: api
    api_opt_in: true
    auth:
      profile: ghost
tiers:
  t1-deep:
    claude-deepseek:
      model: deepseek-flash[1m]
""")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = self._run(
                path=self.routing_path, capacity_path=self.capacity_path, now=self.now
            )
        self.assertEqual(rc, 1)
        self.assertIn("ghost", err.getvalue())

    def test_all_cells_clean_exits_zero(self):
        self._write("""
targets:
  claude-sub:
    harness: claude
    pool: subscription
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    claude-sub: {model: sonnet, effort: high}
    opencode-free: {model: opencode/deepseek-v4-flash-free}
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner("opencode/deepseek-v4-flash-free"),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 0)

    def test_absent_opencode_model_gates_and_records(self):
        self._write("""
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    opencode-free: {model: opencode/retired-model-free}
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner("opencode/other-model-free"),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 1)
        cache = agent_capacity.load(self.capacity_path)
        key = agent_capacity.provider_key(
            "opencode-free", "opencode/retired-model-free"
        )
        state = cache["providers"][key]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["failure_class"], "model_unavailable")

    def test_present_opencode_model_does_not_gate(self):
        self._write("""
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    opencode-free: {model: opencode/deepseek-v4-flash-free}
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner("opencode/deepseek-v4-flash-free"),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 0)
        cache = agent_capacity.load(self.capacity_path)
        self.assertEqual(cache["providers"], {})

    def test_free_pool_id_missing_suffix_warns_but_does_not_gate(self):
        self._write("""
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    opencode-free: {model: opencode/deepseek-v4-flash}
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner("opencode/deepseek-v4-flash"),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 0)

    def test_effort_outside_vocabulary_warns_but_does_not_gate(self):
        self._write("""
targets:
  claude-sub:
    harness: claude
    pool: subscription
tiers:
  t1-deep:
    claude-sub: {model: sonnet, effort: extreme}
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner(),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 0)

    def test_opencode_effort_always_out_of_vocabulary_warns(self):
        self._write("""
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    opencode-free: {model: opencode/deepseek-v4-flash-free, effort: high}
""")
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._run(
                path=self.routing_path,
                runner=self._fake_runner("opencode/deepseek-v4-flash-free"),
                capacity_path=self.capacity_path,
                now=self.now,
            )
        self.assertEqual(rc, 0)
        self.assertIn("outside 'opencode'", out.getvalue())

    def test_prints_per_cell_table(self):
        self._write("""
targets:
  claude-sub:
    harness: claude
    pool: subscription
tiers:
  t1-deep:
    claude-sub: {model: sonnet}
""")
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._run(
                path=self.routing_path,
                runner=self._fake_runner(),
                capacity_path=self.capacity_path,
                now=self.now,
            )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("TIER", text)
        self.assertIn("TARGET", text)
        self.assertIn("STATUS", text)
        self.assertIn("t1-deep", text)
        self.assertIn("claude-sub", text)
        self.assertIn("sonnet", text)

    def test_missing_routing_file_exits_nonzero(self):
        rc = self._run(
            path=Path(self._tmp.name) / "does-not-exist.yaml",
            runner=self._fake_runner(),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 1)

    def test_malformed_yaml_exits_nonzero(self):
        self._write("targets: [this is not: a mapping\n")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner(),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 1)

    def test_legacy_key_exits_nonzero(self):
        self._write("""
agents:
  claude:
    default_model: sonnet
""")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner(),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 1)

    def test_empty_routing_exits_nonzero(self):
        self._write("{}\n")
        rc = self._run(
            path=self.routing_path,
            runner=self._fake_runner(),
            capacity_path=self.capacity_path,
            now=self.now,
        )
        self.assertEqual(rc, 1)

    def test_multiple_gated_cells_counted_in_exit_message(self):
        self._write("""
targets:
  opencode-free:
    harness: opencode
    pool: free
  opencode-free-2:
    harness: opencode
    pool: free
tiers:
  t1-deep:
    opencode-free: {model: opencode/retired-a-free}
    opencode-free-2: {model: opencode/retired-b-free}
""")
        import io
        from contextlib import redirect_stderr

        err = io.StringIO()
        with redirect_stderr(err):
            rc = self._run(
                path=self.routing_path,
                runner=self._fake_runner(),
                capacity_path=self.capacity_path,
                now=self.now,
            )
        self.assertEqual(rc, 1)
        self.assertIn("2 cell(s) gated", err.getvalue())

    def test_servable_table_exits_zero(self):
        """Subscription cells are spawn-ready in any shell: nothing to
        resolve, nothing to opt into -- the readiness stage must not turn a
        working table into a failure."""
        self._write("""
targets:
  claude-sub:
    harness: claude
    pool: subscription
  codex-sub:
    harness: codex
    pool: subscription
tiers:
  t1-deep:
    claude-sub: {model: opus, effort: high}
    codex-sub: {model: gpt-5.6-sol, effort: high}
""")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = self._run(runner=self._fake_runner())
        self.assertEqual(rc, 0)
        self.assertIn("ok", out.getvalue())

    def test_unselectable_api_target_fails_with_the_message_and_no_gate(self):
        """`pool: api` without `api_opt_in` is a cell the selector skips as
        ineligible -- no run can reach it. It must be `FAIL` with the probe's
        message, and record NO `model_unavailable` gate even though its model
        is unserved: a gate is skipped *silently* by the next select, turning
        a loud configuration error into an invisible fallback."""
        self._write("""
targets:
  opencode-api:
    harness: opencode
    pool: api
tiers:
  t2-build:
    opencode-api: {model: opencode/retired-model-free}
""")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(agent_capacity, "record") as record,
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            rc = self._run(
                runner=self._fake_runner(),
                capacity_path=self.capacity_path,
                now=self.now,
            )
        self.assertEqual(rc, 1)
        record.assert_not_called()
        self.assertIn("api_opt_in", err.getvalue())
        self.assertIn("FAIL", out.getvalue())

    def test_unset_auth_variable_fails_naming_the_variable(self):
        """The environment-sensitive class: a claude `api` cell whose named
        variable is not in the checking shell cannot spawn from it."""
        var = "WORKTRAIL_TEST_CHECK_API_KEY"
        self._write(f"""
targets:
  claude-api:
    harness: claude
    pool: api
    api_opt_in: true
    auth:
      env: {var}
tiers:
  t2-build:
    claude-api: {{model: sonnet}}
""")
        err = io.StringIO()
        with mock.patch.dict("os.environ"):
            os.environ.pop(var, None)
            with redirect_stderr(err):
                rc = self._run(capacity_path=self.capacity_path, now=self.now)
        self.assertEqual(rc, 1)
        self.assertIn(var, err.getvalue())
        self.assertIn("export it before spawning", err.getvalue())

    def test_auth_variable_exported_in_the_checking_shell_exits_zero(self):
        var = "WORKTRAIL_TEST_CHECK_API_KEY"
        self._write(f"""
targets:
  claude-api:
    harness: claude
    pool: api
    api_opt_in: true
    auth:
      env: {var}
tiers:
  t2-build:
    claude-api: {{model: sonnet}}
""")
        out = io.StringIO()
        with (
            mock.patch.dict("os.environ", {var: "sk-check"}),
            redirect_stdout(out),
        ):
            rc = self._run(capacity_path=self.capacity_path, now=self.now)
        self.assertEqual(rc, 0)
        self.assertIn("ok", out.getvalue())

    def test_env_profiles_dropped_by_the_resolver_fails_though_the_file_declares_it(
        self,
    ):
        """The incident shape end to end: the file declares the profile, but
        the *resolved* table has lost `env_profiles`, so no spawn could resolve
        the cell -- `--check` must judge the table it was served, not report
        `ok` from the file's declared keys."""
        source = self._write_settings(
            "settings.json", '{"env": {"ANTHROPIC_BASE_URL": "https://ds.test"}}'
        )
        self._write(self._profile_routing(source))
        real = resolve_routing

        def resolver_without_env_profiles(policy, *args, **kwargs):
            resolved = real(policy, *args, **kwargs)
            resolved.pop("env_profiles", None)
            return resolved

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(
                routing_cli, "resolve_routing", resolver_without_env_profiles
            ),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            rc = self._run(capacity_path=self.capacity_path, now=self.now)
        self.assertEqual(rc, 1)
        # The file still declares the profile: the failure is about the
        # resolved table, not the file's keys.
        raw = _load_yaml_mapping(self.routing_path.read_text(encoding="utf-8"))
        self.assertIn("deepseek", raw["env_profiles"])
        self.assertIn("FAIL", out.getvalue())
        self.assertIn("deepseek", err.getvalue())
        self.assertIn("env_profiles", err.getvalue())


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.routing_path = Path(self._tmp.name) / "routing.yaml"

    def _write(self, text: str) -> None:
        self.routing_path.write_text(text, encoding="utf-8")

    def _load(self):
        raw = _load_yaml_mapping(self.routing_path.read_text(encoding="utf-8"))
        meta = {"source": str(self.routing_path), "unknown_keys": [], "warnings": []}
        routing = _validate_routing(raw, meta)
        return routing, meta["warnings"]

    def _raw(self):
        return _load_yaml_mapping(self.routing_path.read_text(encoding="utf-8"))

    def test_migrates_shipped_2026_08_fixture_with_zero_warnings(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        rc = _migrate(path=self.routing_path)
        self.assertEqual(rc, 0)
        routing, warnings = self._load()
        self.assertEqual(warnings, [])
        self.assertIsNotNone(routing)

    def test_targets_built_from_fallback_order(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        routing, _ = self._load()
        self.assertEqual(
            list(routing["targets"]), ["claude-sub", "codex-sub", "opencode-free"]
        )
        self.assertEqual(routing["targets"]["claude-sub"]["pool"], "subscription")
        self.assertEqual(routing["targets"]["opencode-free"]["pool"], "free")

    def test_tier_cells_rekeyed_by_target(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        routing, _ = self._load()
        row = routing["tiers"]["t2-build"]
        self.assertEqual(row["claude-sub"]["model"], "sonnet")
        self.assertEqual(row["codex-sub"]["model"], "gpt-5.6-terra")
        self.assertEqual(row["opencode-free"]["model"], "opencode/x-preview-f-free")

    def test_out_of_vocabulary_effort_clamped_to_highest(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        routing, _ = self._load()
        row = routing["tiers"]["t1-deep"]
        self.assertEqual(row["claude-sub"]["effort"], "high")
        self.assertEqual(row["codex-sub"]["effort"], "high")

    def test_default_tier_matches_agents_default_model_row(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        routing, _ = self._load()
        self.assertEqual(routing["default_tier"], "t2-build")

    def test_default_tier_falls_back_to_t2_build_when_no_row_matches(self):
        self._write("""
agents:
  claude:
    default_model: some-model-no-row-has

fallback:
  - claude

tiers:
  t1-deep:
    claude:
      model: opus
""")
        _migrate(path=self.routing_path)
        # No row's cells match agents.claude.default_model, and this fixture
        # (deliberately) declares no t2-build row either -- assert the
        # written literal directly, since re-validating would null out a
        # default_tier naming an undeclared row.
        self.assertEqual(self._raw()["default_tier"], "t2-build")

    def test_roles_review_migrated_to_tier_prefer_independent(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        routing, _ = self._load()
        self.assertEqual(
            routing["roles"]["review"],
            {"tier": "t1-deep", "prefer": "claude-sub", "independent": True},
        )

    def test_drain_reduced_to_max_workers(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        _migrate(path=self.routing_path)
        # Assert the written raw shape directly: legacy `agent`/`fallback_agents`
        # keys must be gone, not merely nulled out by re-validation.
        self.assertEqual(self._raw()["drain"], {"max_workers": 2})

    def test_writes_backup_before_overwriting(self):
        self._write(SHIPPED_2026_08_FIXTURE)
        rc = _migrate(path=self.routing_path)
        self.assertEqual(rc, 0)
        backup_path = Path(str(self.routing_path) + ".bak")
        self.assertTrue(backup_path.is_file())
        self.assertEqual(
            backup_path.read_text(encoding="utf-8"), SHIPPED_2026_08_FIXTURE
        )

    def test_refuses_when_file_already_loads_cleanly(self):
        clean = """
targets:
  claude-sub:
    harness: claude
    pool: subscription
tiers:
  t2-build:
    claude-sub: {model: sonnet}
default_tier: t2-build
"""
        self._write(clean)
        rc = _migrate(path=self.routing_path)
        self.assertEqual(rc, 1)
        self.assertEqual(self.routing_path.read_text(encoding="utf-8"), clean)
        self.assertFalse(Path(str(self.routing_path) + ".bak").is_file())

    def test_missing_file_returns_nonzero(self):
        rc = _migrate(path=Path(self._tmp.name) / "does-not-exist.yaml")
        self.assertEqual(rc, 1)

    def test_malformed_yaml_returns_nonzero(self):
        self._write("targets: [this is not: a mapping\n")
        rc = _migrate(path=self.routing_path)
        self.assertEqual(rc, 1)


def _uncommented_lines(text: str) -> list:
    return [
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class StarterRoutingYamlTests(unittest.TestCase):
    def test_loads_with_zero_warnings(self):
        raw = _load_yaml_mapping(STARTER_ROUTING_YAML)
        meta = {"source": "starter", "unknown_keys": [], "warnings": []}
        routing = _validate_routing(raw, meta)
        self.assertEqual(meta["warnings"], [])
        self.assertIsNotNone(routing)

    def test_names_no_uncommented_opencode_model(self):
        for line in _uncommented_lines(STARTER_ROUTING_YAML):
            self.assertNotIn("opencode/", line)

    def test_declares_only_subscription_targets(self):
        raw = _load_yaml_mapping(STARTER_ROUTING_YAML)
        meta = {"source": "starter", "unknown_keys": [], "warnings": []}
        routing = _validate_routing(raw, meta)
        self.assertEqual(set(routing["targets"]), {"claude-sub", "codex-sub"})
        for target in routing["targets"].values():
            self.assertEqual(target["pool"], "subscription")

    def test_every_row_filled_for_both_targets(self):
        raw = _load_yaml_mapping(STARTER_ROUTING_YAML)
        meta = {"source": "starter", "unknown_keys": [], "warnings": []}
        routing = _validate_routing(raw, meta)
        self.assertTrue(routing["tiers"])
        for row in routing["tiers"].values():
            self.assertEqual(set(row), {"claude-sub", "codex-sub"})

    def test_has_default_tier_and_review_role(self):
        raw = _load_yaml_mapping(STARTER_ROUTING_YAML)
        meta = {"source": "starter", "unknown_keys": [], "warnings": []}
        routing = _validate_routing(raw, meta)
        self.assertIn(routing["default_tier"], routing["tiers"])
        self.assertIn("review", routing["roles"])

    def test_mentions_opencode_free_only_in_a_comment(self):
        commented = [
            line
            for line in STARTER_ROUTING_YAML.splitlines()
            if "opencode-free" in line
        ]
        self.assertTrue(commented)
        for line in commented:
            self.assertTrue(line.strip().startswith("#"))

    def test_closes_with_a_check_instruction(self):
        self.assertIn("--check", STARTER_ROUTING_YAML)

    def test_contains_no_retired_keys(self):
        for retired in ("\nagents:", "\nfallback:", "purpose_tiers:", "drain.agent"):
            self.assertNotIn(retired, STARTER_ROUTING_YAML)


class InitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.routing_path = Path(self._tmp.name) / "routing.yaml"

    def test_prints_check_reminder(self):
        with mock.patch.dict(
            "os.environ", {"WORKTRAIL_ROUTING_FILE": str(self.routing_path)}
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = _init(force=False)
        self.assertEqual(rc, 0)
        self.assertIn("worktrail-routing --check", out.getvalue())
        self.assertEqual(
            self.routing_path.read_text(encoding="utf-8"), STARTER_ROUTING_YAML
        )


if __name__ == "__main__":
    unittest.main()

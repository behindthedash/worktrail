#!/usr/bin/env python3
"""Tests for check_agent_contract.py -- hermetic via a fake subprocess runner.

Pins the core contract: a clean recognized response passes; a raw-fallback
response (the PR #366 failure class) fails with a diagnostic listing the
unrecognized event types; an infra failure fails; codex's file-based transport
is checked via its --output-last-message file, not JSONL "type" vocabulary.
Run: python3 scripts/test_check_agent_contract.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import agent_capacity
from worktrail.orchestrator import check_agent_contract as cac

Proc = namedtuple("Proc", "returncode stdout stderr")


def _claude_ok_stream() -> str:
    return '{"type":"system","subtype":"init"}\n{"type":"assistant","message":{"content":[]}}\n{"type":"result","result":"ok","usage":{},"session_id":"abc"}'


def _opencode_ok_stream() -> str:
    return '{"type":"step_start","sessionID":"s1"}\n{"type":"text","part":{"text":"ok"}}\n{"type":"step_finish","part":{"tokens":{}}}'


def _raw_fallback_stream() -> str:
    # A schema the parser recognizes NO event types in -- forces the
    # raw-string fallback, the exact symptom PR #366 fixed.
    return '{"type":"totally_new_event","payload":"surprise"}'


class CheckAgentClaude(unittest.TestCase):
    def test_recognized_reply_passes(self):
        def runner(cmd, **kwargs):
            return Proc(0, _claude_ok_stream(), "")

        result = cac.check_agent("claude", ".", runner=runner)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("parsed correctly", result.detail)

    def test_raw_fallback_fails_with_diagnostic(self):
        def runner(cmd, **kwargs):
            return Proc(0, _raw_fallback_stream(), "")

        result = cac.check_agent("claude", ".", runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("fell back to raw output", result.detail)
        self.assertIn("totally_new_event", result.unknown_types)

    def test_infra_failure_fails(self):
        def runner(cmd, **kwargs):
            return Proc(1, "", "boom")

        result = cac.check_agent("claude", ".", runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("infra failure", result.detail)

    def test_unexpected_reply_fails(self):
        def runner(cmd, **kwargs):
            stream = '{"type":"result","result":"definitely not the expected word","usage":{}}'
            return Proc(0, stream, "")

        result = cac.check_agent("claude", ".", runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("did not contain the expected reply", result.detail)


class CheckAgentOpencode(unittest.TestCase):
    def test_recognized_reply_passes(self):
        def runner(cmd, **kwargs):
            return Proc(0, _opencode_ok_stream(), "")

        result = cac.check_agent("opencode", ".", runner=runner)
        self.assertTrue(result.ok, result.detail)


class CheckAgentCodex(unittest.TestCase):
    def _find_output_file(self, cmd):
        idx = cmd.index("--output-last-message")
        return cmd[idx + 1]

    def test_output_last_message_recognized(self):
        def runner(cmd, **kwargs):
            path = self._find_output_file(cmd)
            with open(path, "w") as f:
                f.write("ok\n")
            return Proc(0, '{"type":"final"}', "")

        result = cac.check_agent("codex", ".", runner=runner)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("output-last-message", result.detail)

    def test_output_last_message_missing_reply_fails(self):
        def runner(cmd, **kwargs):
            path = self._find_output_file(cmd)
            with open(path, "w") as f:
                f.write("something else entirely\n")
            return Proc(0, '{"type":"final"}', "")

        result = cac.check_agent("codex", ".", runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("did not contain the expected reply", result.detail)

    def test_codex_argv_is_sandboxed_to_the_probe_cwd(self):
        seen = []

        def runner(cmd, **kwargs):
            seen.append(cmd)
            path = self._find_output_file(cmd)
            with open(path, "w") as f:
                f.write("ok\n")
            return Proc(0, '{"type":"final"}', "")

        with tempfile.TemporaryDirectory() as tmp:
            result = cac.check_agent("codex", tmp, runner=runner)
            self.assertTrue(result.ok, result.detail)
            cmd = seen[0]
            self.assertEqual(cmd[cmd.index("-s") + 1], "workspace-write")
            self.assertNotIn("danger-full-access", cmd)
            add_dirs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--add-dir"]
            self.assertIn(tmp, add_dirs)

    def test_codex_infra_failure_fails(self):
        def runner(cmd, **kwargs):
            return Proc(1, "", "boom")

        result = cac.check_agent("codex", ".", runner=runner)
        self.assertFalse(result.ok)
        self.assertIn("infra failure", result.detail)


class CheckAgentMain(unittest.TestCase):
    def test_main_exits_nonzero_on_any_failure(self):
        def make_runner(fail):
            def runner(cmd, **kwargs):
                if fail:
                    return Proc(1, "", "boom")
                return Proc(0, _claude_ok_stream(), "")

            return runner

        orig = cac.subprocess.run
        try:
            cac.subprocess.run = make_runner(fail=True)
            rc = cac.main(["--agent", "claude"])
            self.assertEqual(rc, 1)

            cac.subprocess.run = make_runner(fail=False)
            rc = cac.main(["--agent", "claude"])
            self.assertEqual(rc, 0)
        finally:
            cac.subprocess.run = orig


class CheckAgentCapacityGate(unittest.TestCase):
    """The `worktrail-agent-capacity check-agent` CLI end of the shared gate
    resolution: the drain persists a bare target key, so the same resolution
    that gates `select_cell` must make `check-agent` report the one resolved
    agent's cell gated -- and stop once the bare entry's window passes."""

    ROUTING: ClassVar[dict] = {
        "targets": {
            "claude-deepseek": {
                "harness": "claude",
                "pool": "api",
                "api_opt_in": True,
            },
        },
        "tiers": {
            "t2-build": {"claude-deepseek": {"model": "deepseek-flash[1m]"}},
        },
        "roles": {},
        "purposes": {},
        "default_tier": "t2-build",
        "drain": {},
    }

    def _check_agent(self, cache: Path, now: datetime) -> tuple[int, dict]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = agent_capacity.cmd_check_agent(
                "claude", json.dumps(self.ROUTING), None, path=cache, now=now
            )
        return rc, json.loads(out.getvalue())

    def _write_bare_gate(self, cache: Path, retry_after: datetime) -> None:
        cache.write_text(
            json.dumps(
                {
                    "version": 1,
                    "providers": {
                        "claude-deepseek": {
                            "status": "unavailable",
                            "failure_class": "billing",
                            "retry_after": retry_after.isoformat(),
                            "checked_at": datetime(
                                2026, 7, 20, 20, 0, tzinfo=UTC
                            ).isoformat(),
                            "source": "drain",
                        }
                    },
                }
            )
        )

    def test_active_bare_target_entry_reports_the_resolved_agent_gated(self):
        now = datetime(2026, 7, 20, 20, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "capacity.json"
            self._write_bare_gate(cache, now + timedelta(hours=1))

            rc, out = self._check_agent(cache, now)

        self.assertEqual(rc, 1)
        self.assertTrue(out["gated"])
        self.assertEqual(out["target"], "claude-deepseek")
        self.assertEqual(out["model"], "deepseek-flash[1m]")
        self.assertEqual(out["failure_class"], "billing")

    def test_expired_bare_target_entry_reports_the_resolved_agent_ungated(self):
        now = datetime(2026, 7, 20, 20, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "capacity.json"
            self._write_bare_gate(cache, now - timedelta(minutes=1))

            rc, out = self._check_agent(cache, now)

        self.assertEqual(rc, 0)
        self.assertEqual(out, {"gated": False})


if __name__ == "__main__":
    unittest.main()

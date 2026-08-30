#!/usr/bin/env python3
"""Tests for check_agent_contract.py -- hermetic via a fake subprocess runner.

Pins the core contract: a clean recognized response passes; a raw-fallback
response (the PR #366 failure class) fails with a diagnostic listing the
unrecognized event types; an infra failure fails; codex's file-based transport
is checked via its --output-last-message file, not JSONL "type" vocabulary.
Run: python3 scripts/test_check_agent_contract.py
"""

import os
import sys
import unittest
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import check_agent_contract as cac

Proc = namedtuple("Proc", "returncode stdout stderr")


def _claude_ok_stream() -> str:
    return "\n".join(
        [
            '{"type":"system","subtype":"init"}',
            '{"type":"assistant","message":{"content":[]}}',
            '{"type":"result","result":"ok","usage":{},"session_id":"abc"}',
        ]
    )


def _opencode_ok_stream() -> str:
    return "\n".join(
        [
            '{"type":"step_start","sessionID":"s1"}',
            '{"type":"text","part":{"text":"ok"}}',
            '{"type":"step_finish","part":{"tokens":{}}}',
        ]
    )


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


if __name__ == "__main__":
    unittest.main()

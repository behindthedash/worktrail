#!/usr/bin/env python3
"""Tests for spawnlib.py -- headless `claude -p` invocation with bounded retry.

Hermetic: subprocess.run is replaced by a scripted fake. Pins the core contract:
a transient infra failure (non-zero exit / empty stdout) is retried; a clean run
is returned immediately; retries are bounded; a wall-clock timeout PROPAGATES (a
stuck worker must not be silently re-run). Run: python3 scripts/test_spawnlib.py
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import spawnlib  # noqa: E402

os.environ.setdefault("GO_AGENT_CAPACITY_CACHE", os.path.join(tempfile.mkdtemp(), "capacity.json"))

Proc = namedtuple("Proc", "returncode stdout stderr")


class FakeRun:
    """Returns scripted outcomes in order (last repeats); an Exception is raised."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.kwargs = []

    def __call__(self, *a, **k):
        self.calls += 1
        self.kwargs.append(k)
        o = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(o, BaseException):
            raise o
        return o


class SpawnRetry(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(self._cache.name, "capacity.json")

    def tearDown(self):
        spawnlib.subprocess.run = self._orig
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _run(self, outcomes, **kw):
        fr = FakeRun(outcomes)
        spawnlib.subprocess.run = fr
        out = spawnlib.spawn_claude_p("prompt", "/tmp", retries=2, sleep=lambda *_: None, **kw)
        return out, fr

    def test_success_first_try_no_retry(self):
        out, fr = self._run([Proc(0, "ok report", "")])
        self.assertEqual(out.text, "ok report")
        self.assertEqual(fr.calls, 1)
        self.assertEqual(fr.kwargs[0]["env"]["CC_HEADLESS"], "1")

    def test_nonzero_exit_retried_then_succeeds(self):
        out, fr = self._run([Proc(1, "", "boom"), Proc(0, "good", "")])
        self.assertEqual(out.text, "good")
        self.assertEqual(fr.calls, 2)

    def test_empty_stdout_is_infra_failure_and_retried(self):
        out, fr = self._run([Proc(0, "   ", ""), Proc(0, "real output", "")])
        self.assertEqual(out.text, "real output")
        self.assertEqual(fr.calls, 2)

    def test_bounded_retries_then_returns_last(self):
        out, fr = self._run([Proc(1, "", "always fails")])
        self.assertEqual(fr.calls, 3)  # first attempt + 2 retries
        self.assertEqual(out.text, "")

    def test_task_failure_report_is_not_retried(self):
        # exit 0 + real output that happens to be a status:failed report -> a task
        # verdict, NOT an infra failure: returned immediately, no retry.
        report = '```json\n{"task":"T","step":"implement","status":"failed"}\n```'
        out, fr = self._run([Proc(0, report, "")])
        self.assertEqual(fr.calls, 1)
        self.assertEqual(out.text, report)

    def test_timeout_propagates(self):
        spawnlib.subprocess.run = FakeRun([subprocess.TimeoutExpired(cmd="claude", timeout=1)])
        with self.assertRaises(subprocess.TimeoutExpired):
            spawnlib.spawn_claude_p("p", "/tmp", retries=2, sleep=lambda *_: None)


class SessionLimitParse(unittest.TestCase):
    def test_returns_none_without_message(self):
        self.assertIsNone(spawnlib.parse_session_limit_reset("normal worker output"))
        self.assertIsNone(spawnlib.parse_session_limit_reset(None))

    def test_parses_reset_same_day(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)
        r = spawnlib.parse_session_limit_reset(
            "You've hit your session limit. Your limit resets at 3:00pm.", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 4, 15, 0))

    def test_rolls_to_tomorrow_when_reset_already_passed(self):
        now = datetime.datetime(2026, 6, 4, 16, 0)
        r = spawnlib.parse_session_limit_reset("hit your session limit, resets 3:00pm", now=now)
        self.assertEqual(r, datetime.datetime(2026, 6, 5, 15, 0))

    def test_handles_am_and_loose_spacing(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)
        r = spawnlib.parse_session_limit_reset(
            "hit your session limit ... resets 9:30 am", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 5, 9, 30))

    def test_ignores_benign_mention_of_session_limit(self):
        # A plain "session limit" reference (no "hit your ... resets <time>") is not a
        # rate-limit hit and must not trigger a wait.
        self.assertIsNone(
            spawnlib.parse_session_limit_reset("note: the session limit config is 5 hours")
        )

    def test_long_transcript_quoting_notice_is_not_a_limit(self):
        # A real cap notice IS the output (one short message). A worker transcript
        # that merely QUOTES the wording — e.g. a worker reading/editing this very
        # module's docstring, or writing test fixtures — must not classify as a cap.
        # Regression for the 2026-07-23 spec-023 incident: TASK-005 workers matched
        # the docstring example on every spawn and parked runs until "3:00pm".
        transcript = (
            "I updated spawnlib.py. The docstring example reads: "
            "\"You've hit your session limit. Your limit resets at 3:00pm.\"\n"
            + ("x" * (spawnlib._SESSION_LIMIT_NOTICE_MAX_CHARS + 1))
        )
        self.assertIsNone(spawnlib.parse_session_limit_reset(transcript))

    def test_short_genuine_notice_still_parses(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)
        r = spawnlib.parse_session_limit_reset(
            "You've hit your session limit. Your limit resets at 3:00pm.", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 4, 15, 0))


class SessionLimitRetry(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(self._cache.name, "capacity.json")

    def tearDown(self):
        spawnlib.subprocess.run = self._orig
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _run(self, outcomes, **kw):
        fr = FakeRun(outcomes)
        spawnlib.subprocess.run = fr
        slept = []
        out = spawnlib.spawn_claude_p(
            "p", "/tmp", retries=2, sleep=lambda s: slept.append(s), **kw
        )
        return out, fr, slept

    def test_waits_then_succeeds_on_retry(self):
        limit = Proc(0, "You've hit your session limit, resets 3:00pm", "")
        out, fr, slept = self._run([limit, Proc(0, "good report", "")])
        self.assertEqual(out.text, "good report")
        self.assertEqual(fr.calls, 2)
        self.assertEqual(len(slept), 1)  # slept once for the reset window

    def test_wait_does_not_consume_infra_retry_budget(self):
        # One session-limit wait, THEN a fully-exhausted infra retry sequence
        # (first attempt + 2 retries). The wait must not eat an infra attempt.
        limit = Proc(0, "hit your session limit resets 3:00pm", "")
        fail = Proc(1, "", "boom")
        out, fr, slept = self._run([limit, fail, fail, fail], session_limit_waits=3)
        self.assertEqual(fr.calls, 4)  # 1 wait + 3 infra attempts
        self.assertEqual(len(slept), 3)  # 1 session wait + 2 infra backoffs

    def test_session_limit_waits_are_bounded(self):
        # A persistently rate-limited account must not loop forever: after
        # session_limit_waits sleeps, the notice is handed back to the caller.
        limit = Proc(0, "hit your session limit resets 3:00pm", "")
        out, fr, slept = self._run([limit], session_limit_waits=2)
        self.assertEqual(len(slept), 2)
        self.assertEqual(fr.calls, 3)  # 2 bounded waits + 1 final fall-through
        self.assertIn("session limit", out.text)

    def test_worker_transcript_quoting_notice_runs_to_success_without_wait(self):
        # End-to-end regression for the spec-023 false positive: a SUCCESSFUL worker
        # whose (long, plain-text) output quotes the cap wording must be treated as
        # a normal success — no session-limit sleep, no retry, work not discarded.
        transcript = (
            "Edited spawnlib.py; the docstring example is "
            "\"You've hit your session limit. Your limit resets at 3:00pm.\" "
            + ("done. " * 200)
        )
        out, fr, slept = self._run([Proc(0, transcript, "")])
        self.assertEqual(fr.calls, 1)
        self.assertEqual(slept, [])
        self.assertEqual(out.text, transcript)

    def test_stream_json_transcript_quoting_notice_in_events_is_not_a_limit(self):
        # stream-json: the quoted wording appears in intermediate transcript events
        # (tool echoes of the file being edited) while the final "result" event is a
        # normal report — the parsed RESULT text is what gets scanned, so no wait.
        lines = [
            json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text":
                    "The docstring says: You've hit your session limit. "
                    "Your limit resets at 3:00pm."}]},
            }),
            json.dumps({"type": "result", "subtype": "success",
                        "result": "final report: all edits committed", "usage": {}}),
        ]
        out, fr, slept = self._run([Proc(0, "\n".join(lines), "")])
        self.assertEqual(fr.calls, 1)
        self.assertEqual(slept, [])
        self.assertEqual(out.text, "final report: all edits committed")


class Helpers(unittest.TestCase):
    def test_is_infra_failure(self):
        self.assertTrue(spawnlib.is_infra_failure(1, "stuff"))
        self.assertTrue(spawnlib.is_infra_failure(0, ""))
        self.assertTrue(spawnlib.is_infra_failure(0, "   \n"))
        self.assertFalse(spawnlib.is_infra_failure(0, "real"))

    def test_build_cmd_includes_model_and_extra(self):
        c = spawnlib.build_cmd("hi", model="haiku", extra_args=["--append-system-prompt", "x"])
        self.assertEqual(c[:3], ["claude", "-p", "hi"])
        self.assertIn("--permission-mode", c)
        self.assertIn("bypassPermissions", c)
        self.assertIn("--model", c)
        self.assertIn("haiku", c)
        self.assertIn("--append-system-prompt", c)

    def test_build_cmd_no_model(self):
        c = spawnlib.build_cmd("hi")
        self.assertNotIn("--model", c)

    def test_build_cmd_uses_stream_json(self):
        c = spawnlib.build_cmd("hi")
        self.assertIn("--output-format", c)
        idx = c.index("--output-format")
        self.assertEqual(c[idx + 1], "stream-json")

    def test_build_cmd_opencode(self):
        c = spawnlib.build_cmd("hi", agent="opencode", model="opencode/claude-sonnet-4-6")
        self.assertEqual(c[:3], ["opencode", "run", "--format"])
        self.assertEqual(c[3], "json")
        self.assertIn("--model", c)
        self.assertIn("opencode/claude-sonnet-4-6", c)
        self.assertEqual(c[-1], "hi")

    def test_build_cmd_codex(self):
        c = spawnlib.build_cmd("hi", agent="codex", model="gpt-5.3-codex", output_last_message="/tmp/out")
        self.assertEqual(c[:2], ["codex", "exec"])
        self.assertIn("--json", c)
        self.assertIn("-s", c)
        self.assertIn("danger-full-access", c)
        self.assertNotIn("-a", c)
        self.assertNotIn("on-request", c)
        self.assertIn("--output-last-message", c)
        self.assertIn("/tmp/out", c)
        self.assertEqual(c[-1], "hi")

    def test_build_cmd_rejects_unknown_agent(self):
        with self.assertRaises(ValueError):
            spawnlib.build_cmd("hi", agent="bogus")

    def test_build_cmd_no_effort_byte_identical_to_pre_change(self):
        # model-tier-routing 3.4: effort=None must not perturb any agent's
        # command line -- byte-identical to the pre-effort build_cmd() output.
        for agent in ("claude", "opencode", "codex"):
            with_effort_none = spawnlib.build_cmd("hi", agent=agent, effort=None)
            without_effort_kwarg = spawnlib.build_cmd("hi", agent=agent)
            self.assertEqual(with_effort_none, without_effort_kwarg)
            self.assertNotIn("--effort", with_effort_none)
            self.assertNotIn("--variant", with_effort_none)
            self.assertFalse(any("model_reasoning_effort" in part for part in with_effort_none))

    def test_build_cmd_claude_effort_flag(self):
        c = spawnlib.build_cmd("hi", agent="claude", effort="high")
        self.assertIn("--effort", c)
        self.assertEqual(c[c.index("--effort") + 1], "high")

    def test_build_cmd_opencode_effort_variant_flag(self):
        c = spawnlib.build_cmd("hi", agent="opencode", effort="max")
        self.assertIn("--variant", c)
        self.assertEqual(c[c.index("--variant") + 1], "max")

    def test_build_cmd_codex_effort_reasoning_config(self):
        c = spawnlib.build_cmd("hi", agent="codex", effort="xhigh")
        self.assertIn("-c", c)
        self.assertEqual(c[c.index("-c") + 1], "model_reasoning_effort=xhigh")


class DefaultSettingSourcesStructural(unittest.TestCase):
    """Structural guard for handoff 20260714-120009 item 3: three PRs
    (#251/#252/#253) each patched a DIFFERENT direct spawnlib call site to add
    --setting-sources project,local (excluding the operator's user-level
    ~/.claude/settings.json and its Stop hook -- investigation 20260711-130900).
    Nothing stopped a fourth call site from reintroducing the gap. build_cmd
    now defaults the flag for every claude spawn regardless of what the caller
    passes, so a brand-new call site is covered for free."""

    def test_claude_gets_default_with_no_extra_args(self):
        c = spawnlib.build_cmd("hi")  # simulates a hypothetical new call site
        self.assertIn("--setting-sources", c)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "project,local")

    def test_claude_gets_default_with_unrelated_extra_args(self):
        c = spawnlib.build_cmd("hi", extra_args=["--append-system-prompt", "x"])
        self.assertIn("--setting-sources", c)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "project,local")

    def test_caller_supplied_setting_sources_is_not_overridden(self):
        # A caller that genuinely wants the operator's user-level settings opts
        # out by passing its own value; the default must never clobber it.
        c = spawnlib.build_cmd("hi", extra_args=["--setting-sources", "all"])
        self.assertEqual(c.count("--setting-sources"), 1)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "all")

    def test_codex_and_opencode_never_get_the_claude_only_flag(self):
        for agent, model in (("codex", "gpt-5.3-codex"), ("opencode", "opencode/claude-sonnet-4-6")):
            c = spawnlib.build_cmd("hi", agent=agent, model=model)
            self.assertNotIn("--setting-sources", c, agent)

    def test_spawn_agent_reaches_the_default_through_to_subprocess(self):
        # End-to-end through spawn_agent (not just build_cmd) so a caller that
        # never passes extra_args at all is still covered.
        captured_cmd = {}

        def fake_run(cmd, **kw):
            captured_cmd["cmd"] = cmd
            return Proc(0, "ok", "")

        orig = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            spawnlib.spawn_agent("prompt", "/tmp", sleep=lambda *_: None)
        finally:
            spawnlib.subprocess.run = orig
        cmd = captured_cmd["cmd"]
        self.assertIn("--setting-sources", cmd)
        idx = cmd.index("--setting-sources")
        self.assertEqual(cmd[idx + 1], "project,local")


class ParseStreamJson(unittest.TestCase):
    def _stream(self, events):
        return "\n".join(json.dumps(e) for e in events)

    def test_extracts_result_and_usage(self):
        raw = self._stream([
            {"type": "system", "subtype": "init"},
            {
                "type": "result",
                "subtype": "success",
                "result": "final answer",
                "total_cost_usd": 0.005,
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 50,
                    "output_tokens": 20,
                },
            },
        ])
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "final answer")
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cache_read_input_tokens"], 50)
        self.assertAlmostEqual(usage["total_cost_usd"], 0.005)
        self.assertEqual(usage["subtype"], "success")
        self.assertEqual(usage["is_error"], False)
        self.assertEqual(usage["stop_reason"], "")
        self.assertEqual(usage["num_turns"], 0)
        self.assertEqual(usage["permission_denials"], [])
        self.assertEqual(tools, [])
        self.assertEqual(skills, [])
        self.assertEqual(sid, "")

    def test_extracts_truncation_diagnostics(self):
        # Regression fixture for the report-back parse gap: when a worker's final
        # turn ends without ever writing the fenced ```json block, dispatch.
        # parse_report_back raises "no report-back JSON block found" with no
        # indication of *why* the turn ended. subtype/stop_reason/num_turns are
        # exactly the signal that distinguishes "the model ran out of budget mid
        # -analysis" from "the model finished cleanly but chose not to write the
        # block" -- verified field names from a live `claude -p --output-format
        # stream-json` result event (handoff 20260711-130900).
        raw = self._stream([
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "(partial analysis, no trailing json block)",
                "stop_reason": "max_tokens",
                "num_turns": 42,
                "permission_denials": ["Task"],
                "total_cost_usd": 1.2,
                "usage": {"input_tokens": 500, "output_tokens": 4000},
            },
        ])
        text, usage, _, _, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "(partial analysis, no trailing json block)")
        self.assertEqual(usage["subtype"], "error_max_turns")
        self.assertEqual(usage["is_error"], True)
        self.assertEqual(usage["stop_reason"], "max_tokens")
        self.assertEqual(usage["num_turns"], 42)
        self.assertEqual(usage["permission_denials"], ["Task"])

    def test_extracts_tool_names(self):
        raw = self._stream([
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "1", "name": "Read", "input": {}},
                        {"type": "tool_use", "id": "2", "name": "Bash", "input": {}},
                        {"type": "text", "text": "thinking"},
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "3", "name": "Edit", "input": {}},
                        {"type": "tool_use", "id": "4", "name": "Read", "input": {}},
                    ]
                },
            },
            {"type": "result", "subtype": "success", "result": "done", "total_cost_usd": 0.0, "usage": {}},
        ])
        _, _, tools, skills, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(tools, ["Bash", "Edit", "Read"])
        self.assertEqual(skills, [])

    def test_extracts_skill_names_from_skill_tool(self):
        raw = self._stream([
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "1",
                            "name": "Skill",
                            "input": {"skill": "developer-kit-specs:parallel-orchestrator"},
                        },
                        {
                            "type": "tool_use",
                            "id": "2",
                            "name": "Skill",
                            "input": {"skill": "developer-kit-specs:parallel-orchestrator"},
                        },
                    ]
                },
            },
            {"type": "result", "subtype": "success", "result": "done", "total_cost_usd": 0.0, "usage": {}},
        ])
        _, _, tools, skills, _ = spawnlib._parse_stream_json(raw)
        self.assertNotIn("Skill", tools)
        self.assertEqual(skills, ["developer-kit-specs:parallel-orchestrator"])

    def test_fallback_on_non_jsonl_input(self):
        raw = "this is just a plain string not JSONL"
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, raw)
        self.assertEqual(usage, {})
        self.assertEqual(tools, [])
        self.assertEqual(skills, [])
        self.assertEqual(sid, "")

    def test_fallback_on_old_json_envelope(self):
        raw = '{"result": "the answer", "total_cost_usd": 0.01, "usage": {"input_tokens": 5}}'
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, raw)
        self.assertEqual(sid, "")

    def test_empty_stream(self):
        text, usage, tools, skills, sid = spawnlib._parse_stream_json("")
        self.assertEqual(text, "")
        self.assertEqual(usage, {})
        self.assertEqual(sid, "")

    def test_spawn_result_has_tools_fields(self):
        r = spawnlib.SpawnResult(text="hi", usage={})
        self.assertEqual(r.tools_used, [])
        self.assertEqual(r.skills_used, [])

    def test_spawn_result_with_tools(self):
        r = spawnlib.SpawnResult(text="hi", usage={}, tools_used=["Bash", "Read"], skills_used=["foo:bar"])
        self.assertEqual(r.tools_used, ["Bash", "Read"])
        self.assertEqual(r.skills_used, ["foo:bar"])


class ParseStreamJsonOpenCode(unittest.TestCase):
    """opencode's real `opencode run --format json` event vocabulary is completely
    different from claude's (step_start/text/tool_use/step_finish/error, each nested
    under "part", vs. claude's system/assistant/result) -- verified against a live
    reproduction (handoff 20260722-152514). Before this fix `_parse_stream_json` did
    not recognize a single opencode event type, so a successful spawn's `result_text`
    fell through to the `= raw` fallback (the entire multi-KB transcript) and `usage`
    stayed empty."""

    def _stream(self, events):
        return "\n".join(json.dumps(e) for e in events)

    def test_text_event_becomes_result_text(self):
        raw = self._stream([
            {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "final opencode text"}},
        ])
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "final opencode text")
        self.assertEqual(sid, "ses_1")

    def test_last_text_event_wins(self):
        raw = self._stream([
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "turn 1"}},
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "turn 2 final"}},
        ])
        text, *_ = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "turn 2 final")

    def test_tool_use_event_collects_tool_name(self):
        raw = self._stream([
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "bash"}},
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "read"}},
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "bash"}},
        ])
        _, _, tools, skills, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(tools, ["bash", "read"])
        self.assertEqual(skills, [])

    def test_step_finish_sums_tokens_across_steps(self):
        # Real shape from a live reproduction: opencode has no single final
        # aggregate event like claude's "result" -- one "step_finish" per step,
        # each carrying that step's own token slice.
        raw = self._stream([
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {
                    "type": "step-finish",
                    "tokens": {"total": 10, "input": 6, "output": 2, "reasoning": 0,
                               "cache": {"write": 1, "read": 1}},
                    "cost": 0.0,
                },
            },
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {
                    "type": "step-finish",
                    "tokens": {"total": 20, "input": 12, "output": 4, "reasoning": 0,
                               "cache": {"write": 0, "read": 4}},
                    "cost": 0.0005,
                },
            },
        ])
        _, usage, _, _, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(usage["input_tokens"], 18)
        self.assertEqual(usage["output_tokens"], 6)
        self.assertEqual(usage["cache_creation_input_tokens"], 1)
        self.assertEqual(usage["cache_read_input_tokens"], 5)
        self.assertAlmostEqual(usage["total_cost_usd"], 0.0005)
        self.assertEqual(sid, "ses_1")

    def test_step_start_and_todowrite_tool_do_not_break_parsing(self):
        raw = self._stream([
            {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "todowrite"}},
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "done"}},
        ])
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "done")
        self.assertEqual(tools, ["todowrite"])

    def test_no_step_finish_leaves_usage_empty(self):
        # A run with only text/tool_use events (no step_finish reached, e.g. an
        # immediate top-level error) must not fabricate a zeroed usage dict.
        raw = self._stream([
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "read"}},
        ])
        _, usage, _, _, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(usage, {})


class OpenCodeErrorEvent(unittest.TestCase):
    """opencode's top-level `{"type":"error",...}` event -- verified against a live
    reproduction (handoff 20260722-152514, /tmp/opencode-error-repro.jsonl): exit 0,
    non-empty stdout, but the provider itself failed. Before this fix
    `is_infra_failure` only checked returncode/empty-output, so this shape was
    misclassified as a clean success."""

    def test_opencode_error_event_helper_extracts_error(self):
        line = json.dumps({
            "type": "error",
            "sessionID": "ses_err",
            "error": {"name": "UnknownError", "data": {"message": "boom", "ref": "err_1"}},
        })
        err = spawnlib._opencode_error_event(line)
        self.assertEqual(err["name"], "UnknownError")
        self.assertEqual(err["data"]["message"], "boom")

    def test_opencode_error_event_helper_none_for_claude_result(self):
        line = json.dumps({"type": "result", "result": "ok"})
        self.assertIsNone(spawnlib._opencode_error_event(line))

    def test_opencode_error_event_helper_none_for_empty(self):
        self.assertIsNone(spawnlib._opencode_error_event(""))
        self.assertIsNone(spawnlib._opencode_error_event(None))

    def test_is_infra_failure_true_for_opencode_error_event(self):
        line = json.dumps({
            "type": "error",
            "error": {"name": "UnknownError", "data": {"message": "Unexpected server error."}},
        })
        self.assertTrue(spawnlib.is_infra_failure(0, line))

    def test_is_infra_failure_false_for_opencode_success_event(self):
        line = json.dumps({"type": "text", "part": {"type": "text", "text": "done"}})
        self.assertFalse(spawnlib.is_infra_failure(0, line))


class CodexSpawn(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run

    def tearDown(self):
        spawnlib.subprocess.run = self._orig

    def test_codex_reads_last_message_file(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w") as f:
                f.write("codex final report")
            return Proc(0, '{"type":"event"}\n', "")

        spawnlib.subprocess.run = fake_run
        with patch.object(
            spawnlib,
            "prepare_codex_child_environment",
            return_value=(os.environ.copy(), "/tmp/worktrail-codex-child", False),
        ):
            out = spawnlib.spawn_agent("prompt", "/tmp", agent="codex", model="gpt-5.3-codex")
        self.assertEqual(out.text, "codex final report")
        self.assertEqual(seen["cmd"][:2], ["codex", "exec"])

    def test_codex_worker_uses_prepared_child_home(self):
        seen = {}
        child_env = {"CODEX_HOME": "/tmp/worktrail-codex-child"}

        def fake_run(cmd, **kwargs):
            seen["env"] = kwargs["env"]
            out_path = cmd[cmd.index("--output-last-message") + 1]
            with open(out_path, "w") as f:
                f.write("codex final report")
            return Proc(0, '{"type":"event"}\n', "")

        spawnlib.subprocess.run = fake_run
        with patch.object(
            spawnlib,
            "prepare_codex_child_environment",
            return_value=(child_env.copy(), child_env["CODEX_HOME"], False),
        ) as prepare:
            spawnlib.spawn_agent("prompt", "/tmp", agent="codex", model="gpt-5.3-codex")

        prepare.assert_called_once_with()
        self.assertEqual(seen["env"]["CODEX_HOME"], child_env["CODEX_HOME"])
        self.assertEqual(seen["env"]["CC_HEADLESS"], "1")


class OpenCodeSpawn(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run

    def tearDown(self):
        spawnlib.subprocess.run = self._orig

    def test_opencode_uses_json_mode_and_default_model(self):
        # Fixture uses opencode's REAL event shape ("text" event, "part.text"),
        # not claude's -- this is the exact gap that let the schema mismatch bug
        # (handoff 20260722-152514) ship undetected: the old fixture here faked a
        # claude-shaped "result" event, which _parse_stream_json happened to accept
        # regardless of which agent produced it.
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return Proc(
                0,
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "ses_abc123",
                        "part": {"type": "text", "text": "opencode final report"},
                    }
                )
                + "\n",
                "",
            )

        spawnlib.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as cache_dir, \
                tempfile.TemporaryDirectory() as cwd:
            with patch.dict(
                os.environ,
                {"GO_AGENT_CAPACITY_CACHE": os.path.join(cache_dir, "capacity.json")},
                clear=True,
            ):
                out = spawnlib.spawn_agent("prompt", cwd, agent="opencode")
        self.assertEqual(out.text, "opencode final report")
        self.assertEqual(out.session_id, "ses_abc123")
        self.assertEqual(seen["cmd"][:2], ["opencode", "run"])
        self.assertIn("--format", seen["cmd"])
        self.assertIn("json", seen["cmd"])
        self.assertIn("deepseek/deepseek-v4-flash", seen["cmd"])

    def test_opencode_step_finish_usage_reaches_spawn_result(self):
        events = [
            {"type": "step_start", "sessionID": "ses_1", "part": {"type": "step-start"}},
            {"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool", "tool": "read"}},
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {
                    "type": "step-finish",
                    "tokens": {"total": 100, "input": 60, "output": 20, "reasoning": 0,
                               "cache": {"write": 5, "read": 15}},
                    "cost": 0.001,
                },
            },
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "opencode report"}},
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"

        def fake_run(cmd, **kwargs):
            return Proc(0, raw, "")

        spawnlib.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("prompt", cwd, agent="opencode")
        self.assertEqual(out.text, "opencode report")
        self.assertEqual(out.usage["input_tokens"], 60)
        self.assertEqual(out.usage["output_tokens"], 20)
        self.assertEqual(out.usage["cache_creation_input_tokens"], 5)
        self.assertEqual(out.usage["cache_read_input_tokens"], 15)
        self.assertAlmostEqual(out.usage["total_cost_usd"], 0.001)

    def test_opencode_error_event_is_infra_failure_and_retried(self):
        # Root-cause reproduction (handoff 20260722-152514): a free-tier rate-limit
        # blip surfaces as a clean exit (0) carrying only a top-level error event.
        # Before this fix that was recorded as outcome="available" with zero
        # retries; now it must retry like any other infra failure.
        error_line = json.dumps({
            "type": "error",
            "sessionID": "ses_err",
            "error": {
                "name": "UnknownError",
                "data": {"message": "Unexpected server error. Check server logs for details.",
                          "ref": "err_55ac1dc1"},
            },
        })
        success_line = json.dumps({
            "type": "text",
            "sessionID": "ses_err",
            "part": {"type": "text", "text": "opencode retried report"},
        })
        fr = FakeRun([Proc(0, error_line, ""), Proc(0, success_line, "")])
        spawnlib.subprocess.run = fr
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent(
                "prompt", cwd, agent="opencode", retries=2, sleep=lambda *_: None
            )
        self.assertEqual(out.text, "opencode retried report")
        self.assertEqual(fr.calls, 2)


class OpencodeHeadlessEnvironment(unittest.TestCase):
    """Brief 20260811-220340: concurrent opencode workers must never share one
    SQLite opencode.db; a headless worker's tool calls inside its authorized
    worktree (+ git common dir) must not be auto-rejected on an "ask"; provider
    identity (auth.json in the data dir) must survive the isolation; and a
    spawn that produces no report-back must surface actionable diagnostics
    (session id, denials, isolated-state location)."""

    def setUp(self):
        self._orig = spawnlib.subprocess.run

    def tearDown(self):
        spawnlib.subprocess.run = self._orig

    # -- prepare_opencode_child_environment ---------------------------------- #

    def test_state_dirs_are_distinct_per_worktree(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            env_a, data_a = spawnlib.prepare_opencode_child_environment(a, {})
            env_b, data_b = spawnlib.prepare_opencode_child_environment(b, {})
            self.assertNotEqual(env_a["XDG_DATA_HOME"], env_b["XDG_DATA_HOME"])
            self.assertTrue(env_a["XDG_DATA_HOME"].startswith(a))
            self.assertTrue(env_b["XDG_DATA_HOME"].startswith(b))
            self.assertNotEqual(data_a, data_b)
            self.assertTrue(data_a.is_dir())
            self.assertTrue(data_b.is_dir())

    def test_scratch_self_gitignores_so_workers_never_commit_it(self):
        with tempfile.TemporaryDirectory() as cwd:
            spawnlib.prepare_opencode_child_environment(cwd, {})
            with open(os.path.join(cwd, ".worktrail", ".gitignore")) as fh:
                self.assertEqual(fh.read().strip(), "*")

    def test_auth_symlinked_from_parent_data_dir(self):
        with tempfile.TemporaryDirectory() as parent_xdg, \
                tempfile.TemporaryDirectory() as cwd:
            parent_data = os.path.join(parent_xdg, "opencode")
            os.makedirs(parent_data)
            auth = os.path.join(parent_data, "auth.json")
            with open(auth, "w") as fh:
                fh.write("{}")
            env, data_dir = spawnlib.prepare_opencode_child_environment(
                cwd, {"XDG_DATA_HOME": parent_xdg}
            )
            link = data_dir / "auth.json"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.path.realpath(link), os.path.realpath(auth))
            # the child's data dir is isolated, not the parent store
            self.assertNotEqual(env["XDG_DATA_HOME"], parent_xdg)
            self.assertTrue(env["XDG_DATA_HOME"].startswith(cwd))

    def test_permission_config_scopes_cwd_and_git_common_dir_only(self):
        with tempfile.TemporaryDirectory() as cwd:
            subprocess.run(["git", "init", "-q", cwd], check=True,
                           capture_output=True)
            env, _ = spawnlib.prepare_opencode_child_environment(cwd, {})
            config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            perm = config["permission"]
            ext = perm["external_directory"]
            resolved = str(spawnlib.Path(cwd).resolve())
            self.assertEqual(ext.get(resolved + "/**"), "allow")
            common = spawnlib._git_common_dir(cwd)
            self.assertTrue(common and common.endswith(".git"))
            self.assertEqual(ext.get(common + "/**"), "allow")
            # scoped containment: no catch-all and no home-wide grant
            self.assertNotIn("*", ext)
            self.assertNotIn("**", ext)
            self.assertNotIn(str(spawnlib.Path.home()) + "/**", ext)
            # headless workers never hit an interactive "ask" on their tools
            for tool in ("read", "edit", "glob", "grep", "bash"):
                self.assertEqual(perm[tool], "allow")

    def test_existing_config_content_is_merged_not_clobbered(self):
        base = {
            "OPENCODE_CONFIG_CONTENT": json.dumps({
                "model": "x/y",
                "permission": {
                    "webfetch": "deny",
                    "edit": "ask",
                    "bash": {"rm -rf *": "deny"},
                },
            })
        }
        with tempfile.TemporaryDirectory() as cwd:
            env, _ = spawnlib.prepare_opencode_child_environment(cwd, base)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        perm = config["permission"]
        self.assertEqual(config["model"], "x/y")       # non-permission key kept
        self.assertEqual(perm["webfetch"], "deny")     # explicit deny never widened
        self.assertEqual(perm["edit"], "allow")        # headless "ask" upgraded
        self.assertEqual(perm["bash"], {"*": "allow", "rm -rf *": "deny"})

    # -- spawn_agent wiring --------------------------------------------------- #

    def test_spawn_env_isolation_reaches_subprocess(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["env"] = kwargs["env"]
            return Proc(0, json.dumps({
                "type": "text", "sessionID": "ses_iso",
                "part": {"type": "text", "text": "done"},
            }) + "\n", "")

        spawnlib.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("p", cwd, agent="opencode")
            self.assertTrue(seen["env"]["XDG_DATA_HOME"].startswith(cwd))
        self.assertEqual(seen["env"]["CC_HEADLESS"], "1")
        self.assertIn("OPENCODE_CONFIG_CONTENT", seen["env"])
        self.assertEqual(out.text, "done")

    def test_permission_denials_parsed_and_diagnostic_logged(self):
        # Event shapes captured from a live v1.17.13 reproduction: a headless
        # "ask" is auto-rejected and surfaces as a tool_use error state.
        events = [
            {"type": "step_start", "sessionID": "ses_deny",
             "part": {"type": "step-start"}},
            {"type": "tool_use", "sessionID": "ses_deny", "part": {
                "type": "tool", "tool": "bash", "callID": "call_1",
                "state": {"status": "error",
                          "input": {"command": "echo hi"},
                          "error": "The user rejected permission to use "
                                   "this specific tool call."}}},
            {"type": "step_finish", "sessionID": "ses_deny", "part": {
                "type": "step-finish",
                "tokens": {"total": 10, "input": 5, "output": 5, "reasoning": 0,
                           "cache": {"write": 0, "read": 0}},
                "cost": 0}},
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"
        logs = []
        spawnlib.subprocess.run = lambda cmd, **kw: Proc(0, raw, "")
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("p", cwd, agent="opencode", log=logs.append)
            data_dir = str(spawnlib.opencode_data_dir(cwd))
        self.assertEqual(out.usage["permission_denials"], [{
            "tool": "bash",
            "error": "The user rejected permission to use this specific tool call.",
        }])
        self.assertEqual(out.usage["opencode_data_dir"], data_dir)
        joined = "\n".join(logs)
        self.assertIn("ses_deny", joined)
        self.assertIn(data_dir, joined)
        self.assertIn("permission_denials=1", joined)

    def test_denials_reach_usage_even_without_step_finish(self):
        line = json.dumps({"type": "tool_use", "sessionID": "ses_d2", "part": {
            "type": "tool", "tool": "edit",
            "state": {"status": "error",
                      "error": "The user rejected permission to use "
                               "this specific tool call."}}})
        _, usage, _, _, sid = spawnlib._parse_stream_json(line + "\n")
        self.assertEqual(sid, "ses_d2")
        self.assertEqual(len(usage["permission_denials"]), 1)
        self.assertEqual(usage["permission_denials"][0]["tool"], "edit")

    def test_happy_path_report_back_has_no_denials(self):
        report = '```json\n{"task":"T1","step":"implement","status":"success"}\n```'
        events = [
            {"type": "tool_use", "sessionID": "ses_ok", "part": {
                "type": "tool", "tool": "bash",
                "state": {"status": "completed",
                          "input": {"command": "echo hi"}}}},
            {"type": "step_finish", "sessionID": "ses_ok", "part": {
                "type": "step-finish",
                "tokens": {"total": 10, "input": 5, "output": 5, "reasoning": 0,
                           "cache": {"write": 0, "read": 0}},
                "cost": 0.002}},
            {"type": "text", "sessionID": "ses_ok",
             "part": {"type": "text", "text": report}},
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"
        spawnlib.subprocess.run = lambda cmd, **kw: Proc(0, raw, "")
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("p", cwd, agent="opencode")
        self.assertEqual(out.text, report)
        self.assertEqual(out.usage["permission_denials"], [])
        self.assertEqual(out.session_id, "ses_ok")

    def test_fallback_hop_to_opencode_rebuilds_child_env(self):
        # claude primary hits a session limit; the opencode hop must get the
        # isolated opencode environment, not the env prepared for claude.
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs["env"]))
            if len(calls) == 1:
                return Proc(0, "You've hit your session limit. "
                               "Your limit resets at 11:59pm.", "")
            return Proc(0, json.dumps({
                "type": "text", "sessionID": "ses_hop",
                "part": {"type": "text", "text": "hopped"},
            }) + "\n", "")

        spawnlib.agent_capacity.save({"version": 1, "providers": {}})
        spawnlib.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent(
                "p", cwd, agent="claude", fallback_agent="opencode",
                sleep=lambda *_: None,
            )
            claude_env, opencode_env = calls[0][1], calls[1][1]
            self.assertFalse(claude_env.get("XDG_DATA_HOME", "").startswith(cwd))
            self.assertTrue(opencode_env["XDG_DATA_HOME"].startswith(cwd))
            self.assertIn("OPENCODE_CONFIG_CONTENT", opencode_env)
        self.assertEqual(out.text, "hopped")


class SessionLimitFallback(unittest.TestCase):
    def setUp(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})

    def test_switches_to_configured_fallback_without_sleeping(self):
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(0, "You've hit your session limit. Your limit resets at 11:59pm.", "")
            return Proc(0, json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n", "")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as cwd:
                out = spawnlib.spawn_agent(
                    "prompt", cwd, agent="claude", fallback_agent="opencode",
                    extra_args=["--append-system-prompt", "claude-only"],
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][:2], ["opencode", "run"])
        self.assertNotIn("--append-system-prompt", calls[1])
        self.assertEqual(sleeps, [])


class SpawnClaudePFallback(unittest.TestCase):
    """brief 20260723-111700-claude-primary-fallback-inert: spawn_claude_p's
    fallback_agent param must actually switch, the same as spawn_agent's own
    session-limit hop -- before this, spawn_claude_p had NO fallback_agent
    parameter at all, so a claude-primary run's --fallback-agent was silently
    ignored regardless of what LiveSpawn passed."""

    def setUp(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})

    def test_switches_to_configured_fallback_without_sleeping(self):
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(0, "You've hit your session limit. Your limit resets at 11:59pm.", "")
            return Proc(0, json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n", "")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as cwd:
                out = spawnlib.spawn_claude_p(
                    "prompt", cwd, fallback_agent="opencode",
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][:2], ["opencode", "run"])
        self.assertEqual(sleeps, [])

    def test_no_fallback_configured_preserves_legacy_sleep_behavior(self):
        """Without fallback_agent, spawn_claude_p keeps its pre-existing
        sleep-until-reset behavior unchanged."""
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(0, "You've hit your session limit. Your limit resets at 11:59pm.", "")
            return Proc(0, json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n", "")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            out = spawnlib.spawn_claude_p(
                "prompt", "/tmp",
                sleep=lambda seconds: sleeps.append(seconds),
            )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][0], "claude")
        self.assertEqual(len(sleeps), 1)


class FallbackChain(unittest.TestCase):
    """TASK-005: fallback_agent as an ordered chain of pools, not just one hop."""

    HOP_MODELS = {"claude": "sonnet", "hopA": "model-a", "hopB": "model-b", "hopC": "model-c"}

    def setUp(self):
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(self._cache.name, "capacity.json")
        self._orig_run = spawnlib.subprocess.run
        self._orig_supported = spawnlib.SUPPORTED_AGENTS
        self._orig_model_fn = spawnlib.default_model_for_agent
        # Synthetic hop names (SUPPORTED_AGENTS only has 3 real agents) so a
        # multi-entry chain can gate/select distinct providers deterministically.
        spawnlib.SUPPORTED_AGENTS = frozenset(self._orig_supported | set(self.HOP_MODELS))
        spawnlib.default_model_for_agent = lambda a: self.HOP_MODELS.get(a, "sonnet")

    def tearDown(self):
        spawnlib.subprocess.run = self._orig_run
        spawnlib.SUPPORTED_AGENTS = self._orig_supported
        spawnlib.default_model_for_agent = self._orig_model_fn
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _gate(self, agent, model, failure_class="transport", seconds=60):
        spawnlib.agent_capacity.record(
            agent, model, outcome="unavailable", failure_class=failure_class,
            retry_after=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=seconds),
        )

    def test_three_entry_chain_rolls_to_third_entry_in_order(self):
        # AC-017: primary + first two chain hops gated -> rolls to the third.
        self._gate("claude", "sonnet")
        self._gate("hopA", "model-a")
        self._gate("hopB", "model-b")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Proc(0, "ok", "")

        spawnlib.subprocess.run = fake_run
        out = spawnlib.spawn_agent(
            "prompt", "/tmp", agent="claude", fallback_agent=["hopA", "hopB", "hopC"],
        )
        self.assertEqual(out.text, "ok")
        self.assertEqual(len(calls), 1)
        self.assertIn("model-c", calls[0])

    def test_session_limit_advances_through_remaining_chain_in_order(self):
        # A session-limit on the active hop should continue through the ordered
        # remainder of the configured chain instead of stopping at the first
        # fallback.
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) < 3:
                return Proc(0, "hit your session limit resets 3:00pm", "")
            return Proc(
                0,
                json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n",
                "",
            )

        spawnlib.subprocess.run = fake_run
        out = spawnlib.spawn_agent(
            "prompt", "/tmp", agent="claude", fallback_agent=["hopA", "hopB", "hopC"],
            sleep=lambda *_: None,
        )
        self.assertEqual(out.text, "done")
        self.assertIn("model-a", calls[1])
        self.assertIn("model-b", calls[2])

    def test_unauthenticated_hop_gated_on_first_attempt_not_prevalidated(self):
        # AC-023: hopA has no cache entry (not pre-validated) so it is selected
        # after the gated primary; a simulated auth failure at spawn time is
        # recorded through the existing failure-classification path.
        self._gate("claude", "sonnet")

        fr = FakeRun([Proc(1, "", "invalid api key")])
        spawnlib.subprocess.run = fr
        out = spawnlib.spawn_agent(
            "prompt", "/tmp", agent="claude", fallback_agent=["hopA"], retries=0,
        )
        # Existing semantics: an infra failure that exhausts retries is handed
        # back to the caller as-is (no exception), same as today's single fallback.
        self.assertEqual(out.text, "")
        state = spawnlib.agent_capacity.load()["providers"]["hopA:model-a"]
        self.assertEqual(state["status"], "unavailable")
        self.assertEqual(state["failure_class"], "auth")

    def test_chain_exhausted_raises_all_providers_unavailable_with_every_hop(self):
        # AC-024: primary and every chain hop gated -> AllProvidersUnavailable
        # lists every configured provider; no subprocess is spawned.
        self._gate("claude", "sonnet")
        self._gate("hopA", "model-a")
        self._gate("hopB", "model-b")

        fr = FakeRun([Proc(0, "unused", "")])
        spawnlib.subprocess.run = fr
        with self.assertRaises(spawnlib.agent_capacity.AllProvidersUnavailable) as ctx:
            spawnlib.spawn_agent(
                "prompt", "/tmp", agent="claude", fallback_agent=["hopA", "hopB"],
            )
        self.assertEqual(
            set(ctx.exception.providers), {"claude:sonnet", "hopA:model-a", "hopB:model-b"},
        )
        self.assertEqual(fr.calls, 0)

    def test_single_string_fallback_matches_single_entry_list(self):
        # AC-025: the legacy `str` shape and an equivalent 1-entry list select
        # the same provider when the primary is gated.
        for shape in ("hopA", ["hopA"]):
            with self.subTest(shape=shape):
                spawnlib.agent_capacity.save({"version": 1, "providers": {}})
                self._gate("claude", "sonnet")
                fr = FakeRun([Proc(0, "ok", "")])
                spawnlib.subprocess.run = fr
                out = spawnlib.spawn_agent(
                    "prompt", "/tmp", agent="claude", fallback_agent=shape,
                )
                self.assertEqual(out.text, "ok")

    def test_empty_chain_is_no_fallback(self):
        # Edge case: an empty ordered chain behaves like fallback_agent=None --
        # a gated primary re-raises the bare ProviderUnavailable, not the
        # all-providers-unavailable wrapper.
        self._gate("claude", "sonnet")
        fr = FakeRun([Proc(0, "unused", "")])
        spawnlib.subprocess.run = fr
        with self.assertRaises(spawnlib.agent_capacity.ProviderUnavailable) as ctx:
            spawnlib.spawn_agent("prompt", "/tmp", agent="claude", fallback_agent=[])
        self.assertNotIsInstance(ctx.exception, spawnlib.agent_capacity.AllProvidersUnavailable)
        self.assertEqual(fr.calls, 0)

    def test_duplicate_of_primary_in_chain_is_dropped(self):
        # A chain entry equal to the primary is dropped, mirroring the legacy
        # same-as-primary rule: a gated primary rolls straight to "hopA",
        # never re-trying the duplicated "claude" entry.
        self._gate("claude", "sonnet")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Proc(0, "ok", "")

        spawnlib.subprocess.run = fake_run
        out = spawnlib.spawn_agent(
            "prompt", "/tmp", agent="claude", fallback_agent=["claude", "hopA"],
        )
        self.assertEqual(out.text, "ok")
        self.assertEqual(len(calls), 1)
        self.assertIn("model-a", calls[0])

    def test_repeated_chain_entry_is_tried_once(self):
        # A repeated hop in the configured chain is normalized away so the walk
        # stays bounded and never loops back to the same provider.
        self._gate("claude", "sonnet")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Proc(0, "ok", "")

        spawnlib.subprocess.run = fake_run
        out = spawnlib.spawn_agent(
            "prompt", "/tmp", agent="claude", fallback_agent=["hopA", "hopA", "hopB"],
        )
        self.assertEqual(out.text, "ok")
        self.assertEqual(len(calls), 1)
        self.assertIn("model-a", calls[0])
        self.assertNotIn("model-b", calls[0])

    def test_unsupported_chain_entry_raises_value_error(self):
        with self.assertRaises(ValueError):
            spawnlib.spawn_agent(
                "prompt", "/tmp", agent="claude", fallback_agent=["hopA", "bogus"],
            )


class ParseStreamJsonSessionId(unittest.TestCase):
    """Tests for session_id extraction from _parse_stream_json (TASK-007)."""

    def _result_line(self, session_id=None, result="done", input_tokens=1):
        event = {
            "type": "result",
            "result": result,
            "usage": {"input_tokens": input_tokens},
            "total_cost_usd": 0.0,
        }
        if session_id is not None:
            event["session_id"] = session_id
        return json.dumps(event)

    def test_parse_stream_json_extracts_session_id(self):
        raw = self._result_line(session_id="sess-abc123")
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(sid, "sess-abc123")
        self.assertEqual(text, "done")

    def test_parse_stream_json_missing_session_id(self):
        raw = self._result_line()  # no session_id key
        text, usage, tools, skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(sid, "")

    def test_spawn_result_session_id_default(self):
        r = spawnlib.SpawnResult(text="hi", usage={})
        self.assertEqual(r.session_id, "")

    def test_spawn_result_session_id_populated(self):
        r = spawnlib.SpawnResult(text="hi", usage={}, session_id="sess-xyz")
        self.assertEqual(r.session_id, "sess-xyz")


class BuildCmdForkSession(unittest.TestCase):
    """Tests for fork-session path in build_cmd (TASK-007)."""

    def test_build_cmd_fork_session(self):
        cmd = spawnlib.build_cmd("p", resume_session_id="sid-42")
        self.assertIn("--resume", cmd)
        idx = cmd.index("--resume")
        self.assertEqual(cmd[idx + 1], "sid-42")
        self.assertIn("--fork-session", cmd)

    def test_build_cmd_no_fork(self):
        cmd = spawnlib.build_cmd("p")
        self.assertNotIn("--fork-session", cmd)
        self.assertNotIn("--resume", cmd)

    def test_build_cmd_fork_session_none(self):
        cmd = spawnlib.build_cmd("p", resume_session_id=None)
        self.assertNotIn("--fork-session", cmd)

    def test_build_cmd_fork_session_empty_string(self):
        # Empty string is falsy — should not add fork args
        cmd = spawnlib.build_cmd("p", resume_session_id="")
        self.assertNotIn("--fork-session", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)

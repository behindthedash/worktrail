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
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import ClassVar

from worktrail.orchestrator import spawnlib

os.environ.setdefault(
    "GO_AGENT_CAPACITY_CACHE", os.path.join(tempfile.mkdtemp(), "capacity.json")
)

Proc = namedtuple("Proc", "returncode stdout stderr")


def _cell(
    harness="claude",
    model=None,
    effort=None,
    pool="subscription",
    auth=None,
    target=None,
):
    """Build a `Cell` for a `build_cmd`/`build_child_env` test without every
    call site spelling out all six fields."""
    return spawnlib.Cell(
        target=target or harness,
        harness=harness,
        model=model,
        effort=effort,
        pool=pool,
        auth=auth,
    )


def _target(harness, pool="subscription", api_opt_in=False, auth=None):
    return {"harness": harness, "pool": pool, "api_opt_in": api_opt_in, "auth": auth}


def _routing(targets, tiers, default_tier=None):
    """A `resolve_routing()`-shaped dict (`{targets, tiers, roles, purposes,
    default_tier, drain}`) for patching `spawnlib.resolve_routing` in a
    `spawn_agent`/`spawn_claude_p` test -- mirrors `tests/runtime/test_selection.py`'s
    own `_routing`/`_target` helpers."""
    return {
        "targets": targets,
        "tiers": tiers,
        "roles": {},
        "purposes": {},
        "default_tier": default_tier,
        "drain": {},
    }


# Single-target row: every session-limit hit on it has nowhere else to hop,
# so it exercises the sleep-and-retry-the-same-cell path.
SINGLE_CLAUDE_ROUTING = _routing(
    {"claude-sub": _target("claude")},
    {"t2-build": {"claude-sub": {"model": "sonnet", "effort": None}}},
    default_tier="t2-build",
)

# Two-target row (claude first, opencode second): a session-limit hit on the
# first cell has somewhere to hop.
CLAUDE_THEN_OPENCODE_ROUTING = _routing(
    {
        "claude-sub": _target("claude"),
        "opencode-free": _target("opencode", pool="free"),
    },
    {
        "t2-build": {
            "claude-sub": {"model": "sonnet", "effort": None},
            "opencode-free": {
                "model": "opencode/deepseek-v4-flash-free",
                "effort": None,
            },
        }
    },
    default_tier="t2-build",
)

# A reset instant comfortably in the future, in the wall-clock wording
# `parse_explicit_reset` matches. Computed rather than hard-coded so the tests
# that rely on the gate actually gating do not silently invert once a fixed date
# slips into the past.
_FUTURE_RESET_TEXT = (
    datetime.datetime.now().astimezone() + datetime.timedelta(days=365)
).strftime("%b %d, %Y %I:%M %p")

SINGLE_CODEX_ROUTING = _routing(
    {"codex-sub": _target("codex")},
    {"t2-build": {"codex-sub": {"model": "gpt-5.3-codex", "effort": None}}},
    default_tier="t2-build",
)

SINGLE_OPENCODE_ROUTING = _routing(
    {"opencode-free": _target("opencode", pool="free")},
    {
        "t2-build": {
            "opencode-free": {
                "model": "opencode/deepseek-v4-flash-free",
                "effort": None,
            }
        }
    },
    default_tier="t2-build",
)


def _patch_routing(routing):
    """Context manager/decorator making `spawnlib.resolve_routing(...)` return
    *routing* regardless of the (real) `load_policy(worktrail_home())` it's
    called with, so `spawn_agent`/`spawn_claude_p` resolve a deterministic
    `Cell` without a real routing.yaml on disk."""
    return patch.object(spawnlib, "resolve_routing", return_value=routing)


# Claude-only argv tokens (spec spawnlib-cross-hop-argv-invariant): every
# element here is legal ONLY on a `claude -p` command line; a codex or opencode
# argv carrying any of them as an exact element means the primary's extra_args
# leaked across a fallback hop (persisted capacity gate or session-limit
# switch). Provenance of each token:
#   --strict-mcp-config / --tools / Read / Edit / Write / Bash / Grep / Glob /
#     --setting-sources / project,local
#       -> live.py `_LEAN_WORKER_FLAGS`, the lean-worker extra_args live.py
#          derives ONLY when the target agent is claude
#   --append-system-prompt
#       -> live.py's review-role system-prompt append, also claude-only there
#   --permission-mode / bypassPermissions
#       -> spawnlib PERM_FLAGS, emitted by build_cmd() only for agent="claude"
#   --output-format / stream-json / --verbose
#       -> spawnlib JSON_OUTPUT_FLAGS, same claude-only build_cmd() branch
#   --setting-sources / project,local (repeated from _LEAN_WORKER_FLAGS above)
#       -> also the structural default _with_default_setting_sources() puts on
#          every claude spawn regardless of caller extra_args
#   --effort
#       -> build_cmd()'s claude branch; codex translates effort to
#          `-c model_reasoning_effort=...` and opencode to `--variant`
#   --resume / --fork-session
#       -> build_cmd()'s claude branch; opencode uses `--session`/`--fork`
#          instead and the codex branch ignores resume_session_id entirely
CLAUDE_ONLY_ARGV_TOKENS = (
    "--strict-mcp-config",
    "--tools",
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Grep",
    "Glob",
    "--append-system-prompt",
    "--permission-mode",
    "bypassPermissions",
    "--output-format",
    "stream-json",
    "--verbose",
    "--setting-sources",
    "project,local",
    "--effort",
    "--resume",
    "--fork-session",
)


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
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._routing_patch = _patch_routing(SINGLE_CLAUDE_ROUTING)
        self._routing_patch.start()

    def tearDown(self):
        self._routing_patch.stop()
        spawnlib.subprocess.run = self._orig
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _run(self, outcomes, **kw):
        fr = FakeRun(outcomes)
        spawnlib.subprocess.run = fr
        out = spawnlib.spawn_claude_p(
            "prompt", "/tmp", tier="t2-build", retries=2, sleep=lambda *_: None, **kw
        )
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
        spawnlib.subprocess.run = FakeRun(
            [subprocess.TimeoutExpired(cmd="claude", timeout=1)]
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            spawnlib.spawn_claude_p(
                "p", "/tmp", tier="t2-build", retries=2, sleep=lambda *_: None
            )


class KeepTranscripts(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._old_keep = os.environ.get("WORKTRAIL_KEEP_TRANSCRIPTS")
        self._routing_patch = _patch_routing(SINGLE_CLAUDE_ROUTING)
        self._routing_patch.start()

    def tearDown(self):
        self._routing_patch.stop()
        spawnlib.subprocess.run = self._orig
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        if self._old_keep is None:
            os.environ.pop("WORKTRAIL_KEEP_TRANSCRIPTS", None)
        else:
            os.environ["WORKTRAIL_KEEP_TRANSCRIPTS"] = self._old_keep
        self._cache.cleanup()

    def test_unset_writes_nothing(self):
        os.environ.pop("WORKTRAIL_KEEP_TRANSCRIPTS", None)
        with tempfile.TemporaryDirectory() as would_be_dir:
            spawnlib.subprocess.run = FakeRun([Proc(0, "ok report", "")])
            spawnlib.spawn_claude_p(
                "prompt", "/tmp", tier="t2-build", retries=0, sleep=lambda *_: None
            )
            # Nothing is written anywhere when the flag is unset -- there is no
            # target dir to inspect, so this only asserts the spawn itself succeeds.
            self.assertTrue(os.path.isdir(would_be_dir))
            self.assertFalse(os.listdir(would_be_dir))

    def test_set_persists_raw_transcript(self):
        with tempfile.TemporaryDirectory() as tdir:
            os.environ["WORKTRAIL_KEEP_TRANSCRIPTS"] = tdir
            raw = '{"type": "result", "result": "ok report"}'
            spawnlib.subprocess.run = FakeRun([Proc(0, raw, "")])
            spawnlib.spawn_claude_p(
                "prompt",
                "/some/task-worktree",
                tier="t2-build",
                retries=0,
                sleep=lambda *_: None,
            )
            files = os.listdir(tdir)
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].startswith("task-worktree-claude-"))
            self.assertEqual(Path(tdir, files[0]).read_text(), raw)

    def test_write_failure_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tdir:
            blocked = os.path.join(tdir, "blocked")
            open(
                blocked, "w"
            ).close()  # a file, not a dir -- mkdir(parents=True) raises
            os.environ["WORKTRAIL_KEEP_TRANSCRIPTS"] = blocked
            spawnlib.subprocess.run = FakeRun([Proc(0, "ok report", "")])
            out = spawnlib.spawn_claude_p(
                "prompt", "/tmp", tier="t2-build", retries=0, sleep=lambda *_: None
            )
            self.assertEqual(out.text, "ok report")


class SessionLimitParse(unittest.TestCase):
    def test_returns_none_without_message(self):
        self.assertIsNone(spawnlib.parse_session_limit_reset("normal worker output"))
        self.assertIsNone(spawnlib.parse_session_limit_reset(None))

    def test_parses_reset_same_day(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)  # noqa: DTZ001
        r = spawnlib.parse_session_limit_reset(
            "You've hit your session limit. Your limit resets at 3:00pm.", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 4, 15, 0))  # noqa: DTZ001

    def test_rolls_to_tomorrow_when_reset_already_passed(self):
        now = datetime.datetime(2026, 6, 4, 16, 0)  # noqa: DTZ001
        r = spawnlib.parse_session_limit_reset(
            "hit your session limit, resets 3:00pm", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 5, 15, 0))  # noqa: DTZ001

    def test_handles_am_and_loose_spacing(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)  # noqa: DTZ001
        r = spawnlib.parse_session_limit_reset(
            "hit your session limit ... resets 9:30 am", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 5, 9, 30))  # noqa: DTZ001

    def test_ignores_benign_mention_of_session_limit(self):
        # A plain "session limit" reference (no "hit your ... resets <time>") is not a
        # rate-limit hit and must not trigger a wait.
        self.assertIsNone(
            spawnlib.parse_session_limit_reset(
                "note: the session limit config is 5 hours"
            )
        )

    def test_long_transcript_quoting_notice_is_not_a_limit(self):
        # A real cap notice IS the output (one short message). A worker transcript
        # that merely QUOTES the wording — e.g. a worker reading/editing this very
        # module's docstring, or writing test fixtures — must not classify as a cap.
        # Regression for the 2026-07-23 spec-023 incident: TASK-005 workers matched
        # the docstring example on every spawn and parked runs until "3:00pm".
        transcript = (
            "I updated spawnlib.py. The docstring example reads: "
            '"You\'ve hit your session limit. Your limit resets at 3:00pm."\n'
            + ("x" * (spawnlib._SESSION_LIMIT_NOTICE_MAX_CHARS + 1))
        )
        self.assertIsNone(spawnlib.parse_session_limit_reset(transcript))

    def test_short_genuine_notice_still_parses(self):
        now = datetime.datetime(2026, 6, 4, 10, 0)  # noqa: DTZ001
        r = spawnlib.parse_session_limit_reset(
            "You've hit your session limit. Your limit resets at 3:00pm.", now=now
        )
        self.assertEqual(r, datetime.datetime(2026, 6, 4, 15, 0))  # noqa: DTZ001


class SessionLimitRetry(unittest.TestCase):
    """Single-target row throughout: every session-limit hit has nowhere to
    hop, so these exercise the sleep-and-retry-the-same-cell path."""

    def setUp(self):
        self._orig = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._routing_patch = _patch_routing(SINGLE_CLAUDE_ROUTING)
        self._routing_patch.start()

    def tearDown(self):
        self._routing_patch.stop()
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
            "p",
            "/tmp",
            tier="t2-build",
            retries=2,
            sleep=lambda s: slept.append(s),
            **kw,
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
        _out, fr, slept = self._run([limit, fail, fail, fail], session_limit_waits=3)
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

    def test_park_is_capped_at_reprobe_cadence_not_stated_reset(self):
        # The park used to be `until_reset + 5s` verbatim from the CLI's notice, so
        # a reset clock already past today rolls to tomorrow and parks for ~24h.
        # Every park must now be <= the re-probe cadence, whatever the notice
        # claims. Cadence is pinned tiny so the assertion is exact regardless of
        # wall-clock time (until_reset is always >= the 5s grace).
        limit = Proc(0, "hit your session limit resets 3:00pm", "")
        with patch.object(spawnlib, "SESSION_LIMIT_REPROBE_MAX_S", 1.0):
            out, _fr, slept = self._run([limit], session_limit_waits=3)
        self.assertEqual(slept, [1.0, 1.0, 1.0])
        self.assertIn("session limit", out.text)

    def test_total_park_time_is_bounded_even_with_probes_remaining(self):
        # A persistently-capped account stops costing wall-clock at
        # SESSION_LIMIT_TOTAL_WAIT_MAX_S even when probes remain.
        limit = Proc(0, "hit your session limit resets 3:00pm", "")
        with (
            patch.object(spawnlib, "SESSION_LIMIT_REPROBE_MAX_S", 1.0),
            patch.object(spawnlib, "SESSION_LIMIT_TOTAL_WAIT_MAX_S", 2.5),
        ):
            out, _fr, slept = self._run([limit], session_limit_waits=99)
        self.assertAlmostEqual(sum(slept), 2.5, places=6)
        self.assertEqual(slept, [1.0, 1.0, 0.5])
        self.assertIn("session limit", out.text)

    def test_capacity_gate_is_clamped_to_reprobe_cadence(self):
        # Without this clamp the cap is self-defeating: the spawn wakes early from
        # a capped park, then agent_capacity refuses the provider for every other
        # spawn because the SAME notice gated it until the stated reset.
        limit = Proc(0, "hit your session limit resets 3:00pm", "")
        before = datetime.datetime.now().astimezone()
        with patch.object(spawnlib, "SESSION_LIMIT_REPROBE_MAX_S", 1.0):
            self._run([limit], session_limit_waits=0)
        state = spawnlib.agent_capacity.load()["providers"]
        entry = next(iter(state.values()))
        self.assertEqual(entry["failure_class"], "rate_limit")
        retry_after = datetime.datetime.fromisoformat(entry["retry_after"])
        self.assertLessEqual(
            retry_after, before + datetime.timedelta(seconds=1.0 + 5.0)
        )

    def test_default_probe_count_covers_the_total_park_budget(self):
        # Guards the derivation: the default probe count must never be the reason
        # total patience falls below the declared budget.
        self.assertGreaterEqual(
            spawnlib.SESSION_LIMIT_WAITS_DEFAULT * spawnlib.SESSION_LIMIT_REPROBE_MAX_S,
            spawnlib.SESSION_LIMIT_TOTAL_WAIT_MAX_S,
        )

    def test_worker_transcript_quoting_notice_runs_to_success_without_wait(self):
        # End-to-end regression for the spec-023 false positive: a SUCCESSFUL worker
        # whose (long, plain-text) output quotes the cap wording must be treated as
        # a normal success — no session-limit sleep, no retry, work not discarded.
        transcript = (
            "Edited spawnlib.py; the docstring example is "
            '"You\'ve hit your session limit. Your limit resets at 3:00pm." '
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
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "The docstring says: You've hit your session limit. "
                                "Your limit resets at 3:00pm.",
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "final report: all edits committed",
                    "usage": {},
                }
            ),
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
        c = spawnlib.build_cmd(
            "hi", _cell(model="haiku"), extra_args=["--append-system-prompt", "x"]
        )
        self.assertEqual(c[:3], ["claude", "-p", "hi"])
        self.assertIn("--permission-mode", c)
        self.assertIn("bypassPermissions", c)
        self.assertIn("--model", c)
        self.assertIn("haiku", c)
        self.assertIn("--append-system-prompt", c)

    def test_build_cmd_no_model(self):
        c = spawnlib.build_cmd("hi", _cell())
        self.assertNotIn("--model", c)

    def test_build_cmd_uses_stream_json(self):
        c = spawnlib.build_cmd("hi", _cell())
        self.assertIn("--output-format", c)
        idx = c.index("--output-format")
        self.assertEqual(c[idx + 1], "stream-json")

    def test_build_cmd_opencode(self):
        c = spawnlib.build_cmd(
            "hi", _cell(harness="opencode", model="opencode/claude-sonnet-4-6")
        )
        self.assertEqual(c[:3], ["opencode", "run", "--format"])
        self.assertEqual(c[3], "json")
        self.assertIn("--model", c)
        self.assertIn("opencode/claude-sonnet-4-6", c)
        self.assertEqual(c[-1], "hi")

    def test_build_cmd_codex(self):
        c = spawnlib.build_cmd(
            "hi",
            _cell(harness="codex", model="gpt-5.3-codex"),
            output_last_message="/tmp/out",
        )
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
            spawnlib.build_cmd("hi", _cell(harness="bogus"))

    def test_build_cmd_no_effort_byte_identical_to_pre_change(self):
        # model-tier-routing 3.4: effort=None must not perturb any agent's
        # command line -- byte-identical to the pre-effort build_cmd() output.
        for agent in ("claude", "opencode", "codex"):
            with_effort_none = spawnlib.build_cmd(
                "hi", _cell(harness=agent, effort=None)
            )
            without_effort_kwarg = spawnlib.build_cmd("hi", _cell(harness=agent))
            self.assertEqual(with_effort_none, without_effort_kwarg)
            self.assertNotIn("--effort", with_effort_none)
            self.assertNotIn("--variant", with_effort_none)
            self.assertFalse(
                any("model_reasoning_effort" in part for part in with_effort_none)
            )

    def test_build_cmd_claude_effort_flag(self):
        c = spawnlib.build_cmd("hi", _cell(effort="high"))
        self.assertIn("--effort", c)
        self.assertEqual(c[c.index("--effort") + 1], "high")

    def test_build_cmd_opencode_effort_variant_flag(self):
        c = spawnlib.build_cmd("hi", _cell(harness="opencode", effort="max"))
        self.assertIn("--variant", c)
        self.assertEqual(c[c.index("--variant") + 1], "max")

    def test_build_cmd_codex_effort_reasoning_config(self):
        c = spawnlib.build_cmd("hi", _cell(harness="codex", effort="xhigh"))
        self.assertIn("-c", c)
        self.assertEqual(c[c.index("-c") + 1], "model_reasoning_effort=xhigh")

    def test_build_cmd_claude_subscription_omits_bare(self):
        c = spawnlib.build_cmd("hi", _cell(pool="subscription"))
        self.assertNotIn("--bare", c)

    def test_build_cmd_claude_api_appends_bare(self):
        c = spawnlib.build_cmd("hi", _cell(pool="api"))
        self.assertIn("--bare", c)

    def test_build_cmd_opencode_api_pool_does_not_add_bare(self):
        # --bare is a claude-only auth-lane flag (D6); pool never perturbs
        # opencode/codex argv beyond the model/effort translation they already do.
        c = spawnlib.build_cmd("hi", _cell(harness="opencode", model="m", pool="api"))
        self.assertNotIn("--bare", c)

    def test_build_cmd_codex_api_pool_does_not_add_bare(self):
        c = spawnlib.build_cmd("hi", _cell(harness="codex", model="m", pool="api"))
        self.assertNotIn("--bare", c)


class DefaultSettingSourcesStructural(unittest.TestCase):
    """Structural guard for handoff 20260714-120009 item 3: three PRs
    (#251/#252/#253) each patched a DIFFERENT direct spawnlib call site to add
    --setting-sources project,local (excluding the operator's user-level
    ~/.claude/settings.json and its Stop hook -- investigation 20260711-130900).
    Nothing stopped a fourth call site from reintroducing the gap. build_cmd
    now defaults the flag for every claude spawn regardless of what the caller
    passes, so a brand-new call site is covered for free."""

    def test_claude_gets_default_with_no_extra_args(self):
        c = spawnlib.build_cmd("hi", _cell())  # simulates a hypothetical new call site
        self.assertIn("--setting-sources", c)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "project,local")

    def test_claude_gets_default_with_unrelated_extra_args(self):
        c = spawnlib.build_cmd(
            "hi", _cell(), extra_args=["--append-system-prompt", "x"]
        )
        self.assertIn("--setting-sources", c)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "project,local")

    def test_caller_supplied_setting_sources_is_not_overridden(self):
        # A caller that genuinely wants the operator's user-level settings opts
        # out by passing its own value; the default must never clobber it.
        c = spawnlib.build_cmd("hi", _cell(), extra_args=["--setting-sources", "all"])
        self.assertEqual(c.count("--setting-sources"), 1)
        idx = c.index("--setting-sources")
        self.assertEqual(c[idx + 1], "all")

    def test_codex_and_opencode_never_get_the_claude_only_flag(self):
        for agent, model in (
            ("codex", "gpt-5.3-codex"),
            ("opencode", "opencode/claude-sonnet-4-6"),
        ):
            c = spawnlib.build_cmd("hi", _cell(harness=agent, model=model))
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
            with _patch_routing(SINGLE_CLAUDE_ROUTING):
                spawnlib.spawn_agent(
                    "prompt", "/tmp", tier="t2-build", sleep=lambda *_: None
                )
        finally:
            spawnlib.subprocess.run = orig
        cmd = captured_cmd["cmd"]
        self.assertIn("--setting-sources", cmd)
        idx = cmd.index("--setting-sources")
        self.assertEqual(cmd[idx + 1], "project,local")


class SpawnAgentSelection(unittest.TestCase):
    """spawn_agent resolves its Cell from select_cell(tier, prefer,
    exclude_harness) -- no agent/model/target/fallback_agent params exist
    anymore; the caller only names a tier row and an optional preference."""

    def setUp(self):
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )

    def tearDown(self):
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def test_tier_resolves_the_only_declared_cell(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return Proc(0, "ok", "")

        with (
            _patch_routing(SINGLE_CLAUDE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            result = spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build", retries=0)

        self.assertEqual(result.text, "ok")
        self.assertEqual(captured["cmd"][0:3], ["claude", "-p", "prompt"])
        self.assertEqual(
            captured["cmd"][captured["cmd"].index("--model") + 1], "sonnet"
        )

    def test_prefer_moves_a_declared_target_to_the_front(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return Proc(
                0,
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "s",
                        "part": {"type": "text", "text": "opencode ok"},
                    }
                )
                + "\n",
                "",
            )

        with (
            _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            result = spawnlib.spawn_agent(
                "prompt", "/tmp", tier="t2-build", prefer="opencode-free", retries=0
            )

        self.assertEqual(result.text, "opencode ok")
        self.assertEqual(captured["cmd"][0:2], ["opencode", "run"])

    def test_exclude_harness_is_soft_and_still_wins_if_nothing_else_has_capacity(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})
        spawnlib.agent_capacity.record(
            "opencode-free",
            "opencode/deepseek-v4-flash-free",
            outcome="unavailable",
            failure_class="billing",
            retry_after=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=300),
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return Proc(0, "ok", "")

        with (
            _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            result = spawnlib.spawn_agent(
                "prompt", "/tmp", tier="t2-build", exclude_harness="claude", retries=0
            )

        # opencode-free is the only one with capacity, so the soft exclusion
        # of claude still lets it win as a last resort.
        self.assertEqual(result.text, "ok")
        self.assertEqual(captured["cmd"][0], "claude")

    def test_preflight_gate_on_prefer_drops_primary_only_extra_args(self):
        """Regression: if `prefer`'s cell is already capacity-gated BEFORE the
        first subprocess ever runs, select_cell() resolves straight to the
        next cell in the row -- the caller's extra_args (derived for the
        harness it *asked* for via `prefer`) must not leak onto that
        different harness's argv (was `test_preflight_fallback_drops_primary_agent_extra_args`
        pre-select_cell; --setting-sources is claude-only, opencode rejects it)."""
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})
        spawnlib.agent_capacity.record(
            "claude-sub",
            "sonnet",
            outcome="unavailable",
            failure_class="billing",
            retry_after=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=300),
        )
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return Proc(
                0,
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "s",
                        "part": {"type": "text", "text": "opencode ok"},
                    }
                )
                + "\n",
                "",
            )

        with (
            _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            result = spawnlib.spawn_agent(
                "prompt",
                "/tmp",
                tier="t2-build",
                prefer="claude-sub",
                extra_args=["--setting-sources", "project,local"],
                retries=0,
            )

        self.assertEqual(result.text, "opencode ok")
        self.assertEqual(captured["cmd"][0:2], ["opencode", "run"])
        self.assertNotIn("--setting-sources", captured["cmd"])

    def test_preflight_no_gate_still_forwards_primary_extra_args(self):
        """Positive control for the above: when the preferred cell is NOT
        gated, its extra_args still reach the argv unchanged."""
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return Proc(0, "ok", "")

        with (
            _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            result = spawnlib.spawn_agent(
                "prompt",
                "/tmp",
                tier="t2-build",
                prefer="claude-sub",
                extra_args=["--setting-sources", "project,local"],
                retries=0,
            )

        self.assertEqual(result.text, "ok")
        self.assertEqual(captured["cmd"][0], "claude")
        self.assertIn("--setting-sources", captured["cmd"])

    def test_every_cell_gated_raises_no_execution_target_before_any_subprocess(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})
        spawnlib.agent_capacity.record(
            "claude-sub",
            "sonnet",
            outcome="unavailable",
            failure_class="billing",
            retry_after=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=300),
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Proc(0, "unused", "")

        with (
            _patch_routing(SINGLE_CLAUDE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
            self.assertRaises(spawnlib.NoExecutionTarget),
        ):
            spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")
        self.assertEqual(calls, [])


class ParseStreamJson(unittest.TestCase):
    def _stream(self, events):
        return "\n".join(json.dumps(e) for e in events)

    def test_extracts_result_and_usage(self):
        raw = self._stream(
            [
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
            ]
        )
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
        raw = self._stream(
            [
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
            ]
        )
        text, usage, _, _, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "(partial analysis, no trailing json block)")
        self.assertEqual(usage["subtype"], "error_max_turns")
        self.assertEqual(usage["is_error"], True)
        self.assertEqual(usage["stop_reason"], "max_tokens")
        self.assertEqual(usage["num_turns"], 42)
        self.assertEqual(usage["permission_denials"], ["Task"])

    def test_extracts_tool_names(self):
        raw = self._stream(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "1",
                                "name": "Read",
                                "input": {},
                            },
                            {
                                "type": "tool_use",
                                "id": "2",
                                "name": "Bash",
                                "input": {},
                            },
                            {"type": "text", "text": "thinking"},
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "3",
                                "name": "Edit",
                                "input": {},
                            },
                            {
                                "type": "tool_use",
                                "id": "4",
                                "name": "Read",
                                "input": {},
                            },
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "done",
                    "total_cost_usd": 0.0,
                    "usage": {},
                },
            ]
        )
        _, _, tools, skills, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(tools, ["Bash", "Edit", "Read"])
        self.assertEqual(skills, [])

    def test_extracts_skill_names_from_skill_tool(self):
        raw = self._stream(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "1",
                                "name": "Skill",
                                "input": {
                                    "skill": "developer-kit-specs:parallel-orchestrator"
                                },
                            },
                            {
                                "type": "tool_use",
                                "id": "2",
                                "name": "Skill",
                                "input": {
                                    "skill": "developer-kit-specs:parallel-orchestrator"
                                },
                            },
                        ]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "done",
                    "total_cost_usd": 0.0,
                    "usage": {},
                },
            ]
        )
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
        text, _usage, _tools, _skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, raw)
        self.assertEqual(sid, "")

    def test_empty_stream(self):
        text, usage, _tools, _skills, sid = spawnlib._parse_stream_json("")
        self.assertEqual(text, "")
        self.assertEqual(usage, {})
        self.assertEqual(sid, "")

    def test_spawn_result_has_tools_fields(self):
        r = spawnlib.SpawnResult(text="hi", usage={})
        self.assertEqual(r.tools_used, [])
        self.assertEqual(r.skills_used, [])

    def test_spawn_result_with_tools(self):
        r = spawnlib.SpawnResult(
            text="hi", usage={}, tools_used=["Bash", "Read"], skills_used=["foo:bar"]
        )
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
        raw = self._stream(
            [
                {
                    "type": "step_start",
                    "sessionID": "ses_1",
                    "part": {"type": "step-start"},
                },
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "final opencode text"},
                },
            ]
        )
        text, _usage, _tools, _skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "final opencode text")
        self.assertEqual(sid, "ses_1")

    def test_last_text_event_wins(self):
        raw = self._stream(
            [
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "turn 1"},
                },
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "turn 2 final"},
                },
            ]
        )
        text, *_ = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "turn 2 final")

    def test_tool_use_event_collects_tool_name(self):
        raw = self._stream(
            [
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"type": "tool", "tool": "bash"},
                },
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"type": "tool", "tool": "read"},
                },
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"type": "tool", "tool": "bash"},
                },
            ]
        )
        _, _, tools, skills, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(tools, ["bash", "read"])
        self.assertEqual(skills, [])

    def test_step_finish_sums_tokens_across_steps(self):
        # Real shape from a live reproduction: opencode has no single final
        # aggregate event like claude's "result" -- one "step_finish" per step,
        # each carrying that step's own token slice.
        raw = self._stream(
            [
                {
                    "type": "step_finish",
                    "sessionID": "ses_1",
                    "part": {
                        "type": "step-finish",
                        "tokens": {
                            "total": 10,
                            "input": 6,
                            "output": 2,
                            "reasoning": 0,
                            "cache": {"write": 1, "read": 1},
                        },
                        "cost": 0.0,
                    },
                },
                {
                    "type": "step_finish",
                    "sessionID": "ses_1",
                    "part": {
                        "type": "step-finish",
                        "tokens": {
                            "total": 20,
                            "input": 12,
                            "output": 4,
                            "reasoning": 0,
                            "cache": {"write": 0, "read": 4},
                        },
                        "cost": 0.0005,
                    },
                },
            ]
        )
        _, usage, _, _, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(usage["input_tokens"], 18)
        self.assertEqual(usage["output_tokens"], 6)
        self.assertEqual(usage["cache_creation_input_tokens"], 1)
        self.assertEqual(usage["cache_read_input_tokens"], 5)
        self.assertAlmostEqual(usage["total_cost_usd"], 0.0005)
        self.assertEqual(sid, "ses_1")

    def test_step_start_and_todowrite_tool_do_not_break_parsing(self):
        raw = self._stream(
            [
                {
                    "type": "step_start",
                    "sessionID": "ses_1",
                    "part": {"type": "step-start"},
                },
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"type": "tool", "tool": "todowrite"},
                },
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "done"},
                },
            ]
        )
        text, _usage, tools, _skills, _sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(text, "done")
        self.assertEqual(tools, ["todowrite"])

    def test_no_step_finish_leaves_usage_empty(self):
        # A run with only text/tool_use events (no step_finish reached, e.g. an
        # immediate top-level error) must not fabricate a zeroed usage dict.
        raw = self._stream(
            [
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {"type": "tool", "tool": "read"},
                },
            ]
        )
        _, usage, _, _, _ = spawnlib._parse_stream_json(raw)
        self.assertEqual(usage, {})


class OpenCodeErrorEvent(unittest.TestCase):
    """opencode's top-level `{"type":"error",...}` event -- verified against a live
    reproduction (handoff 20260722-152514, /tmp/opencode-error-repro.jsonl): exit 0,
    non-empty stdout, but the provider itself failed. Before this fix
    `is_infra_failure` only checked returncode/empty-output, so this shape was
    misclassified as a clean success."""

    def test_opencode_error_event_helper_extracts_error(self):
        line = json.dumps(
            {
                "type": "error",
                "sessionID": "ses_err",
                "error": {
                    "name": "UnknownError",
                    "data": {"message": "boom", "ref": "err_1"},
                },
            }
        )
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
        line = json.dumps(
            {
                "type": "error",
                "error": {
                    "name": "UnknownError",
                    "data": {"message": "Unexpected server error."},
                },
            }
        )
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
        with (
            _patch_routing(SINGLE_CODEX_ROUTING),
            patch.object(
                spawnlib,
                "prepare_codex_child_environment",
                return_value=(os.environ.copy(), "/tmp/worktrail-codex-child", False),
            ),
        ):
            out = spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")
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
        with (
            _patch_routing(SINGLE_CODEX_ROUTING),
            patch.object(
                spawnlib,
                "prepare_codex_child_environment",
                return_value=(child_env.copy(), child_env["CODEX_HOME"], False),
            ) as prepare,
        ):
            spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")

        # Subscription lane: default home selection, ChatGPT auth inherited --
        # same contract as before the codex-api-auth-lane change, now passed
        # explicitly so the auth lane always matches the served cell's pool.
        prepare.assert_called_once_with(None, inherit_auth=True)
        self.assertEqual(seen["env"]["CODEX_HOME"], child_env["CODEX_HOME"])
        self.assertEqual(seen["env"]["CC_HEADLESS"], "1")

    def _api_routing(self, codex_home):
        return _routing(
            {
                "codex-api": _target(
                    "codex",
                    pool="api",
                    api_opt_in=True,
                    auth={"codex_home": codex_home} if codex_home else None,
                )
            },
            {"t2-build": {"codex-api": {"model": "gpt-5.3-codex", "effort": None}}},
            default_tier="t2-build",
        )

    def test_codex_api_cell_uses_declared_home_without_auth_inheritance(self):
        """codex-api-auth-lane: a `pool: api` cell spawns in its declared,
        provisioned CODEX_HOME with inherit_auth=False -- the one live-verified
        per-spawn auth selector (routing-target-selector task 3.6)."""
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "auth.json").write_text("{}")
            child_env = {"CODEX_HOME": home}

            def fake_run(cmd, **kwargs):
                out_path = cmd[cmd.index("--output-last-message") + 1]
                with open(out_path, "w") as f:
                    f.write("ok")
                return Proc(0, '{"type":"event"}\n', "")

            spawnlib.subprocess.run = fake_run
            with (
                _patch_routing(self._api_routing(home)),
                patch.object(
                    spawnlib,
                    "prepare_codex_child_environment",
                    return_value=(child_env.copy(), home, False),
                ) as prepare,
            ):
                spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")
            prepare.assert_called_once_with(home, inherit_auth=False)

    def test_codex_api_cell_without_codex_home_fails_loud_before_launch(self):
        launched = []
        spawnlib.subprocess.run = lambda *a, **k: launched.append(a) or Proc(0, "", "")
        with (
            _patch_routing(self._api_routing(None)),
            self.assertRaises(spawnlib.OperatorConfigError) as ctx,
        ):
            spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")
        self.assertIn("auth.codex_home", str(ctx.exception))
        self.assertIn("codex-api", str(ctx.exception))
        self.assertEqual(launched, [])

    def test_codex_api_cell_with_unprovisioned_home_fails_loud_before_launch(self):
        launched = []
        spawnlib.subprocess.run = lambda *a, **k: launched.append(a) or Proc(0, "", "")
        with (
            tempfile.TemporaryDirectory() as home,
            _patch_routing(self._api_routing(home)),
            self.assertRaises(spawnlib.OperatorConfigError) as ctx,
        ):
            spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build")
        self.assertIn("auth.json", str(ctx.exception))
        self.assertIn("--with-api-key", str(ctx.exception))
        self.assertEqual(launched, [])


class OpenCodeSpawn(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._routing_patch = _patch_routing(SINGLE_OPENCODE_ROUTING)
        self._routing_patch.start()

    def tearDown(self):
        self._routing_patch.stop()
        spawnlib.subprocess.run = self._orig
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

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
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("prompt", cwd, tier="t2-build")
        self.assertEqual(out.text, "opencode final report")
        self.assertEqual(out.session_id, "ses_abc123")
        self.assertEqual(seen["cmd"][:2], ["opencode", "run"])
        self.assertIn("--format", seen["cmd"])
        self.assertIn("json", seen["cmd"])
        self.assertIn("opencode/deepseek-v4-flash-free", seen["cmd"])

    def test_opencode_step_finish_usage_reaches_spawn_result(self):
        events = [
            {
                "type": "step_start",
                "sessionID": "ses_1",
                "part": {"type": "step-start"},
            },
            {
                "type": "tool_use",
                "sessionID": "ses_1",
                "part": {"type": "tool", "tool": "read"},
            },
            {
                "type": "step_finish",
                "sessionID": "ses_1",
                "part": {
                    "type": "step-finish",
                    "tokens": {
                        "total": 100,
                        "input": 60,
                        "output": 20,
                        "reasoning": 0,
                        "cache": {"write": 5, "read": 15},
                    },
                    "cost": 0.001,
                },
            },
            {
                "type": "text",
                "sessionID": "ses_1",
                "part": {"type": "text", "text": "opencode report"},
            },
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"

        def fake_run(cmd, **kwargs):
            return Proc(0, raw, "")

        spawnlib.subprocess.run = fake_run
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent("prompt", cwd, tier="t2-build")
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
        error_line = json.dumps(
            {
                "type": "error",
                "sessionID": "ses_err",
                "error": {
                    "name": "UnknownError",
                    "data": {
                        "message": "Unexpected server error. Check server logs for details.",
                        "ref": "err_55ac1dc1",
                    },
                },
            }
        )
        success_line = json.dumps(
            {
                "type": "text",
                "sessionID": "ses_err",
                "part": {"type": "text", "text": "opencode retried report"},
            }
        )
        fr = FakeRun([Proc(0, error_line, ""), Proc(0, success_line, "")])
        spawnlib.subprocess.run = fr
        with tempfile.TemporaryDirectory() as cwd:
            out = spawnlib.spawn_agent(
                "prompt", cwd, tier="t2-build", retries=2, sleep=lambda *_: None
            )
        self.assertEqual(out.text, "opencode retried report")
        self.assertEqual(fr.calls, 2)

    def test_unknown_error_exhausted_records_model_unavailable_when_id_absent(self):
        # Requirement: a retired model gates its own cell with a distinct
        # failure class. `list_opencode_models()` no longer serves this cell's
        # model id, so the exhausted UnknownError is classified
        # model_unavailable (a long cooldown), not the generic "transport".
        error_line = json.dumps(
            {
                "type": "error",
                "sessionID": "ses_retired",
                "error": {
                    "name": "UnknownError",
                    "data": {
                        "message": "Unexpected server error.",
                        "ref": "err_retired",
                    },
                },
            }
        )
        fr = FakeRun([Proc(0, error_line, "")])
        spawnlib.subprocess.run = fr
        with (
            patch.object(
                spawnlib.routing_cli, "list_opencode_models", return_value=set()
            ) as list_models,
            tempfile.TemporaryDirectory() as cwd,
        ):
            spawnlib.spawn_agent(
                "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
            )
        list_models.assert_called_once_with()
        key = spawnlib.agent_capacity.provider_key(
            "opencode-free", "opencode/deepseek-v4-flash-free"
        )
        state = spawnlib.agent_capacity.load()["providers"][key]
        self.assertEqual(state["failure_class"], "model_unavailable")

    def test_unknown_error_exhausted_records_transport_when_id_present(self):
        # The same top-level UnknownError, but the cell's model id is still
        # listed -- a transient provider-side blip, not a retired model, so
        # the short "transport" cooldown applies instead.
        error_line = json.dumps(
            {
                "type": "error",
                "sessionID": "ses_blip",
                "error": {
                    "name": "UnknownError",
                    "data": {"message": "Unexpected server error.", "ref": "err_blip"},
                },
            }
        )
        fr = FakeRun([Proc(0, error_line, "")])
        spawnlib.subprocess.run = fr
        with (
            patch.object(
                spawnlib.routing_cli,
                "list_opencode_models",
                return_value={"opencode/deepseek-v4-flash-free"},
            ) as list_models,
            tempfile.TemporaryDirectory() as cwd,
        ):
            spawnlib.spawn_agent(
                "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
            )
        list_models.assert_called_once_with()
        key = spawnlib.agent_capacity.provider_key(
            "opencode-free", "opencode/deepseek-v4-flash-free"
        )
        state = spawnlib.agent_capacity.load()["providers"][key]
        self.assertEqual(state["failure_class"], "transport")


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
        with (
            tempfile.TemporaryDirectory() as parent_xdg,
            tempfile.TemporaryDirectory() as cwd,
        ):
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
            subprocess.run(["git", "init", "-q", cwd], check=True, capture_output=True)
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
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                {
                    "model": "x/y",
                    "permission": {
                        "webfetch": "deny",
                        "edit": "ask",
                        "bash": {"rm -rf *": "deny"},
                    },
                }
            )
        }
        with tempfile.TemporaryDirectory() as cwd:
            env, _ = spawnlib.prepare_opencode_child_environment(cwd, base)
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        perm = config["permission"]
        self.assertEqual(config["model"], "x/y")  # non-permission key kept
        self.assertEqual(perm["webfetch"], "deny")  # explicit deny never widened
        self.assertEqual(perm["edit"], "allow")  # headless "ask" upgraded
        self.assertEqual(perm["bash"], {"*": "allow", "rm -rf *": "deny"})

    # -- spawn_agent wiring --------------------------------------------------- #

    def test_spawn_env_isolation_reaches_subprocess(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["env"] = kwargs["env"]
            return Proc(
                0,
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "ses_iso",
                        "part": {"type": "text", "text": "done"},
                    }
                )
                + "\n",
                "",
            )

        spawnlib.subprocess.run = fake_run
        with (
            tempfile.TemporaryDirectory() as cwd,
            _patch_routing(SINGLE_OPENCODE_ROUTING),
        ):
            out = spawnlib.spawn_agent("p", cwd, tier="t2-build")
            self.assertTrue(seen["env"]["XDG_DATA_HOME"].startswith(cwd))
        self.assertEqual(seen["env"]["CC_HEADLESS"], "1")
        self.assertIn("OPENCODE_CONFIG_CONTENT", seen["env"])
        self.assertEqual(out.text, "done")

    def test_permission_denials_parsed_and_diagnostic_logged(self):
        # Event shapes captured from a live v1.17.13 reproduction: a headless
        # "ask" is auto-rejected and surfaces as a tool_use error state.
        events = [
            {
                "type": "step_start",
                "sessionID": "ses_deny",
                "part": {"type": "step-start"},
            },
            {
                "type": "tool_use",
                "sessionID": "ses_deny",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call_1",
                    "state": {
                        "status": "error",
                        "input": {"command": "echo hi"},
                        "error": "The user rejected permission to use "
                        "this specific tool call.",
                    },
                },
            },
            {
                "type": "step_finish",
                "sessionID": "ses_deny",
                "part": {
                    "type": "step-finish",
                    "tokens": {
                        "total": 10,
                        "input": 5,
                        "output": 5,
                        "reasoning": 0,
                        "cache": {"write": 0, "read": 0},
                    },
                    "cost": 0,
                },
            },
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"
        logs = []
        spawnlib.subprocess.run = lambda cmd, **kw: Proc(0, raw, "")
        with (
            tempfile.TemporaryDirectory() as cwd,
            _patch_routing(SINGLE_OPENCODE_ROUTING),
        ):
            out = spawnlib.spawn_agent("p", cwd, tier="t2-build", log=logs.append)
            data_dir = str(spawnlib.opencode_data_dir(cwd))
        self.assertEqual(
            out.usage["permission_denials"],
            [
                {
                    "tool": "bash",
                    "error": "The user rejected permission to use this specific tool call.",
                }
            ],
        )
        self.assertEqual(out.usage["opencode_data_dir"], data_dir)
        joined = "\n".join(logs)
        self.assertIn("ses_deny", joined)
        self.assertIn(data_dir, joined)
        self.assertIn("permission_denials=1", joined)

    def test_denials_reach_usage_even_without_step_finish(self):
        line = json.dumps(
            {
                "type": "tool_use",
                "sessionID": "ses_d2",
                "part": {
                    "type": "tool",
                    "tool": "edit",
                    "state": {
                        "status": "error",
                        "error": "The user rejected permission to use "
                        "this specific tool call.",
                    },
                },
            }
        )
        _, usage, _, _, sid = spawnlib._parse_stream_json(line + "\n")
        self.assertEqual(sid, "ses_d2")
        self.assertEqual(len(usage["permission_denials"]), 1)
        self.assertEqual(usage["permission_denials"][0]["tool"], "edit")

    def test_happy_path_report_back_has_no_denials(self):
        report = '```json\n{"task":"T1","step":"implement","status":"success"}\n```'
        events = [
            {
                "type": "tool_use",
                "sessionID": "ses_ok",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "echo hi"}},
                },
            },
            {
                "type": "step_finish",
                "sessionID": "ses_ok",
                "part": {
                    "type": "step-finish",
                    "tokens": {
                        "total": 10,
                        "input": 5,
                        "output": 5,
                        "reasoning": 0,
                        "cache": {"write": 0, "read": 0},
                    },
                    "cost": 0.002,
                },
            },
            {
                "type": "text",
                "sessionID": "ses_ok",
                "part": {"type": "text", "text": report},
            },
        ]
        raw = "\n".join(json.dumps(e) for e in events) + "\n"
        spawnlib.subprocess.run = lambda cmd, **kw: Proc(0, raw, "")
        with (
            tempfile.TemporaryDirectory() as cwd,
            _patch_routing(SINGLE_OPENCODE_ROUTING),
        ):
            out = spawnlib.spawn_agent("p", cwd, tier="t2-build")
        self.assertEqual(out.text, report)
        self.assertEqual(out.usage["permission_denials"], [])
        self.assertEqual(out.session_id, "ses_ok")

    def test_session_limit_hop_to_opencode_rebuilds_child_env(self):
        # claude primary hits a session limit; the opencode hop it re-selects
        # into must get the isolated opencode environment, not the env
        # prepared for claude.
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs["env"]))
            if len(calls) == 1:
                return Proc(
                    0,
                    "You've hit your session limit. Your limit resets at 11:59pm.",
                    "",
                )
            return Proc(
                0,
                json.dumps(
                    {
                        "type": "text",
                        "sessionID": "ses_hop",
                        "part": {"type": "text", "text": "hopped"},
                    }
                )
                + "\n",
                "",
            )

        spawnlib.agent_capacity.save({"version": 1, "providers": {}})
        spawnlib.subprocess.run = fake_run
        with (
            tempfile.TemporaryDirectory() as cwd,
            _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
        ):
            out = spawnlib.spawn_agent(
                "p",
                cwd,
                tier="t2-build",
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

    def test_hops_to_the_next_cell_in_the_row_without_sleeping(self):
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(
                    0,
                    "You've hit your session limit. Your limit resets at 11:59pm.",
                    "",
                )
            return Proc(
                0,
                json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n",
                "",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            ):
                out = spawnlib.spawn_agent(
                    "prompt",
                    cwd,
                    tier="t2-build",
                    extra_args=["--append-system-prompt", "claude-only"],
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][:2], ["opencode", "run"])
        # extra_args are specific to the first cell this call resolved and do
        # not carry across the session-limit hop.
        self.assertNotIn("--append-system-prompt", calls[1])
        self.assertEqual(sleeps, [])


class InfraFailureFallback(unittest.TestCase):
    """routing-target-selector 3.4: once the primary cell exhausts `retries`
    on an infra failure (not just a session-limit hit), spawn_agent
    re-selects from the SAME row -- the capacity gate `record()` just wrote
    for the failed cell excludes it -- and continues the SAME attempt loop
    against whatever cell is served next, instead of giving up immediately."""

    def setUp(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})

    def test_hops_to_the_next_cell_after_retries_exhausted(self):
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "claude":
                return Proc(1, "", "boom")
            return Proc(
                0,
                json.dumps(
                    {"type": "result", "result": "opencode saved it", "usage": {}}
                )
                + "\n",
                "",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            ):
                out = spawnlib.spawn_agent(
                    "prompt",
                    cwd,
                    tier="t2-build",
                    retries=2,
                    extra_args=["--append-system-prompt", "claude-only"],
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "opencode saved it")
        # first attempt + 2 retries on claude (all infra failures), then one
        # successful call on the opencode cell the row hops to.
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][0], "claude")
        self.assertEqual(calls[2][0], "claude")
        self.assertEqual(calls[3][:2], ["opencode", "run"])
        # extra_args are specific to the FIRST cell this call resolved and do
        # not carry across the infra-failure hop.
        self.assertNotIn("--append-system-prompt", calls[3])
        # 2 backoff sleeps during the exhausted claude attempts; none after
        # the hop, since the opencode cell succeeds on its first try.
        self.assertEqual(len(sleeps), 2)

    def test_auth_failure_gates_the_cell_without_burning_retries(self):
        """brief 20260901-175101: an auth-class failure (consumed refresh token,
        401) is deterministic until the operator re-authenticates, so it must
        gate the served cell on the FIRST attempt and hop -- not burn the
        per-spawn retry budget (and its backoff sleeps) on every spawn."""
        calls = []
        sleeps = []
        logs = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "claude":
                return Proc(
                    1,
                    "",
                    "ERROR: Your access token could not be refreshed because "
                    "your refresh token was already used. Please log out and "
                    "sign in again.\n",
                )
            return Proc(
                0,
                json.dumps({"type": "result", "result": "opencode ok", "usage": {}})
                + "\n",
                "",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            ):
                out = spawnlib.spawn_agent(
                    "prompt",
                    cwd,
                    tier="t2-build",
                    retries=2,
                    sleep=lambda seconds: sleeps.append(seconds),
                    log=logs.append,
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "opencode ok")
        # exactly one claude attempt, then the hop -- no retries, no backoff
        self.assertEqual([c[0] for c in calls], ["claude", "opencode"])
        self.assertEqual(sleeps, [])
        gate = spawnlib.agent_capacity.load()["providers"]["claude-sub:sonnet"]
        self.assertEqual(gate["status"], "unavailable")
        self.assertEqual(gate["failure_class"], "auth")
        self.assertTrue(any("auth failure on claude-sub" in line for line in logs))

    def test_every_cell_exhausted_returns_last_raw_output(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Proc(1, "", "boom")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            ):
                out = spawnlib.spawn_agent(
                    "prompt",
                    cwd,
                    tier="t2-build",
                    retries=1,
                    sleep=lambda *_: None,
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "")
        # 2 attempts on claude (retries=1 -> attempts=2), hop, 2 more attempts
        # on opencode, then no cell left in the row -- give up with the last
        # raw output.
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][0], "claude")
        self.assertEqual(calls[2][:2], ["opencode", "run"])
        self.assertEqual(calls[3][:2], ["opencode", "run"])

    def test_provider_stated_reset_is_honoured_verbatim(self):
        """A usage-cap notice that names its own reset instant is authoritative:
        the gate carries that timestamp, not our class cooldown, and is marked
        `provider`-derived so the probe cadence leaves it alone until then."""

        def fake_run(cmd, **kwargs):
            return Proc(
                1,
                "",
                "Rate limited. ... try again at " + _FUTURE_RESET_TEXT + ".",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(SINGLE_CODEX_ROUTING),
                patch.object(
                    spawnlib,
                    "prepare_codex_child_environment",
                    return_value=(
                        os.environ.copy(),
                        "/tmp/worktrail-codex-child",
                        False,
                    ),
                ),
            ):
                spawnlib.spawn_agent(
                    "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
                )
        finally:
            spawnlib.subprocess.run = original

        gate = spawnlib.agent_capacity.load()["providers"]["codex-sub:gpt-5.3-codex"]
        self.assertEqual(gate["status"], "unavailable")
        self.assertEqual(gate["reset_source"], "provider")
        expected = spawnlib.agent_capacity.parse_explicit_reset(
            "try again at " + _FUTURE_RESET_TEXT + "."
        )
        self.assertIsNotNone(expected)
        self.assertEqual(
            datetime.datetime.fromisoformat(gate["retry_after"]),
            expected,
        )

    def test_failure_without_stated_reset_stays_cooldown_derived(self):
        """No stated reset means the window is only our own guess, so it keeps
        the per-class cooldown and stays probe-eligible."""

        def fake_run(cmd, **kwargs):
            return Proc(1, "", "boom")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(SINGLE_CODEX_ROUTING),
                patch.object(
                    spawnlib,
                    "prepare_codex_child_environment",
                    return_value=(
                        os.environ.copy(),
                        "/tmp/worktrail-codex-child",
                        False,
                    ),
                ),
            ):
                spawnlib.spawn_agent(
                    "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
                )
        finally:
            spawnlib.subprocess.run = original

        gate = spawnlib.agent_capacity.load()["providers"]["codex-sub:gpt-5.3-codex"]
        self.assertEqual(gate["reset_source"], "cooldown")
        checked = datetime.datetime.fromisoformat(gate["checked_at"])
        expected = spawnlib.agent_capacity.retry_time(
            gate["failure_class"], now=checked
        )
        # `record()` derives retry_after from its own `now`, microseconds off
        # this entry's persisted checked_at -- compare the cooldown WINDOW, not
        # an exact instant.
        self.assertAlmostEqual(
            datetime.datetime.fromisoformat(gate["retry_after"]).timestamp(),
            expected.timestamp(),
            delta=1.0,
        )

    def test_stated_reset_already_in_the_past_is_not_honoured(self):
        """A stated reset that has already elapsed would write a gate expired on
        arrival, so the next `_select()` re-serves this same cell and the spawn
        loops forever. Fall back to the class cooldown, which always gates
        forward."""
        past = (
            datetime.datetime.now().astimezone() - datetime.timedelta(days=30)
        ).strftime("%b %d, %Y %I:%M %p")

        def fake_run(cmd, **kwargs):
            return Proc(1, "", f"Rate limited. ... try again at {past}.")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(SINGLE_CODEX_ROUTING),
                patch.object(
                    spawnlib,
                    "prepare_codex_child_environment",
                    return_value=(
                        os.environ.copy(),
                        "/tmp/worktrail-codex-child",
                        False,
                    ),
                ),
            ):
                spawnlib.spawn_agent(
                    "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
                )
        finally:
            spawnlib.subprocess.run = original

        gate = spawnlib.agent_capacity.load()["providers"]["codex-sub:gpt-5.3-codex"]
        self.assertEqual(gate["reset_source"], "cooldown")
        self.assertGreater(
            datetime.datetime.fromisoformat(gate["retry_after"]),
            datetime.datetime.now(datetime.timezone.utc),
        )

    def test_successful_spawn_on_a_probeable_gate_clears_it(self):
        """The probe branch is only useful if the spawn it lets through can
        actually lift the gate: a success records `available`, so the next
        `check()` passes instead of raising."""
        stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            minutes=30
        )
        spawnlib.agent_capacity.record(
            "codex-sub",
            "gpt-5.3-codex",
            outcome="unavailable",
            failure_class="billing",
            retry_after=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=1),
            now=stale,
        )

        def fake_run(cmd, **kwargs):
            return Proc(0, json.dumps({"type": "item.completed", "text": "ok"}), "")

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(SINGLE_CODEX_ROUTING),
                patch.object(
                    spawnlib,
                    "prepare_codex_child_environment",
                    return_value=(
                        os.environ.copy(),
                        "/tmp/worktrail-codex-child",
                        False,
                    ),
                ),
            ):
                spawnlib.spawn_agent(
                    "prompt", cwd, tier="t2-build", retries=0, sleep=lambda *_: None
                )
        finally:
            spawnlib.subprocess.run = original

        gate = spawnlib.agent_capacity.load()["providers"]["codex-sub:gpt-5.3-codex"]
        self.assertEqual(gate["status"], "available")
        spawnlib.agent_capacity.check("codex-sub", "gpt-5.3-codex")


class SpawnClaudePFallback(unittest.TestCase):
    """brief 20260723-111700-claude-primary-fallback-inert: a claude-primary
    run's session-limit hit must actually hop to another cell in the row when
    one exists -- before spawn_agent grew this, LiveSpawn's claude-primary
    runs had no fallback machinery at all."""

    def setUp(self):
        spawnlib.agent_capacity.save({"version": 1, "providers": {}})

    def test_hops_to_the_next_cell_in_the_row_without_sleeping(self):
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(
                    0,
                    "You've hit your session limit. Your limit resets at 11:59pm.",
                    "",
                )
            return Proc(
                0,
                json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n",
                "",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with (
                tempfile.TemporaryDirectory() as cwd,
                _patch_routing(CLAUDE_THEN_OPENCODE_ROUTING),
            ):
                out = spawnlib.spawn_claude_p(
                    "prompt",
                    cwd,
                    tier="t2-build",
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][:2], ["opencode", "run"])
        self.assertEqual(sleeps, [])

    def test_no_alternate_cell_preserves_legacy_sleep_behavior(self):
        """A single-target row keeps spawn_claude_p's pre-existing
        sleep-until-reset behavior unchanged."""
        calls = []
        sleeps = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return Proc(
                    0,
                    "You've hit your session limit. Your limit resets at 11:59pm.",
                    "",
                )
            return Proc(
                0,
                json.dumps({"type": "result", "result": "done", "usage": {}}) + "\n",
                "",
            )

        original = spawnlib.subprocess.run
        spawnlib.subprocess.run = fake_run
        try:
            with _patch_routing(SINGLE_CLAUDE_ROUTING):
                out = spawnlib.spawn_claude_p(
                    "prompt",
                    "/tmp",
                    tier="t2-build",
                    sleep=lambda seconds: sleeps.append(seconds),
                )
        finally:
            spawnlib.subprocess.run = original
        self.assertEqual(out.text, "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "claude")
        self.assertEqual(calls[1][0], "claude")
        self.assertEqual(len(sleeps), 1)


class CrossHopArgvInvariant(unittest.TestCase):
    """Hermetic harness for the cross-hop argv invariant (spec
    spawnlib-cross-hop-argv-invariant): a spawn's caller-supplied extra_args
    (claude-only lean-worker flags, CLAUDE_ONLY_ARGV_TOKENS) must never leak
    into a codex/opencode cell's command line reached via an IN-FLIGHT
    session-limit hop within the same `spawn_agent()` call -- extra_args are
    specific to the FIRST cell a call resolves and are dropped on every
    re-select. `--effort` is no longer a caller-passable kwarg at all: it is
    baked into each row's cell (only claude-sub's carries one below), so a
    hop can't leak it even in principle."""

    # Deterministic per-target models so argv assertions can identify which
    # cell ran by model, independent of the routing fixture's target names.
    HOP_MODELS: ClassVar = {
        "claude": "pin-claude-model",
        "codex": "pin-codex-model",
        "opencode": "pin-opencode-model",
    }

    CLAUDE_CODEX_ROUTING = _routing(
        {"claude-sub": _target("claude"), "codex-sub": _target("codex")},
        {
            "t2-build": {
                "claude-sub": {"model": HOP_MODELS["claude"], "effort": "high"},
                "codex-sub": {"model": HOP_MODELS["codex"], "effort": None},
            }
        },
        default_tier="t2-build",
    )
    CLAUDE_OPENCODE_ROUTING = _routing(
        {
            "claude-sub": _target("claude"),
            "opencode-free": _target("opencode", pool="free"),
        },
        {
            "t2-build": {
                "claude-sub": {"model": HOP_MODELS["claude"], "effort": "high"},
                "opencode-free": {"model": HOP_MODELS["opencode"], "effort": None},
            }
        },
        default_tier="t2-build",
    )
    THREE_TARGET_ROUTING = _routing(
        {
            "claude-sub": _target("claude"),
            "codex-sub": _target("codex"),
            "opencode-free": _target("opencode", pool="free"),
        },
        {
            "t2-build": {
                "claude-sub": {"model": HOP_MODELS["claude"], "effort": "high"},
                "codex-sub": {"model": HOP_MODELS["codex"], "effort": None},
                "opencode-free": {"model": HOP_MODELS["opencode"], "effort": None},
            }
        },
        default_tier="t2-build",
    )
    SINGLE_CLAUDE = _routing(
        {"claude-sub": _target("claude")},
        {"t2-build": {"claude-sub": {"model": HOP_MODELS["claude"], "effort": "high"}}},
        default_tier="t2-build",
    )

    def setUp(self):
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )
        self._orig_run = spawnlib.subprocess.run

    def tearDown(self):
        spawnlib.subprocess.run = self._orig_run
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def _script_run(self, outcomes):
        """Replace spawnlib.subprocess.run with a scripted fake that records
        every invoked argv and returns `outcomes` in invocation order (the
        last outcome repeats for any further invocation, mirroring FakeRun; a
        BaseException outcome is raised instead of returned). Returns the list
        each invocation appends its command line to."""
        calls = []
        state = {"n": 0}

        def runner(cmd, *args, **kwargs):
            idx = min(state["n"], len(outcomes) - 1)
            state["n"] += 1
            calls.append(list(cmd))
            o = outcomes[idx]
            if isinstance(o, BaseException):
                raise o
            return o

        spawnlib.subprocess.run = runner
        return calls

    def assert_no_claude_only_flags(self, cmds):
        """Fail naming the offending element if any non-claude argv (i.e. a
        codex/opencode hop) carries a CLAUDE_ONLY_ARGV_TOKENS member as an
        exact element. Claude-headed argvs are skipped: they legitimately
        carry these flags."""
        for cmd in cmds:
            if not cmd or cmd[0] == "claude":
                continue
            for token in CLAUDE_ONLY_ARGV_TOKENS:
                if token in cmd:
                    self.fail(
                        f"claude-only flag {token!r} leaked into "
                        f"{cmd[0]} argv at element {cmd.index(token)}: {cmd}"
                    )

    # ---------------------------------------------------------------- #
    # Cross-path scenario sweep: the FULL extra_args-derived payload rides
    # on every scenario below; no codex/opencode argv may carry any of it,
    # no matter how many hops it takes to reach that cell.
    # ---------------------------------------------------------------- #

    # The caller-passable slice of CLAUDE_ONLY_ARGV_TOKENS, shaped exactly as
    # live.py emits it: _LEAN_WORKER_FLAGS plus the review-role
    # --append-system-prompt pair. The remaining claude-only tokens reach an
    # ungated claude argv through build_cmd()'s own structural additions
    # (PERM_FLAGS, JSON_OUTPUT_FLAGS, the _with_default_setting_sources
    # default), the routing fixtures' claude-sub effort="high" above, or the
    # resume_session_id kwarg below -- the positive control pins every
    # token, keeping the negative assertions honest.
    SWEEP_EXTRA_ARGS: ClassVar = [
        "--strict-mcp-config",
        "--tools",
        "Read",
        "Edit",
        "Write",
        "Bash",
        "Grep",
        "Glob",
        "--append-system-prompt",
        "lean worker system prompt",
    ]
    SWEEP_RESUME_SESSION_ID = "sess-sweep-0001"

    # Short (<600 chars) genuine usage-cap notice: exit 0, non-empty stdout,
    # no report-back -- the in-flight trigger for a session-limit hop.
    LIMIT_NOTICE = Proc(
        0, "You've hit your session limit. Your limit resets at 11:59pm.", ""
    )

    def _sweep(self, outcomes, routing, **kw):
        """Script *outcomes* and run one spawn_agent call carrying the full
        extra_args + resume_session_id payload against *routing*. The codex
        child-env prep is patched out (as in CodexSpawn) so scenarios reaching
        a codex cell stay hermetic. Returns (out, captured argvs, slept)."""
        kw.setdefault("extra_args", list(self.SWEEP_EXTRA_ARGS))
        kw.setdefault("resume_session_id", self.SWEEP_RESUME_SESSION_ID)
        kw.setdefault("retries", 0)
        sleeps = []
        kw.setdefault("sleep", lambda seconds: sleeps.append(seconds))
        calls = self._script_run(list(outcomes))
        with (
            tempfile.TemporaryDirectory() as cwd,
            _patch_routing(routing),
            patch.object(
                spawnlib,
                "prepare_codex_child_environment",
                return_value=(os.environ.copy(), "/tmp/worktrail-codex-child", False),
            ),
        ):
            out = spawnlib.spawn_agent("prompt", cwd, tier="t2-build", **kw)
        return out, calls, sleeps

    def test_session_limit_hop_claude_to_codex(self):
        out, calls, sleeps = self._sweep(
            [self.LIMIT_NOTICE, Proc(0, "codex report", "")],
            self.CLAUDE_CODEX_ROUTING,
        )
        self.assertEqual(out.text, "codex report")
        self.assertEqual([c[0] for c in calls], ["claude", "codex"])
        self.assertEqual(sleeps, [])  # the hop replaces sleep-until-reset
        self.assertIn(self.HOP_MODELS["codex"], calls[1])
        self.assert_no_claude_only_flags(calls)

    def test_session_limit_hop_claude_to_opencode(self):
        out, calls, sleeps = self._sweep(
            [self.LIMIT_NOTICE, Proc(0, "opencode report", "")],
            self.CLAUDE_OPENCODE_ROUTING,
        )
        self.assertEqual(out.text, "opencode report")
        self.assertEqual([c[0] for c in calls], ["claude", "opencode"])
        self.assertEqual(sleeps, [])
        self.assertIn(self.HOP_MODELS["opencode"], calls[1])
        self.assert_no_claude_only_flags(calls)

    def test_multi_hop_row_hitting_limit_twice_sweeps_second_hop(self):
        # claude -> (limit) -> codex -> (limit) -> opencode: the SECOND hop
        # transition is itself swept -- the rebuilt codex command carried no
        # payload, and the codex->opencode rebuild must not resurrect one.
        out, calls, sleeps = self._sweep(
            [self.LIMIT_NOTICE, self.LIMIT_NOTICE, Proc(0, "opencode report", "")],
            self.THREE_TARGET_ROUTING,
        )
        self.assertEqual(out.text, "opencode report")
        self.assertEqual([c[0] for c in calls], ["claude", "codex", "opencode"])
        self.assertIn(self.HOP_MODELS["codex"], calls[1])
        self.assertIn(self.HOP_MODELS["opencode"], calls[2])
        self.assertEqual(sleeps, [])
        self.assert_no_claude_only_flags(calls)

    def test_positive_control_ungated_claude_receives_every_payload_token(self):
        # Positive control: with no limit hit, the served claude cell
        # receives EVERY CLAUDE_ONLY_ARGV_TOKENS element -- proving the sweep
        # payload really spans the whole token set (values intact), i.e. the
        # negative assertions above are testing something.
        out, calls, _sleeps = self._sweep(
            [Proc(0, "claude report", "")], self.SINGLE_CLAUDE
        )
        self.assertEqual(out.text, "claude report")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "claude")
        for token in CLAUDE_ONLY_ARGV_TOKENS:
            self.assertIn(token, calls[0], token)
        idx = calls[0].index("--append-system-prompt")
        self.assertEqual(calls[0][idx + 1], "lean worker system prompt")
        self.assertEqual(
            calls[0][
                calls[0].index("--tools") + 1 : calls[0].index("--append-system-prompt")
            ],
            ["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
        )
        self.assert_no_claude_only_flags(calls)


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
        text, _usage, _tools, _skills, sid = spawnlib._parse_stream_json(raw)
        self.assertEqual(sid, "sess-abc123")
        self.assertEqual(text, "done")

    def test_parse_stream_json_missing_session_id(self):
        raw = self._result_line()  # no session_id key
        _text, _usage, _tools, _skills, sid = spawnlib._parse_stream_json(raw)
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
        cmd = spawnlib.build_cmd("p", _cell(), resume_session_id="sid-42")
        self.assertIn("--resume", cmd)
        idx = cmd.index("--resume")
        self.assertEqual(cmd[idx + 1], "sid-42")
        self.assertIn("--fork-session", cmd)

    def test_build_cmd_no_fork(self):
        cmd = spawnlib.build_cmd("p", _cell())
        self.assertNotIn("--fork-session", cmd)
        self.assertNotIn("--resume", cmd)

    def test_build_cmd_fork_session_none(self):
        cmd = spawnlib.build_cmd("p", _cell(), resume_session_id=None)
        self.assertNotIn("--fork-session", cmd)

    def test_build_cmd_fork_session_empty_string(self):
        # Empty string is falsy — should not add fork args
        cmd = spawnlib.build_cmd("p", _cell(), resume_session_id="")
        self.assertNotIn("--fork-session", cmd)


class BuildChildEnv(unittest.TestCase):
    """Tests for build_child_env(cell, base_env) -- design D6: claude
    subscription drops any ambient ANTHROPIC_API_KEY, claude api requires and
    copies through its declared auth.env, and every other harness/pool is a
    no-op (routing-target-selector 3.2)."""

    def test_claude_subscription_removes_ambient_api_key(self):
        base = {"ANTHROPIC_API_KEY": "sk-ambient", "PATH": "/usr/bin"}
        env = spawnlib.build_child_env(_cell(pool="subscription"), base)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_claude_subscription_is_a_noop_when_key_already_absent(self):
        base = {"PATH": "/usr/bin"}
        env = spawnlib.build_child_env(_cell(pool="subscription"), base)
        self.assertEqual(env, base)

    def test_base_env_is_not_mutated(self):
        base = {"ANTHROPIC_API_KEY": "sk-ambient"}
        spawnlib.build_child_env(_cell(pool="subscription"), base)
        self.assertIn("ANTHROPIC_API_KEY", base)

    def test_claude_api_copies_named_auth_var_through(self):
        base = {"ANTHROPIC_API_KEY": "sk-real", "PATH": "/usr/bin"}
        cell = _cell(pool="api", auth={"env": "ANTHROPIC_API_KEY"})
        env = spawnlib.build_child_env(cell, base)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-real")

    def test_claude_api_copies_a_differently_named_auth_var(self):
        base = {"CLAUDE_API_KEY_PROD": "sk-prod"}
        cell = _cell(pool="api", auth={"env": "CLAUDE_API_KEY_PROD"})
        env = spawnlib.build_child_env(cell, base)
        self.assertEqual(env["CLAUDE_API_KEY_PROD"], "sk-prod")

    def test_claude_api_raises_when_auth_env_var_is_unset(self):
        base = {"PATH": "/usr/bin"}  # ANTHROPIC_API_KEY not present
        cell = _cell(pool="api", auth={"env": "ANTHROPIC_API_KEY"}, target="claude-api")
        with self.assertRaises(spawnlib.OperatorConfigError) as ctx:
            spawnlib.build_child_env(cell, base)
        self.assertIn("claude-api", str(ctx.exception))
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))

    def test_claude_api_raises_when_auth_env_var_is_empty(self):
        base = {"ANTHROPIC_API_KEY": ""}
        cell = _cell(pool="api", auth={"env": "ANTHROPIC_API_KEY"})
        with self.assertRaises(spawnlib.OperatorConfigError):
            spawnlib.build_child_env(cell, base)

    def test_claude_api_raises_when_auth_is_not_configured(self):
        base = {"ANTHROPIC_API_KEY": "sk-real"}
        cell = _cell(pool="api", auth=None, target="claude-api")
        with self.assertRaises(spawnlib.OperatorConfigError) as ctx:
            spawnlib.build_child_env(cell, base)
        self.assertIn("claude-api", str(ctx.exception))
        self.assertIn("auth.env", str(ctx.exception))

    def test_opencode_is_a_noop_regardless_of_pool(self):
        base = {"ANTHROPIC_API_KEY": "sk-ambient"}
        for pool in ("subscription", "free", "api"):
            with self.subTest(pool=pool):
                env = spawnlib.build_child_env(
                    _cell(harness="opencode", pool=pool), base
                )
                self.assertEqual(env, base)

    def test_codex_is_a_noop_regardless_of_pool(self):
        base = {"ANTHROPIC_API_KEY": "sk-ambient"}
        for pool in ("subscription", "api"):
            with self.subTest(pool=pool):
                env = spawnlib.build_child_env(_cell(harness="codex", pool=pool), base)
                self.assertEqual(env, base)


class DispatchIdEnvVar(unittest.TestCase):
    """Tests for dispatch_id parameter and WORKTRAIL_DISPATCH_ID env var plumbing."""

    def setUp(self):
        self._cache = tempfile.TemporaryDirectory()
        self._old_cache = os.environ.get("GO_AGENT_CAPACITY_CACHE")
        os.environ["GO_AGENT_CAPACITY_CACHE"] = os.path.join(
            self._cache.name, "capacity.json"
        )

    def tearDown(self):
        if self._old_cache is None:
            os.environ.pop("GO_AGENT_CAPACITY_CACHE", None)
        else:
            os.environ["GO_AGENT_CAPACITY_CACHE"] = self._old_cache
        self._cache.cleanup()

    def test_dispatch_id_sets_env_var_in_child(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            return Proc(0, "ok", "")

        with (
            _patch_routing(SINGLE_CLAUDE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            spawnlib.spawn_agent(
                "prompt", "/tmp", tier="t2-build", dispatch_id="go-abc123", retries=0
            )

        self.assertEqual(captured["env"]["WORKTRAIL_DISPATCH_ID"], "go-abc123")

    def test_dispatch_id_absent_when_omitted(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            return Proc(0, "ok", "")

        with (
            _patch_routing(SINGLE_CLAUDE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            spawnlib.spawn_agent("prompt", "/tmp", tier="t2-build", retries=0)

        self.assertNotIn("WORKTRAIL_DISPATCH_ID", captured["env"])

    def test_dispatch_id_absent_when_none(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs["env"]
            return Proc(0, "ok", "")

        with (
            _patch_routing(SINGLE_CLAUDE_ROUTING),
            patch.object(spawnlib.subprocess, "run", side_effect=fake_run),
        ):
            spawnlib.spawn_agent(
                "prompt", "/tmp", tier="t2-build", dispatch_id=None, retries=0
            )

        self.assertNotIn("WORKTRAIL_DISPATCH_ID", captured["env"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- headless `claude -p` worker invocation.

Single home for "run a cold headless worker and return its final message", shared
by the task fan-out (live.py) and the post-PR verify workers (verify.py) so the
retry/timeout/logging policy lives in one place.

Why a retry layer at all: a worker outcome has two very different failure modes
that the old code conflated --

  * INFRA failure  -- the `claude -p` subprocess exited non-zero or produced empty
    output (an API 5xx/overload blip, a transient network error). The task itself
    is fine; the right response is to retry the spawn.
  * TASK failure   -- the subprocess ran fine (exit 0, real output) but the worker
    reported `status: failed`, or hit its wall-clock timeout. That is a real
    outcome the caller must act on, NOT a retry.

`spawn_claude_p` retries only INFRA failures (non-zero exit / empty stdout), with
a short backoff, before giving up and returning the last output (so the caller's
report-back parse still runs and can decide). A `TimeoutExpired` is propagated
unchanged: a genuinely-stuck worker should not be silently re-run for another full
timeout -- the caller marks the task failed (and the run journal makes a resume
cheap). The retry count is overridable via $ORCH_SPAWN_RETRIES.

Token + tool tracking
---------------------
`spawn_claude_p` runs workers with `--output-format stream-json` so `claude -p`
emits a JSONL stream. The final `result` event carries exact token counts and USD
cost; the `assistant` events carry `tool_use` blocks naming every tool and skill
the worker invoked. The caller receives a `SpawnResult` named-tuple with:

  text        — worker's final message (what the orchestrator parses for report-back)
  usage       — {input_tokens, cache_creation_input_tokens, cache_read_input_tokens,
                  output_tokens, total_cost_usd}
  tools_used  — sorted list of distinct tool names (Read, Edit, Write, Bash, …)
  skills_used — sorted list of distinct skill names invoked via the Skill tool

Callers that only need the text can still do `result.text`. The orchestrator logs
all four fields to the run journal under each task entry; `progress.render_tools_used`
aggregates them into a per-run footprint report.

Setting `$WORKTRAIL_KEEP_TRANSCRIPTS` to a directory path persists each spawn's raw
stream-json JSONL there (diagnostic-only, off by default) -- the per-turn capture the
turn-count audit (docs/specs/research/worker-spawn-cache-read-amplification.md) needs to
bucket turns by activity instead of only seeing the aggregate `num_turns` count.

Switching from `--output-format json` to `--output-format stream-json` is
token-neutral: the flag only changes how the worker serialises its result to stdout
(JSONL instead of a single JSON envelope). The model's `usage` is identical; the
stream is parsed by the Python parent, not fed to any LLM.

opencode workers additionally get an isolated, provider-preserving child
environment (`prepare_opencode_child_environment`): a per-worktree data dir so
concurrent workers never share one SQLite `opencode.db`, and inline permission
config so headless tool calls inside the authorized worktree are never
auto-rejected. See the "opencode headless worker environment" section below.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from ..router import routing_cli
from ..router.policy import (
    ROUTING_FILE_ENV,
    OperatorConfigError,
    load_policy,
    resolve_routing,
    resolved_routing_file_path,
)
from ..router.skill_dispatch import prepare_codex_child_environment
from ..runtime.selection import Cell, NoExecutionTarget, select_cell
from ..shared.homedir import env_setting, worktrail_home
from . import agent_capacity


class SpawnResult(NamedTuple):
    text: str
    usage: dict
    tools_used: list[str] = []  # noqa: RUF012 -- NamedTuple field default, not a class attribute
    skills_used: list[str] = []  # noqa: RUF012
    # Cumulative seconds the spawn spent sleeping on session-limit waits. The
    # caller subtracts this from the run's wall-clock elapsed time so a rate-limit
    # pause never consumes the --run-budget (without it a 4h reset window would
    # count against a 4h budget even though no work was happening).
    paused_s: float = 0.0
    # Session ID returned by the result event; populated for every live spawn.
    # Callers can pass this as resume_session_id to build_cmd to fork from this session.
    session_id: str = ""
    # The (target, model) cell that actually served this spawn -- may differ
    # from the caller's requested `prefer` after a session-limit/infra hop.
    # Empty strings for callers that never resolve a Cell (e.g. a caller
    # patching spawn_agent/spawn_claude_p out entirely in a test).
    served_target: str = ""
    served_model: str = ""
    served_harness: str = ""
    # True when the spawn gave up without the worker ever producing a verdict --
    # every cell in the row was capacity-gated or the wait budget ran out. `text`
    # in that case is the provider's notice, not a result: consumers must fail
    # closed rather than parse it. `failure_class` is the class the last give-up
    # was classified as (`billing` for a provider usage cap, `rate_limit` for a
    # session limit), empty on a successful spawn.
    exhausted: bool = False
    failure_class: str = ""


# verified from `claude --help`: bypass perms so a headless worker can edit/commit
PERM_FLAGS = ["--permission-mode", "bypassPermissions"]

# Request stream-json output: JSONL per turn, final result event carries usage +
# cost; assistant events carry tool_use blocks for tool/skill instrumentation.
JSON_OUTPUT_FLAGS = ["--output-format", "stream-json", "--verbose"]

# Retries for a transient (infra) spawn failure, on top of the first attempt.
SPAWN_RETRIES_DEFAULT = int(os.environ.get("ORCH_SPAWN_RETRIES", "2"))

# Longest a single "session limit" park may last before re-probing the cell,
# whatever reset time the notice claims. A reset clock already past today rolls
# to tomorrow and would otherwise park a spawn for ~24h on a notice that was
# wrong, stale, or lifted early.
SESSION_LIMIT_REPROBE_MAX_S = float(
    os.environ.get("ORCH_SESSION_LIMIT_REPROBE_MAX_S", "900")
)

# Total wall-clock one spawn may spend parked on session limits, across every
# park. The real safety bound for a persistently-capped account.
SESSION_LIMIT_TOTAL_WAIT_MAX_S = float(
    os.environ.get("ORCH_SESSION_LIMIT_TOTAL_WAIT_MAX_S", "14400")
)

# How many times a single spawn will wait out a "session limit" reset before
# giving the limit message back to the caller as a task failure. Derived from
# the two bounds above so the probe count always covers the total budget: a
# hardcoded low count would silently shorten patience the moment the per-park
# cap shrinks, turning "probe more often" into "give up sooner".
SESSION_LIMIT_WAITS_DEFAULT = int(
    os.environ.get(
        "ORCH_SESSION_LIMIT_WAITS",
        str(
            max(
                1,
                math.ceil(SESSION_LIMIT_TOTAL_WAIT_MAX_S / SESSION_LIMIT_REPROBE_MAX_S),
            )
        ),
    )
)

# `claude -p` prints this when the account's usage window is exhausted, e.g.
# "You've hit your session limit. Your limit resets at 3:00pm." The reset clock
# time is local. We match leniently (the wording varies) on the "resets ... H:MM(am|pm)" tail.
_SESSION_LIMIT_RE = re.compile(
    r"hit your session limit.*?reset[^0-9]*?(\d{1,2}:\d{2}\s*[ap]m)",
    re.IGNORECASE | re.DOTALL,
)


# A genuine usage-cap notice is a one-to-two-sentence message that is essentially
# the CLI's ENTIRE output. Text longer than this is a worker transcript/result, and
# any regex match inside it is the worker quoting the notice wording (docstrings,
# test fixtures, code it is editing) — not a real cap. Spec-023's TASK-005 workers,
# whose job was editing this very file, matched the docstring example above on every
# spawn and put runs into false "sleep until 3:00pm" parks for 16 hours (2026-07-23).
_SESSION_LIMIT_NOTICE_MAX_CHARS = 600


def parse_session_limit_reset(
    text: str | None, now: datetime.datetime | None = None
) -> datetime.datetime | None:
    """If `text` reports a Claude session-limit hit, return the next local datetime
    the limit resets (parsed from a 'resets ... H:MMam/pm' clock time, rolled to
    tomorrow when that time has already passed today). Returns None when no
    session-limit message is present, so callers can branch on truthiness.

    Only notice-sized text qualifies (see _SESSION_LIMIT_NOTICE_MAX_CHARS): a real
    cap response IS the output; a long text merely CONTAINS the wording.
    """
    if text and len(text.strip()) > _SESSION_LIMIT_NOTICE_MAX_CHARS:
        return None
    m = _SESSION_LIMIT_RE.search(text or "")
    if not m:
        return None
    now = now or datetime.datetime.now()  # noqa: DTZ005
    clock = datetime.datetime.strptime(  # noqa: DTZ007
        m.group(1).replace(" ", "").lower(), "%I:%M%p"
    ).time()
    reset = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    if reset <= now:
        reset += datetime.timedelta(days=1)
    return reset


def _parse_stream_json(raw: str) -> tuple[str, dict, list[str], list[str], str]:
    """Extract (result_text, usage_dict, tools_used, skills_used, session_id) from a
    --output-format stream-json (JSONL) response.

    Falls back to (raw, {}, [], [], "") when no parseable stream is found, so callers
    are never broken by test fixtures or infra failures that return raw strings.

    Stream structure (claude `claude -p --output-format stream-json`):
      Each line is a JSON object. We read two event types:
        "result"    — final event; carries the result text, usage, total_cost_usd,
                      and session_id (used to fork workers from a shared research session).
        "assistant" — carries a message.content array; tool_use blocks in that array
                      name every tool (Read/Edit/Bash/…) or skill (Skill tool with
                      input.skill) the worker invoked.

    `usage` also carries a few non-token diagnostic fields lifted straight from the
    "result" event: `subtype` (e.g. "success", "error_max_turns",
    "error_during_execution"), `is_error`, `stop_reason` (e.g. "end_turn",
    "max_tokens"), `num_turns`, and `permission_denials`. None of these are used for
    billing -- they exist so a report-back parse failure (dispatch.parse_report_back
    raising "no report-back JSON block found") can be diagnosed from *why* the
    worker's final turn ended instead of only the error message and duration.

    Stream structure (`opencode run --format json`) -- a completely different JSONL
    vocabulary (verified against a live reproduction, handoff 20260722-152514):
      Every event nests its payload under "part" and carries a top-level "sessionID".
        "text"        — one per assistant turn; `part.text` is that turn's message.
                        The LAST "text" event's `part.text` becomes `result_text`
                        (opencode has no single final "result" event the way claude
                        does -- each turn's text event overwrites the previous one,
                        same overwrite-on-last-occurrence pattern as claude's "result").
        "tool_use"    — `part.tool` names the tool invoked (e.g. "read", "bash",
                        "write", "todowrite"); no separate skill signal (opencode has
                        no Skill-tool equivalent). A headless permission rejection
                        arrives HERE, not as a top-level error: `part.state.status`
                        is "error" and `part.state.error` reads "The user rejected
                        permission to use this specific tool call." (verified live
                        v1.17.13: `opencode run` auto-rejects any permission that
                        resolves to "ask" -- stderr shows `! permission requested:
                        bash (...); auto-rejecting`). These are collected into the
                        usage dict's `permission_denials` diagnostic field (shape
                        parity with claude's same-named field) so a missing
                        report-back can be traced to rejected tool calls.
        "step_finish" — one per step (not one per run); `part.tokens` carries
                        {total, input, output, reasoning, cache: {write, read}} and
                        `part.cost` the step's USD cost. Summed across every
                        "step_finish" event to build an aggregate usage dict with the
                        same field names claude's usage dict uses, so downstream
                        consumers (progress.render_tools_used, run-journal cost
                        aggregation) don't need to know which agent produced a spawn.
        "step_start"  — informational only, no fields extracted (mirrors how claude's
                        "system" event is silently skipped).
      A top-level "error" event (`{"type":"error","error":{"name":...,"data":{...}}}`)
      signals an opencode-side failure (auth/quota/rate-limit/server error) on an
      otherwise-clean exit; `is_infra_failure` below is what recognizes it -- this
      function only extracts text/usage/tools/session_id and does not classify errors.
    """
    result_text = raw
    usage: dict = {}
    tools_seen: set = set()
    skills_seen: set = set()
    session_id: str = ""
    opencode_usage_seen = False
    opencode_denials: list[dict] = []
    opencode_totals = {
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "total_cost_usd": 0.0,
    }

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")

        if not session_id:
            opencode_sid = event.get("sessionID")
            if isinstance(opencode_sid, str) and opencode_sid:
                session_id = opencode_sid

        if event_type == "result":
            result_text = event.get("result") or raw
            raw_usage = event.get("usage") or {}
            usage = {
                "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
                "cache_creation_input_tokens": int(
                    raw_usage.get("cache_creation_input_tokens", 0) or 0
                ),
                "cache_read_input_tokens": int(
                    raw_usage.get("cache_read_input_tokens", 0) or 0
                ),
                "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
                "total_cost_usd": float(event.get("total_cost_usd", 0.0) or 0.0),
                # Diagnostic-only fields (not used for billing/token accounting) --
                # see the module docstring above for why these are captured.
                "subtype": event.get("subtype") or "",
                "is_error": bool(event.get("is_error", False)),
                "stop_reason": event.get("stop_reason") or "",
                "num_turns": int(event.get("num_turns", 0) or 0),
                "permission_denials": event.get("permission_denials") or [],
            }
            session_id = event.get("session_id") or ""

        elif event_type == "assistant":
            message = event.get("message") or {}
            content = message.get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or ""
                if name == "Skill":
                    skill_name = (block.get("input") or {}).get("skill") or ""
                    if skill_name:
                        skills_seen.add(skill_name)
                elif name:
                    tools_seen.add(name)

        elif event_type == "text":
            part = event.get("part") or {}
            text_val = part.get("text")
            if text_val:
                result_text = text_val

        elif event_type == "tool_use":
            part = event.get("part") or {}
            tool_name = part.get("tool") or ""
            if tool_name:
                tools_seen.add(tool_name)
            state = part.get("state")
            if isinstance(state, dict) and state.get("status") == "error":
                err = str(state.get("error") or "")
                low = err.lower()
                if "permission" in low and "reject" in low:
                    opencode_denials.append({"tool": tool_name, "error": err})

        elif event_type == "step_finish":
            part = event.get("part") or {}
            tokens = part.get("tokens") or {}
            if tokens:
                opencode_usage_seen = True
                cache = tokens.get("cache") or {}
                opencode_totals["input_tokens"] += int(tokens.get("input", 0) or 0)
                opencode_totals["output_tokens"] += int(tokens.get("output", 0) or 0)
                opencode_totals["cache_creation_input_tokens"] += int(
                    cache.get("write", 0) or 0
                )
                opencode_totals["cache_read_input_tokens"] += int(
                    cache.get("read", 0) or 0
                )
                opencode_totals["total_cost_usd"] += float(part.get("cost", 0.0) or 0.0)

    if (opencode_usage_seen or opencode_denials) and not usage:
        usage = {
            **opencode_totals,
            # Diagnostic-only fields kept for shape parity with the claude usage
            # dict (see the module docstring) -- opencode's step_finish events
            # carry no equivalent signal, so these stay at their empty defaults
            # EXCEPT permission_denials, which opencode reports through tool_use
            # error states (see the module docstring above).
            "subtype": "",
            "is_error": False,
            "stop_reason": "",
            "num_turns": 0,
            "permission_denials": opencode_denials,
        }

    return result_text, usage, sorted(tools_seen), sorted(skills_seen), session_id


def _opencode_error_event(stdout: str | None) -> dict | None:
    """Return opencode's top-level error dict (`{"name": ..., "data": {...}}`) when
    `stdout` (a `opencode run --format json` JSONL response) contains a
    `{"type":"error","error":{...}}` event, else None.

    opencode emits this on exit 0 with non-empty stdout when the provider itself
    failed (confirmed live: a free-tier rate-limit/server error surfaced as
    `{"type":"error","error":{"name":"UnknownError","data":{"message":"Unexpected
    server error...","ref":"..."}}}`, handoff 20260722-152514) -- a case the
    exit-code/empty-output check in `is_infra_failure` alone cannot see.
    """
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "error"
            and isinstance(event.get("error"), dict)
        ):
            return event["error"]
    return None


def is_infra_failure(returncode: int, stdout: str | None) -> bool:
    """A spawn that exited non-zero, produced no output, or (opencode) reported a
    top-level error event -- a transient blip, not a task verdict. (A real task
    failure is exit 0 + a `status:failed` report.)"""
    if returncode != 0 or not (stdout or "").strip():
        return True
    return _opencode_error_event(stdout) is not None


def _is_auth_failure(proc: subprocess.CompletedProcess, raw: str | None) -> bool:
    """True when an infra failure's output classifies as an auth failure (401 /
    consumed refresh token / "log out and sign in again") -- a condition that
    cannot clear itself, so `spawn_agent` gates the cell without retrying."""
    return (
        agent_capacity.classify_failure(proc.returncode, raw or "", proc.stderr or "")
        == "auth"
    )


def _opencode_unknown_error_failure_class(cell: Cell, raw: str | None) -> str | None:
    """When *cell*'s exhausted-retries raw output carries a top-level opencode
    `UnknownError` (see `_opencode_error_event`), distinguish a retired/renamed
    model id from a transient provider-side error by checking whether
    `routing_cli.list_opencode_models()` (task 6.1) still serves it: absent ->
    `model_unavailable` (the long cooldown in `agent_capacity.DEFAULT_COOLDOWNS`,
    since a retired model will not come back on its own); present -> `transport`
    (an ordinary blip, short cooldown). Returns None for every other
    harness/error shape, so the caller falls back to
    `agent_capacity.classify_failure`'s generic text matching."""
    if cell.harness != "opencode":
        return None
    error = _opencode_error_event(raw)
    if not error or error.get("name") != "UnknownError":
        return None
    known_models = routing_cli.list_opencode_models()
    return "model_unavailable" if cell.model not in known_models else "transport"


SUPPORTED_AGENTS = {"claude", "codex", "opencode"}


def _with_default_setting_sources(
    agent: str, extra_args: Sequence[str] | None
) -> list[str]:
    """Default `--setting-sources project,local` and the worker worktree guard
    (`--settings`, see `worker_guard_settings_json`) onto every `claude` spawn.

    Excludes the operator's USER-level ~/.claude/settings.json (and its Stop
    hook) from headless worker sessions -- confirmed root cause (investigation
    20260711-130900): a user-level Stop hook fires on every worker that commits
    or writes a file, forcing an extra continuation turn whose text becomes the
    final message the orchestrator parses for the report-back JSON, which never
    repeats the trailing ```json block. Three PRs (#251/#252/#253) each patched
    a different direct spawn_agent/spawn_claude_p call site with this flag
    before it landed here as the structural default -- nothing stopped a future
    call site from reintroducing the gap. A caller that explicitly passes
    `--setting-sources` (e.g. one that genuinely wants the operator's
    user-level settings) is respected as-is and never overridden.
    """
    args = list(extra_args or [])
    if agent != "claude":
        return args
    if "--setting-sources" not in args:
        args = ["--setting-sources", "project,local", *args]
    if "--settings" not in args:
        args = ["--settings", worker_guard_settings_json(), *args]
    return args


def worker_guard_settings_json() -> str:
    """The `--settings` JSON that injects `worktree_guard_hook` into a claude
    worker as a PreToolUse hook. `--settings` is an additional settings source
    that `--setting-sources project,local` does not drop, so the guard reaches
    every worker regardless of the operator's own ~/.claude/settings.json --
    which is exactly what let a worker write into the canonical checkout on
    2026-09-05 (see the hook module's docstring). Runs the hook with the same
    interpreter the orchestrator runs under, so it resolves the installed
    `worktrail` package without depending on PATH."""
    from . import worktree_guard_hook

    return json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": worktree_guard_hook.HOOK_MATCHER,
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{sys.executable} -m worktrail.orchestrator.worktree_guard_hook",
                                "timeout": 5,
                            }
                        ],
                    }
                ]
            }
        }
    )


def build_cmd(
    prompt: str,
    cell: Cell,
    *,
    extra_args: Sequence[str] | None = None,
    resume_session_id: str | None = None,
    output_last_message: str | None = None,
) -> list[str]:
    """Build the launcher argv for *cell* (design D3/D6): `cell.harness` picks the
    CLI, `cell.model`/`cell.effort` are translated per-harness exactly as before,
    and `cell.pool` decides claude's auth lane -- `--bare` is appended only for
    a claude `api`-pool cell (`subscription` omits it, matching every existing
    claude spawn); opencode/codex are unaffected by pool."""
    agent = cell.harness
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    model = cell.model
    effort = cell.effort
    extra_args = _with_default_setting_sources(agent, extra_args)

    if agent == "claude":
        cmd = ["claude", "-p", prompt, *PERM_FLAGS, *JSON_OUTPUT_FLAGS]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        if cell.pool == "api":
            cmd += ["--bare"]
        if resume_session_id:
            cmd += ["--resume", resume_session_id, "--fork-session"]
        if extra_args:
            cmd += list(extra_args)
        return cmd

    if agent == "opencode":
        cmd = ["opencode", "run", "--format", "json"]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--variant", effort]
        if resume_session_id:
            cmd += ["--session", resume_session_id, "--fork"]
        if extra_args:
            cmd += list(extra_args)
        cmd.append(prompt)
        return cmd

    cmd = ["codex", "exec", "--json", "-s", "danger-full-access"]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    if output_last_message:
        cmd += ["--output-last-message", output_last_message]
    if extra_args:
        cmd += list(extra_args)
    cmd.append(prompt)
    return cmd


def build_child_env(cell: Cell, base_env: Mapping[str, str]) -> dict[str, str]:
    """The auth lane *cell*'s harness/pool draws from (design D6), layered onto
    a copy of *base_env*.

    A claude `subscription` cell has `ANTHROPIC_API_KEY` removed so an ambient
    key can never silently switch a subscription spawn's billing to the API.
    A claude `api` cell requires its target's declared `auth: {env: <NAME>}`
    and that named variable set (non-empty) in *base_env*; both are load-bearing
    identity the launcher cannot guess, so a missing one raises
    `OperatorConfigError` naming the target and what to fix rather than spawning
    an unauthenticated worker. Every other harness/pool combination returns
    *base_env* unchanged -- opencode/codex auth is unaffected by pool (D6)."""
    env = dict(base_env)
    if cell.harness != "claude":
        return env
    if cell.pool == "subscription":
        env.pop("ANTHROPIC_API_KEY", None)
        return env
    if cell.pool == "api":
        auth = cell.auth if isinstance(cell.auth, Mapping) else {}
        var_name = auth.get("env")
        if not var_name:
            raise OperatorConfigError(
                f"routing target {cell.target!r} (harness claude, pool api) has no "
                "auth.env configured -- add `auth: {env: <ENV_VAR_NAME>}` to its "
                f"routing.targets entry in {resolved_routing_file_path()}"
            )
        value = env.get(var_name)
        if not value:
            raise OperatorConfigError(
                f"routing target {cell.target!r} requires {var_name} to be set in "
                "the environment for its 'api' pool -- export it before spawning"
            )
        env[var_name] = value
    return env


# --------------------------------------------------------------------------- #
# opencode headless worker environment
# --------------------------------------------------------------------------- #
# opencode keeps ALL of its mutable state -- opencode.db (SQLite) plus log/,
# repos/, snapshot/ -- under one per-user data dir resolved from XDG_DATA_HOME
# (verified with `opencode debug paths`, v1.17.13: setting XDG_DATA_HOME moves
# data/log/repos; config under ~/.config/opencode is unaffected). Concurrent
# workers sharing ~/.local/share/opencode/opencode.db is the SQLite-contention
# defect of brief 20260811-220340; pointing each worker's XDG_DATA_HOME into
# its own worktree scratch gives every spawn a private db/log, and the state is
# deleted with the worktree by the standard `git worktree remove --force`
# teardown (the scratch dir self-gitignores so `git add -A` never commits it).
#
# Provider identity: auth.json/account.json are read from the DATA dir, so a
# fresh isolated dir silently drops credentials (verified: `opencode providers
# list` reports 0 credentials under an XDG_DATA_HOME override). Symlinking the
# parent's files into the isolated dir preserves identity and lets token
# refreshes write through to the real store. Env-var-keyed providers
# (GEMINI_API_KEY etc.) are unaffected either way.
#
# Permissions: headless `opencode run` has no channel to answer an "ask" -- it
# AUTO-REJECTS it (verified live: stderr `! permission requested: bash (...);
# auto-rejecting`; the JSONL stream records a tool_use error "The user rejected
# permission to use this specific tool call."). `external_directory` defaults
# to "ask", and opencode resolves the project root through the git COMMON dir,
# so inside a linked worktree every file in the worker's OWN cwd counts as
# external (observed in the shared opencode.log: `evaluated
# permission=external_directory pattern=<own-worktree>/... action=ask`) -- the
# exact mechanism that produced zero report-backs in run go-20260811-213553.
# The fix is OPENCODE_CONFIG_CONTENT (inline config, merged after global and
# project config -- verified: an inline `"bash": "ask"` overrode the default)
# scoping allow rules to exactly the worker's cwd and its git common dir.
# `--auto` is deliberately NOT used: it approves everything not explicitly
# denied, i.e. broad home access.


def opencode_state_root(cwd: str | Path) -> Path:
    """Per-worktree opencode scratch, deleted with the worktree on teardown."""
    return Path(cwd) / ".worktrail" / "opencode"


def opencode_data_dir(cwd: str | Path) -> Path:
    """The isolated opencode data dir (opencode.db, log/, repos/) for *cwd*."""
    return opencode_state_root(cwd) / "xdg" / "opencode"


def _parent_opencode_data_dir(env: dict[str, str]) -> Path:
    """The invoking user's real opencode data dir (credential source)."""
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


# The real subprocess.run, captured at import: the hermetic spawn tests script
# `spawnlib.subprocess.run` with fake worker outcomes, and the git probe below
# must never consume one of those scripted outcomes (or feed its own output
# into them).
_REAL_SUBPROCESS_RUN = subprocess.run


def _git_common_dir(cwd: str | Path) -> str | None:
    """Absolute git common dir for *cwd* (the shared .git a linked worktree's
    objects live in), or None when cwd is not a git checkout."""
    try:
        proc = _REAL_SUBPROCESS_RUN(
            [
                "git",
                "-C",
                str(cwd),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _opencode_permission_config(cwd: str | Path, existing_content: str | None) -> dict:
    """Inline opencode config granting a headless worker non-interactive use of
    its tools inside the authorized roots: the worktree itself plus its git
    common dir. File access anywhere else still falls through to opencode's
    built-in external_directory "ask" default, which a headless run
    auto-rejects -- scoped containment, never a broad home grant.

    `existing_content` (a caller-set $OPENCODE_CONFIG_CONTENT) is merged rather
    than clobbered: its non-permission keys and unmatched permission rules
    survive; the worker grants below win on conflict (a headless worker that
    inherited an interactive-oriented "ask" would silently lose every tool
    call).
    """
    roots: list[str] = []
    for candidate in (str(cwd), str(Path(cwd).resolve())):
        if candidate not in roots:
            roots.append(candidate)
    common = _git_common_dir(cwd)
    if common and common not in roots:
        roots.append(common)
    external: dict[str, str] = {}
    for root in roots:
        external[root] = "allow"
        external[root.rstrip("/") + "/**"] = "allow"
    permission: dict = {
        # Parity with claude's --permission-mode bypassPermissions and codex's
        # -s danger-full-access: tool USE is granted, while file access outside
        # the roots above is still auto-rejected via external_directory.
        "read": "allow",
        "edit": "allow",
        "glob": "allow",
        "grep": "allow",
        "bash": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "task": "allow",
        "skill": "allow",
        "lsp": "allow",
        "external_directory": external,
    }
    config: dict = {}
    if existing_content:
        try:
            parsed = json.loads(existing_content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            config = dict(parsed)
    existing_perm = config.get("permission")
    if isinstance(existing_perm, dict):
        existing_ext = existing_perm.get("external_directory")
        if isinstance(existing_ext, dict):
            permission["external_directory"] = {**existing_ext, **external}
        merged = {**existing_perm, **permission}
        for key, prev in existing_perm.items():
            if key == "external_directory":
                continue
            if prev == "deny":
                # An explicit operator deny is never widened -- only "ask"
                # (which a headless run auto-rejects) is upgraded to allow.
                merged[key] = "deny"
            elif isinstance(prev, dict) and not isinstance(merged.get(key), dict):
                # Pattern rules survive under our blanket grant; opencode's
                # "last matching rule wins" keeps the specific patterns
                # authoritative over the leading "*" allow.
                merged[key] = {"*": "allow", **prev}
        permission = merged
    config["permission"] = permission
    return config


def prepare_opencode_child_environment(
    cwd: str | Path, base_env: dict[str, str] | None = None
) -> tuple[dict[str, str], Path]:
    """Prepare an isolated, provider-preserving, prompt-free environment for a
    headless opencode worker running in *cwd*.

    Returns `(child_env, data_dir)`: `child_env` carries the per-worktree
    XDG_DATA_HOME override plus the scoped OPENCODE_CONFIG_CONTENT permission
    grants; `data_dir` is where this worker's opencode.db and log/ land
    (inspectable after a failure, deleted with the worktree on teardown).
    """
    env: dict[str, str] = dict(base_env if base_env is not None else os.environ)
    data_dir = opencode_data_dir(cwd)
    data_dir.mkdir(parents=True, exist_ok=True)
    # Self-ignoring scratch: a worker running `git add -A` must never commit
    # orchestrator state (a .gitignore of "*" ignores its own whole tree).
    marker = Path(cwd) / ".worktrail" / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n", encoding="utf-8")
    parent_data = _parent_opencode_data_dir(env)
    for name in ("auth.json", "account.json"):
        src = parent_data / name
        dst = data_dir / name
        if dst.is_symlink():
            if dst.readlink() == src:
                continue
            dst.unlink()
        elif dst.exists():
            continue  # a real (operator-provisioned) file is preserved as-is
        if src.exists():
            dst.symlink_to(src)
    env["XDG_DATA_HOME"] = str(opencode_state_root(cwd) / "xdg")
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        _opencode_permission_config(cwd, env.get("OPENCODE_CONFIG_CONTENT"))
    )
    return env, data_dir


def _preflight_primary_target(
    routing: Mapping[str, Any],
    tier: str,
    prefer: str | None,
    exclude_harness: str | None,
) -> str | None:
    """The target `select_cell()` would try FIRST for this (tier, prefer,
    exclude_harness), ignoring capacity (mirrors `select_cell`'s steps 1-3:
    order + prefer promotion, drop unconfigured/opted-out targets, soft
    `exclude_harness` partition). Used only so `spawn_agent` can tell whether
    a persisted capacity gate has already moved selection past the cell the
    caller derived its `extra_args` for, before the first subprocess ever
    runs -- see the call site below."""
    targets: Mapping[str, Any] = routing.get("targets") or {}
    row: Mapping[str, Any] = (routing.get("tiers") or {}).get(tier) or {}

    order = list(targets.keys())
    if prefer and prefer in order and prefer in row:
        order.remove(prefer)
        order.insert(0, prefer)

    candidates: list[str] = []
    for name in order:
        target = targets.get(name)
        if not isinstance(target, Mapping):
            continue
        if target.get("pool") == "api" and not target.get("api_opt_in"):
            continue
        if not row.get(name):
            continue
        candidates.append(name)

    if exclude_harness:
        other = [n for n in candidates if targets[n].get("harness") != exclude_harness]
        excluded = [
            n for n in candidates if targets[n].get("harness") == exclude_harness
        ]
        candidates = other + excluded

    return candidates[0] if candidates else None


@contextlib.contextmanager
def explicit_cell_override(target: str, model: str, *, effort: str | None = None):
    """Temporarily point `WORKTRAIL_ROUTING_FILE` at a throwaway routing file
    declaring exactly one target/tier ("explicit") that reuses `target`'s
    already-declared harness/pool but serves `model`/`effort` instead of its
    configured tier cell -- so `spawn_agent(tier="explicit", ...)` resolves to
    exactly that cell for the duration of the `with` block. Never writes to
    the operator's real routing file. Shared by `conductor.compile`'s
    `--agent`/`--model` override and `LiveSpawn.__call__`'s `--model-map`/
    `--effort` override -- same mechanism, same guarantee.

    Raises `OperatorConfigError` when `target` does not already name a
    declared `routing.targets` entry: an explicit override reuses a real
    target's harness/pool, it never invents one.
    """
    routing = resolve_routing(load_policy(worktrail_home()))
    declared = (routing.get("targets") or {}).get(target)
    if not isinstance(declared, dict):
        raise OperatorConfigError(
            f"{target!r} does not name a declared routing.targets entry -- "
            "an explicit model/effort override requires the target already exist"
        )
    fd, path = tempfile.mkstemp(prefix="worktrail-explicit-cell-", suffix=".yaml")
    os.close(fd)
    explicit_file = Path(path)
    try:
        effort_line = f"      effort: {effort}\n" if effort else ""
        explicit_file.write_text(
            "targets:\n"
            f"  {target}:\n"
            f"    harness: {declared['harness']}\n"
            f"    pool: {declared.get('pool', 'subscription')}\n"
            "tiers:\n"
            "  explicit:\n"
            f"    {target}:\n"
            f"      model: {model}\n"
            f"{effort_line}",
            encoding="utf-8",
        )
        previous = os.environ.get(ROUTING_FILE_ENV)
        os.environ[ROUTING_FILE_ENV] = str(explicit_file)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(ROUTING_FILE_ENV, None)
            else:
                os.environ[ROUTING_FILE_ENV] = previous
    finally:
        explicit_file.unlink(missing_ok=True)


def spawn_agent(
    prompt: str,
    cwd: str | Path,
    *,
    tier: str,
    prefer: str | None = None,
    exclude_harness: str | None = None,
    timeout: int = 3600,
    retries: int = SPAWN_RETRIES_DEFAULT,
    session_limit_waits: int = SESSION_LIMIT_WAITS_DEFAULT,
    extra_args: Sequence[str] | None = None,
    resume_session_id: str | None = None,
    dispatch_id: str | None = None,
    log: Callable[[str], None] = lambda *_: None,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """Run one cold headless agent worker in *cwd*, retrying transient infra failures.

    Returns a SpawnResult(text, usage). `text` is the worker's final message (what
    the orchestrator parses for the report-back JSON). `usage` is a dict with
    input_tokens, output_tokens, cache_*_tokens, and total_cost_usd from the API,
    plus the diagnostic fields documented on `_parse_stream_json` (subtype,
    is_error, stop_reason, num_turns, permission_denials).

    The launch cell is resolved from `select_cell()` (design D3): `tier` names
    a `routing.tiers` row and `prefer`/`exclude_harness` are threaded straight
    through to it, walking that row across its declared targets in preference
    order. Every outcome below is recorded into `agent_capacity` (the
    machine-local cooldown cache `select_cell()` itself reads back in) keyed
    on the SERVED cell's `(target, model)`, never the requested tier/preference.
    Raises `runtime.selection.NoExecutionTarget` when every cell in the row is
    already capacity-gated, before any subprocess is spawned.

    A "session limit" response (a successful exit whose only output is the usage-cap
    notice) is NOT a task verdict: the worker never ran. It gates the served cell
    (`failure_class="rate_limit"`, `retry_after` the parsed reset time) and
    re-selects from the same row -- the fresh gate is exactly what excludes the
    served cell from that re-selection, so no separate "excluded cell" state is
    needed. When another cell is available this hop happens immediately and does
    not consume the infra-retry budget. When the row has nothing else to offer we
    instead sleep until the reported reset time and retry the SAME cell, up to
    `session_limit_waits` times (also without consuming the infra-retry budget);
    a subsequent success un-gates it again via the ordinary outcome recording
    below. After the wait budget is exhausted the limit message is handed back to
    the caller (parsed as a missing report-back -> task failure) rather than
    looping forever.

    An ordinary infra failure (non-zero exit / empty stdout) retries the SAME
    cell up to `retries` times first -- except an auth-class failure (401,
    consumed refresh token), which cannot clear itself and so gates the cell
    on the first attempt (`_is_auth_failure`) with no retry or backoff. Once
    that budget is exhausted the cell
    is gated `failure_class="infra"` (via `agent_capacity.classify_failure`)
    and, exactly like the session-limit path above, we re-select from the same
    row -- the fresh gate excludes the failed cell -- and continue this same
    attempt loop against whatever cell is served next, with its own fresh
    `retries` budget. Only when every cell in the row has exhausted its budget
    (re-selection raises `NoExecutionTarget`) do we give up and return the
    last raw output to the caller.

    Raises `subprocess.TimeoutExpired` on a wall-clock timeout.
    """
    routing = resolve_routing(load_policy(worktrail_home()))

    def _select() -> Cell:
        return select_cell(
            routing,
            tier,
            prefer=prefer,
            exclude_harness=exclude_harness,
            capacity=agent_capacity,
        )

    cell = _select()
    primary_target = _preflight_primary_target(routing, tier, prefer, exclude_harness)

    output_file = None
    if cell.harness == "codex":
        fd, output_file = tempfile.mkstemp(prefix="orch-codex-last-", suffix=".txt")
        os.close(fd)

    def cleanup_output_file() -> None:
        if output_file:
            try:
                os.unlink(output_file)
            except OSError:
                pass

    cmd = build_cmd(
        prompt,
        cell,
        # Callers derive extra_args for the requested primary cell. A
        # persisted capacity gate can already have moved select_cell() past
        # it before this first command is built, so do not leak
        # primary-only flags onto whatever cell actually got served (matches
        # the session-limit fallback hop below, which always drops them).
        extra_args=extra_args if cell.target == primary_target else None,
        resume_session_id=resume_session_id,
        output_last_message=output_file,
    )

    def _prepare_child_env(current_cell: Cell) -> tuple[dict[str, str], Path | None]:
        """Cell-specific child environment. Rebuilt on every session-limit hop
        so a codex-prepared env (CODEX_HOME) never leaks into an opencode hop
        and vice versa, and so the auth lane always matches the served cell's
        pool (`build_child_env`, design D6)."""
        env: dict[str, str] = {**os.environ, "CC_HEADLESS": "1"}
        oc_data_dir: Path | None = None
        if current_cell.harness == "codex":
            # Auth lane follows the cell's pool (design D6): an `api` cell
            # spawns in its own declared, pre-provisioned CODEX_HOME with the
            # parent's ChatGPT login NOT inherited -- CODEX_HOME isolation is
            # the one live-verified per-spawn auth selector for codex
            # (routing-target-selector task 3.6: `-c preferred_auth_method`
            # and OPENAI_API_KEY are both inert against a persisted login).
            codex_home_override: str | None = None
            inherit_auth = True
            if current_cell.pool == "api":
                auth = (
                    current_cell.auth if isinstance(current_cell.auth, Mapping) else {}
                )
                codex_home_override = auth.get("codex_home")
                if not codex_home_override:
                    raise OperatorConfigError(
                        f"routing target {current_cell.target!r} (harness codex, pool "
                        "api) has no auth.codex_home configured -- add `auth: "
                        "{codex_home: <path>}` to its routing.targets entry in "
                        f"{resolved_routing_file_path()}, naming a home provisioned "
                        "with `codex login --with-api-key`"
                    )
                if not (Path(codex_home_override).expanduser() / "auth.json").exists():
                    raise OperatorConfigError(
                        f"routing target {current_cell.target!r}'s auth.codex_home "
                        f"({codex_home_override}) has no auth.json -- provision it "
                        "once with `CODEX_HOME=<that path> codex login --with-api-key` "
                        "before spawning this 'api' pool"
                    )
                inherit_auth = False
            env, codex_home, automatic_home = prepare_codex_child_environment(
                codex_home_override, inherit_auth=inherit_auth
            )
            env["CC_HEADLESS"] = "1"
            if automatic_home:
                log(f"    using automatic Worktrail Codex home: {codex_home}")
            elif codex_home_override:
                log(f"    using declared codex api home: {codex_home}")
        elif current_cell.harness == "opencode":
            env, oc_data_dir = prepare_opencode_child_environment(cwd, env)
            log(
                f"    opencode state isolated at {oc_data_dir} "
                "(db + log; removed with the worktree)"
            )
        # Keep the environment marker on the prepared environment too.
        if "WORKTRAIL_SKILL_DISPATCH_DEPTH" in os.environ:
            env["WORKTRAIL_SKILL_DISPATCH_DEPTH"] = os.environ[
                "WORKTRAIL_SKILL_DISPATCH_DEPTH"
            ]
        if dispatch_id is not None:
            env["WORKTRAIL_DISPATCH_ID"] = dispatch_id
        else:
            env.pop("WORKTRAIL_DISPATCH_ID", None)
        return build_child_env(current_cell, env), oc_data_dir

    child_env, opencode_dir = _prepare_child_env(cell)

    def _persist_transcript(raw: str) -> None:
        """Best-effort raw stream-json JSONL dump, gated by $WORKTRAIL_KEEP_TRANSCRIPTS
        (a directory path). Diagnostic-only: never lets a write failure break a real
        spawn. Written outside `cwd` since that is the task's own git worktree."""
        transcript_dir = env_setting("WORKTRAIL_KEEP_TRANSCRIPTS")
        if not transcript_dir or not raw:
            return
        try:
            out_dir = Path(transcript_dir).expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            name = f"{Path(cwd).name}-{cell.harness}-{int(time.time())}-{os.getpid()}.jsonl"
            (out_dir / name).write_text(raw)
        except OSError as exc:
            log(f"    WORKTRAIL_KEEP_TRANSCRIPTS write failed (non-fatal): {exc}")

    def finish(
        raw: str, *, exhausted: bool = False, failure_class: str = ""
    ) -> SpawnResult:
        """Parse the final raw output and return the SpawnResult, attaching the
        opencode diagnostics the report-back contract needs when the worker
        produced nothing parseable: the session id, whether headless permission
        auto-rejections occurred, and where the isolated state/logs live."""
        _persist_transcript(raw)
        text, usage, tools_used, skills_used, sid = _parse_stream_json(raw)
        if cell.harness == "opencode":
            usage = dict(usage) if usage else {}
            denials = usage.get("permission_denials") or []
            usage.setdefault("permission_denials", denials)
            usage["opencode_data_dir"] = str(opencode_dir) if opencode_dir else ""
            log(
                f"    opencode diagnostics: session={sid or 'none'} "
                f"permission_denials={len(denials)} state={opencode_dir}"
            )
            if denials:
                first = denials[0]
                log(
                    f"    opencode auto-rejected {len(denials)} tool call(s) on a "
                    f"headless 'ask' (first: {first.get('tool') or '?'}) -- a missing "
                    f"report-back is explained by this; inspect {opencode_dir}/log "
                    f"and session {sid or '?'} in {opencode_dir}/opencode.db"
                )
        cleanup_output_file()
        return SpawnResult(
            text=text,
            usage=usage,
            tools_used=tools_used,
            skills_used=skills_used,
            paused_s=paused_s_total,
            session_id=sid,
            served_target=cell.target,
            served_model=cell.model,
            served_harness=cell.harness,
            exhausted=exhausted,
            failure_class=failure_class,
        )

    attempts = max(1, retries + 1)
    waits_left = max(0, session_limit_waits)
    last_raw = ""
    last_failure_class = ""
    attempt = 0
    paused_s_total = 0.0  # cumulative session-limit sleep seconds this spawn
    while attempt < attempts:
        attempt += 1
        try:
            proc = subprocess.run(  # TimeoutExpired propagates by design
                cmd,
                check=False,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
            )
        except BaseException:
            cleanup_output_file()
            raise
        last_raw = proc.stdout or ""

        # Session limit takes precedence over the success/infra split: the notice can
        # arrive on a clean exit (exit 0, non-empty output) yet carries no report-back.
        # Scan the PARSED result text, never the raw stream-json transcript: the
        # transcript echoes every file the worker read/edited, so a worker touching
        # session-limit code (spec-023 TASK-005) reproduces the notice wording in
        # last_raw on every successful run. _parse_stream_json falls back to the raw
        # text when the output isn't a stream, so a plain-text real notice still parses.
        reset_at = parse_session_limit_reset(_parse_stream_json(last_raw)[0])
        if reset_at is not None:
            served = cell
            agent_capacity.record(
                served.target,
                served.model,
                outcome="unavailable",
                failure_class="rate_limit",
                # reset_at is naive LOCAL wall-clock time (parse_session_limit_reset);
                # agent_capacity stores/compares as UTC, so a naive value here would
                # be silently misread as UTC and could gate the served cell for the
                # wrong instant (or appear already-expired), breaking the re-select
                # below's only mechanism for excluding it. astimezone() attaches the
                # system's real local offset without perturbing the wall-clock value.
                # Clamped to the re-probe cadence: rate_limit is the one failure
                # class whose retry_after comes from vendor text, so an unclamped
                # value would gate every LATER spawn (and every concurrent worker)
                # until the stated reset -- this spawn would wake early from its
                # capped park only to be refused by its own cache entry.
                retry_after=min(
                    reset_at,
                    datetime.datetime.now()  # noqa: DTZ005
                    + datetime.timedelta(seconds=SESSION_LIMIT_REPROBE_MAX_S),
                ).astimezone(),
            )
            cleanup_output_file()
            try:
                next_cell = _select()
            except NoExecutionTarget:
                next_cell = None
            if next_cell is not None:
                cell = next_cell
                output_file = None
                if cell.harness == "codex":
                    fd, output_file = tempfile.mkstemp(
                        prefix="orch-codex-last-", suffix=".txt"
                    )
                    os.close(fd)
                cmd = build_cmd(
                    prompt,
                    cell,
                    # extra_args/resume_session_id are specific to the FIRST
                    # cell this call resolved; neither carries across a
                    # session-limit hop.
                    extra_args=None,
                    resume_session_id=None,
                    output_last_message=output_file,
                )
                child_env, opencode_dir = _prepare_child_env(cell)
                log(
                    f"    session limit hit on {served.target}; switching to "
                    f"{cell.target} ({cell.harness}:{cell.model})"
                )
                attempt -= 1
                continue
            budget_left = max(0.0, SESSION_LIMIT_TOTAL_WAIT_MAX_S - paused_s_total)
            if waits_left > 0 and budget_left > 0:
                waits_left -= 1
                until_reset = (
                    max(0.0, (reset_at - datetime.datetime.now()).total_seconds()) + 5.0  # noqa: DTZ005
                )
                wait_s = min(until_reset, SESSION_LIMIT_REPROBE_MAX_S, budget_left)
                log(
                    f"    session limit hit; sleeping {wait_s:.0f}s (reset stated "
                    f"{reset_at:%H:%M}) then re-probing ({waits_left} probe(s) left)"
                )
                sleep(wait_s)
                paused_s_total += wait_s
                attempt -= 1  # a session-limit wait does not consume an infra attempt
                continue
            log(
                f"    session limit hit on {served.target}; no alternate cell and "
                "wait budget exhausted, giving up to caller"
            )
            return finish(last_raw, exhausted=True, failure_class="rate_limit")

        if cell.harness == "codex" and output_file:
            try:
                final_text = Path(output_file).read_text()
            except OSError:
                final_text = ""
            if final_text.strip():
                last_raw = final_text

        if not is_infra_failure(proc.returncode, last_raw):
            agent_capacity.record(cell.target, cell.model, outcome="available")
            return finish(last_raw)
        if attempt < attempts and _is_auth_failure(proc, last_raw):
            # An auth failure (401, consumed refresh token) is deterministic
            # until the operator re-authenticates: retrying the same cell only
            # burns the budget + backoff on every spawn of the run (brief
            # 20260901-175101). Treat the budget as exhausted -- gate now, hop.
            log(
                f"    auth failure on {cell.target} ({cell.harness}:{cell.model}); "
                "gating without retry -- clear with `worktrail-agent-capacity "
                "clear` once re-authenticated"
            )
            attempt = attempts
        if attempt >= attempts:
            failure_class = _opencode_unknown_error_failure_class(cell, last_raw)
            if failure_class is None:
                failure_class = agent_capacity.classify_failure(
                    proc.returncode, last_raw, proc.stderr or ""
                )
            # A provider that states its own reset time ("... try again at Aug
            # 8th, 2026 2:17 AM.") is authoritative: honour it verbatim and mark
            # the gate `provider`-derived so the probe cadence leaves it alone
            # until that instant. Without a stated reset the gate is only our
            # own guess from the failure class, so it stays `cooldown`-derived
            # and remains probe-eligible.
            #
            # A stated reset that is already in the PAST is not honoured: the
            # gate it would write is expired the moment it lands, so the
            # `_select()` below re-serves this very cell, fails the same way,
            # and loops forever (no attempt budget stops it -- a re-selected
            # cell gets a fresh one). Stale notice text and clock skew both
            # produce that shape, so fall back to the class cooldown, which
            # always gates forward.
            explicit_reset = agent_capacity.parse_explicit_reset(
                f"{last_raw}\n{proc.stderr or ''}"
            )
            if explicit_reset is not None and explicit_reset <= datetime.datetime.now(
                datetime.timezone.utc
            ):
                explicit_reset = None
            agent_capacity.record(
                cell.target,
                cell.model,
                outcome="unavailable",
                failure_class=failure_class,
                retry_after=explicit_reset or agent_capacity.retry_time(failure_class),
                reset_source="provider" if explicit_reset else "cooldown",
            )
            last_failure_class = failure_class

        if proc.returncode != 0:
            sys.stderr.write(
                f"[{cell.harness} worker exit {proc.returncode}] {(proc.stderr or '')[-400:]}\n"
            )
        if attempt >= attempts:
            # This cell's retry budget is exhausted. The `agent_capacity.record`
            # gate just recorded above excludes it from re-selection, so hop to
            # the next cell in the same row (same mechanism the session-limit
            # hop above uses) and give it its own fresh attempt budget. Only
            # when the row has nothing left do we give up and hand the last
            # raw output back to the caller.
            cleanup_output_file()
            try:
                next_cell = _select()
            except NoExecutionTarget:
                log(
                    f"    spawn still failing after {attempts} attempt(s) on "
                    f"{cell.target}; no alternate cell left in the row, giving up to caller"
                )
                return finish(
                    last_raw, exhausted=True, failure_class=last_failure_class
                )
            failed_cell = cell
            cell = next_cell
            output_file = None
            if cell.harness == "codex":
                fd, output_file = tempfile.mkstemp(
                    prefix="orch-codex-last-", suffix=".txt"
                )
                os.close(fd)
            cmd = build_cmd(
                prompt,
                cell,
                # extra_args/resume_session_id are specific to the FIRST
                # cell this call resolved; neither carries across an
                # infra-failure hop.
                extra_args=None,
                resume_session_id=None,
                output_last_message=output_file,
            )
            child_env, opencode_dir = _prepare_child_env(cell)
            log(
                f"    spawn still failing after {attempts} attempt(s) on "
                f"{failed_cell.target}; hopping to {cell.target} ({cell.harness}:{cell.model})"
            )
            attempt = 0
            continue
        backoff = min(30.0, 5.0 * attempt)
        log(
            f"    spawn infra failure (attempt {attempt}/{attempts}); retrying in {backoff:.0f}s"
        )
        sleep(backoff)
    return finish(last_raw, exhausted=True, failure_class=last_failure_class)


def spawn_claude_p(
    prompt: str,
    cwd: str | Path,
    *,
    tier: str,
    prefer: str | None = None,
    exclude_harness: str | None = None,
    timeout: int = 3600,
    retries: int = SPAWN_RETRIES_DEFAULT,
    session_limit_waits: int = SESSION_LIMIT_WAITS_DEFAULT,
    extra_args: Sequence[str] | None = None,
    resume_session_id: str | None = None,
    dispatch_id: str | None = None,
    log: Callable[[str], None] = lambda *_: None,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """Thin wrapper around `spawn_agent` -- a distinctly-named entry point for
    claude task workers. The cell it actually spawns (harness/model/effort/
    pool/auth) is resolved the same way as any other `spawn_agent` call, from
    `select_cell(tier, prefer, exclude_harness)`; nothing here pins it to the
    claude harness beyond whatever `tier`/`prefer` the caller supplies.
    """
    return spawn_agent(
        prompt,
        cwd,
        tier=tier,
        prefer=prefer,
        exclude_harness=exclude_harness,
        timeout=timeout,
        retries=retries,
        session_limit_waits=session_limit_waits,
        extra_args=extra_args,
        resume_session_id=resume_session_id,
        dispatch_id=dispatch_id,
        log=log,
        sleep=sleep,
    )

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

Switching from `--output-format json` to `--output-format stream-json` is
token-neutral: the flag only changes how the worker serialises its result to stdout
(JSONL instead of a single JSON envelope). The model's `usage` is identical; the
stream is parsed by the Python parent, not fed to any LLM.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

import yaml

from . import agent_capacity
from ..router.skill_dispatch import prepare_codex_child_environment


class SpawnResult(NamedTuple):
    text: str
    usage: Dict
    tools_used: List[str] = []
    skills_used: List[str] = []
    # Cumulative seconds the spawn spent sleeping on session-limit waits. The
    # caller subtracts this from the run's wall-clock elapsed time so a rate-limit
    # pause never consumes the --run-budget (without it a 4h reset window would
    # count against a 4h budget even though no work was happening).
    paused_s: float = 0.0
    # Session ID returned by the result event; populated for every live spawn.
    # Callers can pass this as resume_session_id to build_cmd to fork from this session.
    session_id: str = ""


# verified from `claude --help`: bypass perms so a headless worker can edit/commit
PERM_FLAGS = ["--permission-mode", "bypassPermissions"]

# Request stream-json output: JSONL per turn, final result event carries usage +
# cost; assistant events carry tool_use blocks for tool/skill instrumentation.
JSON_OUTPUT_FLAGS = ["--output-format", "stream-json", "--verbose"]

# Retries for a transient (infra) spawn failure, on top of the first attempt.
SPAWN_RETRIES_DEFAULT = int(os.environ.get("ORCH_SPAWN_RETRIES", "2"))

# How many times a single spawn will wait out a "session limit" reset before
# giving the limit message back to the caller as a task failure. Bounded so a
# persistently-rate-limited account can never loop forever.
SESSION_LIMIT_WAITS_DEFAULT = int(os.environ.get("ORCH_SESSION_LIMIT_WAITS", "3"))

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
    text: Optional[str], now: Optional[datetime.datetime] = None
) -> Optional[datetime.datetime]:
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
    now = now or datetime.datetime.now()
    clock = datetime.datetime.strptime(m.group(1).replace(" ", "").lower(), "%I:%M%p").time()
    reset = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    if reset <= now:
        reset += datetime.timedelta(days=1)
    return reset


def _parse_stream_json(raw: str) -> tuple[str, Dict, List[str], List[str], str]:
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
                        no Skill-tool equivalent).
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
    usage: Dict = {}
    tools_seen: set = set()
    skills_seen: set = set()
    session_id: str = ""
    opencode_usage_seen = False
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

        elif event_type == "step_finish":
            part = event.get("part") or {}
            tokens = part.get("tokens") or {}
            if tokens:
                opencode_usage_seen = True
                cache = tokens.get("cache") or {}
                opencode_totals["input_tokens"] += int(tokens.get("input", 0) or 0)
                opencode_totals["output_tokens"] += int(tokens.get("output", 0) or 0)
                opencode_totals["cache_creation_input_tokens"] += int(cache.get("write", 0) or 0)
                opencode_totals["cache_read_input_tokens"] += int(cache.get("read", 0) or 0)
                opencode_totals["total_cost_usd"] += float(part.get("cost", 0.0) or 0.0)

    if opencode_usage_seen and not usage:
        usage = {
            **opencode_totals,
            # Diagnostic-only fields kept for shape parity with the claude usage
            # dict (see the module docstring) -- opencode's step_finish events
            # carry no equivalent signal, so these stay at their empty defaults.
            "subtype": "",
            "is_error": False,
            "stop_reason": "",
            "num_turns": 0,
            "permission_denials": [],
        }

    return result_text, usage, sorted(tools_seen), sorted(skills_seen), session_id


def _opencode_error_event(stdout: Optional[str]) -> Optional[Dict]:
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


def is_infra_failure(returncode: int, stdout: Optional[str]) -> bool:
    """A spawn that exited non-zero, produced no output, or (opencode) reported a
    top-level error event -- a transient blip, not a task verdict. (A real task
    failure is exit 0 + a `status:failed` report.)"""
    if returncode != 0 or not (stdout or "").strip():
        return True
    return _opencode_error_event(stdout) is not None


# Last-resort fallbacks only -- never the sole source of truth. Codex and
# opencode ship new model generations under new names on their own schedule
# (confirmed live 2026-08-03: DEFAULT_CODEX_MODEL had drifted to a
# discontinued-looking "gpt-5.4-mini" while the operator's actual codex CLI
# listed "gpt-5.6-sol" as current), and Claude's own "sonnet"/"opus"/"haiku"
# aliases are the only one of the three that don't go stale this way.
# default_model_for_agent() resolves, in order: an explicit ORCH_*_MODEL env
# var (an intentional per-invocation choice) > the operator-maintained
# ~/.go/model-defaults.yaml (kept current without a code change) > these
# constants (only reached when neither is set -- e.g. a fresh machine with no
# config file yet).
DEFAULT_CLAUDE_MODEL = "sonnet"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_OPENCODE_MODEL = "deepseek/deepseek-v4-flash"

SUPPORTED_AGENTS = {"claude", "codex", "opencode"}

MODEL_DEFAULTS_FILE_ENV = "GO_MODEL_DEFAULTS_FILE"
DEFAULT_MODEL_DEFAULTS_FILE = "~/.go/model-defaults.yaml"


def _load_model_defaults() -> Dict[str, str]:
    """`{agent: model}` from the operator-maintained model-defaults file, or
    `{}` on anything short of a valid mapping -- malformed/missing/unreadable
    all degrade the same way agent_capacity.py's own cache loads do: never
    raise, never block a spawn over a config-file problem.
    """
    path = Path(os.environ.get(MODEL_DEFAULTS_FILE_ENV, DEFAULT_MODEL_DEFAULTS_FILE)).expanduser()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def default_model_for_agent(agent: str) -> str:
    defaults = _load_model_defaults()
    if agent == "codex":
        return os.environ.get("ORCH_CODEX_MODEL") or defaults.get("codex") or DEFAULT_CODEX_MODEL
    if agent == "opencode":
        return (os.environ.get("ORCH_OPENCODE_MODEL")
                or defaults.get("opencode") or DEFAULT_OPENCODE_MODEL)
    return defaults.get("claude") or DEFAULT_CLAUDE_MODEL


def _with_default_setting_sources(
    agent: str, extra_args: Optional[Sequence[str]]
) -> List[str]:
    """Default `--setting-sources project,local` onto every `claude` spawn.

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
    if agent == "claude" and "--setting-sources" not in args:
        return ["--setting-sources", "project,local", *args]
    return args


def build_cmd(
    prompt: str,
    *,
    agent: str = "claude",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    extra_args: Optional[Sequence[str]] = None,
    resume_session_id: Optional[str] = None,
    output_last_message: Optional[str] = None,
) -> List[str]:
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    extra_args = _with_default_setting_sources(agent, extra_args)

    if agent == "claude":
        cmd = ["claude", "-p", prompt, *PERM_FLAGS, *JSON_OUTPUT_FLAGS]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
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


def _normalize_fallback_chain(
    agent: str, fallback_agent: "Optional[str | Sequence[str]]"
) -> List[str]:
    """Turn `fallback_agent` into an ordered list of hop names.

    Accepts the legacy single-agent shape (`str` or `None`) or an ordered
    sequence of agent names (a fallback chain), preserving list order. Each
    hop must be a `SUPPORTED_AGENTS` member (else `ValueError`, matching the
    legacy single-fallback check); a hop equal to `agent` is dropped rather
    than raising, mirroring the legacy same-as-primary behavior. Repeated hops
    are deduplicated while preserving the first-seen order so a spawn never
    loops back to the same provider.
    """
    if fallback_agent is None:
        hops: List[str] = []
    elif isinstance(fallback_agent, str):
        hops = [fallback_agent]
    else:
        hops = list(fallback_agent)

    chain: List[str] = []
    seen = {agent}
    for hop in hops:
        if hop not in SUPPORTED_AGENTS:
            raise ValueError(f"unsupported fallback agent: {hop}")
        if hop in seen:
            continue
        seen.add(hop)
        chain.append(hop)
    return chain


def spawn_agent(
    prompt: str,
    cwd: "str | Path",
    *,
    agent: str = "claude",
    model: Optional[str] = None,
    effort: Optional[str] = None,
    fallback_agent: "Optional[str | Sequence[str]]" = None,
    timeout: int = 3600,
    retries: int = SPAWN_RETRIES_DEFAULT,
    session_limit_waits: int = SESSION_LIMIT_WAITS_DEFAULT,
    extra_args: Optional[Sequence[str]] = None,
    resume_session_id: Optional[str] = None,
    log: Callable[[str], None] = lambda *_: None,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """Run one cold headless agent worker in *cwd*, retrying transient infra failures.

    Returns a SpawnResult(text, usage). `text` is the worker's final message (what
    the orchestrator parses for the report-back JSON). `usage` is a dict with
    input_tokens, output_tokens, cache_*_tokens, and total_cost_usd from the API,
    plus the diagnostic fields documented on `_parse_stream_json` (subtype,
    is_error, stop_reason, num_turns, permission_denials).

    `fallback_agent` accepts either the legacy single-agent shape (`str` or
    `None`) or an ordered sequence of agent names -- a fallback chain. Each
    hop's persisted capacity gate (`agent_capacity.check`) is walked in list
    order; the first ungated hop is selected as the running agent. This is a
    cache-only check, not build-time credential validation -- a hop with no
    cache entry (never tried, or genuinely broken) is treated as available and
    is only discovered/gated through the existing failure-classification path
    below the first time it is actually spawned. Exhausting every hop raises
    `agent_capacity.AllProvidersUnavailable` listing every configured
    provider; a single agent with no fallback re-raises the underlying
    `ProviderUnavailable` unchanged.

    A "session limit" response (a successful exit whose only output is the usage-cap
    notice) is NOT a task verdict: the worker never ran. If a fallback chain is
    configured, we advance through it in order without reusing a hop and without
    pre-validating any entry. Otherwise we sleep until the reported reset time and
    retry, up to `session_limit_waits` times, WITHOUT consuming the infra-retry
    budget. After that many waits the limit message is handed back to the caller
    (parsed as a missing report-back -> task failure) rather than looping forever.

    Raises `subprocess.TimeoutExpired` on a wall-clock timeout.
    """
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported agent: {agent}")
    model = model or default_model_for_agent(agent)
    fallback_chain = _normalize_fallback_chain(agent, fallback_agent)

    configured = [(agent, model)] + [
        (hop, default_model_for_agent(hop)) for hop in fallback_chain
    ]
    agent_capacity.configure(configured)

    # A persisted gate prevents repeated launches when a provider is known to be
    # unavailable. Each hop is tried in list order, at most once; the first
    # ungated hop wins and becomes the running agent for the rest of this call.
    selected = None
    last_exc: Optional[agent_capacity.ProviderUnavailable] = None
    for idx, (candidate_agent, candidate_model) in enumerate(configured):
        try:
            agent_capacity.check(candidate_agent, candidate_model)
        except agent_capacity.ProviderUnavailable as exc:
            last_exc = exc
            continue
        selected = idx
        break
    if selected is None:
        if len(configured) == 1:
            raise last_exc
        states = agent_capacity.load().get("providers", {})
        raise agent_capacity.AllProvidersUnavailable(
            providers=[agent_capacity.provider_key(a, m) for a, m in configured],
            states=states,
        ) from last_exc
    agent, model = configured[selected]
    remaining_chain = [hop for hop, _ in configured[selected + 1:]]

    output_file = None
    if agent == "codex":
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
        agent=agent,
        model=model,
        effort=effort,
        extra_args=extra_args,
        resume_session_id=resume_session_id,
        output_last_message=output_file,
    )
    child_env = {**os.environ, "CC_HEADLESS": "1"}
    if agent == "codex":
        child_env, codex_home, automatic_home = prepare_codex_child_environment()
        child_env["CC_HEADLESS"] = "1"
        if automatic_home:
            log(f"    using automatic Worktrail Codex home: {codex_home}")
    # Keep the environment marker on the prepared Codex environment too.
    if "WORKTRAIL_SKILL_DISPATCH_DEPTH" in os.environ:
        child_env["WORKTRAIL_SKILL_DISPATCH_DEPTH"] = os.environ[
            "WORKTRAIL_SKILL_DISPATCH_DEPTH"
        ]
    attempts = max(1, retries + 1)
    waits_left = max(0, session_limit_waits)
    last_raw = ""
    attempt = 0
    paused_s_total = 0.0  # cumulative session-limit sleep seconds this spawn
    while attempt < attempts:
        attempt += 1
        try:
            proc = subprocess.run(  # TimeoutExpired propagates by design
                cmd,
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
        if reset_at is not None and remaining_chain:
            previous_agent = agent
            cleanup_output_file()
            agent = remaining_chain.pop(0)
            model = default_model_for_agent(agent)
            output_file = None
            if agent == "codex":
                fd, output_file = tempfile.mkstemp(prefix="orch-codex-last-", suffix=".txt")
                os.close(fd)
            cmd = build_cmd(
                prompt,
                agent=agent,
                model=model,
                # effort is a CLI-agnostic tier semantic (build_cmd translates it
                # per agent) so it carries across the fallback boundary; unlike
                # extra_args, which are primary-agent-specific and cannot.
                effort=effort,
                extra_args=None,
                resume_session_id=None,
                output_last_message=output_file,
            )
            log(f"    session limit hit on {previous_agent}; switching once to {agent}")
            attempt -= 1
            continue
        if reset_at is not None and waits_left > 0:
            waits_left -= 1
            wait_s = max(0.0, (reset_at - datetime.datetime.now()).total_seconds()) + 5.0
            log(
                f"    session limit hit; sleeping {wait_s:.0f}s until "
                f"{reset_at:%H:%M} then retrying ({waits_left} wait(s) left)"
            )
            sleep(wait_s)
            paused_s_total += wait_s
            attempt -= 1  # a session-limit wait does not consume an infra attempt
            continue

        if agent == "codex" and output_file:
            try:
                final_text = Path(output_file).read_text()
            except OSError:
                final_text = ""
            if final_text.strip():
                last_raw = final_text

        if reset_at is not None:
            agent_capacity.record(
                agent,
                model,
                outcome="unavailable",
                failure_class="rate_limit",
                retry_after=reset_at,
            )
        elif not is_infra_failure(proc.returncode, last_raw):
            agent_capacity.record(agent, model, outcome="available")
        elif attempt >= attempts:
            failure_class = agent_capacity.classify_failure(
                proc.returncode, last_raw, proc.stderr or ""
            )
            agent_capacity.record(
                agent,
                model,
                outcome="unavailable",
                failure_class=failure_class,
                retry_after=agent_capacity.retry_time(failure_class),
            )

        if not is_infra_failure(proc.returncode, last_raw):
            text, usage, tools_used, skills_used, sid = _parse_stream_json(last_raw)
            cleanup_output_file()
            return SpawnResult(
                text=text, usage=usage, tools_used=tools_used, skills_used=skills_used,
                paused_s=paused_s_total, session_id=sid,
            )
        if proc.returncode != 0:
            sys.stderr.write(f"[{agent} worker exit {proc.returncode}] {(proc.stderr or '')[-400:]}\n")
        if attempt >= attempts:
            log(f"    spawn still failing after {attempts} attempt(s); giving up to caller")
            text, usage, tools_used, skills_used, sid = _parse_stream_json(last_raw)
            cleanup_output_file()
            return SpawnResult(
                text=text, usage=usage, tools_used=tools_used, skills_used=skills_used,
                paused_s=paused_s_total, session_id=sid,
            )
        backoff = min(30.0, 5.0 * attempt)
        log(f"    spawn infra failure (attempt {attempt}/{attempts}); retrying in {backoff:.0f}s")
        sleep(backoff)
    text, usage, tools_used, skills_used, sid = _parse_stream_json(last_raw)
    cleanup_output_file()
    return SpawnResult(
        text=text, usage=usage, tools_used=tools_used, skills_used=skills_used,
        paused_s=paused_s_total, session_id=sid,
    )


def spawn_claude_p(
    prompt: str,
    cwd: "str | Path",
    *,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    timeout: int = 3600,
    retries: int = SPAWN_RETRIES_DEFAULT,
    session_limit_waits: int = SESSION_LIMIT_WAITS_DEFAULT,
    extra_args: Optional[Sequence[str]] = None,
    resume_session_id: Optional[str] = None,
    fallback_agent: "Optional[str | Sequence[str]]" = None,
    log: Callable[[str], None] = lambda *_: None,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """Thin `agent="claude"` wrapper around `spawn_agent` -- the claude-specific
    transport LiveSpawn's claude task workers call. `fallback_agent` (single
    agent or an ordered chain) is threaded straight through to `spawn_agent`,
    which already implements the full session-limit hop; before this
    parameter existed, LiveSpawn's claude-primary runs had no fallback
    machinery at all (`--fallback-agent`/`--agent claude` silently did
    nothing), while a non-claude primary agent got the hop for free via this
    same underlying `spawn_agent` call (see brief
    20260723-111700-claude-primary-fallback-inert).
    """
    return spawn_agent(
        prompt,
        cwd,
        agent="claude",
        model=model,
        effort=effort,
        fallback_agent=fallback_agent,
        timeout=timeout,
        retries=retries,
        session_limit_waits=session_limit_waits,
        extra_args=extra_args,
        resume_session_id=resume_session_id,
        log=log,
        sleep=sleep,
    )

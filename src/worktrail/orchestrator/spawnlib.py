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
from ..shared.homedir import env_setting, worktrail_home


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
    usage: Dict = {}
    tools_seen: set = set()
    skills_seen: set = set()
    session_id: str = ""
    opencode_usage_seen = False
    opencode_denials: List[Dict] = []
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
                opencode_totals["cache_creation_input_tokens"] += int(cache.get("write", 0) or 0)
                opencode_totals["cache_read_input_tokens"] += int(cache.get("read", 0) or 0)
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
# model-defaults.yaml under worktrail_home() (kept current without a code
# change) > these
# constants (only reached when neither is set -- e.g. a fresh machine with no
# config file yet).
DEFAULT_CLAUDE_MODEL = "sonnet"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
# The bare "deepseek/" provider needs its own credential; the OpenCode Zen
# gateway serves the same family under "opencode/" against the standard Zen
# login (verified live 2026-08-13: bare-prefix spawns fail with an opencode
# "Unexpected server error" at zero tokens on a machine with only Zen auth).
DEFAULT_OPENCODE_MODEL = "opencode/deepseek-v4-flash-free"

SUPPORTED_AGENTS = {"claude", "codex", "opencode"}

MODEL_DEFAULTS_FILE_ENV = "WORKTRAIL_MODEL_DEFAULTS_FILE"


def _model_defaults_file() -> Path:
    """`$WORKTRAIL_MODEL_DEFAULTS_FILE` if set, else `worktrail_home()/model-defaults.yaml`."""
    override = env_setting(MODEL_DEFAULTS_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "model-defaults.yaml"


def _load_model_defaults() -> Dict[str, str]:
    """`{agent: model}` from the operator-maintained model-defaults file, or
    `{}` on anything short of a valid mapping -- malformed/missing/unreadable
    all degrade the same way agent_capacity.py's own cache loads do: never
    raise, never block a spawn over a config-file problem.
    """
    path = _model_defaults_file()
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
        return defaults.get("codex") or DEFAULT_CODEX_MODEL
    if agent == "opencode":
        return defaults.get("opencode") or DEFAULT_OPENCODE_MODEL
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


def opencode_state_root(cwd: "str | Path") -> Path:
    """Per-worktree opencode scratch, deleted with the worktree on teardown."""
    return Path(cwd) / ".worktrail" / "opencode"


def opencode_data_dir(cwd: "str | Path") -> Path:
    """The isolated opencode data dir (opencode.db, log/, repos/) for *cwd*."""
    return opencode_state_root(cwd) / "xdg" / "opencode"


def _parent_opencode_data_dir(env: Dict[str, str]) -> Path:
    """The invoking user's real opencode data dir (credential source)."""
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


# The real subprocess.run, captured at import: the hermetic spawn tests script
# `spawnlib.subprocess.run` with fake worker outcomes, and the git probe below
# must never consume one of those scripted outcomes (or feed its own output
# into them).
_REAL_SUBPROCESS_RUN = subprocess.run


def _git_common_dir(cwd: "str | Path") -> Optional[str]:
    """Absolute git common dir for *cwd* (the shared .git a linked worktree's
    objects live in), or None when cwd is not a git checkout."""
    try:
        proc = _REAL_SUBPROCESS_RUN(
            ["git", "-C", str(cwd), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _opencode_permission_config(
    cwd: "str | Path", existing_content: Optional[str]
) -> Dict:
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
    roots: List[str] = []
    for candidate in (str(cwd), str(Path(cwd).resolve())):
        if candidate not in roots:
            roots.append(candidate)
    common = _git_common_dir(cwd)
    if common and common not in roots:
        roots.append(common)
    external: Dict[str, str] = {}
    for root in roots:
        external[root] = "allow"
        external[root.rstrip("/") + "/**"] = "allow"
    permission: Dict = {
        # Parity with claude's --permission-mode bypassPermissions and codex's
        # -s danger-full-access: tool USE is granted, while file access outside
        # the roots above is still auto-rejected via external_directory.
        "read": "allow", "edit": "allow", "glob": "allow", "grep": "allow",
        "bash": "allow", "webfetch": "allow", "websearch": "allow",
        "task": "allow", "skill": "allow", "lsp": "allow",
        "external_directory": external,
    }
    config: Dict = {}
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
    cwd: "str | Path", base_env: Optional[Dict[str, str]] = None
) -> "tuple[Dict[str, str], Path]":
    """Prepare an isolated, provider-preserving, prompt-free environment for a
    headless opencode worker running in *cwd*.

    Returns `(child_env, data_dir)`: `child_env` carries the per-worktree
    XDG_DATA_HOME override plus the scoped OPENCODE_CONFIG_CONTENT permission
    grants; `data_dir` is where this worker's opencode.db and log/ land
    (inspectable after a failure, deleted with the worktree on teardown).
    """
    env: Dict[str, str] = dict(base_env if base_env is not None else os.environ)
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
        # Callers derive extra_args for the requested primary CLI. A persisted
        # capacity gate can select a different CLI before this first command is
        # built, so do not leak primary-only flags across that boundary. This
        # matches the session-limit fallback hop below.
        extra_args=extra_args if selected == 0 else None,
        resume_session_id=resume_session_id,
        output_last_message=output_file,
    )

    def build_child_env(current_agent: str) -> "tuple[Dict[str, str], Optional[Path]]":
        """Agent-specific child environment. Rebuilt on every fallback hop
        switch so a codex-prepared env (CODEX_HOME) never leaks into an
        opencode hop and vice versa."""
        env: Dict[str, str] = {**os.environ, "CC_HEADLESS": "1"}
        oc_data_dir: Optional[Path] = None
        if current_agent == "codex":
            env, codex_home, automatic_home = prepare_codex_child_environment()
            env["CC_HEADLESS"] = "1"
            if automatic_home:
                log(f"    using automatic Worktrail Codex home: {codex_home}")
        elif current_agent == "opencode":
            env, oc_data_dir = prepare_opencode_child_environment(cwd, env)
            log(f"    opencode state isolated at {oc_data_dir} "
                "(db + log; removed with the worktree)")
        # Keep the environment marker on the prepared environment too.
        if "WORKTRAIL_SKILL_DISPATCH_DEPTH" in os.environ:
            env["WORKTRAIL_SKILL_DISPATCH_DEPTH"] = os.environ[
                "WORKTRAIL_SKILL_DISPATCH_DEPTH"
            ]
        return env, oc_data_dir

    child_env, opencode_dir = build_child_env(agent)

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
            name = f"{Path(cwd).name}-{agent}-{int(time.time())}-{os.getpid()}.jsonl"
            (out_dir / name).write_text(raw)
        except OSError as exc:
            log(f"    WORKTRAIL_KEEP_TRANSCRIPTS write failed (non-fatal): {exc}")

    def finish(raw: str) -> SpawnResult:
        """Parse the final raw output and return the SpawnResult, attaching the
        opencode diagnostics the report-back contract needs when the worker
        produced nothing parseable: the session id, whether headless permission
        auto-rejections occurred, and where the isolated state/logs live."""
        _persist_transcript(raw)
        text, usage, tools_used, skills_used, sid = _parse_stream_json(raw)
        if agent == "opencode":
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
            text=text, usage=usage, tools_used=tools_used, skills_used=skills_used,
            paused_s=paused_s_total, session_id=sid,
        )
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
            child_env, opencode_dir = build_child_env(agent)
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
            return finish(last_raw)
        if proc.returncode != 0:
            sys.stderr.write(f"[{agent} worker exit {proc.returncode}] {(proc.stderr or '')[-400:]}\n")
        if attempt >= attempts:
            log(f"    spawn still failing after {attempts} attempt(s); giving up to caller")
            return finish(last_raw)
        backoff = min(30.0, 5.0 * attempt)
        log(f"    spawn infra failure (attempt {attempt}/{attempts}); retrying in {backoff:.0f}s")
        sleep(backoff)
    return finish(last_raw)


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

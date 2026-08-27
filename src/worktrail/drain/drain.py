#!/usr/bin/env python3
"""Queue-drain driver: repeatedly launch fresh-context one-shots of the
worktrail-go skill's auto mode until the work queue is empty or a stop
condition fires.

Each iteration spawns ONE headless agent CLI process (`claude -p
"worktrail-go auto"` by default), waits for it to exit, classifies the
outcome from the newest run record under the runs dir, then re-checks the
queue. A fresh process per iteration means fresh context by construction —
nothing accumulates.

The prompt is "worktrail-go auto", NOT "/go auto" -- worktrail-go has never
been a registered slash command (only a skill; confirmed live 2026-08-03: no
commands/go.md exists anywhere in this plugin, and plugin.json has no
top-level "commands" key at all). Codex and opencode apparently tolerate an
unrecognized leading "/" and fall through to normal skill-trigger matching
regardless; Claude Code's own CLI does not -- `claude -p "/go auto"` fails
immediately with "Unknown command: /go. Did you mean /goal?" and exits 0,
which this module's own no_pick classification then reads as "nothing was
eligible to claim" rather than "the one-shot never even started." Every
drain iteration that used agent=claude was silently a no-op for this reason
until this fix, and no_pick's own stop condition means no fallback agent ever
got a chance to run either.

Stop conditions (each printed, never silent):
  queue_empty            no ready briefs before an iteration
  no_pick                an iteration claimed nothing and produced no run record
  capacity_gated         every configured agent (primary + --fallback-agent
                         chain) is capacity-gated
   circuit_breaker        N consecutive failed iterations (default 2)
   max_items              iteration ceiling reached
   budget_exhausted       wall-clock budget reached
   lock_held              another drain already owns this queue
   pending_user_decision  an iteration yielded a structured pending-decision
                          handoff (run-record final_status or unresolved
                          `pending_decisions` audit entries)

`completed_awaiting_human_approval` NOTES the pending PR and continues — that
is a gate working, not a stall.

A `pending_user_decision` iteration is a fail-closed, recoverable handoff,
never a failure: the run asked a human a question it must not answer itself
(neither guesses), so drain records the exact decision id(s) from the run
record's `pending_decisions` audit trail and stops instead of re-spawning
into the same unanswered guard (neither spins). The iteration does not count
toward `circuit_breaker`. Recovery is attended: present/answer the decision
(`worktrail-decision answer <id> --answer ...`), then resume through the
exact id (`worktrail-skill-dispatch --resume-decision <id>`); only an
explicit success state outranks leftover audit entries, so a completed run
is never wedged by stale bookkeeping.

An iteration killed by the iteration timeout (exit 124) whose run record
already carries a PR is classified `timeout_after_pr`, not `failed`: the
substantive work succeeded and only post-PR wrap-up was still running when
the timeout fired, so it does not count toward `circuit_breaker`. A timeout
with no PR remains a plain `failed` iteration.

A record-less iteration whose captured output classifies as an account-level
failure (agent_capacity.classify_failure: auth/billing -- the latter now also
covers "usage limit"/"session limit" wording) is `blocked`, not `failed`: it
does not count toward `circuit_breaker`, and it persists a capacity gate
(agent_capacity cache, bare-agent-keyed) with a retry_after parsed from the
notice itself when present, else the class's generic cooldown. Every
iteration re-selects the first non-gated agent from `[--agent] +
--fallback-agent...` in that fixed priority order (see
select_available_agent) -- a gated primary is skipped in favor of a fallback
automatically, and picked back up automatically once its gate expires. Only
`capacity_gated` (every configured agent gated) stops the drain.

A PR a one-shot creates itself is not reachable by the Claude Code PreToolUse
label-enforcement hook (Codex/OpenCode have no such mechanism at all); see
`ensure_pr_risk_label` for the minimal, safe correction this module applies
so a missing go:risk-* label doesn't stall an otherwise-eligible PR.

Permission posture is explicit: no permission-bypass flag is ever added by
default; pass each one via a repeated --permission-arg.

Pass --transcript-dir to persist each iteration's raw one-shot stdout/stderr
(bounded to the most recent 50 files) -- omitted by default, since a no_pick
or a clean success leaves no other trace of what the one-shot actually did,
and reconstructing that after the fact otherwise means a fresh manual
reproduction (confirmed live 2026-08-03).

Before and after each drain pass, `--repos-root` (default ~/projects; a nonexistent path
is a no-op sweep) is swept via quarantine_selfcheck.check_repo()['resumable'] for
QUARANTINED groups whose quarantine_reason is budget_exhausted -- the group never failed,
it just never got a chance to run before the run's --run-budget expired, so it is safely
resumable with a plain `worktrail-live full-real` re-run (no --fresh, resume=True by
default). These specs are invisible to `worktrail-go auto` itself (auto mode only claims
work-queue briefs, never "Ready to implement" specs), so without this sweep they sit until
a human notices the dashboard's "Resumable quarantines" line and re-runs full-real by hand.

Usage:
  drain.py [--max-items N] [--budget-minutes M] [--agent claude|codex|opencode]
           [--fallback-agent AGENT]... [--transcript-dir DIR]
           [--agent-cmd TEMPLATE] [--permission-arg FLAG]...
           [--consecutive-failures N] [--iteration-timeout-minutes M]
           [--max-workers N] [--queue-dir DIR] [--runs-dir DIR] [--repos-root DIR]
           [--capacity-cache PATH] [--lock-file PATH] [--stuck-threshold N]
           [--dry-run] [--json]

Exit codes: 0 = drained/stopped cleanly with a reported reason; 2 = refused to
start (lock held, bad args, missing queue dir).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import stuck_remediation
from ..orchestrator import agent_capacity
from ..runtime.routing_source import routing_candidates
from ..runtime.selection import NoExecutionTarget, select_execution_target
from ..orchestrator.integrate import _refresh_pr_labels
from ..router import branch_selfcheck, dashboard, quarantine_selfcheck
from ..router.policy import load_policy
from ..router.policy_selfcheck import discover_repo_names
from ..router.poll_run import unresolved_decision_ids as _poll_unresolved_decision_ids
from ..router.pr_labels import ensure_pr_risk_label
from ..shared.homedir import worktrail_home
from ..shared.operator_config import (
    OperatorConfigError,
    config_path as operator_config_path,
    drain_config as operator_drain_config,
)
from ..taskformats.devkit.schema import set_status_completed
from ..taskformats.openspec.schema import STATUS_COMPLETED, parse_tasks_md
from ..workqueue import decisions as decisions_mod
from ..workqueue import seed_backlog as seed_backlog_mod
from ..workqueue.invocation import WORK_QUEUE_PY, build_work_queue_argv

PROMPT = (
    "worktrail-go auto. This is an unattended drain one-shot: keep this process "
    "in the foreground until the run record reaches a real final_status; do not "
    "return after a bounded background-dispatch poll or while PR checks are pending."
)

# Failure classes (see agent_capacity.classify_failure) that mean the account
# itself is blocked -- retrying the same agent is pointless until the cache's
# retry_after passes. Other classes (sandbox/startup/transport) stay plain
# "failed" iterations so the existing circuit breaker still catches genuine
# environment/code problems rather than gating the whole agent.
CAPACITY_FAILURE_CLASSES = frozenset({"auth", "billing"})

SUPPORTED_AGENTS = ("claude", "codex", "opencode")

# Mirrors specs-parallel-orchestrator/scripts/spawnlib.py build_cmd() shapes,
# minus JSON-output flags (outcomes are read from run records, not stdout).
BASE_CMDS: Dict[str, List[str]] = {
    "claude": ["claude", "-p"],
    "opencode": ["opencode", "run"],
    "codex": ["codex", "exec", "-s", "danger-full-access"],
}

AGENT_RUNTIME_EXECUTABLES: Dict[str, tuple[str, ...]] = {
    "claude": ("claude", "node"),
    "codex": ("codex", "node"),
    "opencode": ("opencode", "node"),
}

SUCCESS_STATES = frozenset({
    "completed_and_merged",
    "completed_pr_open",
    "completed_awaiting_human_approval",
    "planned_ready_for_implementation",
    "investigation_complete",
})
BLOCKED_STATES = frozenset({
    "blocked_external_dependency",
    "blocked_product_decision",
    "blocked_security_or_safety",
})
FAILED_STATES = frozenset({"failed_recoverable", "failed_terminal"})


def build_command(agent: str, permission_args: List[str],
                  template: Optional[str] = None,
                  go_repo: Optional[str] = None) -> List[str]:
    """Build the one-shot CLI argv. A template with {prompt} overrides the
    per-agent shape entirely (permission args are the caller's job then)."""
    prompt = (PROMPT.replace("worktrail-go auto", f"worktrail-go {go_repo} auto", 1)
              if go_repo else PROMPT)
    if template:
        parts = template.split()
        if "{prompt}" not in parts:
            raise ValueError("--agent-cmd template must contain {prompt}")
        return [prompt if p == "{prompt}" else p for p in parts]
    if agent not in BASE_CMDS:
        raise ValueError(f"unsupported agent {agent!r}; one of {SUPPORTED_AGENTS}")
    if agent == "claude":
        return ["claude", "-p", prompt, *permission_args]
    if agent == "opencode":
        return ["opencode", "run", *permission_args, prompt]
    return ["codex", "exec", "-s", "danger-full-access", *permission_args, prompt]


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", path.parent.name))


def build_agent_environment(home: Optional[Path] = None) -> Dict[str, str]:
    """Return the supported environment shared by every unattended provider.

    Cron and service managers commonly supply only ``/usr/bin:/bin``.  Provider
    CLIs and their plugin runtimes are user installs, so relying on an
    interactive shell to have initialized PATH makes hooks fail after an
    otherwise successful run.  Resolve the supported user locations here,
    once, and pass the exact same environment to the provider subprocess.
    """
    home = home or Path.home()
    nvm_bins = list((home / ".nvm" / "versions" / "node").glob("v*/bin"))
    newest_nvm_bin = max(nvm_bins, key=_version_key) if nvm_bins else None
    preferred = [
        home / ".local" / "bin",
        home / "bin",
        home / ".opencode" / "bin",
    ]
    if newest_nvm_bin is not None:
        preferred.append(newest_nvm_bin)
    inherited = os.environ.get("PATH", os.defpath).split(os.pathsep)
    entries: List[str] = []
    for entry in [*(str(path) for path in preferred), *inherited]:
        if entry and entry not in entries:
            entries.append(entry)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(entries)
    return env


def worker_scratch_dir(slot: int, home: Optional[Path] = None) -> Path:
    """Return the per-slot scratch cwd a worker's spawned one-shots run from."""
    return (home or worktrail_home()) / "drain-workers" / f"worker-{slot}"


def validate_agent_runtime(
    agent: str,
    env: Dict[str, str],
    which: Callable[..., Optional[str]] = shutil.which,
) -> None:
    """Fail closed before launch when a provider or hook runtime is absent."""
    required = AGENT_RUNTIME_EXECUTABLES.get(agent)
    if required is None:
        raise ValueError(f"unsupported agent {agent!r}; one of {SUPPORTED_AGENTS}")
    path = env.get("PATH", "")
    missing = [name for name in required if which(name, path=path) is None]
    if missing:
        raise RuntimeError(
            f"unattended {agent} runtime preflight failed; required executable(s) "
            f"unavailable: {', '.join(missing)}; PATH={path}. Install the missing "
            "runtime in a supported user location (~/.local/bin, ~/bin, "
            "~/.opencode/bin, or ~/.nvm/versions/node/v*/bin), then retry."
        )


# ---------------------------------------------------------------------------
# Queue state


def count_ready_briefs(queue_json: dict) -> int:
    """Ready = not blocked and not deferred by next-check-after backoff."""
    briefs = queue_json.get("briefs") or []
    return sum(1 for b in briefs
               if not b.get("blocked") and not b.get("not_yet_due"))


def _queue_filenames(queue_json: dict) -> set:
    return {b["filename"] for b in (queue_json.get("briefs") or []) if b.get("filename")}


def claimed_brief_ids(before: dict, after: dict) -> List[str]:
    """Brief ids that left queue/ during an iteration (a claim moves a brief's
    file from queue/ to picked/), by set difference between the `list_queue`
    JSON snapshots taken immediately before and after the iteration.

    Sorted for determinism; a batch-consuming iteration that claims more than
    one brief returns all of them, but callers attribute a `brief_id` to the
    iteration only when exactly one came back — an ambiguous multi-claim
    iteration is left unattributed rather than guessed at.
    """
    gone = sorted(_queue_filenames(before) - _queue_filenames(after))
    return [name[:-3] if name.endswith(".md") else name for name in gone]


def list_queue(work_queue_py: Path, queue_dir: Optional[Path]) -> dict:
    env = dict(os.environ)
    if queue_dir is not None:
        env["WORK_QUEUE_DIR"] = str(queue_dir)
    argv = build_work_queue_argv(work_queue_py, ["list", "--json"])
    out = subprocess.run(
        argv,
        capture_output=True, text=True, env=env, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"work_queue.py list failed: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout)


# ---------------------------------------------------------------------------
# Run-record outcome parsing (line-oriented, same discipline as poll_run.py)


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_run_record(text: str) -> Dict[str, Optional[str]]:
    """Extract top-level scalars needed for outcome classification."""
    fields: Dict[str, Optional[str]] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t", "-")) or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        value = _yaml_scalar(raw)
        fields[key.strip()] = None if value in ("", "null", "~") else value
    return fields


def pending_decision_entries(text: str) -> List[str]:
    """The `- item` lines under a run record's top-level `pending_decisions:`
    key -- the audit list `run_record.record_decision_event` appends to.

    parse_run_record deliberately drops list items (it extracts scalars
    only), so the pending-decision handoff gets its own line-oriented read:
    the list starts at an unindented `pending_decisions:` key and ends at
    the next unindented key. Malformed records yield whatever entries are
    readable -- advisory context, never a parser crash.
    """
    entries: List[str] = []
    in_list = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not in_list:
            if stripped == "pending_decisions:":
                in_list = True
            continue
        if line[:1] in (" ", "\t") and stripped.startswith("- "):
            entries.append(_yaml_scalar(stripped[2:]))
        elif ":" in stripped and not line[:1].isspace():
            break
    return entries


def unresolved_decision_ids(record_text: str) -> List[str]:
    """Decision ids in `record_text`'s `pending_decisions` audit list whose
    most recent lifecycle event is neither consumed nor superseded, in
    first-seen order -- the exact same outstanding-answer semantics as the
    poller surface (poll_run.unresolved_decision_ids), imported rather than
    re-derived so drain and poll_run can never disagree about which
    decisions still block an attended resume."""
    if not record_text:
        return []
    return _poll_unresolved_decision_ids(
        {"pending_decisions": pending_decision_entries(record_text)})


def newest_run_record(
    runs_dir: Path,
    known: Iterable[Path] = (),
    repo_filter: Optional[str] = None,
) -> Optional[Path]:
    """The run-record YAML this iteration produced, across repos.

    Attribution is by set difference against a before-spawn snapshot (`known`),
    not by mtime comparison: two records written within the same filesystem
    mtime-resolution tick can tie or invert under an mtime `>= since_epoch`
    filter, so a stale record can outrank -- or exclude -- the real one
    depending on directory-iteration order, which Python does not guarantee.

    `repo_filter`, when given, restricts the glob to that repo's subdirectory
    so an iteration attributed to a single claimed brief can't be misclassified
    by a newer record landing concurrently in a different repo's directory.
    """
    if not runs_dir.is_dir():
        return None
    known_set = known if isinstance(known, (set, frozenset)) else set(known)
    glob_pattern = f"{repo_filter}/*.yaml" if repo_filter is not None else "*/*.yaml"
    candidates = [path for path in runs_dir.glob(glob_pattern) if path not in known_set]
    if not candidates:
        return None
    def _mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return -1
    return max(candidates, key=lambda path: (_mtime_ns(path), path.name))


# ---------------------------------------------------------------------------
# Capacity cache


def _entry_gated(state: dict, now: datetime) -> bool:
    """True when `state` carries a gated status whose retry_after/reset_at has
    not passed yet. A gated status with no timestamp at all is treated as
    gated indefinitely (until cleared) -- unchanged from prior behavior. A
    gated status with a timestamp that has already passed is NOT gated: the
    whole point of persisting retry_after is so the drain picks the agent
    back up automatically once its cooldown expires (see module docstring
    above and record_capacity_gate()), which requires comparing it to now.
    """
    if str(state.get("status", "")).lower() not in ("gated", "unavailable", "blocked"):
        return False
    retry_at = (agent_capacity._parse_time(state.get("retry_after"))
                or agent_capacity._parse_time(state.get("reset_at")))
    if retry_at is None:
        return True
    return retry_at > now


def capacity_gated(cache: dict, agent: str, now: Optional[datetime] = None) -> bool:
    """True when every cached entry for `agent` carries an active (unexpired)
    gate.

    The cache (agent_capacity.py) keys entries by provider identifiers like
    'claude' or 'claude:opus'. No entry for the agent means no known gate.
    """
    now = now or agent_capacity._now()
    providers = cache.get("providers") if isinstance(cache.get("providers"), dict) else cache
    if not isinstance(providers, dict):
        return False
    bare = providers.get(agent)
    if isinstance(bare, dict):
        return _entry_gated(bare, now)
    matched = [v for k, v in providers.items()
               if isinstance(v, dict) and str(k).startswith(agent + ":")]
    if not matched:
        return False
    return all(_entry_gated(v, now) for v in matched)


def read_capacity_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def machine_wide_routing() -> Optional[Dict[str, Any]]:
    """`policy["routing"]` with no repo-local override in play.

    `worktrail_home()` never carries a repo-local `.worktrail/policy.yaml`
    (it is the operator state directory routing.yaml itself lives in), so
    `load_policy()`'s repo-local-then-machine-wide fallback always resolves
    the single machine-wide `routing.yaml` here -- the one file D1 names.
    """
    return load_policy(worktrail_home()).get("routing")


def select_available_agent(cache: dict, candidates: List[str],
                            routing: Optional[Dict[str, Any]] = None,
                            now: Optional[datetime] = None) -> Optional[str]:
    """First candidate (in configured order) that is not capacity-gated; None
    when every candidate is gated. A candidate with no cache entry at all
    counts as available, matching capacity_gated()'s own semantics -- an
    agent never tried, or genuinely broken in a way that has not yet been
    classified, is not pre-emptively excluded.

    Called fresh every iteration (not cached across the drain run), so a
    higher-priority agent is picked back up automatically once its persisted
    gate's retry_after passes -- no restart or config edit needed.
    """
    # routing_candidates(routing) yields the real (provider, model) pairs
    # routing.agents/routing.tiers actually configure (D4). A candidate with
    # no routing entry (routing unset, or that agent absent from routing.yaml)
    # falls back to the old sentinel model -- the eventual provider adapter
    # still resolves its configured/default model exactly as before, so an
    # operator who has not adopted routing.yaml yet sees no behavior change.
    by_provider: Dict[str, List[dict]] = {}
    for entry in routing_candidates(routing):
        by_provider.setdefault(entry["provider"], []).append(entry)

    catalog = [
        {"provider": candidate, "model": entry["model"]}
        for candidate in candidates
        for entry in (by_provider.get(candidate) or [{"model": "configured-default"}])
    ]

    def available(provider: str, model: str, **_kwargs: object) -> dict:
        # A real model keys the per-model cache entry (e.g. "claude:opus"),
        # so a gate on one model no longer blocks every model of that
        # provider; the sentinel keeps the prior provider-wide gate when no
        # routing-sourced model is known.
        key = provider if model == "configured-default" else agent_capacity.provider_key(provider, model)
        return {
            "available": not capacity_gated(cache, key, now=now),
            "source": "agent-capacity.json",
        }

    try:
        return select_execution_target(catalog, capacity=available, now=now).provider
    except NoExecutionTarget:
        return None


MAX_TRANSCRIPT_FILES = 50  # bounded like agent_capacity.py's audit log / dashboard.py's miss log


def write_iteration_transcript(
    transcript_dir: Optional[Path],
    iteration: int,
    agent: str,
    exit_code: int,
    outcome: "Outcome",
    stdout: str,
    stderr: str,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Persist one iteration's raw one-shot output so a later "why did this
    outcome happen" question has evidence to inspect, instead of only the
    classified outcome drain already logs.

    Live incident (2026-08-03): a `no_pick` iteration left no trace of WHAT
    the one-shot actually did -- run_one_shot() captures stdout/stderr per
    iteration (PR #109), but drain() only ever fed them into
    agent_capacity.classify_failure() for a record-less failure and then
    discarded them; a clean no-claim exit (no_pick) never even reached that
    branch, so its transcript was gone the moment the iteration ended.
    Reproducing the one-shot by hand afterward was the only way to see it.

    None when `transcript_dir` is None -- callers that never configure a
    directory (every existing test, the interactive `/go drain` skill path
    until wired) get no transcripts and no new disk usage, matching prior
    behavior exactly. Best-effort like record_capacity_gate/log_auto_pick_miss:
    a write failure never raises.
    """
    if transcript_dir is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        path = transcript_dir / f"{stamp}-iter{iteration}-{agent}.log"
        header = (
            f"iteration: {iteration}\n"
            f"agent: {agent}\n"
            f"exit_code: {exit_code}\n"
            f"outcome: {outcome.state or outcome.kind}\n"
            f"brief: {outcome.brief_id or '-'}\n"
            f"pr: {outcome.pr_url or '-'}\n"
            f"at: {now.isoformat()}\n"
        )
        path.write_text(
            f"{header}\n=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}\n",
            encoding="utf-8",
        )
        existing = sorted(transcript_dir.glob("*.log"))
        for stale in existing[:-MAX_TRANSCRIPT_FILES] if len(existing) > MAX_TRANSCRIPT_FILES else []:
            stale.unlink(missing_ok=True)
        return path
    except OSError:
        return None


def record_capacity_gate(cache_path: Path, agent: str, failure_class: str,
                         retry_after: datetime) -> None:
    """Persist a bare-agent-keyed capacity gate so the next iteration's
    select_available_agent()/capacity_gated() check sees it immediately.

    Keyed by plain agent name (no model), unlike agent_capacity.record()'s
    "agent:model" keys -- drain.py has no model concept of its own, and
    capacity_gated() already matches a bare key by exact agent-name equality.
    """
    with agent_capacity.write_lock(cache_path):
        data = agent_capacity.load(cache_path)
        data.setdefault("providers", {})[agent] = {
            "status": "unavailable",
            "failure_class": failure_class,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "retry_after": retry_after.isoformat(),
            "source": "drain",
        }
        agent_capacity.save(data, cache_path)


# ---------------------------------------------------------------------------
# Resumable quarantine sweep
#
# QUARANTINED groups whose quarantine_reason is budget_exhausted never failed --
# the fan-out's --run-budget simply ran out before their tasks got a chance to
# run (see quarantine_selfcheck.check_repo docstring). They are safely resumable
# with a plain `worktrail-live full-real` re-run (resume=True is the default; no
# --fresh), but `worktrail-go auto` never surfaces them -- auto mode only claims
# work-queue briefs, never the "Ready to implement" specs shown in the dashboard's
# Active Work section. Without this sweep they sit until a human notices the
# dashboard's "Resumable quarantines" line.


def resolve_spec_rel(repo: Path, spec_id: str) -> Optional[str]:
    """The --spec path full-real expects, relative to `repo`, for whichever
    task format the spec was authored in. None if neither location exists
    (e.g. the spec folder was since deleted/archived)."""
    if (repo / "docs" / "specs" / spec_id).is_dir():
        return f"docs/specs/{spec_id}"
    if (repo / "openspec" / "changes" / spec_id).is_dir():
        return f"openspec/changes/{spec_id}"
    return None


def find_resumable_quarantines(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair with a QUARANTINED/budget_exhausted group, across
    every repo under `repos_root` (or just `go_repo` when given). One entry per
    spec even when multiple groups in its journal are resumable -- a single
    full-real re-run covers the whole journal."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    seen: set = set()
    for name in names:
        repo_path = repos_root / name
        resumable = quarantine_selfcheck.check_repo(repo_path).get("resumable") or []
        for finding in resumable:
            spec_id = finding.get("spec_id")
            key = (name, spec_id)
            if not spec_id or key in seen:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            seen.add(key)
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
            })
    return found


def find_verify_pending_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair currently in the `verify-pending` stage, across
    every repo under `repos_root` (or just `go_repo` when given). These are
    invisible to `worktrail-go auto` the same way budget_exhausted quarantines
    are -- auto mode only claims work-queue briefs -- so without this sweep
    they sit until a human notices the dashboard's stage."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("stage") != "verify-pending":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
            })
    return found


def find_sync_pending_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair currently in the `sync-pending` stage, across
    every repo under `repos_root` (or just `go_repo` when given). These are
    invisible to `worktrail-go auto` the same way verify-pending specs are --
    auto mode only claims work-queue briefs -- so without this sweep they sit
    until a human notices the dashboard's stage."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("stage") != "sync-pending":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
            })
    return found


def find_stale_bookkeeping_specs(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair currently in the `stale-bookkeeping` stage with
    at least one stale task id, across every repo under `repos_root` (or just
    `go_repo` when given). These are invisible to `worktrail-go auto` the same
    way verify-pending specs are -- auto mode only claims work-queue briefs --
    so without this sweep they sit until a human notices the dashboard's
    stage."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("stage") != "stale-bookkeeping":
                continue
            stale_task_ids = row.get("stale_task_ids") or []
            if not stale_task_ids:
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
                "stale_task_ids": stale_task_ids,
            })
    return found


def find_complete_openspec_changes(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every (repo, spec) pair currently at OpenSpec's `complete` stage, across
    every repo under `repos_root` (or just `go_repo` when given). `complete`
    is OpenSpec-only -- the devkit format has no equivalent terminal stage --
    so unlike the other finders this filters on `format == "openspec"` too,
    the critical scope guard that keeps a devkit spec from ever being routed
    into `openspec archive`."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        rows = dashboard.scan(repo_path / "docs" / "specs")
        for row in rows:
            if row.get("format") != "openspec" or row.get("stage") != "complete":
                continue
            spec_id = row.get("id")
            if not spec_id:
                continue
            spec_rel = resolve_spec_rel(repo_path, spec_id)
            if spec_rel is None:
                continue
            found.append({
                "repo": repo_path, "repo_name": name,
                "spec_id": spec_id, "spec_rel": spec_rel,
            })
    return found


def _base_branch_for(repo: Path) -> str:
    try:
        return load_policy(repo).get("base_branch") or "dev"
    except Exception:
        return "dev"


def build_full_real_resume_command(repo: Path, spec_rel: str, base: str, agent: str) -> List[str]:
    """No --fresh: resume=True is full-real's own default, so this simply
    continues the interrupted fan-out from its run journal."""
    return ["worktrail-live", "full-real", "--repo", str(repo),
            "--spec", spec_rel, "--base", base, "--agent", agent]


def build_sync_command(agent: str, wt: Path, spec_id: str) -> List[str]:
    """A sync-pending finding needs `/opsx:sync` re-dispatched against the
    spec, not a full-real resume -- unlike verify-pending/quarantine, there is
    no interrupted worker fan-out to continue. `--write` applies the sync
    rather than just previewing it. Dispatched against an isolated worktree
    (`wt`), never the finding's canonical checkout: `opsx:sync`
    (openspec-sync-specs/SKILL.md) is a pure agent-driven file-edit
    operation with no commit step of its own, so writing straight into the
    live checkout leaves the edit uncommitted -- and this machine's own
    `git reset --hard origin/main` doctrine after the next merge (see
    AGENTS.md Git Workflow) silently discards it before anyone notices."""
    return ["worktrail-skill-dispatch", "--agent", agent,
            "--skill", "opsx:sync", "--args", spec_id,
            "--cwd", str(wt), "--write"]


def _existing_sync_pending_pr(repo: Path, branch: str, timeout: int) -> Optional[str]:
    """Same best-effort open-PR lookup as `_existing_stale_bookkeeping_pr` --
    a `gh` failure (network, auth) reads as "no open PR" rather than
    aborting the finding."""
    result = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open",
         "--json", "url", "--jq", ".[0].url"],
        capture_output=True, text=True, cwd=str(repo), timeout=timeout)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def _open_sync_pending_pr(
    repo: Path, wt: Path, repo_name: str, spec_id: str, base: str, branch: str, timeout: int,
) -> str:
    """`gh pr create` for the sync branch, via the same enforced
    label-resolution path `_open_openspec_archive_pr`/`_open_stale_bookkeeping_pr`
    use (`_refresh_pr_labels` -> `pre_pr_gate.py --labels-only`), not
    hand-rolled. Raises rather than returning a fabricated URL when
    `gh pr create` fails outright."""
    labels = _refresh_pr_labels(wt, ["go:risk-low"], base) or ["go:risk-low"]
    cmd = ["gh", "pr", "create", "--base", base, "--head", branch]
    for label in labels:
        cmd += ["--label", label]
    cmd += [
        "--title", f"chore({spec_id}): sync specs from change",
        "--body",
        f"Runs `/opsx:sync {spec_id}` and commits whatever it wrote into "
        f"main specs.\n\nOpened by drain's sync-pending sweep.",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(wt), timeout=timeout)
    out = ((result.stdout or result.stderr).strip().splitlines()[-1]
           if (result.stdout or result.stderr) else "(no output)")
    if result.returncode != 0 or not out.startswith("http"):
        raise RuntimeError(f"gh pr create failed for {repo_name} {spec_id}: {out}")
    return out


def _run_sync_pending(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """A sync-pending finding's remediation: dispatch `/opsx:sync` into a
    short-lived worktree off the target repo's base branch (mirroring
    `archive_openspec_change`'s pattern -- see `build_sync_command` for why
    the dispatch itself is never pointed at the canonical checkout), then
    commit, push, and open a PR for whatever it wrote before tearing the
    worktree down. Previously this only spawned the skill and reported its
    bare exit code, which stayed `0` every night even though nothing ever
    reached `base` -- `pr_url` is the regression signal that was missing:
    `None` means "ran but produced nothing to land" (a no-op sync, or a
    non-zero `exit_code`), a real URL means the change is actually on its
    way to `base`.

    Re-entrant across sweeps like the other worktree-based remediations: an
    already-open PR for this finding's branch is detected up front and
    returned as-is, and a worktree/branch left behind by a prior run's
    mid-flight failure is reset before retrying."""
    repo, repo_name, spec_id = finding["repo"], finding["repo_name"], finding["spec_id"]
    base = _base_branch_for(repo)
    slug = f"sync-{spec_id}"
    branch = f"chore/{slug}"
    wt = repo.parent / f"{repo.name}-worktrees" / slug

    existing_pr = _existing_sync_pending_pr(repo, branch, timeout)
    if existing_pr:
        log(f"resume-sync-pending: {repo_name} {spec_id} already has an "
            f"open PR, skipping: {existing_pr}")
        return {"repo": repo_name, "spec_id": spec_id, "exit_code": 0,
                "pr_url": existing_pr}

    _reset_stale_bookkeeping_worktree(repo, branch, wt, timeout)
    _run_git(repo, "worktree", "add", "-b", branch, str(wt), base, timeout=timeout)
    pr_url: Optional[str] = None
    try:
        cmd = build_sync_command(agent, wt, spec_id)
        log(f"resume-sync-pending: {repo_name} {spec_id} -> /opsx:sync")
        outcome = spawner(cmd, timeout)
        log(f"resume-sync-pending result: {repo_name} {spec_id} "
            f"exit={outcome.exit_code}")
        if outcome.exit_code == 0:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=str(wt), timeout=timeout)
            if status.stdout.strip():
                _run_git(wt, "add", "-A", timeout=timeout)
                _run_git(wt, "commit", "-m",
                         f"chore({spec_id}): sync specs from change", timeout=timeout)
                # --force: this branch is exclusively owned by this action
                # and rebuilt from `base` on every retry, mirroring
                # archive_openspec_change/close_stale_bookkeeping's identical
                # push.
                _run_git(wt, "push", "--force", "-u", "origin", branch, timeout=timeout)
                pr_url = _open_sync_pending_pr(
                    repo, wt, repo_name, spec_id, base, branch, timeout)
            else:
                log(f"resume-sync-pending: {repo_name} {spec_id} produced no "
                    f"changes to sync")
    finally:
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           capture_output=True, text=True, cwd=str(repo), timeout=timeout)
        except Exception:
            pass

    return {
        "repo": repo_name, "spec_id": spec_id,
        "exit_code": outcome.exit_code, "pr_url": pr_url,
    }


def resume_sync_pending(
    repos_root: Path,
    go_repo: Optional[str],
    candidates: List[str],
    capacity_cache: Path,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Resume every sync-pending spec found under `repos_root` by spawning
    `/opsx:sync <spec_id>`. Best-effort: a spec whose repo has since gone
    away is silently skipped by find_sync_pending_specs, and one spec's sync
    failing does not stop the others. Thin wrapper over sweep_remediations,
    restricted to this row's key."""
    return sweep_remediations(
        repos_root, go_repo, candidates, capacity_cache, timeout, spawner, log,
        keys=["sync_pending"],
    )["sync_pending"]


def _resume_via_full_real(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
    *,
    label: str,
) -> Dict[str, Any]:
    """Shared body of the full-real-resume actions: build the resume command
    for a single finding, spawn it, log before/after, and return the result
    dict. `label` is the log-line prefix (e.g. "resume-quarantine",
    "resume-verify-pending") the existing tests assert on."""
    repo, spec_id = finding["repo"], finding["spec_id"]
    base = _base_branch_for(repo)
    cmd = build_full_real_resume_command(repo, finding["spec_rel"], base, agent)
    log(f"{label}: {finding['repo_name']} {spec_id} -> full-real --base {base}")
    outcome = spawner(cmd, timeout)
    log(f"{label} result: {finding['repo_name']} {spec_id} exit={outcome.exit_code}")
    return {
        "repo": finding["repo_name"], "spec_id": spec_id,
        "exit_code": outcome.exit_code,
    }


def resume_quarantined_budget_exhausted(
    repos_root: Path,
    go_repo: Optional[str],
    candidates: List[str],
    capacity_cache: Path,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Resume every budget_exhausted-only QUARANTINED spec found under
    `repos_root` with a plain full-real re-run. Best-effort: a spec whose repo
    or journal has since gone away is silently skipped by
    find_resumable_quarantines, and one spec's resume failing does not stop
    the others. Thin wrapper over sweep_remediations, restricted to this
    row's key."""
    return sweep_remediations(
        repos_root, go_repo, candidates, capacity_cache, timeout, spawner, log,
        keys=["quarantined_budget_exhausted"],
    )["quarantined_budget_exhausted"]


def resume_verify_pending(
    repos_root: Path,
    go_repo: Optional[str],
    candidates: List[str],
    capacity_cache: Path,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> List[Dict[str, Any]]:
    """Resume every verify-pending spec found under `repos_root` with a plain
    full-real re-run. Best-effort: a spec whose repo or journal has since gone
    away is silently skipped by find_verify_pending_specs, and one spec's
    resume failing does not stop the others. Thin wrapper over
    sweep_remediations, restricted to this row's key."""
    return sweep_remediations(
        repos_root, go_repo, candidates, capacity_cache, timeout, spawner, log,
        keys=["verify_pending"],
    )["verify_pending"]


# ---------------------------------------------------------------------------
# Stale-bookkeeping closeout (status-flip PR, no orchestrator involved)


def _resolve_stale_task_path(repo: Path, spec_id: str, task_id: str) -> Optional[Path]:
    """The TASK-*.md file for `task_id` under this spec's task dirs -- the
    top-level tasks/ dir plus every changes/<slug>/tasks/ dir, mirroring
    dashboard._task_dirs. Matched by filename stem, the same id
    find_stale_bookkeeping_specs' `stale_task_ids` already carries
    (frontmatter `id:` defaults to the file stem when absent -- see
    dashboard._load_tasks). None if no matching file exists."""
    spec_dir = repo / "docs" / "specs" / spec_id
    task_dirs = [spec_dir / "tasks"]
    changes_dir = spec_dir / "changes"
    if changes_dir.is_dir():
        task_dirs += sorted(d / "tasks" for d in changes_dir.iterdir() if d.is_dir())
    for tasks_dir in task_dirs:
        candidate = tasks_dir / f"{task_id}.md"
        if candidate.is_file():
            return candidate
    return None


def _run_git(cwd: Path, *args: str, timeout: int) -> None:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=str(cwd), timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} (in {cwd}) failed: {(result.stderr or result.stdout).strip()}")


def find_stale_branches(
    repos_root: Path, go_repo: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every locally merged, prunable branch across every repo under
    `repos_root` (or just `go_repo` when given), flattened to one finding
    per branch -- `branch_selfcheck.sweep`'s per-repo `prunable` lists merged
    into drain's usual one-finding-per-remediable-item shape."""
    names = discover_repo_names(repos_root)
    if go_repo:
        names = [n for n in names if n == go_repo]
    found: List[Dict[str, Any]] = []
    for name in names:
        repo_path = repos_root / name
        report = branch_selfcheck.check_repo(repo_path)
        for entry in report["prunable"]:
            found.append({
                "repo": repo_path, "repo_name": name,
                "branch": entry["branch"],
                "worktree_path": entry["worktree_path"],
                "method": entry["method"],
            })
    return found


def prune_stale_branch(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """Delete one branch `find_stale_branches` proved is fully merged.

    Removes the branch's worktree first when it has one -- git refuses to
    delete a branch checked out in any worktree, and `git worktree remove`
    (no `--force`) is itself the safety net against a branch that went dirty
    between the finder's read and this action's write: it raises rather than
    discarding uncommitted changes, which the sweep engine's per-finding
    try/except catches and logs, leaving the branch for the next sweep to
    re-evaluate rather than losing work.

    `agent`/`spawner` are unused -- mechanical git only, no one-shot spawn --
    kept only so the signature matches StageRemediation's uniform `action`
    shape, same as `close_stale_bookkeeping`."""
    repo, branch = finding["repo"], finding["branch"]
    worktree_path = finding.get("worktree_path")
    if worktree_path:
        _run_git(repo, "worktree", "remove", worktree_path, timeout=timeout)
    _run_git(repo, "branch", "-D", branch, timeout=timeout)
    log(f"branch-selfcheck: pruned {finding['repo_name']} {branch} "
        f"(method={finding['method']})")
    return {"repo": finding["repo_name"], "branch": branch,
            "method": finding["method"], "pruned": True}


def _existing_stale_bookkeeping_pr(repo: Path, branch: str, timeout: int) -> Optional[str]:
    """The URL of an already-open PR for `branch`, or None. Best-effort: a
    `gh` failure (network, auth) is treated the same as "no open PR" rather
    than aborting the finding, since the git steps below are the ones that
    actually need to succeed or raise."""
    result = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open",
         "--json", "url", "--jq", ".[0].url"],
        capture_output=True, text=True, cwd=str(repo), timeout=timeout)
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    return url or None


def _reset_stale_bookkeeping_worktree(repo: Path, branch: str, wt: Path, timeout: int) -> None:
    """Best-effort teardown of a previous run's leftover worktree/branch --
    e.g. one left behind by a `git commit`/`push`/`gh pr create` failure
    before the `try/finally` below existed -- so `worktree add -b branch`
    below is safe to retry. Errors are swallowed, including timeouts: the
    common case (nothing left over from a prior run) exits non-zero on all
    three calls."""
    for cmd in (
        ["git", "worktree", "remove", "--force", str(wt)],
        ["git", "worktree", "prune"],
        ["git", "branch", "-D", branch],
    ):
        try:
            subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo), timeout=timeout)
        except Exception:
            pass


def _open_stale_bookkeeping_pr(
    repo: Path, wt: Path, repo_name: str, spec_id: str, task_ids: List[str], base: str,
    branch: str, timeout: int,
) -> str:
    """`gh pr create` for the status-flip-only branch. A status-only flip of
    already-shipped work is inherently low risk -- no code change -- but the
    label(s) are still sourced from the enforced label-resolution path
    (`_refresh_pr_labels` -> `pre_pr_gate.py --labels-only`), not
    hand-rolled, so a future policy change (e.g. a new required label) is
    never silently missed here the way it was for four prior call sites
    (see test_pr_creation_callsite_enforcement_coverage.py). Falls back to
    the seed label only if refresh itself is unavailable (gate script
    unresolvable). Resolved against `wt`, not `repo`: `wt`'s HEAD is this
    PR's actual diff (the flip commit off `base`), while `repo` is whatever
    branch the drain target happens to be checked out to -- diffing that
    against `base` would stamp labels computed from an unrelated diff.
    Raises rather than returning a fabricated URL when `gh pr create` fails
    outright, e.g. an unresolvable --label -- it fails the WHOLE command
    with a non-zero exit and no PR created (see orchestrator/integrate.py's
    identical guard)."""
    labels = _refresh_pr_labels(wt, ["go:risk-low"], base) or ["go:risk-low"]
    cmd = ["gh", "pr", "create", "--base", base, "--head", branch]
    for label in labels:
        cmd += ["--label", label]
    cmd += [
        "--title", f"chore({spec_id}): close stale bookkeeping",
        "--body",
        f"Flips `status:` to `completed` for already-shipped task(s) "
        f"{', '.join(task_ids)} in `{spec_id}` -- their `files:` are already merged "
        f"on `{base}`; no code change.\n\nOpened by drain's stale-bookkeeping sweep.",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(wt), timeout=timeout)
    out = ((result.stdout or result.stderr).strip().splitlines()[-1]
           if (result.stdout or result.stderr) else "(no output)")
    if result.returncode != 0 or not out.startswith("http"):
        raise RuntimeError(f"gh pr create failed for {repo_name} {spec_id}: {out}")
    return out


def close_stale_bookkeeping(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """Flip each of the finding's stale task ids' `status:` to `completed`
    and open a docs-only PR. Not a spec-owned change (no artifact under
    `openspec/changes/<id>/` or `docs/specs/<id>/changes/<slug>/` is being
    authored), so it does not fit the `new`/`modify` pipelines' spec-worktree
    setup -- it uses the same direct fix-branch worktree pattern Route F
    already uses for unspecced-code fixes (subagent-prompts.md
    #fix-branch-worktree-setup/#fix-branch-worktree-teardown): a short-lived
    worktree off the target repo's base branch, commit, push, `gh pr create`,
    then tear the worktree down once the PR is open -- this does not wait for
    merge, mirroring how the two full-real-resume actions spawn and record
    the outcome without blocking on their run finishing either.

    `agent`/`spawner` are unused -- this action never spawns a one-shot --
    kept only so the signature matches StageRemediation's uniform `action`
    shape (see design.md D1). `timeout` IS used, as the per-call timeout for
    every `git`/`gh` subprocess call below -- this is the unattended
    queue-drainer holding an exclusive lockfile for the run's duration, and
    `git push`/`gh pr create` are its only network calls.

    Re-entrant across sweeps: an already-open PR for this finding's branch is
    detected up front and returned as-is rather than re-attempted (a PR that
    has not yet merged must not read as a remediation *failure* on every
    sweep between opening and merge), and any worktree/branch left behind by
    a prior run's mid-flight failure is reset before retrying.

    Raises on `gh pr create` failure (caught by the sweep engine's
    per-finding try/except, per D2) rather than returning a result dict with
    no PR.

    If every resolved task file already reads `status: completed` with no
    unticked checkboxes on `base` (e.g. the flip landed via another route
    since this finding was computed), `set_status_completed` makes no
    change to any of them: there is nothing to commit, so no branch/PR is
    opened and `pr_url` comes back `None` -- a clean no-op rather than a
    `git commit` "nothing to commit" `RuntimeError` on every sweep."""
    repo, repo_name, spec_id = finding["repo"], finding["repo_name"], finding["spec_id"]
    task_ids = finding["stale_task_ids"]
    task_paths = [_resolve_stale_task_path(repo, spec_id, task_id) for task_id in task_ids]
    missing = [task_id for task_id, path in zip(task_ids, task_paths) if path is None]
    if missing:
        raise RuntimeError(
            f"no TASK-*.md found for {repo_name} {spec_id}: {', '.join(missing)}")

    base = _base_branch_for(repo)
    slug = f"close-stale-{spec_id}"
    branch = f"fix/{slug}"
    wt = repo.parent / f"{repo.name}-worktrees" / slug

    existing_pr = _existing_stale_bookkeeping_pr(repo, branch, timeout)
    if existing_pr:
        log(f"close-stale-bookkeeping: {repo_name} {spec_id} already has an "
            f"open PR, skipping: {existing_pr}")
        return {"repo": repo_name, "spec_id": spec_id, "task_ids": task_ids,
                "pr_url": existing_pr}

    _reset_stale_bookkeeping_worktree(repo, branch, wt, timeout)
    log(f"close-stale-bookkeeping: {repo_name} {spec_id} -> {', '.join(task_ids)}")
    _run_git(repo, "worktree", "add", "-b", branch, str(wt), base, timeout=timeout)
    try:
        changed_rel = [str(path.relative_to(repo)) for path in task_paths]
        changed = [set_status_completed(wt / rel) for rel in changed_rel]
        if not any(changed):
            log(f"close-stale-bookkeeping: {repo_name} {spec_id} tasks already "
                f"completed on {base}, nothing to flip")
            return {"repo": repo_name, "spec_id": spec_id, "task_ids": task_ids,
                    "pr_url": None}
        _run_git(wt, "add", *changed_rel, timeout=timeout)
        _run_git(wt, "commit", "-m",
                 f"chore({spec_id}): close stale bookkeeping ({', '.join(task_ids)})",
                 timeout=timeout)
        # --force: this branch is exclusively owned by this action (the
        # drain sweep holds an exclusive lockfile for its duration) and is
        # rebuilt from `base` on every retry, so a remote copy left behind by
        # a prior run's mid-flight failure (e.g. `gh pr create` rejected, or
        # a `gh pr list` outage during the open-PR check above) must be
        # overwritten rather than rejected non-fast-forward -- otherwise the
        # finding wedges permanently even after the original cause clears.
        _run_git(wt, "push", "--force", "-u", "origin", branch, timeout=timeout)
        pr_url = _open_stale_bookkeeping_pr(
            repo, wt, repo_name, spec_id, task_ids, base, branch, timeout)
    finally:
        # Best-effort: on the success path this must not raise past a real
        # result, and on the failure path it must not mask the real
        # exception -- either way, a worktree left behind here is cleaned up
        # by _reset_stale_bookkeeping_worktree on the finding's next sweep.
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           capture_output=True, text=True, cwd=str(repo), timeout=timeout)
        except Exception:
            pass

    log(f"close-stale-bookkeeping result: {repo_name} {spec_id} -> {pr_url}")
    return {"repo": repo_name, "spec_id": spec_id, "task_ids": task_ids, "pr_url": pr_url}


# ---------------------------------------------------------------------------
# OpenSpec archive remediation (complete-stage changes)


def _run_openspec_archive(wt: Path, spec_id: str, timeout: int) -> None:
    """`openspec archive -y <change-id>` in the worktree -- non-interactive
    (`-y`), so it never blocks on a confirmation prompt. Raises on failure
    (per D2's per-finding isolation, caught by sweep_remediations).

    Refuses (raises, no subprocess invoked) if `tasks.md` still has an
    unchecked task -- `openspec archive -y` itself only downgrades that case
    to a stdout warning and archives anyway, so this pre-check is the only
    thing standing between drain's unattended sweep and silently archiving
    partial work."""
    tasks_md = wt / "openspec" / "changes" / spec_id / "tasks.md"
    if tasks_md.is_file():
        pending = [t.id for t in parse_tasks_md(tasks_md.read_text()).tasks
                   if t.status != STATUS_COMPLETED]
        if pending:
            raise RuntimeError(
                f"refusing to archive {spec_id} (in {wt}): tasks.md has "
                f"unchecked task(s) {pending} -- openspec archive -y would "
                f"proceed anyway (it only warns), so this hard-refuses instead")
    result = subprocess.run(
        ["openspec", "archive", "-y", spec_id],
        capture_output=True, text=True, cwd=str(wt), timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"openspec archive -y {spec_id} (in {wt}) failed: "
            f"{(result.stderr or result.stdout).strip()}")


def _open_openspec_archive_pr(
    repo: Path, wt: Path, repo_name: str, spec_id: str, base: str, branch: str, timeout: int,
) -> str:
    """`gh pr create` for the archive-only branch, via the same enforced
    label-resolution path `_open_stale_bookkeeping_pr` uses (see that
    docstring) -- labels are sourced from `_refresh_pr_labels`, not
    hand-rolled. Raises rather than returning a fabricated URL when
    `gh pr create` fails outright."""
    labels = _refresh_pr_labels(wt, ["go:risk-low"], base) or ["go:risk-low"]
    cmd = ["gh", "pr", "create", "--base", base, "--head", branch]
    for label in labels:
        cmd += ["--label", label]
    cmd += [
        "--title", f"chore({spec_id}): archive completed change",
        "--body",
        f"Runs `openspec archive -y {spec_id}` for the completed change "
        f"`{spec_id}` and commits whatever it moved/wrote.\n\n"
        "Opened by drain's OpenSpec archive sweep.",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(wt), timeout=timeout)
    out = ((result.stdout or result.stderr).strip().splitlines()[-1]
           if (result.stdout or result.stderr) else "(no output)")
    if result.returncode != 0 or not out.startswith("http"):
        raise RuntimeError(f"gh pr create failed for {repo_name} {spec_id}: {out}")
    return out


def archive_openspec_change(
    finding: Dict[str, Any],
    agent: str,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
) -> Dict[str, Any]:
    """Archive a `complete`-stage OpenSpec change and open a PR. Reuses
    `close_stale_bookkeeping`'s fix-branch worktree lifecycle directly
    (`_existing_stale_bookkeeping_pr`/`_reset_stale_bookkeeping_worktree`
    are already generic over `repo`/`branch`/`timeout` -- nothing
    stale-bookkeeping-specific about either): a short-lived worktree off the
    target repo's base branch, `openspec archive -y <change-id>`, commit,
    push, `gh pr create`, then tear the worktree down once the PR is open --
    this does not wait for merge, mirroring `close_stale_bookkeeping`.

    `agent`/`spawner` are unused -- this action never spawns a one-shot --
    kept only so the signature matches StageRemediation's uniform `action`
    shape (see design.md D1). `timeout` IS used, as the per-call timeout for
    every `git`/`gh`/`openspec` subprocess call below.

    Re-entrant across sweeps: an already-open PR for this finding's branch is
    detected up front and returned as-is rather than re-attempted, and any
    worktree/branch left behind by a prior run's mid-flight failure is reset
    before retrying.

    Raises on `openspec archive` failure or `gh pr create` failure (caught by
    the sweep engine's per-finding try/except, per D2) rather than returning
    a result dict with no PR."""
    repo, repo_name, spec_id = finding["repo"], finding["repo_name"], finding["spec_id"]

    base = _base_branch_for(repo)
    slug = f"archive-{spec_id}"
    branch = f"chore/{slug}"
    wt = repo.parent / f"{repo.name}-worktrees" / slug

    existing_pr = _existing_stale_bookkeeping_pr(repo, branch, timeout)
    if existing_pr:
        log(f"archive-openspec-change: {repo_name} {spec_id} already has an "
            f"open PR, skipping: {existing_pr}")
        return {"repo": repo_name, "spec_id": spec_id, "pr_url": existing_pr}

    _reset_stale_bookkeeping_worktree(repo, branch, wt, timeout)
    log(f"archive-openspec-change: {repo_name} {spec_id}")
    _run_git(repo, "worktree", "add", "-b", branch, str(wt), base, timeout=timeout)
    try:
        _run_openspec_archive(wt, spec_id, timeout)
        _run_git(wt, "add", "-A", timeout=timeout)
        _run_git(wt, "commit", "-m",
                 f"chore({spec_id}): archive completed change", timeout=timeout)
        # --force: see close_stale_bookkeeping's identical push -- this
        # branch is exclusively owned by this action and rebuilt from `base`
        # on every retry.
        _run_git(wt, "push", "--force", "-u", "origin", branch, timeout=timeout)
        pr_url = _open_openspec_archive_pr(
            repo, wt, repo_name, spec_id, base, branch, timeout)
    finally:
        try:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           capture_output=True, text=True, cwd=str(repo), timeout=timeout)
        except Exception:
            pass

    log(f"archive-openspec-change result: {repo_name} {spec_id} -> {pr_url}")
    return {"repo": repo_name, "spec_id": spec_id, "pr_url": pr_url}


@dataclass(frozen=True)
class StageRemediation:
    """One row of the remediation-sweep table. `finder(repos_root, go_repo)`
    returns findings for this stage; `action(finding, agent, timeout, spawner,
    log)` remediates a single finding and returns a result dict, raising on
    failure so the sweep engine can catch and log per-finding without
    aborting the rest of the sweep."""
    key: str
    label: str
    finder: Callable[[Path, Optional[str]], List[Dict[str, Any]]]
    action: Callable[
        [Dict[str, Any], str, int,
         Callable[[List[str], int], "SpawnOutcome"], Callable[[str], None]],
        Dict[str, Any],
    ]


# `orchestrator-stuck` (`fanout_failed`) is intentionally never a table entry:
# routes.md §E and dashboard.py's detect_stage() both document it as unsafe
# to silently re-launch -- it stays human-recovery-only.
REMEDIATION_TABLE: List[StageRemediation] = [
    StageRemediation(
        "quarantined_budget_exhausted", "resume-quarantine",
        find_resumable_quarantines,
        functools.partial(_resume_via_full_real, label="resume-quarantine")),
    StageRemediation(
        "verify_pending", "resume-verify-pending",
        find_verify_pending_specs,
        functools.partial(_resume_via_full_real, label="resume-verify-pending")),
    StageRemediation(
        "stale_bookkeeping", "close-stale-bookkeeping",
        find_stale_bookkeeping_specs, close_stale_bookkeeping),
    StageRemediation(
        "sync_pending", "resume-sync-pending",
        find_sync_pending_specs, _run_sync_pending),
    StageRemediation(
        "openspec_archive", "archive-openspec-change",
        find_complete_openspec_changes, archive_openspec_change),
    StageRemediation(
        "stale_branches", "branch-selfcheck",
        find_stale_branches, prune_stale_branch),
]


def sweep_remediations(
    repos_root: Path,
    go_repo: Optional[str],
    candidates: List[str],
    capacity_cache: Path,
    timeout: int,
    spawner: Callable[[List[str], int], SpawnOutcome],
    log: Callable[[str], None],
    keys: Optional[Iterable[str]] = None,
    routing: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run every `REMEDIATION_TABLE` row's finder + action (or only the rows
    whose `key` is in `keys`, when given), one result dict per remediated
    finding, keyed by each row's `key` -- even a row with zero findings gets
    an empty-list entry, so callers can rely on the key always being present.

    Re-selects an available agent from `candidates` (the same claude -> codex
    -> opencode capacity fallback chain the main drain loop uses, via
    `select_available_agent`) before each finding, instead of being handed one
    fixed agent for the whole sweep -- a sweep can run long after the main
    loop last picked an agent, and re-reading `capacity_cache` per finding
    lets a since-recovered higher-priority agent (or a since-gated one) take
    effect mid-sweep, same as the main loop's own per-iteration re-check. A
    finding is skipped (logged, not raised) when every candidate is currently
    gated -- best-effort, matching the rest of this function.

    A finding's action raising is caught and logged (`{label} error: ...`)
    without aborting the rest of that row's findings or the other rows --
    the same best-effort guarantee `resume_quarantined_budget_exhausted` and
    `resume_verify_pending` already documented individually."""
    wanted = None if keys is None else set(keys)
    selected = (REMEDIATION_TABLE if wanted is None
                else [row for row in REMEDIATION_TABLE if row.key in wanted])
    results: Dict[str, List[Dict[str, Any]]] = {}
    current_agent: Optional[str] = None
    for remediation in selected:
        applied: List[Dict[str, Any]] = []
        for finding in remediation.finder(repos_root, go_repo):
            chosen = select_available_agent(
                read_capacity_cache(capacity_cache), candidates, routing=routing)
            if chosen is None:
                log(f"{remediation.label}: {finding.get('repo_name')} "
                    f"{finding.get('spec_id')}: skipped, every candidate agent "
                    f"is capacity-gated ({', '.join(candidates)})")
                continue
            if chosen != current_agent:
                log(f"agent switch: {current_agent or candidates[0]} -> "
                    f"{chosen} (capacity)")
                current_agent = chosen
            try:
                applied.append(remediation.action(
                    finding, current_agent, timeout, spawner, log))
            except Exception as exc:  # noqa: BLE001 — one finding must not
                                        # block the rest of the sweep
                log(f"{remediation.label} error: "
                    f"{finding.get('repo_name')} {finding.get('spec_id')}: {exc}")
        results[remediation.key] = applied
    return results


@dataclass(frozen=True)
class StageRemediation:
    """One row of the remediation-sweep table. `finder(repos_root, go_repo)`
    returns findings for this stage; `action(finding, agent, timeout, spawner,
    log)` remediates a single finding and returns a result dict, raising on
    failure so the sweep engine can catch and log per-finding without
    aborting the rest of the sweep."""
    key: str
    label: str
    finder: Callable[[Path, Optional[str]], List[Dict[str, Any]]]
    action: Callable[
        [Dict[str, Any], str, int,
         Callable[[List[str], int], "SpawnOutcome"], Callable[[str], None]],
        Dict[str, Any],
    ]


# ---------------------------------------------------------------------------
# Lockfile


def acquire_lock(lock_file: Path) -> bool:
    """Atomic O_EXCL lock; a lock owned by a dead pid is stale and replaced."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "started": time.time()})
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            holder = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(holder.get("pid", -1))
        except (OSError, ValueError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            return False
        # Stale lock: previous drain died. Take over.
        lock_file.unlink(missing_ok=True)
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    return True


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def release_lock(lock_file: Path) -> None:
    lock_file.unlink(missing_ok=True)


def slot_lock_path(lock_file: Path, slot: int) -> Path:
    """Per-slot lock file path; slot 0 is the unmodified `lock_file`."""
    if slot == 0:
        return lock_file
    return lock_file.with_name(f"{lock_file.name}.{slot}")


def acquire_lock_slot(lock_file: Path, max_workers: int) -> Optional[int]:
    """Try slots 0..max_workers-1 in order, returning the first acquired slot
    index or None if every slot is held by a live pid."""
    for slot in range(max_workers):
        if acquire_lock(slot_lock_path(lock_file, slot)):
            return slot
    return None


def release_lock_slot(lock_file: Path, slot: int) -> None:
    release_lock(slot_lock_path(lock_file, slot))


# ---------------------------------------------------------------------------
# Decision function (pure — the unit-tested core)


@dataclass
class Outcome:
    """Classified result of one iteration."""
    kind: str                 # "success" | "blocked" | "failed"
                               # | "timeout_after_pr" | "no_pick"
                               # | "pending_user_decision"
    state: Optional[str] = None      # run-record completion state, if any
    brief_id: Optional[str] = None
    pr_url: Optional[str] = None
    pending_decisions: Optional[List[str]] = None  # unresolved decision ids


@dataclass
class LoopState:
    iteration: int = 0               # completed iterations
    max_items: int = 0               # 0 = unlimited
    deadline: Optional[float] = None  # epoch seconds; None = no budget
    consecutive_failures: int = 0
    failure_threshold: int = 2
    ready_count: int = 0
    last_outcome: Optional[Outcome] = None
    agent_capacity_gated: bool = False


@dataclass
class Decision:
    proceed: bool
    reason: str


def decide(state: LoopState, now: float) -> Decision:
    """Decide whether to launch another iteration. Evaluated BEFORE each spawn."""
    last = state.last_outcome
    if last is not None:
        if last.kind == "pending_user_decision":
            ids = ", ".join(last.pending_decisions or []) or "(decision id not recorded)"
            return Decision(False, "pending_user_decision: awaiting human answer(s): "
                                   f"{ids} -- present/answer the decision, then resume "
                                   "attended via `worktrail-skill-dispatch --resume-decision <id>`")
        if last.kind == "no_pick":
            return Decision(False, "no_pick: worktrail-go auto claimed nothing "
                                   "(null auto_pick or picks not eligible)")
        if last.kind == "blocked" and state.agent_capacity_gated:
            return Decision(False, "capacity_gated: provider capacity gate persisted "
                                   "for the configured agent")
        if state.consecutive_failures >= state.failure_threshold:
            return Decision(False, f"circuit_breaker: {state.consecutive_failures} "
                                   f"consecutive failed iterations")
    if state.ready_count <= 0:
        return Decision(False, "queue_empty: no ready briefs")
    if state.max_items and state.iteration >= state.max_items:
        return Decision(False, f"max_items: {state.max_items} iterations done")
    if state.deadline is not None and now >= state.deadline:
        return Decision(False, "budget_exhausted: wall-clock budget reached")
    return Decision(True, "ready briefs remain")


def classify_outcome(record_fields: Optional[Dict[str, Optional[str]]],
                     claimed_delta: int,
                     exit_code: int,
                     claimed_briefs: Iterable[str] = (),
                     failure_class: Optional[str] = None,
                     pending_decisions: Iterable[str] = ()) -> Outcome:
    """Classify one iteration from its newest run record + queue movement.

    record_fields  — parsed run record created/updated during the iteration,
                     or None when no record appeared.
    claimed_delta  — briefs that left queue/ during the iteration (>=0).
    exit_code      — the one-shot process exit code.
    claimed_briefs — brief ids (see `claimed_brief_ids`) that left queue/
                     during this iteration; attributed to the iteration only
                     when exactly one came back.
    failure_class  — agent_capacity.classify_failure() result for this
                     iteration's captured output, when the process produced
                     no run record at all. A class in CAPACITY_FAILURE_CLASSES
                     (account-level: auth/billing) is a "blocked" outcome, not
                     a plain "failed" one -- it should not count toward the
                     circuit breaker (the agent itself is unavailable, not
                     misbehaving) and should stop the drain via the existing
                     capacity_gated path once the cache reflects it.
    pending_decisions — decision ids from the record's `pending_decisions`
                     audit trail that still await a human answer. They make
                     the iteration a first-class pending_user_decision
                     handoff rather than a generic blocked/failed state --
                     the one-shot yielded ownership instead of guessing --
                     except after an explicit success state, which outranks
                     leftover audit entries so completed work is never wedged
                     by stale bookkeeping.
    """
    claimed_briefs = list(claimed_briefs)
    unresolved = list(pending_decisions)
    brief = claimed_briefs[0] if len(claimed_briefs) == 1 else None
    if record_fields is not None:
        state = record_fields.get("final_status") or record_fields.get("finish")
        pr = record_fields.get("pull_request") or record_fields.get("pr_url")
        if state in SUCCESS_STATES:
            return Outcome("success", state, brief, pr)
        if state == "pending_user_decision" or unresolved:
            return Outcome("pending_user_decision", "pending_user_decision",
                           brief, pr, pending_decisions=unresolved)
        if state in BLOCKED_STATES:
            return Outcome("blocked", state, brief, pr)
        if state in FAILED_STATES:
            return Outcome("failed", state, brief, pr)
        # Record exists but never finished — the one-shot died mid-run.
        # A timeout (exit 124) with a PR already captured means the
        # substantive work (claim, implement, open/merge PR) succeeded and
        # only post-PR wrap-up was still running when the iteration timeout
        # killed the process — that is not a failure to count against the
        # circuit breaker. A timeout with no PR is a real failure.
        if exit_code == 124 and pr:
            return Outcome("timeout_after_pr", state, brief, pr)
        # Drain launches unattended terminal one-shots. A clean provider exit
        # with an unfinished record is therefore a lifecycle failure, not a
        # legitimate asynchronous hand-off: no durable process owns the
        # remaining CI/review-thread loop (PR #2244 incident).
        if exit_code == 0:
            return Outcome("failed", "failed_recoverable", brief, pr)
        return Outcome("failed", state, brief, pr)
    if claimed_delta == 0 and exit_code == 0:
        return Outcome("no_pick")
    if failure_class in CAPACITY_FAILURE_CLASSES:
        return Outcome("blocked", f"blocked_capacity_{failure_class}", brief_id=brief)
    return Outcome("failed", brief_id=brief)


# ---------------------------------------------------------------------------
# Driver loop


@dataclass
class DrainConfig:
    work_queue_py: Path
    runs_dir: Path
    capacity_cache: Path
    lock_file: Path
    agent: str = "claude"
    fallback_agents: List[str] = field(default_factory=list)
    transcript_dir: Optional[Path] = None
    agent_cmd: Optional[str] = None
    go_repo: Optional[str] = None
    permission_args: List[str] = field(default_factory=list)
    max_items: int = 0
    budget_minutes: int = 0
    failure_threshold: int = 2
    iteration_timeout: int = 45 * 60
    queue_dir: Optional[Path] = None
    dry_run: bool = False
    repos_root: Optional[Path] = None
    seed_backlog: bool = True
    max_workers: int = 1
    stuck_threshold: int = 3
    stuck_history_path: Optional[Path] = None


@dataclass
class SpawnOutcome:
    """One-shot process result, including captured output so a record-less
    failure can still be classified (see agent_capacity.classify_failure) --
    the prior stdout/stderr=DEVNULL discipline made that classification
    impossible, so every record-less failure looked identical regardless of
    cause (confirmed live 2026-08-02: a Codex usage-cap exhaustion produced
    the same bare `outcome=failed exit=1` as a generic crash)."""
    exit_code: int
    stdout: str = ""
    stderr: str = ""


def run_one_shot(cmd: List[str], timeout: int,
                 env: Optional[Dict[str, str]] = None,
                 cwd: Optional[Path] = None) -> SpawnOutcome:
    """Spawn `cmd` (typically `worktrail-skill-dispatch`) and enforce
    `timeout`. Runs the child in its own session (`start_new_session=True`)
    so that, on timeout, the whole process group -- not just the immediate
    child -- can be killed. This matters because `worktrail-skill-dispatch`
    itself spawns the actual provider CLI (claude/codex/opencode) without
    redirecting its own stdout/stderr, so that grandchild inherits the
    immediate child's process group by default; a plain `subprocess.run`
    timeout only SIGKILLs the immediate child's PID, orphaning a still-live
    provider-CLI grandchild that never gets cleaned up (confirmed live
    2026-08-20: a rate-limited opencode session left running indefinitely
    after its parent `worktrail-skill-dispatch` process was gone)."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, cwd=str(cwd) if cwd else None, start_new_session=True)
    except FileNotFoundError:
        return SpawnOutcome(127)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return SpawnOutcome(proc.returncode, stdout or "", stderr or "")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return SpawnOutcome(124, stdout or "", stderr or "")


def drain(config: DrainConfig,
          spawner: Optional[Callable[[List[str], int], SpawnOutcome]] = None,
          clock: Callable[[], float] = time.time,
          log: Callable[[str], None] = print) -> Dict[str, object]:
    """Run the drain loop. Returns a summary dict (also the --json payload)."""
    uses_builtin_spawner = spawner is None
    agent_env = build_agent_environment()
    slot = acquire_lock_slot(config.lock_file, config.max_workers)
    if slot is None:
        return {"stopped": "lock_held",
                "detail": f"another drain owns {config.lock_file}",
                "iterations": []}
    scratch_dir = worker_scratch_dir(slot)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    if uses_builtin_spawner:
        spawner = functools.partial(run_one_shot, env=agent_env, cwd=scratch_dir)
    started = clock()
    state = LoopState(
        max_items=config.max_items,
        deadline=(started + config.budget_minutes * 60) if config.budget_minutes else None,
        failure_threshold=config.failure_threshold,
    )
    iterations: List[Dict[str, object]] = []
    pending_approvals: List[str] = []
    pending_user_decisions: List[str] = []
    resumed: Dict[str, List[Dict[str, Any]]] = {}
    stuck_remediations: List[Dict[str, Any]] = []
    # Candidates are evaluated in this fixed priority order every iteration
    # (see select_available_agent) -- a fallback is never "sticky": once a
    # higher-priority agent's persisted gate expires, the very next iteration
    # picks it back up automatically, no restart or config edit required.
    candidates = [config.agent] + list(config.fallback_agents)
    active_agent = config.agent
    seeded_backlog: Dict[str, Any] = {}
    routing = machine_wide_routing()
    try:
        if slot == 0 and config.repos_root is not None and not config.dry_run:
            resumed = sweep_remediations(
                config.repos_root, config.go_repo, candidates, config.capacity_cache,
                config.iteration_timeout, spawner, log, routing=routing)
            if config.seed_backlog:
                # Top the queue up from backlog invisible to auto mode
                # (needs-tasks specs, under-specced epics) BEFORE the loop's
                # first ready-count check, so freshly seeded briefs drain in
                # this same pass. Best-effort like the sweep: a seeding
                # failure never aborts the drain.
                try:
                    seeded_backlog = seed_backlog_mod.seed_backlog(
                        config.repos_root, config.go_repo,
                        queue_base=config.queue_dir, log=log)
                except Exception as exc:  # noqa: BLE001
                    log(f"seed-backlog error: {exc}")
        cmd = build_command(active_agent, config.permission_args,
                            config.agent_cmd, config.go_repo)
        while True:
            queue = list_queue(config.work_queue_py, config.queue_dir)
            state.ready_count = count_ready_briefs(queue)
            cache = read_capacity_cache(config.capacity_cache)
            # A templated --agent-cmd has no per-agent identity to switch, so
            # fallback selection only applies to the named-agent shapes.
            if config.agent_cmd is None:
                chosen = select_available_agent(cache, candidates, routing)
                if chosen is None:
                    state.agent_capacity_gated = True
                else:
                    state.agent_capacity_gated = False
                    if chosen != active_agent:
                        log(f"agent switch: {active_agent} -> {chosen} (capacity)")
                        active_agent = chosen
                        cmd = build_command(active_agent, config.permission_args,
                                            config.agent_cmd, config.go_repo)
            else:
                state.agent_capacity_gated = capacity_gated(cache, active_agent)
            decision = decide(state, clock())
            if not decision.proceed:
                log(f"drain stop: {decision.reason}")
                break
            if config.dry_run:
                log(f"dry-run: would launch {' '.join(cmd)} "
                    f"({state.ready_count} ready briefs)")
                break
            if uses_builtin_spawner:
                validate_agent_runtime(active_agent, agent_env)
            iter_start = clock()
            ready_before = state.ready_count
            known_records = (set(config.runs_dir.glob("*/*.yaml"))
                              if config.runs_dir.is_dir() else set())
            open_decisions_before = set(
                decisions_mod.open_decision_ids(config.queue_dir))
            spawned = spawner(cmd, config.iteration_timeout)
            exit_code = spawned.exit_code
            queue_after = list_queue(config.work_queue_py, config.queue_dir)
            ready_after = count_ready_briefs(queue_after)
            claimed_delta = max(0, ready_before - ready_after)
            claimed_briefs = claimed_brief_ids(queue, queue_after)
            # A single claimed brief pins the run-record lookup to that
            # brief's own repo, so a concurrently-running worker's record
            # landing in a different repo's run directory can't be
            # misattributed to this iteration; an ambiguous multi-claim (or
            # no-claim) iteration keeps today's unfiltered lookup.
            repo_filter = None
            if len(claimed_briefs) == 1:
                claimed_id = claimed_briefs[0]
                for brief in queue.get("briefs") or []:
                    filename = brief.get("filename") or ""
                    brief_id = filename[:-3] if filename.endswith(".md") else filename
                    if brief_id == claimed_id:
                        repo = brief.get("repo")
                        repo_filter = Path(repo).name if repo else None
                        break
            record_path = newest_run_record(
                config.runs_dir, known_records, repo_filter=repo_filter)
            fields = None
            record_text = None
            if record_path is not None:
                try:
                    record_text = record_path.read_text(encoding="utf-8")
                    fields = parse_run_record(record_text)
                except OSError:
                    fields = None
            # classify_outcome only consults failure_class on its record-less
            # fallback path (and there only past the no_pick check), so it is
            # harmless to compute unconditionally whenever no run record
            # appeared -- a clean no-claim exit still resolves to "no_pick".
            failure_class = (agent_capacity.classify_failure(
                                 exit_code, spawned.stdout, spawned.stderr)
                             if fields is None else None)
            unresolved_decisions = unresolved_decision_ids(record_text or "")
            outcome = classify_outcome(fields, claimed_delta, exit_code,
                                       claimed_briefs, failure_class,
                                       pending_decisions=unresolved_decisions)
            if outcome.pr_url and fields is not None:
                applied = ensure_pr_risk_label(
                    fields.get("repository"), outcome.pr_url, fields.get("risk_level"))
                if applied:
                    log(f"drain: added missing {applied} label to {outcome.pr_url}")
            if outcome.state and outcome.state.startswith("blocked_capacity_"):
                # Only classify_outcome's own CAPACITY_FAILURE_CLASSES check
                # produces this state, and only when failure_class was a
                # member of that (non-None) set -- see classify_outcome.
                assert failure_class is not None
                reset_at = (agent_capacity.parse_explicit_reset(
                                f"{spawned.stdout}\n{spawned.stderr}")
                            or agent_capacity.retry_time(failure_class))
                record_capacity_gate(config.capacity_cache, active_agent,
                                     failure_class, reset_at)
            state.iteration += 1
            state.last_outcome = outcome
            # A blocked_product_decision one-shot that actually FILED a
            # decision record this iteration handled the block cleanly: the
            # question is queued for a human and the brief left the ready
            # pool, so there is nothing to escalate. A decision-less block
            # still counts toward the breaker -- that pressure is what keeps
            # filing honest (decision-queue.md#decision-filing-guardrails).
            decisions_filed = (
                sorted(set(decisions_mod.open_decision_ids(config.queue_dir))
                       - open_decisions_before)
                if outcome.state == "blocked_product_decision" else [])
            if decisions_filed:
                log("decision filed for a human: "
                    f"{', '.join(decisions_filed)} (worktrail-decision list)")
            if outcome.kind == "pending_user_decision":
                # Fail-closed, recoverable handoff: record the exact ids for
                # the summary, tell the operator how to resume, and let the
                # decide() check at the top of the next pass stop the drain.
                surfaced = list(outcome.pending_decisions or [])
                pending_user_decisions.extend(surfaced)
                log(f"pending user decision ({outcome.brief_id or 'no brief claimed'}): "
                    f"{', '.join(surfaced) or '(decision id not recorded)'} -- "
                    "present/answer the decision, then resume attended via "
                    "`worktrail-skill-dispatch --resume-decision <id>`")
            if outcome.kind in ("failed", "blocked") and not decisions_filed:
                state.consecutive_failures += 1
            elif outcome.kind == "success":
                state.consecutive_failures = 0
            # timeout_after_pr (and a decision-filed block) leaves
            # consecutive_failures unchanged: the automation did its job but
            # did not reach a full terminal success state.
            if outcome.state == "completed_awaiting_human_approval":
                pending_approvals.append(outcome.pr_url or outcome.brief_id or "?")
            elapsed = int(clock() - iter_start)
            transcript_path = write_iteration_transcript(
                config.transcript_dir, state.iteration, active_agent,
                exit_code, outcome, spawned.stdout, spawned.stderr)
            line = (f"[{state.iteration}"
                    f"{'/' + str(config.max_items) if config.max_items else ''}] "
                    f"agent={active_agent} "
                    f"outcome={outcome.state or outcome.kind} "
                    f"brief={outcome.brief_id or '-'} pr={outcome.pr_url or '-'} "
                    f"failure_class={failure_class or '-'} "
                    f"claimed_delta={claimed_delta} "
                    f"claimed_brief_count={len(claimed_briefs)} "
                    f"exit={exit_code} elapsed={elapsed}s"
                    f"{' transcript=' + str(transcript_path) if transcript_path else ''}")
            log(line)
            iterations.append({
                "n": state.iteration, "agent": active_agent,
                "kind": outcome.kind, "state": outcome.state,
                "brief": outcome.brief_id, "pr": outcome.pr_url,
                "failure_class": failure_class,
                "claimed_delta": claimed_delta,
                "claimed_brief_count": len(claimed_briefs),
                "exit_code": exit_code, "elapsed_s": elapsed,
                "decisions_filed": decisions_filed,
                "transcript": str(transcript_path) if transcript_path else None,
            })
        if slot == 0 and config.repos_root is not None and not config.dry_run and state.iteration > 0:
            # Re-swept post-pass, but only when this pass actually ran a queue
            # iteration -- an empty queue means nothing could have changed
            # since the pre-loop sweep above, so re-sweeping would just
            # re-invoke full-real for the exact same still-open quarantine.
            post = sweep_remediations(
                config.repos_root, config.go_repo, candidates, config.capacity_cache,
                config.iteration_timeout, spawner, log)
            for key, findings in post.items():
                resumed.setdefault(key, []).extend(findings)
        if slot == 0 and config.repos_root is not None and not config.dry_run:
            # Best-effort like the sweeps above: the detector is advisory
            # state, and a failure here (e.g. an unwritable history file)
            # must never abort the drain pass.
            try:
                stuck_remediations = stuck_remediation.sweep_and_record(
                    resumed,
                    config.stuck_history_path or stuck_remediation.history_path(),
                    config.stuck_threshold)
                for entry in stuck_remediations:
                    log(f"stuck remediation: {entry['key']} {entry['repo_name']} "
                        f"{entry['spec_id']} streak={entry['streak']}")
            except Exception as exc:  # noqa: BLE001
                log(f"stuck-remediation error: {exc}")
    finally:
        release_lock_slot(config.lock_file, slot)
    summary: Dict[str, object] = {
        "stopped": decision.reason if not decision.proceed else "dry_run",
        "iterations": iterations,
        "pending_approvals": pending_approvals,
        "pending_user_decisions": pending_user_decisions,
        "resumed_quarantines": resumed.get("quarantined_budget_exhausted", []),
        "resumed_verify_pending": resumed.get("verify_pending", []),
        "resumed_stale_bookkeeping": resumed.get("stale_bookkeeping", []),
        "resumed_sync_pending": resumed.get("sync_pending", []),
        "resumed_openspec_archive": resumed.get("openspec_archive", []),
        "stuck_remediations": stuck_remediations,
        "seeded_backlog": seeded_backlog,
        "decisions_open": len(decisions_mod.open_decision_ids(config.queue_dir)),
        "elapsed_s": int(clock() - started),
    }
    if pending_approvals:
        log(f"pending human approval: {', '.join(pending_approvals)}")
    if pending_user_decisions:
        log(f"pending user decision(s) blocking resume: "
            f"{', '.join(pending_user_decisions)} -- answer with "
            "`worktrail-decision answer <id> --answer ...`, then resume via "
            "`worktrail-skill-dispatch --resume-decision <id>`")
    if summary["decisions_open"]:
        log(f"decisions awaiting a human: {summary['decisions_open']} "
            "-- review with `worktrail-decision list` and answer with "
            "`worktrail-decision answer <id> --answer ...`")
    return summary


# ---------------------------------------------------------------------------
# CLI


def default_work_queue_py() -> Optional[Path]:
    return WORK_QUEUE_PY if WORK_QUEUE_PY.is_file() else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain the work queue via fresh-context worktrail-go auto one-shots.")
    parser.add_argument("--max-items", type=int, default=0,
                        help="iteration ceiling (0 = until queue empty)")
    parser.add_argument("--budget-minutes", type=int, default=0,
                        help="wall-clock budget (0 = none)")
    parser.add_argument("--agent", default=None, choices=SUPPORTED_AGENTS,
                        help="one-shot provider; when omitted, falls back to the "
                             "operator config's drain.agent "
                             "(worktrail_home()/config.json), then to claude")
    parser.add_argument("--fallback-agent", action="append", default=[],
                        dest="fallback_agents", choices=SUPPORTED_AGENTS, metavar="AGENT",
                        help="additional agent to try, in priority order, when a "
                             "higher-priority agent is capacity-gated (repeatable). "
                             "Re-checked every iteration, so a fallback is never "
                             "sticky -- the primary is used again automatically "
                             "once its gate's retry_after passes.")
    parser.add_argument("--go-repo", default=None, metavar="REPO",
                        help="restrict picks to one repo: prompt becomes "
                             "'worktrail-go REPO auto'")
    parser.add_argument("--agent-cmd", default=None,
                        help="full command template with {prompt}; overrides --agent shape")
    parser.add_argument("--permission-arg", action="append", default=[],
                        dest="permission_args", metavar="FLAG",
                        help="explicit passthrough flag for the one-shot CLI "
                             "(repeatable; nothing is added by default)")
    parser.add_argument("--consecutive-failures", type=int, default=2,
                        help="circuit-breaker threshold (default 2)")
    parser.add_argument("--iteration-timeout-minutes", type=int, default=45)
    parser.add_argument("--max-workers", type=int, default=None,
                        help="concurrent drain-worker slots against the same "
                             "--lock-file; resolved CLI > config > built-in -- "
                             "this flag when passed, else the operator config's "
                             "drain.max_workers (worktrail_home()/config.json), "
                             "else 2")
    parser.add_argument("--queue-dir", type=Path, default=None,
                        help="WORK_QUEUE_DIR override for queue checks")
    parser.add_argument("--runs-dir", type=Path,
                        default=worktrail_home() / "runs")
    parser.add_argument("--capacity-cache", type=Path,
                        default=agent_capacity.cache_path())
    parser.add_argument("--lock-file", type=Path,
                        default=worktrail_home() / "drain.lock")
    parser.add_argument("--transcript-dir", type=Path, default=None,
                        help="persist each iteration's raw one-shot stdout/stderr here "
                             "(bounded to the most recent 50 files); omit to write "
                             "nothing, matching prior behavior")
    parser.add_argument("--work-queue-py", type=Path, default=default_work_queue_py(),
                        help="path to work_queue.py (auto-resolved from sibling skill)")
    parser.add_argument("--repos-root", type=Path,
                        default=Path.home() / "projects",
                        help="swept before and after each drain pass for QUARANTINED/"
                             "budget_exhausted groups (quarantine_selfcheck), each resumed "
                             "with a plain full-real re-run; a nonexistent path is a no-op")
    parser.add_argument("--no-seed-backlog", action="store_true",
                        help="skip the pre-loop backlog seeding step (needs-tasks "
                             "specs and under-specced epics are then not converted "
                             "into queue briefs this pass)")
    parser.add_argument("--stuck-threshold", type=int, default=3,
                        help="remediation-table findings whose action reports "
                             "apparent success this many sweeps in a row are "
                             "flagged stuck (default 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the first decision + command, launch nothing")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="print the run summary as JSON on exit")
    args = parser.parse_args(argv)

    if args.work_queue_py is None or not Path(args.work_queue_py).is_file():
        print("error: work_queue.py not found; pass --work-queue-py", file=sys.stderr)
        return 2
    # CLI > operator config (worktrail_home()/config.json, "drain" section) >
    # built-in default. Explicit automation (the nightly drain script passes
    # --agent/--fallback-agent itself) is never affected by the config file;
    # a config-less manual `worktrail-drain` picks up the operator's stated
    # provider preference instead of silently defaulting to claude.
    try:
        operator_drain = operator_drain_config()
    except OperatorConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    agent = args.agent or operator_drain["agent"] or "claude"
    fallback_agents = list(args.fallback_agents) or list(operator_drain["fallback_agents"])
    invalid = [a for a in [agent, *fallback_agents] if a not in SUPPORTED_AGENTS]
    if invalid:
        print(f"error: unsupported agent(s) {', '.join(sorted(set(invalid)))} "
              f"from {operator_config_path()}; supported: "
              f"{', '.join(SUPPORTED_AGENTS)}", file=sys.stderr)
        return 2
    max_workers = (args.max_workers if args.max_workers is not None
                   else operator_drain["max_workers"])
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        print(f"error: max_workers must be a positive integer, got {max_workers!r} "
              f"from --max-workers or {operator_config_path()}", file=sys.stderr)
        return 2
    config = DrainConfig(
        work_queue_py=Path(args.work_queue_py),
        runs_dir=args.runs_dir,
        capacity_cache=args.capacity_cache,
        lock_file=args.lock_file,
        agent=agent,
        fallback_agents=fallback_agents,
        transcript_dir=args.transcript_dir,
        agent_cmd=args.agent_cmd,
        go_repo=args.go_repo,
        permission_args=list(args.permission_args),
        max_items=args.max_items,
        budget_minutes=args.budget_minutes,
        failure_threshold=args.consecutive_failures,
        iteration_timeout=args.iteration_timeout_minutes * 60,
        queue_dir=args.queue_dir,
        dry_run=args.dry_run,
        repos_root=args.repos_root,
        seed_backlog=not args.no_seed_backlog,
        max_workers=max_workers,
        stuck_threshold=args.stuck_threshold,
        stuck_history_path=stuck_remediation.history_path(),
    )
    try:
        summary = drain(config)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if summary.get("stopped") == "lock_held":
        print(f"refused: {summary['detail']}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

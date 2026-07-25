#!/usr/bin/env python3
"""Queue-drain driver: repeatedly launch fresh-context one-shots of `/go auto`
until the work queue is empty or a stop condition fires.

Each iteration spawns ONE headless agent CLI process (`claude -p "/go auto"` by
default), waits for it to exit, classifies the outcome from the newest run
record under the runs dir, then re-checks the queue. A fresh process per
iteration means fresh context by construction — nothing accumulates.

Stop conditions (each printed, never silent):
  queue_empty            no ready briefs before an iteration
  no_pick                an iteration claimed nothing and produced no run record
  capacity_gated         persisted capacity gate for the configured agent
  circuit_breaker        N consecutive failed iterations (default 2)
  max_items              iteration ceiling reached
  budget_exhausted       wall-clock budget reached
  lock_held              another drain already owns this queue

`completed_awaiting_human_approval` NOTES the pending PR and continues — that
is a gate working, not a stall.

Permission posture is explicit: no permission-bypass flag is ever added by
default; pass each one via a repeated --permission-arg.

Usage:
  drain.py [--max-items N] [--budget-minutes M] [--agent claude|codex|opencode]
           [--agent-cmd TEMPLATE] [--permission-arg FLAG]...
           [--consecutive-failures N] [--iteration-timeout-minutes M]
           [--queue-dir DIR] [--runs-dir DIR] [--capacity-cache PATH]
           [--lock-file PATH] [--dry-run] [--json]

Exit codes: 0 = drained/stopped cleanly with a reported reason; 2 = refused to
start (lock held, bad args, missing queue dir).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

PROMPT = "/go auto"

SUPPORTED_AGENTS = ("claude", "codex", "opencode")

# Mirrors specs-parallel-orchestrator/scripts/spawnlib.py build_cmd() shapes,
# minus JSON-output flags (outcomes are read from run records, not stdout).
BASE_CMDS: Dict[str, List[str]] = {
    "claude": ["claude", "-p"],
    "opencode": ["opencode", "run"],
    "codex": ["codex", "exec", "-s", "workspace-write"],
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
    prompt = f"/go {go_repo} auto" if go_repo else PROMPT
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
    return ["codex", "exec", "-s", "workspace-write", *permission_args, prompt]


# ---------------------------------------------------------------------------
# Queue state


def count_ready_briefs(queue_json: dict) -> int:
    """Ready = not blocked and not deferred by next-check-after backoff."""
    briefs = queue_json.get("briefs") or []
    return sum(1 for b in briefs
               if not b.get("blocked") and not b.get("not_yet_due"))


def list_queue(work_queue_py: Path, queue_dir: Optional[Path]) -> dict:
    env = dict(os.environ)
    if queue_dir is not None:
        env["WORK_QUEUE_DIR"] = str(queue_dir)
    out = subprocess.run(
        [sys.executable, str(work_queue_py), "list", "--json"],
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


def newest_run_record(runs_dir: Path, known: Iterable[Path] = ()) -> Optional[Path]:
    """The run-record YAML this iteration produced, across repos.

    Attribution is by set difference against a before-spawn snapshot (`known`),
    not by mtime comparison: two records written within the same filesystem
    mtime-resolution tick can tie or invert under an mtime `>= since_epoch`
    filter, so a stale record can outrank -- or exclude -- the real one
    depending on directory-iteration order, which Python does not guarantee.
    """
    if not runs_dir.is_dir():
        return None
    known_set = known if isinstance(known, (set, frozenset)) else set(known)
    candidates = [path for path in runs_dir.glob("*/*.yaml") if path not in known_set]
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


def capacity_gated(cache: dict, agent: str) -> bool:
    """True when every cached entry for `agent` carries an active gate.

    The cache (agent_capacity.py) keys entries by provider identifiers like
    'claude' or 'claude:opus'. No entry for the agent means no known gate.
    """
    providers = cache.get("providers") if isinstance(cache.get("providers"), dict) else cache
    if not isinstance(providers, dict):
        return False
    matched = [v for k, v in providers.items()
               if isinstance(v, dict) and (k == agent or str(k).startswith(agent + ":"))]
    if not matched:
        return False
    return all(str(v.get("status", "")).lower() in ("gated", "unavailable", "blocked")
               for v in matched)


def read_capacity_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


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


# ---------------------------------------------------------------------------
# Decision function (pure — the unit-tested core)


@dataclass
class Outcome:
    """Classified result of one iteration."""
    kind: str                 # "success" | "blocked" | "failed" | "no_pick"
    state: Optional[str] = None      # run-record completion state, if any
    brief_id: Optional[str] = None
    pr_url: Optional[str] = None


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
        if last.kind == "no_pick":
            return Decision(False, "no_pick: /go auto claimed nothing "
                                   "(null auto_pick or picks not eligible)")
        if state.consecutive_failures >= state.failure_threshold:
            return Decision(False, f"circuit_breaker: {state.consecutive_failures} "
                                   f"consecutive failed iterations")
        if last.kind == "blocked" and state.agent_capacity_gated:
            return Decision(False, "capacity_gated: provider capacity gate persisted "
                                   "for the configured agent")
    if state.ready_count <= 0:
        return Decision(False, "queue_empty: no ready briefs")
    if state.max_items and state.iteration >= state.max_items:
        return Decision(False, f"max_items: {state.max_items} iterations done")
    if state.deadline is not None and now >= state.deadline:
        return Decision(False, "budget_exhausted: wall-clock budget reached")
    return Decision(True, "ready briefs remain")


def classify_outcome(record_fields: Optional[Dict[str, Optional[str]]],
                     claimed_delta: int,
                     exit_code: int) -> Outcome:
    """Classify one iteration from its newest run record + queue movement.

    record_fields — parsed run record created/updated during the iteration,
                    or None when no record appeared.
    claimed_delta — briefs that left queue/ during the iteration (>=0).
    exit_code     — the one-shot process exit code.
    """
    if record_fields is not None:
        state = record_fields.get("final_status") or record_fields.get("finish")
        brief = record_fields.get("handoffs_consumed")  # scalar only when single
        pr = record_fields.get("pull_request") or record_fields.get("pr_url")
        if state in SUCCESS_STATES:
            return Outcome("success", state, brief, pr)
        if state in BLOCKED_STATES:
            return Outcome("blocked", state, brief, pr)
        if state in FAILED_STATES:
            return Outcome("failed", state, brief, pr)
        # Record exists but never finished — the one-shot died mid-run.
        return Outcome("failed", state, brief, pr)
    if claimed_delta == 0 and exit_code == 0:
        return Outcome("no_pick")
    return Outcome("failed")


# ---------------------------------------------------------------------------
# Driver loop


@dataclass
class DrainConfig:
    work_queue_py: Path
    runs_dir: Path
    capacity_cache: Path
    lock_file: Path
    agent: str = "claude"
    agent_cmd: Optional[str] = None
    go_repo: Optional[str] = None
    permission_args: List[str] = field(default_factory=list)
    max_items: int = 0
    budget_minutes: int = 0
    failure_threshold: int = 2
    iteration_timeout: int = 45 * 60
    queue_dir: Optional[Path] = None
    dry_run: bool = False


def run_one_shot(cmd: List[str], timeout: int) -> int:
    try:
        proc = subprocess.run(cmd, timeout=timeout,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124
    except FileNotFoundError:
        return 127


def drain(config: DrainConfig,
          spawner: Optional[Callable[[List[str], int], int]] = None,
          clock: Callable[[], float] = time.time,
          log: Callable[[str], None] = print) -> Dict[str, object]:
    """Run the drain loop. Returns a summary dict (also the --json payload)."""
    spawner = spawner or run_one_shot
    if not acquire_lock(config.lock_file):
        return {"stopped": "lock_held",
                "detail": f"another drain owns {config.lock_file}",
                "iterations": []}
    started = clock()
    state = LoopState(
        max_items=config.max_items,
        deadline=(started + config.budget_minutes * 60) if config.budget_minutes else None,
        failure_threshold=config.failure_threshold,
    )
    iterations: List[Dict[str, object]] = []
    pending_approvals: List[str] = []
    try:
        cmd = build_command(config.agent, config.permission_args,
                            config.agent_cmd, config.go_repo)
        while True:
            queue = list_queue(config.work_queue_py, config.queue_dir)
            state.ready_count = count_ready_briefs(queue)
            state.agent_capacity_gated = capacity_gated(
                read_capacity_cache(config.capacity_cache), config.agent)
            decision = decide(state, clock())
            if not decision.proceed:
                log(f"drain stop: {decision.reason}")
                break
            if config.dry_run:
                log(f"dry-run: would launch {' '.join(cmd)} "
                    f"({state.ready_count} ready briefs)")
                break
            iter_start = clock()
            ready_before = state.ready_count
            known_records = (set(config.runs_dir.glob("*/*.yaml"))
                              if config.runs_dir.is_dir() else set())
            exit_code = spawner(cmd, config.iteration_timeout)
            record_path = newest_run_record(config.runs_dir, known_records)
            fields = None
            if record_path is not None:
                try:
                    fields = parse_run_record(record_path.read_text(encoding="utf-8"))
                except OSError:
                    fields = None
            ready_after = count_ready_briefs(
                list_queue(config.work_queue_py, config.queue_dir))
            claimed_delta = max(0, ready_before - ready_after)
            outcome = classify_outcome(fields, claimed_delta, exit_code)
            state.iteration += 1
            state.last_outcome = outcome
            if outcome.kind in ("failed", "blocked"):
                state.consecutive_failures += 1
            elif outcome.kind == "success":
                state.consecutive_failures = 0
            if outcome.state == "completed_awaiting_human_approval":
                pending_approvals.append(outcome.pr_url or outcome.brief_id or "?")
            elapsed = int(clock() - iter_start)
            line = (f"[{state.iteration}"
                    f"{'/' + str(config.max_items) if config.max_items else ''}] "
                    f"outcome={outcome.state or outcome.kind} "
                    f"brief={outcome.brief_id or '-'} pr={outcome.pr_url or '-'} "
                    f"exit={exit_code} elapsed={elapsed}s")
            log(line)
            iterations.append({
                "n": state.iteration, "kind": outcome.kind, "state": outcome.state,
                "brief": outcome.brief_id, "pr": outcome.pr_url,
                "exit_code": exit_code, "elapsed_s": elapsed,
            })
    finally:
        release_lock(config.lock_file)
    summary: Dict[str, object] = {
        "stopped": decision.reason if not decision.proceed else "dry_run",
        "iterations": iterations,
        "pending_approvals": pending_approvals,
        "elapsed_s": int(clock() - started),
    }
    if pending_approvals:
        log(f"pending human approval: {', '.join(pending_approvals)}")
    return summary


# ---------------------------------------------------------------------------
# CLI


def default_work_queue_py() -> Optional[Path]:
    candidate = (Path(__file__).resolve().parent.parent
                 / "workqueue" / "work_queue.py")
    return candidate if candidate.is_file() else None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain the work queue via fresh-context /go auto one-shots.")
    parser.add_argument("--max-items", type=int, default=0,
                        help="iteration ceiling (0 = until queue empty)")
    parser.add_argument("--budget-minutes", type=int, default=0,
                        help="wall-clock budget (0 = none)")
    parser.add_argument("--agent", default="claude", choices=SUPPORTED_AGENTS)
    parser.add_argument("--go-repo", default=None, metavar="REPO",
                        help="restrict picks to one repo: prompt becomes '/go REPO auto'")
    parser.add_argument("--agent-cmd", default=None,
                        help="full command template with {prompt}; overrides --agent shape")
    parser.add_argument("--permission-arg", action="append", default=[],
                        dest="permission_args", metavar="FLAG",
                        help="explicit passthrough flag for the one-shot CLI "
                             "(repeatable; nothing is added by default)")
    parser.add_argument("--consecutive-failures", type=int, default=2,
                        help="circuit-breaker threshold (default 2)")
    parser.add_argument("--iteration-timeout-minutes", type=int, default=45)
    parser.add_argument("--queue-dir", type=Path, default=None,
                        help="WORK_QUEUE_DIR override for queue checks")
    parser.add_argument("--runs-dir", type=Path,
                        default=Path.home() / ".go" / "runs")
    parser.add_argument("--capacity-cache", type=Path,
                        default=Path(os.environ.get(
                            "GO_AGENT_CAPACITY_CACHE",
                            Path.home() / ".go" / "agent-capacity.json")))
    parser.add_argument("--lock-file", type=Path,
                        default=Path.home() / ".go" / "drain.lock")
    parser.add_argument("--work-queue-py", type=Path, default=default_work_queue_py(),
                        help="path to work_queue.py (auto-resolved from sibling skill)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the first decision + command, launch nothing")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="print the run summary as JSON on exit")
    args = parser.parse_args(argv)

    if args.work_queue_py is None or not Path(args.work_queue_py).is_file():
        print("error: work_queue.py not found; pass --work-queue-py", file=sys.stderr)
        return 2
    config = DrainConfig(
        work_queue_py=Path(args.work_queue_py),
        runs_dir=args.runs_dir,
        capacity_cache=args.capacity_cache,
        lock_file=args.lock_file,
        agent=args.agent,
        agent_cmd=args.agent_cmd,
        go_repo=args.go_repo,
        permission_args=list(args.permission_args),
        max_items=args.max_items,
        budget_minutes=args.budget_minutes,
        failure_threshold=args.consecutive_failures,
        iteration_timeout=args.iteration_timeout_minutes * 60,
        queue_dir=args.queue_dir,
        dry_run=args.dry_run,
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

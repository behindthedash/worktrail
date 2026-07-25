#!/usr/bin/env python3
"""Live progress for the orchestrator: per-step timing + an on-demand checklist.

The live fan-out spawns each worker as a single BLOCKING `claude -p` subprocess
with captured (not streamed) output, so the terminal shows nothing between a
step starting and finishing -- potentially many minutes. This module closes that
visibility gap with two complementary pieces:

  - TIMING (in the run journal). live.py stamps each journal entry with
    ``started_at`` / ``ended_at`` / ``duration_s`` when a role completes. The
    journal therefore becomes auditable after the fact: "which step took
    longest" is answerable from the recorded data alone.

  - HEARTBEAT (a sidecar file). The journal only records a step AFTER it
    finishes, so it cannot represent the worker currently in flight. live.py
    writes a tiny sidecar (``run-<spec>.status.json``) the moment a step starts,
    naming the active task/role and its start time, so ``status`` can show live
    elapsed time for the blocking worker that has produced no entry yet.

The ``render`` function reads journal (completed steps + timing) + heartbeat
(in-flight step) and returns a checklist string.

Design rule: progress is observability, never load-bearing. Every write is
best-effort and swallows its own errors -- a progress failure must never break a
run. ``render`` is pure (no writes). No third-party deps; no import of live/
coordinator/dispatch (keeps this leaf-importable and avoids a cycle).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Status-string classification, kept in sync with coordinator semantics WITHOUT
# importing coordinator (this module must stay a dependency-free leaf).
_DONE = {"done", "completed"}
_FAILED = {"failed", "escalated"}


def heartbeat_path(journal_path: "str | Path") -> Path:
    """Sidecar status file beside the journal: ``run-X.json`` -> ``run-X.status.json``."""
    p = Path(journal_path)
    return p.with_name(p.stem + ".status.json")


def _now() -> float:
    return time.time()


def atomic_write_text(path: "str | Path", text: str) -> None:
    """Write *text* to *path* atomically: sibling temp file + ``os.replace``.

    The run journal and heartbeat are the state a resume relies on, and a run is
    designed to survive a process kill. A plain ``write_text`` interrupted
    mid-write leaves a TRUNCATED file, which then fails ``json.loads`` on resume
    and silently discards all prior progress. ``os.replace`` is atomic on POSIX
    (same directory == same filesystem), so a reader always sees either the old
    complete file or the new complete file -- never a torn one. Raises on real IO
    errors; callers that must never raise (heartbeat) wrap it.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _pid_alive(pid: Optional[int]) -> bool:
    """True if *pid* is a live process (best-effort POSIX ``kill -0``).

    Tells a genuinely-slow in-flight worker (orchestrator process still alive)
    from a phantom one left by a crashed run (process gone): the journal records a
    step only AFTER it finishes, so a dead run would otherwise show its last step
    ``running`` forever with ever-growing elapsed.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def _safe_load(path: "str | Path") -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_write(path: "str | Path", data: Dict[str, Any]) -> None:
    """Best-effort atomic JSON write. Never raises into a running orchestration."""
    try:
        atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass


def append_safety_net_events(journal_path: "str | Path", events: "list[Dict[str, Any]]") -> None:
    """Merge structured "took the safety net" observability events into the run
    journal's dedicated `safety_net_events` list, preserving every other key
    (`spec_id`, `entries`, `run_id`, ...) already on disk.

    Kept in a list separate from `entries` -- the per-task role-transition log
    `reconcile_from_journal` replays on resume -- so group-level events (e.g. an
    `automerge_preflight_fallback`, which has no single owning task) never need
    to look like a task step. `safety_net_report.py` scans both this list and
    `entries`' `dependency_file_drift` markers for cross-run aggregation.
    Best-effort via `_safe_load`/`_safe_write`: never raises into a running
    orchestration, and a no-op when `events` is empty.
    """
    if not events:
        return
    data = _safe_load(journal_path)
    if not data:
        return  # no journal on disk yet (or unreadable) -- nothing to attach to
    existing = list(data.get("safety_net_events", []))
    existing.extend(events)
    data["safety_net_events"] = existing
    _safe_write(journal_path, data)


def begin_step(
    journal_path: "str | Path",
    *,
    run_id: Optional[str],
    spec_id: str,
    task_id: str,
    role: str,
    phase: str = "fanout",
    started_at: Optional[float] = None,
) -> None:
    """Record the step now starting as the single in-flight worker (heartbeat).

    Used by the serial paths (resumed mid-flight tasks, verify). The concurrent
    fan-out uses ``write_actives`` to show several workers at once. ``pid`` lets
    ``render`` detect a phantom worker left by a crashed run.
    """
    _safe_write(
        heartbeat_path(journal_path),
        {
            "run_id": run_id,
            "spec_id": spec_id,
            "phase": phase,
            "pid": os.getpid(),
            "updated_at": _now(),
            "active": {
                "task": task_id,
                "role": role,
                "started_at": started_at if started_at is not None else _now(),
                "pid": os.getpid(),
            },
        },
    )


def write_actives(
    journal_path: "str | Path",
    *,
    run_id: Optional[str],
    spec_id: str,
    actives: List[Dict[str, Any]],
    phase: str = "fanout",
) -> None:
    """Heartbeat for N concurrent in-flight workers (parallel fan-out).

    ``actives`` is the orchestrator's in-memory snapshot of currently-running
    workers, each ``{"task", "role", "started_at", "pid"}``. The whole sidecar is
    rewritten on every change (no read-modify-write), so concurrent callers under
    one lock never race on the file. ``active`` (singular) is also written for any
    legacy single-worker reader.
    """
    actives = list(actives)
    _safe_write(
        heartbeat_path(journal_path),
        {
            "run_id": run_id,
            "spec_id": spec_id,
            "phase": phase,
            "pid": os.getpid(),
            "updated_at": _now(),
            "actives": actives,
            "active": actives[0] if len(actives) == 1 else None,
        },
    )


def set_phase(
    journal_path: "str | Path", phase: str, detail: "Dict[str, Any] | None" = None
) -> None:
    """Mark a phase change (e.g. fanout -> integrate -> verify -> done) and clear
    the in-flight workers, preserving run_id/spec_id when the sidecar exists."""
    hb = heartbeat_path(journal_path)
    cur = _safe_load(hb)
    payload = {
        "run_id": cur.get("run_id"),
        "spec_id": cur.get("spec_id"),
        "phase": phase,
        "pid": os.getpid(),
        "updated_at": _now(),
        "actives": [],
        "active": None,
    }
    if detail:
        payload.update(detail)
    _safe_write(hb, payload)


def set_idle(journal_path: "str | Path", phase: str = "idle") -> None:
    """No worker in flight (alias of set_phase for end-of-fan-out clarity)."""
    set_phase(journal_path, phase)


def set_group_phases(
    journal_path: "str | Path", group_phases: Dict[str, str]
) -> None:
    """Write a concurrent-phase summary to the heartbeat (pipelined runs).

    group_phases maps each active group to its current phase string
    ("fanout" | "integrating" | "verifying"). render() uses this dict to
    show "N fanning / M verifying" instead of a single misleading phase label.
    Existing heartbeat fields (actives, run_id, spec_id) are preserved.
    Best-effort via _safe_write: never raises into a running orchestration.
    """
    hb = heartbeat_path(journal_path)
    cur = _safe_load(hb)
    _safe_write(
        hb,
        {
            "run_id": cur.get("run_id"),
            "spec_id": cur.get("spec_id"),
            "phase": cur.get("phase", "fanout"),
            "pid": os.getpid(),
            "updated_at": _now(),
            "actives": cur.get("actives", []),
            "active": cur.get("active"),
            "group_phases": group_phases,
        },
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt_dur(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _marker(status: Optional[str], *, active: bool) -> str:
    if active:
        return "▶"
    if status in _DONE:
        return "✓"
    if status in _FAILED:
        return "✗"
    if status in (None, "pending"):
        return " "
    return "▶"  # claimed / implementing / reviewing / fixing / cleaning


# --- token-usage reporting ---------------------------------------------------
# Per-spawn `usage` (what `claude -p --output-format json` returns) is recorded on
# each journal entry by live.py. These aggregate it so a finished run can report
# WHERE the tokens went -- the prerequisite for any token-reduction work.

_USAGE_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


def summarize_usage(journal: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate journal entries' per-spawn `usage` into a per-role + grand-total
    breakdown. Returns roles -> {<token fields>, cost, spawns}, a matching `total`,
    and how many entries actually carried usage (older runs carry none)."""
    roles: Dict[str, Dict[str, Any]] = {}
    total: Dict[str, Any] = {f: 0 for f in _USAGE_FIELDS}
    total.update(cost=0.0, spawns=0)
    entries = journal.get("entries", []) or []
    with_usage = 0
    for e in entries:
        u = e.get("usage")
        if not u:
            continue
        with_usage += 1
        bucket = roles.setdefault(
            e.get("role", "?"), {**{f: 0 for f in _USAGE_FIELDS}, "cost": 0.0, "spawns": 0}
        )
        for f in _USAGE_FIELDS:
            v = int(u.get(f, 0) or 0)
            bucket[f] += v
            total[f] += v
        c = float(u.get("total_cost_usd", 0.0) or 0.0)
        bucket["cost"] += c
        total["cost"] += c
        bucket["spawns"] += 1
        total["spawns"] += 1
    return {
        "roles": roles,
        "total": total,
        "entries_with_usage": with_usage,
        "entries_total": len(entries),
    }


# --- pool-usage reporting ---------------------------------------------------
# TASK-007 stamps each journal entry with the `agent` CLI that actually served
# it (`claude`/`codex`/`opencode`/...). That label doubles as a pool identity:
# claude/codex are subscription CLIs, opencode is free, and any other agent
# name is assumed to be a raw API key (the opt-in, per-token-billed path) --
# defined once here so the mapping has a single source of truth (task DoD).
_POOL_BY_AGENT: Dict[str, str] = {
    "claude": "subscription",
    "codex": "subscription",
    "opencode": "free",
}
_DEFAULT_POOL = "api"


def _pool_for_agent(agent: str) -> str:
    return _POOL_BY_AGENT.get(agent, _DEFAULT_POOL)


def summarize_pool_usage(journal: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate journal entries' per-spawn `usage` into a per-pool -> per-agent
    breakdown, reading the `agent` label TASK-007 writes onto each entry.

    Entries missing either the `agent` label (pre-spec journals, AC-029) or
    `usage` are skipped entirely -- they contribute nothing to the pool
    grouping but are still counted by `summarize_usage`'s per-role breakdown,
    which never reads `agent`. That's the documented degradation for mixed
    labeled/unlabeled journals: unlabeled entries are omitted from the pool
    section rather than guessed at.
    """
    pools: Dict[str, Dict[str, Dict[str, Any]]] = {}
    with_agent = 0
    for e in journal.get("entries", []) or []:
        agent = e.get("agent")
        u = e.get("usage")
        if not agent or not u:
            continue
        with_agent += 1
        pool = _pool_for_agent(agent)
        bucket = pools.setdefault(pool, {}).setdefault(
            agent, {**{f: 0 for f in _USAGE_FIELDS}, "cost": 0.0, "spawns": 0}
        )
        for f in _USAGE_FIELDS:
            bucket[f] += int(u.get(f, 0) or 0)
        bucket["cost"] += float(u.get("total_cost_usd", 0.0) or 0.0)
        bucket["spawns"] += 1
    return {"pools": pools, "entries_with_agent_label": with_agent}


def _cache_hit_ratio(d: Dict[str, Any]) -> float:
    """Share of input-side tokens served from cache: cache_read / (input + cache_read
    + cache_creation). High = the cold-spawn boot prefix is being reused, not re-paid."""
    read = d.get("cache_read_input_tokens", 0)
    base = read + d.get("input_tokens", 0) + d.get("cache_creation_input_tokens", 0)
    return (read / base) if base else 0.0


def render_usage(journal: Dict[str, Any]) -> str:
    """Human-readable per-role token + cost table for a finished (or in-flight) run."""
    s = summarize_usage(journal)
    t = s["total"]
    if not t["spawns"]:
        n = s["entries_total"]
        return (
            f"token usage: no per-spawn usage recorded on {n} "
            f"entr{'y' if n == 1 else 'ies'} "
            "(run predates usage capture, or used a scripted/replay spawn)."
        )

    def _k(n: int) -> str:
        return f"{n / 1000:.0f}K" if abs(n) >= 1000 else str(n)

    header = (
        f"  {'role':10} {'spawns':>6} {'input':>8} {'cache_rd':>9} "
        f"{'cache_wr':>9} {'output':>7} {'$cost':>8}"
    )
    lines = ["token usage (per role):", header]
    for role in sorted(s["roles"]):
        d = s["roles"][role]
        lines.append(
            f"  {role:10} {d['spawns']:>6} {_k(d['input_tokens']):>8} "
            f"{_k(d['cache_read_input_tokens']):>9} {_k(d['cache_creation_input_tokens']):>9} "
            f"{_k(d['output_tokens']):>7} {d['cost']:>8.2f}"
        )
    lines.append(
        f"  {'TOTAL':10} {t['spawns']:>6} {_k(t['input_tokens']):>8} "
        f"{_k(t['cache_read_input_tokens']):>9} {_k(t['cache_creation_input_tokens']):>9} "
        f"{_k(t['output_tokens']):>7} {t['cost']:>8.2f}"
    )
    lines.append(
        f"  cache hit: {100 * _cache_hit_ratio(t):.0f}% of input-side tokens from cache "
        f"| ${t['cost']:.2f} across {t['spawns']} spawn(s)"
    )

    # Per-pool grouping (AC-027): only shown when at least one entry carries an
    # `agent` label. Pre-spec journals (no labels, AC-029) fall through here
    # with entries_with_agent_label == 0, leaving this report byte-identical
    # to the pre-pool-grouping output.
    pool_usage = summarize_pool_usage(journal)
    if pool_usage["entries_with_agent_label"]:
        lines.append("")
        lines.append("usage by pool:")
        for pool in sorted(pool_usage["pools"]):
            agents = pool_usage["pools"][pool]
            pool_spawns = sum(d["spawns"] for d in agents.values())
            pool_cost = sum(d["cost"] for d in agents.values())
            lines.append(f"  {pool} ({pool_spawns} spawn(s), ${pool_cost:.2f}):")
            for agent in sorted(agents):
                d = agents[agent]
                lines.append(
                    f"    {agent:10} {d['spawns']:>6} {_k(d['input_tokens']):>8} "
                    f"{_k(d['cache_read_input_tokens']):>9} {_k(d['cache_creation_input_tokens']):>9} "
                    f"{_k(d['output_tokens']):>7} {d['cost']:>8.2f}"
                )
    return "\n".join(lines)


def summarize_tools(journal: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate journal entries' tools_used and skills_used into run-wide sorted lists."""
    tools: set = set()
    skills: set = set()
    for e in journal.get("entries", []) or []:
        for t in e.get("tools_used", []) or []:
            if t:
                tools.add(t)
        for s in e.get("skills_used", []) or []:
            if s:
                skills.add(s)
    return {"tools_used": sorted(tools), "skills_used": sorted(skills)}


def render_tools_used(journal: Dict[str, Any]) -> str:
    """Human-readable tools + skills footprint for a finished (or in-flight) run."""
    s = summarize_tools(journal)
    tools = s["tools_used"]
    skills = s["skills_used"]
    if not tools and not skills:
        return (
            "tools/skills used: none recorded "
            "(run predates instrumentation, or used a scripted/replay spawn)"
        )
    lines = []
    if tools:
        lines.append(f"tools used ({len(tools)}): {', '.join(tools)}")
    if skills:
        lines.append(f"skills used ({len(skills)}): {', '.join(skills)}")
    return "\n".join(lines)


_CONTEXT_QUALITY_VALUES = ("sufficient", "too_much", "insufficient")


def summarize_context_quality(journal: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate each worker report-back's self-assessed `context_quality` and
    `missing_context`. dispatch.build_worker_prompt asks every worker whether the
    context it was handed was sufficient / too much / insufficient; this is where
    that signal is finally read back. Returns per-value `counts`, how many reports
    carried the signal, and the attributed `missing` items (task + role) workers
    flagged as absent."""
    counts: Dict[str, int] = {}
    missing: List[Dict[str, Any]] = []
    with_signal = 0
    for e in journal.get("entries", []) or []:
        rep = e.get("report") or {}
        q = rep.get("context_quality")
        if q:
            with_signal += 1
            counts[q] = counts.get(q, 0) + 1
        items = [m for m in (rep.get("missing_context") or []) if m]
        if items:
            missing.append({"task": e.get("task"), "role": e.get("role"), "items": items})
    return {"counts": counts, "entries_with_signal": with_signal, "missing": missing}


def render_context_quality(journal: Dict[str, Any]) -> str:
    """Human-readable worker context-fit footprint: how many workers judged their
    handed context sufficient / too_much / insufficient, plus any missing-context
    items. Turns the previously-discarded report-back signal into a tuning hint for
    per-role reads (too_much) and upstream spec/task authoring (missing)."""
    s = summarize_context_quality(journal)
    if not s["entries_with_signal"] and not s["missing"]:
        return (
            "context quality: none recorded "
            "(run predates context-quality capture, or used a scripted/replay spawn)"
        )
    counts = s["counts"]
    ordered = [v for v in _CONTEXT_QUALITY_VALUES if counts.get(v)]
    extra = sorted(v for v in counts if v not in _CONTEXT_QUALITY_VALUES)
    parts = [f"{counts[v]} {v}" for v in ordered + extra]
    n = s["entries_with_signal"]
    lines = [f"context quality: {' · '.join(parts)} (of {n} report{'s' if n != 1 else ''})"]
    if counts.get("too_much"):
        lines.append(
            f"  {counts['too_much']} worker(s) reported too_much context — "
            "candidates for thinner per-role reads in dispatch.build_worker_prompt."
        )
    for m in s["missing"]:
        loc = " ".join(p for p in (m.get("task"), m.get("role")) if p) or "?"
        lines.append(f"  missing context: {loc} → [{', '.join(m['items'])}]")
    return "\n".join(lines)


def render(
    journal_path: "str | Path",
    tasks: Optional[List[Dict[str, Any]]] = None,
    now: Optional[float] = None,
) -> str:
    """Build a checklist string from the journal (+ heartbeat sidecar).

    journal_path — path to the run journal (entries carry per-step timing).
    tasks        — optional reconciled task dicts ({"id","status",...}); when
                   given they drive ordering and surface not-yet-started tasks as
                   pending. When omitted, only tasks present in the journal show.
    now          — clock override for tests; defaults to wall time.
    """
    now = now if now is not None else _now()
    journal = _safe_load(journal_path)
    hb = _safe_load(heartbeat_path(journal_path))
    entries = journal.get("entries", []) or []
    # N concurrent workers (parallel fan-out) live in `actives`; the serial paths
    # write a single `active`. Accept both so the checklist works for either.
    actives = hb.get("actives")
    if actives is None:
        single = hb.get("active") or None
        actives = [single] if single else []
    active_by_task = {a.get("task"): a for a in actives if a and a.get("task")}

    def _is_stale(a: Dict[str, Any]) -> bool:
        # Only judge staleness when a pid was recorded; legacy sidecars (no pid)
        # are taken at face value.
        return "pid" in a and not _pid_alive(a.get("pid"))

    # Per-task ordered list of completed (role, duration_s) steps.
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    journal_order: List[str] = []
    for e in entries:
        tid = e.get("task")
        if tid is None:
            continue
        if tid not in by_task:
            by_task[tid] = []
            journal_order.append(tid)
        by_task[tid].append(e)

    # Task display order: spec order when tasks given, else journal-appearance.
    if tasks:
        order = [t["id"] for t in tasks]
        status_of = {t["id"]: t.get("status") for t in tasks}
        for tid in journal_order:  # include any journal task missing from the slice
            if tid not in status_of:
                order.append(tid)
                status_of[tid] = None
    else:
        order = journal_order
        status_of = {tid: None for tid in journal_order}

    spec_id = journal.get("spec_id") or hb.get("spec_id") or "(unknown spec)"
    run_id = journal.get("run_id") or hb.get("run_id") or "(no run_id)"
    phase = hb.get("phase") or "fanout"
    group_phases = hb.get("group_phases")
    if (
        phase == "done"
        and tasks
        and any(t.get("status") not in _DONE and t.get("status") not in _FAILED for t in tasks)
        and journal.get("integrate_complete")
        and not journal.get("groups")
    ):
        phase = "fanout_failed"

    lines: List[str] = []
    if group_phases and isinstance(group_phases, dict):
        # Pipelined run: show concurrent-phase summary (AC-016/AC-017).
        # Falls back to single phase line if summary computation fails (AC-018).
        try:
            _PHASE_DISPLAY = {"fanout": "fanning", "integrating": "integrating", "verifying": "verifying"}
            counts: Dict[str, int] = {}
            for ph in group_phases.values():
                counts[ph] = counts.get(ph, 0) + 1
            ordered = ["fanout", "integrating", "verifying"]
            parts = [
                f"{counts[ph]} {_PHASE_DISPLAY.get(ph, ph)}"
                for ph in ordered
                if counts.get(ph, 0) > 0
            ]
            for ph, n in sorted(counts.items()):
                if ph not in ordered and n > 0:
                    parts.append(f"{n} {ph}")
            phase_summary = " / ".join(parts) if parts else "idle"
            lines.append(f"spec {spec_id}   run {run_id}   {phase_summary}")
        except Exception:
            lines.append(f"spec {spec_id}   run {run_id}   phase: {phase}")
    else:
        lines.append(f"spec {spec_id}   run {run_id}   phase: {phase}")

    # Header timing: span from first started step to now.
    starts = [e.get("started_at") for e in entries if e.get("started_at") is not None]
    for a in actives:
        if a and a.get("started_at") is not None:
            starts.append(a["started_at"])
    if starts:
        lines.append(f"elapsed since first step: {_fmt_dur(now - min(starts))}")
    lines.append("")

    slowest = None  # (duration_s, task, role)
    done_ct = inflight_ct = stale_ct = 0

    for tid in order:
        status = status_of.get(tid)
        act = active_by_task.get(tid)
        is_active = act is not None
        stale = is_active and _is_stale(act)
        steps = by_task.get(tid, [])

        parts: List[str] = []
        for e in steps:
            role = e.get("role", "?")
            dur = e.get("duration_s")
            rev = e.get("report", {}).get("review_status")
            tag = f" [{rev}]" if rev else ""
            parts.append(f"{role} {_fmt_dur(dur)}{tag}")
            if dur is not None and (slowest is None or dur > slowest[0]):
                slowest = (dur, tid, role)
        if act is not None:
            a_role = act.get("role", "?")
            a_start = act.get("started_at")
            running = _fmt_dur(now - a_start) if a_start is not None else "?"
            if stale:
                parts.append(f"{a_role} … STALE (process gone; run dead) {running}")
            else:
                parts.append(f"{a_role} … running {running}")

        mark = _marker(status, active=is_active)
        if stale:
            mark = "?"  # phantom worker from a dead run -> not counted in flight
            stale_ct += 1
        elif mark == "✓":
            done_ct += 1
        elif mark == "▶":
            inflight_ct += 1

        status_txt = (status or ("pending" if not steps and not is_active else "")).ljust(10)
        detail = "  ·  ".join(parts)
        lines.append(f"  [{mark}] {tid:14} {status_txt} {detail}".rstrip())

    lines.append("")
    total = len(order)
    summary = f"{done_ct}/{total} tasks done · {inflight_ct} in flight"
    if stale_ct:
        summary += f" · {stale_ct} STALE (run process gone — re-run to resume)"
    if slowest is not None:
        summary += f" · slowest step: {slowest[1]} {slowest[2]} {_fmt_dur(slowest[0])}"
    lines.append(summary)
    return "\n".join(lines) + "\n"

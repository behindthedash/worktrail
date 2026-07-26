#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- live spawn via a headless agent CLI.

orchestrate.py is a plain Python process and CANNOT call the Claude Code Agent
tool (that's available only to an interactive assistant). So the standalone,
scriptable live path spawns a worker by shelling out to the configured headless
agent CLI. This keeps the whole pipeline
self-contained: record each worker's real output into a cassette, and the
deterministic replay of that cassette is the golden.

  instantiate(template, dest)  copy the committed sample-spec template into a
                               FRESH git repo (worktrees/branches isolated from
                               the kit). Defaults under /tmp so nothing pollutes
                               the kit repo.
  LiveSpawn(...)               build the worker prompt (dispatch.build_worker_
                               prompt) and run the selected agent with cwd=worktree.
  spawn_one(task, role)        instantiate -> worktree -> ONE real worker ->
                               parse report-back -> show the diff. The bounded
                               first live run.
  smoke()                      trivial agent round-trip (plumbing check).

GATED: real task spawns implement code and cost tokens. This module deliberately
exposes `spawn-one` (single worker) before the full fan-out loop is wired.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import agent_capacity
from . import coordinator
from . import dispatch
from . import orchestrate
from . import progress
from . import spawnlib
from ..taskformats import resolve as taskformats
from ..taskformats.devkit import schema as _devkit_schema
from ..taskformats.devkit import source as loader


def _ts() -> str:
    return datetime.now().strftime("[%H:%M:%S]")


# Per-worker headless agent timeout (seconds). Raised to 1800s: sonnet on
# complex tasks routinely needs 20–30 min; 900s was calibrated for haiku and
# killed legitimate sonnet work mid-flight. Overridable via the
# ORCH_WORKER_TIMEOUT env var or the --timeout CLI flag.
WORKER_TIMEOUT_DEFAULT = int(os.environ.get("ORCH_WORKER_TIMEOUT", "1800"))

# Whole-run wall-clock budget. Once exceeded, the fan-out stops dispatching
# NEW tasks (in-flight ones finish) so a run can't silently sprawl for hours.
# 0 disables. ORCH_RUN_BUDGET / --run-budget are specified in MINUTES (a run
# is normally tens of minutes to hours, and a bare seconds count is easy to
# mistake for minutes); RUN_BUDGET_DEFAULT itself stays in SECONDS since
# that's the unit threaded through the internal `run_budget` parameter
# (live_run_real/_pipeline_scheduler and their unit tests use sub-second
# precision, e.g. run_budget=0.001) -- only the CLI/env boundary is minutes.
RUN_BUDGET_DEFAULT_MINUTES = float(os.environ.get("ORCH_RUN_BUDGET", "0"))
RUN_BUDGET_DEFAULT = RUN_BUDGET_DEFAULT_MINUTES * 60

_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent
SAMPLE_TEMPLATE = _SKILL / ".fixtures" / "sample-spec"
SAMPLE_SPEC_REL = "docs/specs/001-url-shortener"
DEFAULT_DEST = Path("/tmp/orchestrator-live/url-shortener-target")


# Agent defaults are resolved by CLI so Claude Code, Codex, and OpenCode can each
# use their own baseline model without affecting explicit --model values.
def _detect_default_agent() -> str:
    if os.environ.get("GO_AGENT_CLI"):
        return os.environ["GO_AGENT_CLI"]
    if os.environ.get("ORCH_AGENT"):
        return os.environ["ORCH_AGENT"]
    # OpenCode supplies this explicit marker to the parent process. Do not
    # infer the host from process names.
    if os.environ.get("OPENCODE_PARENT"):
        return "opencode"
    if os.environ.get("CODEX_CI") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    return "claude"


# DEFAULT_AGENT is resolved from the launching host via _detect_default_agent()
# so that Claude Code, Codex, and OpenCode each use their own headless CLI
# without an invocation-wide env var or a per-call --agent flag. Explicit
# --agent, policy agent_cli, and GO_AGENT_CLI env var all override this.
DEFAULT_AGENT = _detect_default_agent()
DEFAULT_MODEL = spawnlib.DEFAULT_CLAUDE_MODEL
DEFAULT_CODEX_MODEL = spawnlib.DEFAULT_CODEX_MODEL
CODEX_DEFAULT_ROLE_MODELS = {
    "implement": DEFAULT_CODEX_MODEL,
    "review": DEFAULT_CODEX_MODEL,
    "fix": DEFAULT_CODEX_MODEL,
    "cleanup": DEFAULT_CODEX_MODEL,
    "ci-fix": DEFAULT_CODEX_MODEL,
}

# Reviewer independence (locked decision 13.3): the headless review worker is the
# same binary as the implementer, so we enforce independence with an appended
# system prompt (a real behavioural change) rather than only a prompt line. Kept
# to the review role so implement/fix/cleanup keep the DEFAULT system prompt and
# its prompt-cache reuse across the many cold workers of a run.
_REVIEWER_SYSTEM_PROMPT = (
    "You are an INDEPENDENT code reviewer. You did NOT write this code. Be "
    "skeptical: verify the diff against the task's Acceptance Criteria, look for "
    "bugs, missing tests, and scope drift, and do not rubber-stamp. Do not modify "
    "source."
)

# Lean worker flags: applied to every task worker spawn.
# --strict-mcp-config with no --mcp-config suppresses MCP server startup.
# --tools limits to the tool set measured across real spec runs
# (Read/Edit/Write/Bash confirmed; Grep/Glob included as safe read-only extras).
# --setting-sources project,local excludes the operator's USER-level
# ~/.claude/settings.json (and its ~/.claude/CLAUDE.md / global instruction
# chain) from headless worker sessions. Confirmed root cause (investigation
# 20260711-130900): a user-level Stop hook fires on every worker that commits
# or writes a file -- i.e. every implement/review/fix/cleanup worker by
# design -- and blocks the turn from ending, forcing an extra "next-step
# suggestion" continuation turn whose text becomes the final message the
# orchestrator parses for the report-back JSON. That continuation never
# repeats the trailing ```json block, so parse_report_back sees a clean
# `stop_reason: "end_turn"` spawn with no report-back at all. Project- and
# local-level settings (this repo's own hooks, e.g.
# hooks/task_lifecycle.py) are unaffected -- only the "user" source is
# dropped. Live-verified: the identical review prompt against the same
# worktree failed without this flag and produced a valid report-back with it
# (also cut cache-read tokens by ~65% by dropping the operator's unrelated
# global CLAUDE.md/AGENTS.md content from every worker's context).
# NOTE: --bare is intentionally omitted. It skips keychain reads, which breaks
# OAuth-based auth (ANTHROPIC_API_KEY env var not set in typical dev setups).
_LEAN_WORKER_FLAGS: list = [
    "--strict-mcp-config",
    "--tools",
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Grep",
    "Glob",
    "--setting-sources",
    "project,local",
]


class RunLockHeld(RuntimeError):
    """Raised when another orchestrator run already holds this spec's run lock."""


class WorktreeAddError(RuntimeError):
    """`git worktree add` failed even after pruning a stale registration."""


class WorktreeMissingTaskFileError(RuntimeError):
    """A freshly created task worktree does not contain its own task file --
    it branched from a ref that predates the tasks/ commit landing."""


class WorktreeMissingDependencyFileError(RuntimeError):
    """A dependency's declared `files:` are missing from the dependent task's
    stacked worktree after add_stacked_worktree() merged the dependency's
    branch in."""


class RunLock:
    """Single-owner advisory lock for one (repo, spec) run, via ``flock`` on a
    sidecar beside the journal.

    Two runs for the same spec share one journal (keyed by spec name); without a
    lock a second invocation -- a user re-running, not realising one is still
    backgrounded -- interleaves journal/branch writes and corrupts the first.
    ``flock`` is released automatically if the holding process dies, so a crash
    never leaves a stale lock. Best-effort: if ``flock`` is unavailable, the lock
    degrades to a no-op rather than blocking a run.
    """

    def __init__(self, journal_path: "str | Path") -> None:
        self.path = Path(journal_path).with_suffix(".lock")
        self._fh = None

    def acquire(self) -> "RunLock":
        try:
            import fcntl
        except ImportError:  # non-POSIX: degrade to no-op
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise RunLockHeld(
                f"another run holds {self.path} (a backgrounded run for this spec "
                f"is still active); wait for it or kill it before re-running"
            )
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is not None:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *_) -> None:
        self.release()


def apply_run_plan(repo: "Path", spec_rel: str, spec_id: str, tasks: list) -> list:
    """Enrich freshly-loaded tasks with a compiled RunPlan, if one is cached.

    This reads a plan; it never compiles one. Compiling costs a model call, and a
    fan-out that silently spent one mid-run would be both a surprise on the bill
    and a source of nondeterminism between two runs of the same spec. Producing a
    plan is the explicit, out-of-band `worktrail-compile` step
    (`conductor/compile.py`); consuming it is free and happens here.

    With no cached plan this is a no-op and the run behaves exactly as it did
    before P3b -- which is what keeps every existing devkit spec unaffected.
    """
    from ..conductor import compile as conductor_compile
    from ..conductor import runplan as _runplan

    try:
        spec_dir = repo / spec_rel
        fp = _runplan.fingerprint(spec_dir, tasks)
        plan = _runplan.load_cached(conductor_compile.default_cache_dir(repo), spec_id, fp)
    except OSError as exc:  # an unreadable cache must never take a run down
        print(f"{_ts()} run plan: cache unreadable ({exc}); using the spec's own deps")
        return tasks
    if plan is None:
        return tasks

    merged, notes = _runplan.apply_to_tasks(tasks, plan)
    for n in notes:
        print(f"{_ts()} {n}")
    return merged


def journal_path_for(repo: "Path", spec_rel: str) -> Path:
    """Run-journal path for a (repo, spec) pair: beside the worktrees, keyed by the
    spec folder name. Single source of truth shared by full_real and `status` so
    the checklist reads exactly the journal a run writes."""
    wt_base = repo.parent / f"{repo.name}-worktrees"
    return wt_base / f"run-{Path(spec_rel.rstrip('/')).name}.json"


def _print_usage_report(journal_path: "str | Path") -> None:
    """Print the per-role token + cost report and tools/skills footprint (best-effort)."""
    try:
        journal = json.loads(Path(journal_path).read_text())
    except (OSError, json.JSONDecodeError):
        return
    print(progress.render_usage(journal))
    print(progress.render_tools_used(journal))
    print(progress.render_context_quality(journal))


def _format_automerge_evidence_note(evidence: "dict[str, dict[str, str]]") -> "str | None":
    """Human-readable note for `verify.run_all()`'s `automerge_evidence` -- explained
    self-merges (a group's PR flipped to MERGED mid-turn because external automation
    had it pre-armed, not because a worker ran `gh pr merge` itself). Previously
    computed but never surfaced outside an optional `--notify-cmd` payload or the
    (subprocess-discarded) CLI return value -- see docs/specs/research/
    go-policy-integrity-audit.md's automerge_evidence consumer audit. Returns None
    for empty evidence so callers can `if note:` without a separate emptiness check.
    """
    if not evidence:
        return None
    return (
        f"NOTE: {len(evidence)} group(s) merged mid-turn by pre-armed external "
        f"auto-merge (explained self-merge, not a violation): "
        + ", ".join(f"{name} (enabledBy={ev.get('enabledBy')})" for name, ev in evidence.items())
    )


def _safety_net_events_from_preflight_fallbacks(fallbacks: "dict[str, dict]") -> "list[dict]":
    """Convert `verify.run_all()`'s `preflight_fallbacks` (group -> detail) into
    `progress.append_safety_net_events` entries, so a required-checks preflight
    READ failure (see `Verifier.auto_merge`) is queryable across runs the same
    way `dependency_file_drift` is, not just printed/notified once."""
    return [
        {"event": "automerge_preflight_fallback", "group": group, **detail}
        for group, detail in fallbacks.items()
    ]


def read_or_create_run_id(journal_path: "Path") -> str:
    """Read a stable run_id from *journal_path* or generate and persist a fresh one.

    - Journal exists with ``run_id`` key → return it unchanged.
    - Journal exists without ``run_id`` (legacy PR-16 journal) → inject a new
      ``run_id``, persist it, and return it (existing ``entries`` preserved).
    - Journal absent → write a minimal journal with the new ``run_id`` and return it.
    """
    p = Path(journal_path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            existing = data.get("run_id")
            if existing:
                return existing
            # Legacy journal: inject run_id, preserve entries
            run_id = f"full-{int(time.time())}"
            data["run_id"] = run_id
            progress.atomic_write_text(p, json.dumps(data, indent=2, sort_keys=True) + "\n")
            return run_id
        except (OSError, json.JSONDecodeError):
            pass
    # No journal or unreadable: write a fresh one
    run_id = f"full-{int(time.time())}"
    p.parent.mkdir(parents=True, exist_ok=True)
    progress.atomic_write_text(
        p, json.dumps({"run_id": run_id, "entries": []}, indent=2, sort_keys=True) + "\n"
    )
    return run_id


def reconcile_from_journal(tasks: list, journal: dict) -> list:
    """Replay a run journal onto in-memory task state so a RESUMED run skips
    roles that already completed.

    The journal is the same incremental record `record()` writes during a run:
    `{"spec_id": ..., "entries": [{"task", "role", "report"}, ...]}`, plus
    observability-only `{"event": ...}` markers (e.g. `dependency_file_drift`)
    that are skipped here, not replayed. Each role-step entry is appended only
    *after* its role finished, so replaying every entry through
    the normal `dispatch.apply_report` transition reconstructs each task's exact
    post-interruption status (e.g. implement+review done -> "cleaning"; cleanup
    done -> "done"). A fully-done task then drops out of the runnable frontier;
    a mid-flight one is continued (see `live_run_real`).

    Mutates `tasks` in place; returns the entries list so the caller can keep
    appending to it (monotonic journal growth, never a truncating overwrite).
    Pure: no git, no IO, no spawns -- unit-testable in isolation.

    Frontmatter successfully-terminal tasks (in coordinator.DONE) are never
    downgraded: replay is skipped for any task already marked done (FR-6, AC-2).
    """
    entries = list(journal.get("entries", []))
    for e in entries:
        if e.get("event"):
            # Observability-only marker (e.g. a `dependency_file_drift` safety-net
            # fire from `_require_dependency_files`) -- not a role-transition step,
            # never replayed through `dispatch.apply_report`.
            continue
        task_id = e.get("task")
        task = next((t for t in tasks if t["id"] == task_id), None)
        if task and task.get("status") in coordinator.DONE:
            # Frontmatter pre-marked done: never replay journal entries that would
            # downgrade it (e.g., claiming, re-driving). Keep it in its done state.
            continue
        report = dict(e.get("report") or {})
        report["task"] = task_id
        if task and e.get("scope_escalated"):
            added = list(e.get("scope_added_files") or [])
            task["_scope_escalated"] = True
            task["_scope_escalation_files"] = added
            task["files"] = list(dict.fromkeys(list(task.get("files") or []) + added))
            task["_extra_reads"] = added
            task["status"] = "fixing"
            continue
        terminal_status = report.get("terminal_status")
        if task and terminal_status == "retryable":
            # A clean-slate implement parse failure should be retried on resume,
            # not replayed through the normal failed transition.
            continue
        if task and terminal_status in orchestrate.TERMINAL:
            task["status"] = terminal_status
            continue
        try:
            dispatch.apply_report(tasks, report, e.get("role"))
        except (ValueError, KeyError):
            # entry references a task outside this spec slice, or is malformed --
            # reconciliation is best-effort and must never block a resume.
            continue
    return entries


def _journaled_task_heads(entries: list) -> dict[str, str]:
    """Return the latest non-empty worker head recorded for each task."""
    heads: dict[str, str] = {}
    for entry in entries:
        task_id = entry.get("task")
        head_sha = (entry.get("report") or {}).get("head_sha")
        if task_id and head_sha:
            heads[task_id] = str(head_sha)
    return heads


def validate_task_metadata(tasks: list) -> None:
    """Refuse live fan-out when implementation tasks lack file scope metadata."""
    missing = [
        t["id"]
        for t in tasks
        if t.get("status") == "pending"
        and t.get("kind", "impl") not in ("docs", "e2e", "cleanup")
        and not t.get("files")
    ]
    if missing:
        raise RuntimeError(
            "implementation task(s) missing required frontmatter files: " + ", ".join(missing)
        )


def _fanout_failed_status(repo: Path, spec_rel: str) -> Optional[dict]:
    """Best-effort read of the prior-run heartbeat sidecar for this spec."""
    try:
        spec_id = Path(spec_rel).name
        status_path = repo.parent / f"{repo.name}-worktrees" / f"run-{spec_id}.status.json"
        if not status_path.is_file():
            return None
        status = json.loads(status_path.read_text())
        return status if status.get("phase") == "fanout_failed" else None
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return None


def precheck(repo: Path, spec_rel: str) -> int:
    """Check whether pending impl tasks have their declared files already present.

    For each pending impl task from the `new` pipeline (Route C/D — task id
    is plain `TASK-NNN`):
    - WARN (exit 1) when all files exist
    - INFO (exit 0) when some files exist
    - Silent (exit 0) when no files exist

    For a `modify`-pipeline task (Route F/G change-spec — task id is
    `TASK-CHG-*`), the "files already exist" check is inverted: a bugfix/
    spec-change task's whole point is to modify pre-existing files, so
    `files:` already existing is the CORRECT, expected state, not a sign of
    prior completion. Instead:
    - WARN (exit 1) when one or more listed files do NOT exist yet — the
      actually suspicious case for a modify task (wrong path, file moved
      or renamed since planning)
    - Silent (exit 0) when every listed file already exists

    Non-impl tasks and tasks with empty files: are silently skipped.
    All tasks completed: silent, exit 0.

    Returns exit code 1 if any WARN was emitted, else 0.
    """
    status = _fanout_failed_status(repo.resolve(), spec_rel)
    if status:
        spec_id = Path(spec_rel).name
        journal_path = repo.parent / f"{repo.name}-worktrees" / f"run-{spec_id}.json"
        print(
            f"WARN: {spec_id} — prior orchestrator run is stuck in fanout_failed; "
            f"inspect {journal_path} before re-running full-real."
        )
        failed = [t.get("id") for t in status.get("failed_tasks") or [] if t.get("id")]
        blocked = [t.get("id") for t in status.get("blocked_tasks") or [] if t.get("id")]
        if failed:
            print(
                "WARN: clear the failed task cassette entries before retrying: " + ", ".join(failed)
            )
        if blocked:
            print("INFO: blocked tasks recorded in status.json: " + ", ".join(blocked))
        return 1

    _, tasks = taskformats.load_spec(str(repo / spec_rel))
    warn_count = 0

    for task in tasks:
        task_id = task["id"]
        status = task.get("status")
        kind = task.get("kind", "impl")
        files = task.get("files", [])
        repo_root = repo.resolve()

        # Frontmatter typo guard (route:J loader-frontmatter-schema-
        # validation): applies to every task regardless of kind/status --
        # an unrecognized-key typo is a real problem even on a completed or
        # e2e task, since it's silently ignored wherever it's read.
        for fm_warning in task.get("frontmatter_warnings") or []:
            print(f"WARN: {task_id} — {fm_warning}")
            warn_count += 1

        # External-dependencies resolution/reporting (contracts/precheck-
        # external-deps-report.md): applies to every pending task regardless
        # of kind/files, since a task can declare `external-dependencies:`
        # without declaring `files:` (e.g. an e2e task blocked on another
        # spec's deliverable).
        if status == "pending":
            for ref in task.get("external_deps") or []:
                result = loader.resolve_external_dependency(repo_root, ref)
                if result["resolved"]:
                    print(
                        f"INFO: {task_id} — external dependency {ref} resolved, "
                        f"status={result['status']}"
                    )
                else:
                    print(
                        f"WARN: {task_id} — external dependency {ref} unresolved "
                        f"({result['reason']})"
                    )
                    warn_count += 1

        if kind in ("e2e", "cleanup", "docs"):
            continue
        if not files:
            continue
        if status != "pending":
            continue

        existing_count = 0
        for file_path in files:
            full_path = repo_root / file_path
            if full_path.exists():
                existing_count += 1

        total_count = len(files)

        # Modify-only detection: prefer the task BODY's own "Files to Create"
        # vs "Files to Modify" split (spec-to-tasks' actual authoring source
        # for the combined `files:` frontmatter) over the bare `TASK-CHG-*`
        # id-prefix heuristic -- a plain `TASK-NNN` (Route C/D) task that adds
        # steps to an existing file and creates nothing new is legitimately
        # modify-only too, and the id prefix alone can't see that (see brief
        # 20260723-162700: TASK-005 only wired new steps into an existing
        # workflow file and false-WARNed as "already implemented"). Fall back
        # to the id-prefix heuristic when the body has no such sections at all
        # (older task files predating the template, or a non-file task).
        is_modify_only = task_id.startswith("TASK-CHG-")
        task_path = task.get("path")
        if task_path:
            try:
                body_text = Path(task_path).read_text()
            except OSError:
                body_text = None
            if body_text is not None:
                create_files, modify_files = loader.parse_files_sections(body_text)
                if create_files or modify_files:
                    is_modify_only = not create_files and bool(modify_files)

        if is_modify_only:
            # Modify pipeline: files: already existing is the correct,
            # expected state -- warn only when a listed file is MISSING, which
            # is the actually suspicious case here.
            missing_count = total_count - existing_count
            if missing_count > 0:
                print(
                    f"WARN: {task_id} — {missing_count} of {total_count} listed files do not "
                    "exist yet; a modify-pipeline task should target pre-existing files. "
                    "Verify the file paths."
                )
                warn_count += 1
            continue

        if existing_count == total_count:
            print(
                f"WARN: {task_id} — all listed files already exist; possible: already implemented. Consider marking completed or verifying."
            )
            warn_count += 1
        elif existing_count > 0:
            print(
                f"INFO: {task_id} — {existing_count} of {total_count} listed files already exist (partial)."
            )

    return 1 if warn_count > 0 else 0


def _journal_failure_entry(
    task: dict,
    role: str,
    reason: str,
    t0: float,
    t1: float,
    terminal_status: str = "failed",
) -> dict:
    return {
        "task": task["id"],
        "role": role,
        "report": {
            "status": "failed",
            "head_sha": "",
            "tests": "none",
            "review_status": None,
            "critical_issues": 0,
            "major_issues": 0,
            "notes": reason,
            "terminal_status": terminal_status,
        },
        "started_at": round(t0, 3),
        "ended_at": round(t1, 3),
        "duration_s": round(t1 - t0, 1),
    }


def _clear_integration_state(journal: dict) -> bool:
    """Drop the integrate_complete marker and non-MERGED per-group integrate records
    from a journal so a forced `--re-integrate` rebuilds them. Returns True when
    anything was actually cleared (so the caller only persists/logs a real reset).

    MERGED group records are preserved: a merged PR cannot be reopened or rebuilt,
    and keeping the record lets integrate_one skip the group (and allows the dep-group
    start-ref fallback to recognise the deleted branch as an expected post-merge state).
    """
    had = bool(journal.get("integrate_complete") or journal.get("groups"))
    journal.pop("integrate_complete", None)
    groups = journal.get("groups", {})
    merged_only = {k: v for k, v in groups.items() if v.get("state") == "MERGED"}
    if merged_only:
        journal["groups"] = merged_only
    else:
        journal.pop("groups", None)
    return had


def skip_tasks(repo: Path, spec_rel: str, task_ids: list, reason: str = "manually skipped") -> int:
    """Mark stuck tasks terminal so the next `full-real` resume stops waiting on them.

    Appends one `escalated` journal entry per task id (reconcile_from_journal then
    carries each to a terminal status, and deliverable_subset drops escalated tasks
    from their group). This is the supported alternative to hand-editing the journal
    JSON when a task is wedged: after skipping, re-run the SAME `full-real` command
    and the fan-out reaches completeness and proceeds to integrate the rest.

    Returns 0 on success, 1 when there is no journal to amend.
    """
    journal_path = journal_path_for(repo, spec_rel)
    if not journal_path.exists():
        print(f"{_ts()} SKIP: no run journal at {journal_path} -- nothing to skip")
        return 1
    journal = json.loads(journal_path.read_text())
    entries = journal.setdefault("entries", [])
    now = time.time()
    for tid in task_ids:
        entries.append(
            _journal_failure_entry(
                {"id": tid}, "manual-skip", reason, now, now, terminal_status="escalated"
            )
        )
    progress.atomic_write_text(
        str(journal_path), json.dumps(journal, indent=2, sort_keys=True) + "\n"
    )
    print(f"{_ts()} SKIP: marked {', '.join(task_ids)} escalated ({reason}) in {journal_path}")
    return 0


def _annotate_external_deps(repo: Path, tasks: list) -> None:
    """Freshly set `external_deps_ok`/`external_deps_blockers` on every pending task
    with a non-empty `external_deps` list (contracts/frontier-external-deps-gate.md).

    Called immediately before each `coordinator.runnable_frontier(...)` call site so
    `coordinator.py` itself never needs to touch the filesystem. Never cached across
    ticks: a referenced sibling task's on-disk status can flip between ticks,
    including from a separate, concurrently running orchestrator invocation against
    the sibling spec (REQ-013).
    """
    repo_root = repo.resolve()
    for t in tasks:
        if t.get("status") != "pending":
            continue
        refs = t.get("external_deps") or []
        if not refs:
            continue
        blockers = []
        for ref in refs:
            result = loader.resolve_external_dependency(repo_root, ref)
            if result["satisfied"]:
                continue
            if result["resolved"]:
                blockers.append(f"{ref} {result['status']}")
            else:
                blockers.append(f"{ref} unresolved ({result['reason']})")
        t["external_deps_ok"] = not blockers
        t["external_deps_blockers"] = blockers


def _is_terminal(task: dict) -> bool:
    status = task.get("status")
    return status in orchestrate.TERMINAL or status in coordinator.DONE


def _fanout_dispatchable(tasks: list, with_tail: bool) -> list:
    """Tasks the fan-out actually dispatches. Tail-kind tasks (e2e, cleanup) are
    only dispatched with `--with-tail`; otherwise they stay `pending` by design
    and must be excluded from completeness checks (else the run never integrates)."""
    if with_tail:
        return tasks
    held_out = set(coordinator.tail_held_out_task_ids(tasks))
    return [
        t for t in tasks if t.get("kind") not in coordinator.TAIL_KINDS and t["id"] not in held_out
    ]


def _fanout_complete(tasks: list, with_tail: bool = True) -> bool:
    return all(_is_terminal(t) for t in _fanout_dispatchable(tasks, with_tail))


def _fanout_incomplete_detail(tasks: list, with_tail: bool) -> dict:
    dispatchable = _fanout_dispatchable(tasks, with_tail)
    failed = [t for t in dispatchable if t.get("status") in coordinator.FAILED_STATUSES]
    failed_ids = {t["id"] for t in failed}
    blocked = []
    summary = []

    for t in failed:
        summary.append(f"{t['id']}={t.get('status', 'failed')}")

    for t in dispatchable:
        if _is_terminal(t):
            continue
        blockers = [dep for dep in t.get("deps", []) if dep in failed_ids]
        blockers = blockers + list(t.get("external_deps_blockers") or [])
        blocked.append(
            {
                "id": t["id"],
                "status": t.get("status", "pending"),
                "blocked_by": blockers,
            }
        )
        if blockers:
            summary.append(
                f"{t['id']}={t.get('status', 'pending')} (blocked by {', '.join(blockers)})"
            )
        else:
            summary.append(f"{t['id']}={t.get('status', 'pending')}")

    return {
        "failed_tasks": [{"id": t["id"], "status": t.get("status", "failed")} for t in failed],
        "blocked_tasks": blocked,
        "summary": summary,
    }


def _stranded_after_integrate(tasks: list, journal_groups: dict) -> list:
    """Task ids whose work was fanned out but lands in NO integrated group.

    When the journal already marks every recorded group integrated
    (`integrate_complete` + every group has a `pr_url`), `full-real` skips the
    integrate stage and reuses those records. If new tasks were added to the
    spec and fanned out on this resume, they form group(s) whose name is absent
    from the journal -- their work never reaches a PR. Returns the terminal task
    ids in those un-integrated groups (sorted) so the operator can be told to
    re-run with `--re-integrate`. Tail-kind tasks never appear in a group, so
    they are excluded by construction."""
    by_id = {t["id"]: t for t in tasks}
    stranded = []
    for g in coordinator.plan_groups(tasks):
        if g["name"] in journal_groups:
            continue
        stranded.extend(
            tid for tid in g["tasks"] if (t := by_id.get(tid)) is not None and _is_terminal(t)
        )
    return sorted(stranded)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _branch_exists(repo: Path, name: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", name, check=False).returncode == 0


def _refresh_base_branch(repo: Path, remote: str, base: str) -> None:
    """Fetch `<remote>/<base>` and fast-forward the LOCAL `base` ref before
    integration starts (brief 20260723-171500-orchestrator-long-run-base-
    refresh). A run that spans hours/days forks task branches once from
    `base` and never refreshes them; integrate.py DOES cut group branches
    from current local `base`, but nothing fetched/ff'd it first -- it was
    only as fresh as the operator's last manual sync. A long-running run
    therefore integrates against a base that's silently stale, generating
    avoidable assembly conflicts a fresher base would have absorbed via
    ordinary git history.

    ff-only and best-effort by design: never merges/rebases local work
    (a diverged local `base` -- local-only commits -- is left untouched with
    a printed warning, not force-updated), and a fetch failure (offline,
    auth) degrades to using the local ref as-is rather than aborting the run.
    Uses `update-ref` (not `git pull`/`merge`) so it's safe even when `base`
    is the branch currently checked out in this exact working tree.
    """
    fetch = _git(repo, "fetch", "--quiet", remote, base, check=False)
    if fetch.returncode != 0:
        print(
            f"{_ts()} BASE REFRESH: fetch {remote}/{base} failed (offline/auth?) "
            "-- using local ref as-is"
        )
        return
    old_sha = _git(repo, "rev-parse", base, check=False).stdout.strip()
    remote_sha = _git(repo, "rev-parse", f"{remote}/{base}", check=False).stdout.strip()
    if not old_sha or not remote_sha or old_sha == remote_sha:
        return
    ff = _git(repo, "merge-base", "--is-ancestor", base, f"{remote}/{base}", check=False)
    if ff.returncode != 0:
        print(
            f"{_ts()} BASE REFRESH: local '{base}' ({old_sha[:8]}) has diverged from "
            f"{remote}/{base} ({remote_sha[:8]}) -- not fast-forwardable; leaving the "
            "local ref untouched (it may carry local-only commits; resolve manually)."
        )
        return
    upd = _git(repo, "update-ref", f"refs/heads/{base}", remote_sha, check=False)
    if upd.returncode == 0:
        print(f"{_ts()} BASE REFRESH: {base} {old_sha[:8]} -> {remote_sha[:8]} ({remote}/{base})")
    else:
        print(f"{_ts()} BASE REFRESH: update-ref failed for '{base}'; leaving local ref untouched")


def _resume_drift_report(repo: Path, base: str, spec_id: str, tasks: list) -> None:
    """Best-effort: on a pipeline resume, print how many commits `base` has
    moved since the spec's task branches were originally forked from it, so
    an operator sees drift as a visible number BEFORE integration turns it
    into a surprise merge conflict -- not a hard gate, purely informational.

    Uses the merge-base of the first task branch that already exists locally
    against `base` as the "spec base" proxy (task branches fork once from
    base and are never rebased, so this merge-base IS that original fork
    point). Silently no-ops when no task branch exists yet (fresh run, no
    worker has been dispatched) or when the git calls fail for any reason.
    """
    for t in tasks:
        branch = f"{spec_id}/{t['id'].lower()}"
        if not _branch_exists(repo, branch):
            continue
        mb = _git(repo, "merge-base", branch, base, check=False)
        if mb.returncode != 0 or not mb.stdout.strip():
            return
        spec_base_sha = mb.stdout.strip()
        count = _git(repo, "rev-list", "--count", f"{spec_base_sha}..{base}", check=False)
        if count.returncode == 0 and count.stdout.strip().isdigit():
            n = count.stdout.strip()
            if n != "0":
                print(
                    f"{_ts()} PIPELINE RESUME: base '{base}' is {n} commit(s) ahead of "
                    f"the spec's original fork point ({spec_base_sha[:8]}) -- integration "
                    "conflicts are more likely the longer this run has spanned"
                )
        return


def build_external_deps_by_ref(repo: Path, tasks: list) -> dict:
    """Resolve every task's `external_deps` refs (cross-spec `external-dependencies:`
    entries, spec 025) into the `external_deps_by_ref` shape `dispatch.build_worker_prompt`
    expects: `ref -> {"id", "files"}` for the sibling task, included ONLY when
    `loader.resolve_external_dependency` reports `satisfied=True` -- an unresolved or
    unsatisfied ref is omitted so a worker prompt never implies a sibling's files are
    already present when they aren't (contracts/worker-context-worktree-stacking.md Part A).
    Read-only; safe to call every drive-loop tick since it only reads the repo's own
    docs/specs/ tree.
    """
    external_deps_by_ref: dict = {}
    for t in tasks:
        for ref in t.get("external_deps", []) or []:
            if ref in external_deps_by_ref:
                continue
            resolved = loader.resolve_external_dependency(repo, ref)
            if not resolved.get("satisfied"):
                continue
            spec_id, _, task_id = ref.partition("/")
            task_file = repo / loader.DEFAULT_SPEC_ROOT / spec_id / "tasks" / f"{task_id}.md"
            try:
                sibling_fm = loader.parse_frontmatter(task_file.read_text())
            except OSError:
                continue
            external_deps_by_ref[ref] = {"id": task_id, "files": sibling_fm.get("files", [])}
    return external_deps_by_ref


def dependency_start_ref(repo: Path, spec_id: str, task: dict, by_id: dict) -> tuple:
    """Pick the git ref a task's worktree should branch FROM so it STACKS on its
    dependencies' commits instead of bare HEAD.

    Returns (start_ref, extra_merges): branch off the first satisfied
    dependency's branch and merge the rest in, so an integration / e2e /
    wire-it-together task SEES the code it builds on rather than running against
    an empty tree (which guarantees its tests fail in isolation). Falls back to
    ('HEAD', []) when the task has no dependency branches materialized -- roots,
    or deps that were pre-marked done outside this run (e.g. `--only`).

    Also folds in satisfied cross-spec `external_deps` entries
    (`<sibling_spec_id>/<sibling_task_id>`): each contributes the sibling's OWN
    branch name (`f"{sibling_spec_id}/{sibling_task_id.lower()}"`, computed with
    the SIBLING's spec id -- never this task's own `spec_id`) as an extra
    candidate, subject to the same "must exist" filter as same-spec dependency
    branches. An unresolved or unsatisfied external dep contributes nothing --
    the frontier gate (TASK-004) would not have dispatched this task at all
    while unsatisfied, so this is defense in depth, not the primary gate
    (contracts/worker-context-worktree-stacking.md Part B).

    Orchestrator feedback defect A (dependent tasks were fanned out off base).
    """
    deps = [d for d in task.get("deps", []) if d in by_id]
    dep_branches = [f"{spec_id}/{d.lower()}" for d in deps]

    repo_root = repo.resolve()
    for ref in task.get("external_deps") or []:
        result = loader.resolve_external_dependency(repo_root, ref)
        if not result["satisfied"]:
            continue
        sibling_spec_id, _, sibling_task_id = ref.partition("/")
        dep_branches.append(f"{sibling_spec_id}/{sibling_task_id.lower()}")

    existing = [b for b in dep_branches if _branch_exists(repo, b)]
    if not existing:
        return "HEAD", []
    return existing[0], existing[1:]


def _validate_retained_task_branch(
    repo: Path,
    branch: str,
    start_ref: str,
    expected_head_sha: str | None = None,
) -> None:
    """Reject a retained task branch whose lineage no longer matches the run.

    Retained branches may contain user work, so this function only validates and
    raises; it never resets, deletes, or recreates them implicitly.
    """
    branch_head = _git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}", check=False)
    if branch_head.returncode != 0:
        raise WorktreeAddError(f"retained task branch {branch} has no resolvable commit")

    if _git(repo, "merge-base", "--is-ancestor", start_ref, branch, check=False).returncode != 0:
        raise WorktreeAddError(
            f"retained task branch {branch} is stale: {start_ref} is not an ancestor of "
            f"{branch}. Repair the branch or choose an explicit fresh run before retrying."
        )

    if expected_head_sha:
        expected = _git(
            repo, "rev-parse", "--verify", f"{expected_head_sha}^{{commit}}", check=False
        )
        if (
            expected.returncode != 0
            or _git(
                repo, "merge-base", "--is-ancestor", expected_head_sha, branch, check=False
            ).returncode
            != 0
        ):
            raise WorktreeAddError(
                f"retained task branch {branch} does not contain journaled task head "
                f"{expected_head_sha}. Repair the branch or clear the stale run explicitly."
            )


def add_stacked_worktree(
    repo: Path,
    spec_id: str,
    task: dict,
    by_id: dict,
    wt: Path,
    expected_head_sha: str | None = None,
) -> None:
    """Create `wt` on a fresh task branch, stacked on the task's dependencies.

    Branches off the first dependency (dependency_start_ref) and merges any
    sibling dependencies into the new worktree so it carries ALL dependency
    commits. A merge conflict between sibling deps is aborted and logged (rare --
    deps are usually a chain), leaving the worktree on the primary dependency.
    """
    start, extra = dependency_start_ref(repo, spec_id, task, by_id)
    branch = f"{spec_id}/{task['id'].lower()}"

    if _branch_exists(repo, branch):
        _validate_retained_task_branch(repo, branch, start, expected_head_sha)

    def _add() -> subprocess.CompletedProcess:
        # Resume-safe: a prior partial run may have left the branch committed (and
        # then had its worktree dir cleaned). Reuse the existing branch instead of
        # crashing on `add -b <branch>` ("already exists"); only create with -b
        # when the branch is genuinely new.
        if _branch_exists(repo, branch):
            return _git(repo, "worktree", "add", str(wt), branch, check=False)
        return _git(repo, "worktree", "add", "-b", branch, str(wt), start, check=False)

    r = _add()
    if r.returncode != 0:
        _git(repo, "worktree", "prune", check=False)  # clear a stale registration, retry once
        r = _add()
        if r.returncode != 0:
            raise WorktreeAddError(
                f"git worktree add failed for {task['id']} (branch {branch}): "
                f"{(r.stderr or '').strip()[:300]}"
            )
    for mb in extra:  # carry sibling dependencies' commits too
        mr = _git(wt, "merge", "--no-edit", mb, check=False)
        if mr.returncode != 0:
            _git(wt, "merge", "--abort", check=False)
            print(
                f"  !! {task['id']}: could not stack dependency branch {mb} "
                f"(conflict) -- continuing on {start}"
            )


def bootstrap_worktree(wt: Path, bootstrap_cmd: str | None, log=print) -> bool:
    """Install a freshly-created task worktree's local dependencies before a worker
    is spawned into it.

    Task worktrees branch off the base commit and start WITHOUT the base checkout's
    `node_modules` / installed dependencies -- git worktrees share `.git`, not the
    gitignored install output in the working tree. Without this step every
    implement/fix/review worker rediscovers the missing install mid-task and runs it
    itself -- real time and tokens on every task, every run. This mirrors the spec
    worktree's documented bootstrap (`subagent-prompts.md#spec-worktree-setup`) for
    the per-task fan-out.

    `bootstrap_cmd` is the repo's documented install command (e.g. "npm ci" or
    "cd app && npm ci"), sourced from go-policy.yaml's `worktree_bootstrap_cmd`.
    None/empty -> skip entirely, so repos without a wired command are unaffected.

    Non-fatal by design: a failed install is logged loudly and the worker still
    self-recovers by running its own install, so a flaky registry never quarantines
    a task. Returns True only when the command ran and exited zero.

    MUST be called OUTSIDE the caller's `git_lock`: the shared `.git` registry
    mutation (`git worktree add`) is already done, and a slow `npm ci` must not
    serialize every other task's worktree creation.
    """
    if not bootstrap_cmd:
        return False
    log(f"{_ts()} BOOTSTRAP: {bootstrap_cmd}  (in {wt.name})")
    try:
        proc = subprocess.run(
            bootstrap_cmd, shell=True, cwd=str(wt), capture_output=True, text=True
        )
    except OSError as e:
        log(f"{_ts()} BOOTSTRAP: !! could not launch ({e}); worker will self-install")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        log(
            f"{_ts()} BOOTSTRAP: !! failed (rc={proc.returncode}); "
            f"worker will self-install. {tail}"
        )
        return False
    return True


def instantiate(template: Path = SAMPLE_TEMPLATE, dest: Path = DEFAULT_DEST) -> Path:
    dest = Path(dest).resolve()
    wt_base = dest.parent / f"{dest.name}-wt"
    if dest.exists():
        shutil.rmtree(dest)
    if wt_base.exists():  # hermetic: drop stale worktrees from prior runs
        shutil.rmtree(wt_base)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, dest, ignore=shutil.ignore_patterns("node_modules", "dist", ".git"))
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    _git(dest, "config", "user.email", "orchestrator@example.com")
    _git(dest, "config", "user.name", "Orchestrator")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "chore: instantiate url-shortener sample target")
    return dest


def run_research_session(
    spec_folder: Path,
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    timeout: int = WORKER_TIMEOUT_DEFAULT,
) -> str:
    """Pre-load shared source files into a single agent session and return its session_id.

    Workers that fork from this session inherit the pre-loaded context at cache-read token
    rates instead of each re-reading the same files cold. Enabled via --fork-research (opt-in).

    Strategy:
    1. Read spec markdown + data-model.md from spec_folder.
    2. Count file references across all TASK-*.md frontmatter `files:` lists.
    3. Pre-load the top-N (N ≤ 10) most-referenced source files.
    4. Run a single agent session with those files as context.
    5. Return the session_id from SpawnResult so workers can fork from it.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    # --- collect spec content ---
    spec_lines: list[str] = []
    for candidate in sorted(spec_folder.glob("*.md")):
        if candidate.parent == spec_folder:  # root only, not tasks/ or changes/
            spec_lines.append(f"=== {candidate.name} ===\n{candidate.read_text()[:4000]}")

    dm = spec_folder / "data-model.md"
    if dm.exists() and not any("data-model" in s for s in spec_lines):
        spec_lines.append(f"=== data-model.md ===\n{dm.read_text()[:4000]}")

    # --- count file references in task frontmatter ---
    from collections import Counter

    file_counts: Counter = Counter()
    tasks_dir = spec_folder / "tasks"
    if tasks_dir.is_dir():
        for tf in sorted(tasks_dir.glob("TASK-*.md")):
            try:
                fm = loader.parse_frontmatter(tf.read_text())
                for f in (fm or {}).get("files", []) or []:
                    file_counts[f] += 1
            except Exception:
                pass

    # --- pre-load top-N most-referenced source files ---
    repo_root = spec_folder.parent.parent  # docs/specs/NNN → docs/specs → repo
    file_sections: list[str] = []
    for rel_path, _ in file_counts.most_common(10):
        abs_path = repo_root / rel_path
        if abs_path.exists():
            try:
                content = abs_path.read_text()[:6000]
                file_sections.append(f"=== {rel_path} ===\n{content}")
            except OSError:
                pass

    # --- build research prompt ---
    spec_block = "\n\n".join(spec_lines) or "(no spec files found)"
    files_block = "\n\n".join(file_sections) or "(no shared source files identified)"
    prompt = (
        "You are a research assistant pre-loading context for parallel implementation workers. "
        "Read and understand the following spec and source files. "
        "No output is needed beyond confirming you have read them.\n\n"
        f"## Spec context\n\n{spec_block}\n\n"
        f"## Shared source files\n\n{files_block}\n\n"
        "Context pre-loaded. Workers forking this session will inherit it."
    )

    print(f"{_ts()} RESEARCH: pre-loading {len(file_sections)} shared source file(s) into session")
    # --setting-sources project,local drops the operator's USER-level settings
    # (and Stop hook) from this pre-load spawn too -- same investigation
    # 20260711-130900 mechanism as _LEAN_WORKER_FLAGS below, applied here since
    # this call bypassed LiveSpawn.__call__ entirely. No --tools restriction:
    # this is a read-only context pre-load, not a worker that edits/commits.
    extra_args = ["--setting-sources", "project,local"] if agent == "claude" else []
    result = spawnlib.spawn_agent(
        prompt,
        spec_folder.parent.parent,
        agent=agent,
        model=model,
        timeout=timeout,
        extra_args=extra_args,
        log=print,
    )
    if result.session_id:
        print(f"{_ts()} RESEARCH: session_id={result.session_id} (workers will fork from this)")
    else:
        print(f"{_ts()} RESEARCH: WARNING — no session_id returned; --fork-research has no effect")
    return result.session_id


def _serving_agent_guess(agent: str, model: str, fallback) -> str:
    """Best-effort guess at which configured hop will actually serve a spawn,
    mirroring `spawn_agent`'s own capacity-gate walk (REQ-027) -- used ONLY to
    label the journal entry; `spawn_agent` performs the authoritative selection
    itself. `fallback` is either a single agent name, an ordered sequence of
    names, or falsy (no chain configured). Never raises: any error while
    reading the capacity cache just falls back to the primary `agent`."""
    hops = (
        list(fallback) if isinstance(fallback, (list, tuple)) else ([fallback] if fallback else [])
    )
    candidates = [(agent, model)] + [
        (hop, spawnlib.default_model_for_agent(hop)) for hop in hops if hop and hop != agent
    ]
    try:
        for cand_agent, cand_model in candidates:
            try:
                agent_capacity.check(cand_agent, cand_model)
            except agent_capacity.ProviderUnavailable:
                continue
            return cand_agent
    except Exception:
        pass
    return agent


class LiveSpawn:
    def __init__(
        self,
        spec_id: str,
        spec_folder_rel: str,
        timeout: int = WORKER_TIMEOUT_DEFAULT,
        agent: str = DEFAULT_AGENT,
        model: str | None = None,
        role_models: dict | None = None,
        role_agents: dict | None = None,
        fallback_agent: str | None = None,
        tier_map: dict | None = None,
        fallback_chain: "list[str] | None" = None,
    ) -> None:
        self.agent = agent
        self.label = f"LIVE {agent}"
        self.spec_id = spec_id
        self.spec_folder_rel = spec_folder_rel.rstrip("/") + "/"
        self.timeout = timeout
        self.model = model or spawnlib.default_model_for_agent(agent)
        self.role_models = role_models or {}  # per-role overrides (production)
        # per-role agent CLI overrides (e.g. review=claude while implement/fix
        # stay on a cheaper --agent) -- lets the reviewer run on a genuinely
        # different/independent headless CLI instead of the same one that wrote
        # the code. Falls back to `agent` for any role not listed.
        self.role_agents = role_agents or {}
        self.fallback_agent = fallback_agent
        # TASK-007: resolved routing table pieces, consumed as plain data (no
        # policy.py import -- see live.py's cross-plugin note). tier_map is
        # {(complexity, domain): agent-entry}, the exact shape dispatch.agent_for
        # expects; fallback_chain is an ordered list of agent names that -- when
        # configured -- takes precedence over the legacy single fallback_agent
        # (REQ-018). Both default to "off" so a caller that never sets them gets
        # byte-identical pre-spec dispatch (REQ-016).
        self.tier_map = tier_map or {}
        self.fallback_chain = list(fallback_chain) if fallback_chain else None
        self.research_session_id: str = ""  # set by --fork-research before fan-out
        # task-id -> task dict for the whole spec; set by the caller (drive loop
        # already has it built) so build_worker_prompt can surface a dependency's
        # delivered files to ROLE_IMPLEMENT workers. None is safe -- dispatch
        # just skips the injection.
        self.by_id: dict | None = None
        # ref (`<spec-id>/<task-id>`) -> resolved sibling task dict, for cross-spec
        # `external-dependencies:` entries (spec 025); set by the caller the same
        # way as by_id, via build_external_deps_by_ref(). None is safe -- dispatch
        # just skips the injection.
        self.external_deps_by_ref: dict | None = None
        # Best-effort label for the journal entry (REQ-027): which agent actually
        # served the most recently completed __call__. Set every call; read by
        # the caller (drive()/_commit_step) right after the call returns.
        self.last_agent: str | None = None

    def _task_brief_ctx(self) -> dict:
        """Format templates for where a worker reads its brief.

        Resolved once per spawn against a probe id, then re-templated per task
        in `dispatch._task_brief` -- the path is identical for every task in an
        OpenSpec change and differs only by id in devkit, so asking the adapter
        per task would buy nothing.
        """
        probe = "\x00TASKID\x00"
        try:
            path, anchor = taskformats.task_brief_ref_for(self.spec_folder_rel, probe)
        except (OSError, ValueError, AttributeError):
            return {}
        return {
            "path_fmt": path.replace(probe, "{task_id}"),
            "anchor_fmt": anchor.replace(probe, "{task_id}"),
        }

    def __call__(self, role: str, task: dict, worktree: Path) -> "spawnlib.SpawnResult":
        effective_timeout = task.get("timeout") or self.timeout
        # Consume any extra reads staged by drive() for adaptive context widening
        # (set when a prior review reported context_quality=insufficient). Only
        # injected on fix dispatches; popped here so they don't bleed into later roles.
        extra_reads = (
            list(task.pop("_extra_reads", None) or []) if role == dispatch.ROLE_FIX else []
        )
        ctx = {
            "spec_id": self.spec_id,
            "spec_folder": self.spec_folder_rel,
            "worktree_path": str(worktree),
            "branch": "(checked out)",
            "base_commit": "HEAD",
            "default_agent": self.agent,
            "spec_root_prefix": taskformats.spec_root_prefix_for(self.spec_folder_rel),
            "task_brief": self._task_brief_ctx(),
        }
        prompt = dispatch.build_worker_prompt(
            role,
            task,
            ctx,
            extra_reads=extra_reads,
            by_id=self.by_id,
            external_deps_by_ref=self.external_deps_by_ref,
        )
        # TASK-007: resolve every spawn's agent through the shared precedence
        # function (per-task override > role override > tier match > run
        # default) instead of the old bare `role_agents.get(role, self.agent)`.
        # Judgment roles (review here -- resolve/ci-fix/assembly-resolve never
        # reach LiveSpawn.__call__, see dispatch.JUDGMENT_ROLES) only ever
        # consult the role override / run default, never task["agent"] or
        # self.tier_map (AC-014). Passing reviewer_agent=self.agent (not
        # dispatch.DEFAULT_REVIEWER_AGENT) preserves the pre-spec fallback
        # (self.agent) when review has no role_agents override (REQ-016).
        resolved = dispatch.agent_for(
            role,
            task,
            reviewer_agent=self.agent,
            default_agent=self.agent,
            role_agent_map=self.role_agents,
            tier_map=self.tier_map,
        )
        agent = resolved["agent_cli"] or self.agent
        # A role/tier pinned to a different agent than the run's default has no
        # sensible fallback model in `self.model` (resolved for the DEFAULT
        # agent) -- resolve that agent's own default instead (live.py:934-935
        # pattern), unless the resolution itself carried an explicit model (a
        # pinned tier/role entry, AC-011).
        if agent == self.agent:
            default_model = resolved["agent_model"] or self.model
        else:
            default_model = resolved["agent_model"] or spawnlib.default_model_for_agent(agent)
        model = self.role_models.get(role, default_model)
        # Claude workers get lean flags (bare + measured tool set).
        # Reviewer independence (13.3): review role additionally gets an appended
        # system prompt; implement/fix/cleanup keep the DEFAULT system prompt so
        # prompt-cache reuse stays active across the run's many cold workers.
        extra_args = list(_LEAN_WORKER_FLAGS) if agent == "claude" else []
        resume_session_id = self.research_session_id or None
        if role == dispatch.ROLE_REVIEW:
            if agent == "claude":
                extra_args += ["--append-system-prompt", _REVIEWER_SYSTEM_PROMPT]
            else:
                prompt = f"{_REVIEWER_SYSTEM_PROMPT}\n\n{prompt}"
        if agent != "claude":
            resume_session_id = None
        # An ordered fallback chain (routing-table-resolved) wins over the
        # legacy single --fallback-agent when configured (REQ-018); either way
        # only applies when this spawn is running on the run's OWN default
        # agent (a role/tier override has no sensible fallback of its own),
        # unchanged from the pre-spec gating.
        fallback = self.fallback_chain if self.fallback_chain else self.fallback_agent
        effective_fallback = fallback if agent == self.agent else None
        self.last_agent = agent
        if effective_fallback:
            # Claude task workers now carry real fallback machinery too (see
            # spawn_claude_p's fallback_agent param, brief
            # 20260723-111700-claude-primary-fallback-inert) -- this guess is
            # accurate for claude the same way it already was for codex/opencode.
            self.last_agent = _serving_agent_guess(agent, model, effective_fallback)
        if agent == "claude":
            return spawnlib.spawn_claude_p(
                prompt,
                worktree,
                model=model,
                timeout=effective_timeout,
                extra_args=extra_args,
                resume_session_id=resume_session_id,
                fallback_agent=effective_fallback,
                log=print,
            )
        return spawnlib.spawn_agent(
            prompt,
            worktree,
            agent=agent,
            model=model,
            fallback_agent=effective_fallback,
            timeout=effective_timeout,
            extra_args=extra_args,
            resume_session_id=resume_session_id,
            log=print,
        )


def spawn_one(
    task_id: str, role: str, dest: Path = DEFAULT_DEST, keep: bool = False
) -> dict | None:
    repo = instantiate(SAMPLE_TEMPLATE, dest)
    spec_folder = repo / SAMPLE_SPEC_REL
    spec_id, tasks = taskformats.load_spec(str(spec_folder))
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        print(f"no such task: {task_id}")
        return None

    wt = repo.parent / f"{repo.name}-wt" / task_id.lower()
    branch = f"{spec_id}/{task_id.lower()}"
    _git(repo, "worktree", "add", "-b", branch, str(wt), "HEAD")

    print(f"== LIVE spawn: {role} {task_id} in {wt} ==")
    _spawn_result = LiveSpawn(spec_id, SAMPLE_SPEC_REL)(role, task, wt)
    out = _spawn_result.text
    print("---- worker final message (tail) ----")
    print(out[-1500:] if out else "(no stdout)")

    report = None
    try:
        report = dispatch.parse_report_back(out)
        print("---- parsed report-back ----")
        print(json.dumps(report))
    except Exception as e:
        print(f"---- report-back parse FAILED: {e} ----")

    print("---- worker commit (files) ----")
    print(_git(wt, "show", "--stat", "--oneline", "HEAD", check=False).stdout or "(no commit)")

    if not keep:
        _git(repo, "worktree", "remove", str(wt), "--force", check=False)
    return report


def live_run(
    dest: Path = DEFAULT_DEST,
    max_workers: int = 2,
    out_cassette: str | None = None,
    only: list | None = None,
    with_tail: bool = False,
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
) -> dict:
    """Drive the spec with LIVE workers, recording a REAL cassette.

    Each frontier batch is parallel-eligible but run serially (one claude -p at a
    time) for a safe first pass. Every report-back is appended to `out_cassette`
    incrementally, so a cancel/crash never loses recorded progress. Tail tasks
    (e2e/cleanup) are skipped unless --with-tail, since faithful e2e needs the
    impl branches integrated first (next phase).
    """
    model = model or spawnlib.default_model_for_agent(agent)
    repo = instantiate(SAMPLE_TEMPLATE, dest)
    spec_id, tasks = taskformats.load_spec(str(repo / SAMPLE_SPEC_REL))
    if only:
        keep = set(only)
        tasks = [t for t in tasks if t["id"] in keep]
    for t in tasks:
        t["status"], t["retry_count"] = "pending", 0
    by_id = {t["id"]: t for t in tasks}
    spawn = LiveSpawn(spec_id, SAMPLE_SPEC_REL, agent=agent, model=model)
    wt_base = repo.parent / f"{repo.name}-wt"
    entries: list = []

    def record() -> None:
        if out_cassette:
            progress.atomic_write_text(
                out_cassette,
                json.dumps({"spec_id": spec_id, "entries": entries}, indent=2, sort_keys=True)
                + "\n",
            )

    def ensure_wt(task: dict) -> Path:
        wt = wt_base / task["id"].lower()
        if not wt.exists():
            add_stacked_worktree(repo, spec_id, task, by_id, wt)
        return wt

    def drive(task: dict) -> None:
        wt = ensure_wt(task)
        task["status"] = "claimed"
        while task["status"] not in orchestrate.TERMINAL:
            role = orchestrate.ROLE_BY_STATUS[task["status"]]
            try:
                _spawn_result = spawn(role, task, wt)
                raw = _spawn_result.text
                _usage = _spawn_result.usage
                _tools_used = _spawn_result.tools_used
                _skills_used = _spawn_result.skills_used
            except subprocess.TimeoutExpired:
                limit = task.get("timeout") or getattr(spawn, "timeout", "?")
                print(
                    f"{_ts()}   !! {task['id']}/{role} TIMED OUT after {limit}s "
                    f"-- marking failed so completed work can still integrate"
                )
                task["status"] = "failed"
                break
            try:
                rep = dispatch.parse_report_back(raw)
            except Exception as e:
                print(f"{_ts()}   !! {task['id']}/{role} report parse FAILED: {e}")
                print(f"     raw tail: {raw[-220:]!r}")
                task["status"] = "failed"
                break
            entries.append(
                {
                    "task": rep["task"],
                    "role": rep["step"],
                    "report": {k: rep.get(k) for k in orchestrate._REPORT_FIELDS},
                    **({"usage": _usage} if _usage else {}),
                    **({"tools_used": _tools_used} if _tools_used else {}),
                    **({"skills_used": _skills_used} if _skills_used else {}),
                    **(
                        {"agent": getattr(spawn, "last_agent", None)}
                        if getattr(spawn, "last_agent", None)
                        else {}
                    ),
                }
            )
            record()
            old, new = dispatch.apply_report(tasks, rep, role)
            extra = f" [{rep.get('review_status')}]" if role == dispatch.ROLE_REVIEW else ""
            print(
                f"{_ts()}   {task['id']} {role:9} {old:12} -> {new}{extra}  (sha {str(rep.get('head_sha',''))[:8]})"
            )

    tick = 0
    while True:
        _annotate_external_deps(repo, tasks)
        frontier = coordinator.runnable_frontier(tasks, max_workers)
        if not frontier:
            break
        tick += 1
        print(f"{_ts()} TICK {tick}: {', '.join(t['id'] for t in frontier)}")
        for ft in frontier:
            drive(by_id[ft["id"]])
    if with_tail:
        for tid in [t["id"] for t in tasks if t.get("kind") in ("e2e", "cleanup")]:
            print(f"{_ts()} TAIL: {tid}")
            drive(by_id[tid])

    done = sum(1 for t in tasks if t["status"] in coordinator.DONE)
    print(f"{_ts()} LIVE RUN DONE: {done}/{len(tasks)} tasks done; {len(entries)} cassette entries")
    if out_cassette:
        print(f"cassette: {out_cassette}")
    return {
        "done": done,
        "total": len(tasks),
        "entries": len(entries),
        "spec_id": spec_id,
        "tasks": tasks,
    }


def full(
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    sandbox: str = "behindthedash/orch-sandbox",
    keep_prs: bool = False,
    max_workers: int = 3,
) -> dict:
    """End-to-end: live fan-out -> integrate + grouped PRs -> e2e/cleanup tail on
    the integrated branch -> final PR -> cleanup. Records the real cassette."""
    from . import integrate

    model = model or spawnlib.default_model_for_agent(agent)
    out_cassette = str(_SKILL / ".fixtures" / "sample-spec" / "cassette.live.json")

    res = live_run(DEFAULT_DEST, max_workers, out_cassette, None, False, agent, model)  # fan-out
    repo = Path(DEFAULT_DEST)
    spec_id, tasks = res["spec_id"], res["tasks"]  # carry real per-task statuses
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t.setdefault("retry_count", 0)
    base = integrate.current_branch(repo)
    run_id = f"full-{int(time.time())}"

    prs, group_branch, quarantined = integrate.finish(
        repo, spec_id, tasks, sandbox, run_id, base, cleanup=False
    )

    if not group_branch:
        print("No groups integrated cleanly -- nothing to assemble; see quarantine above.")
        print("=== FULL RUN COMPLETE (quarantined only) ===")
        return {"group_prs": prs, "final": None, "quarantined": quarantined}

    integ = f"{run_id}/integration"  # final integration branch
    _git(repo, "checkout", "-q", "-B", integ, base)
    for gb in group_branch.values():
        m = _git(repo, "merge", "--no-edit", gb, check=False)
        if m.returncode != 0:
            _git(repo, "merge", "--abort", check=False)
            print(f"  integration conflict merging {gb} -- skipped from final branch")

    print("=== TAIL (e2e + cleanup) live on integrated branch ===")
    spawn = LiveSpawn(spec_id, SAMPLE_SPEC_REL, agent=agent, model=model)
    pending_tail = [t for t in tasks if t.get("kind") in ("e2e", "cleanup")]
    while pending_tail:
        progressed = False
        for t in list(pending_tail):
            unmet = [
                dep
                for dep in t.get("deps", [])
                if dep in by_id and by_id[dep].get("status") not in coordinator.DONE
            ]
            failed = [
                dep for dep in unmet if by_id[dep].get("status") in coordinator.FAILED_STATUSES
            ]
            if unmet and not failed:
                continue
            tid = t["id"]
            if failed:
                t["status"] = "failed"
                print(f"  !! {tid} blocked by failed prerequisite(s): {', '.join(failed)}")
            else:
                t["status"] = "claimed"
                while t["status"] not in orchestrate.TERMINAL:
                    role = orchestrate.ROLE_BY_STATUS[t["status"]]
                    try:
                        rep = dispatch.parse_report_back(
                            spawn(role, t, repo).text
                        )  # cwd = repo (on integ)
                    except Exception as e:
                        print(f"  !! {tid}/{role} parse failed: {e}")
                        t["status"] = "failed"
                        break
                    old, new = dispatch.apply_report(tasks, rep, role)
                    print(f"  {tid} {role:9} {old:12} -> {new}")
            pending_tail.remove(t)
            progressed = True
        if not progressed:
            unresolved = ", ".join(t["id"] for t in pending_tail)
            raise RuntimeError(f"tail dependency gate stalled with unresolved tasks: {unresolved}")

    _git(repo, "push", "-q", "sandbox", f"{integ}:{integ}")
    r = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            sandbox,
            "--base",
            "main",
            "--head",
            integ,
            "--title",
            f"[{run_id}] integration + e2e/cleanup",
            "--body",
            f"Final integration of all groups + e2e/cleanup tail ({run_id}).",
        ],
        capture_output=True,
        text=True,
    )
    final = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "(none)"
    print(f"  PR [integration] base=main -> {final}")

    if not keep_prs:
        print("cleanup: closing PRs + deleting branches ...")
        for branch in [integ, *reversed(list(group_branch.values()))]:
            subprocess.run(
                ["gh", "pr", "close", "--repo", sandbox, branch, "--delete-branch"],
                capture_output=True,
                text=True,
            )
    if quarantined:
        print(
            f"NOTE: {len(quarantined)} group(s) quarantined for human review: {', '.join(quarantined)}"
        )
    print("=== FULL RUN COMPLETE ===")
    return {"group_prs": prs, "final": final, "quarantined": quarantined}


# --------------------------------------------------------------------------- #
# Fast-path / deterministic step helpers (remove a spawn where one isn't needed)
# --------------------------------------------------------------------------- #
def _review_exempt(task: dict) -> bool:
    """A task can opt out of the review gate (fast path) via its frontmatter:
    ``review: skip|false|no`` or ``kind: docs``. Everything else is still reviewed
    -- this is opt-in, never inferred from diff size, so quality gating is the
    author's explicit choice."""
    rv = str(task.get("review", "")).strip().lower()
    if rv in ("skip", "false", "no", "none", "off"):
        return True
    return task.get("kind") == "docs"


def _scope_escalation_files(task: dict, report: dict, wt: Path, by_id: dict) -> list[str]:
    """Validate one bounded fix-scope escalation from concrete `missing_context` paths.

    Only an actually failed fix qualifies. Paths must be existing repo-relative
    files and must not collide with another in-flight task's declared files.
    """
    if (
        report.get("status") != "failed"
        or task.get("_scope_escalated")
        or not report.get("missing_context")
    ):
        return []
    root = wt.resolve()
    candidates: list[str] = []
    for raw in report.get("missing_context") or []:
        value = str(raw).strip()
        path = Path(value)
        if not value or path.is_absolute() or any(ch.isspace() for ch in value):
            continue
        resolved = (root / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if resolved.is_file():
            candidates.append(path.as_posix())
    candidates = sorted(set(candidates) - set(task.get("files") or []))
    if not candidates:
        return []
    locked = {
        os.path.normpath(path)
        for other in by_id.values()
        if other is not task and other.get("status") in coordinator.IN_FLIGHT
        for path in (other.get("files") or [])
    }
    if locked & {os.path.normpath(path) for path in candidates}:
        return []
    return candidates


def salvage_report(role: str, task: dict, wt: Path, pre_sha: str) -> dict | None:
    """Recover a usable report-back from git when the worker's report didn't parse
    (e.g. it hit its token cap before emitting the JSON block) but it DID leave a
    commit -- so a good diff isn't thrown away over a formatting miss.

    Only implement/fix are salvageable: a review verdict (PASSED/FAILED) and a
    cleanup completion cannot be safely inferred. Returns a report dict, or None
    when the worker committed nothing (a genuine failure, not salvageable).
    """
    if role not in (dispatch.ROLE_IMPLEMENT, dispatch.ROLE_FIX):
        return None
    head = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
    if not head or head == pre_sha:
        return None
    return {
        "task": task["id"],
        "step": role,
        "status": "success",
        "head_sha": head[:8],
        "tests": "none",
        "notes": "salvaged from git (commit present; report-back unparseable)",
    }


def _task_file_in_worktree(wt: Path, spec_rel: str, task_id: str) -> Path:
    """Locate the file holding a task's definition inside a worktree.

    Asks the `TaskSource` first, so a format that keeps every task in one file
    (OpenSpec's `tasks.md`) resolves correctly. Hardcoding `tasks/<id>.md` here
    made `_require_task_file` reject every OpenSpec worktree as "missing its
    task file" before any worker was spawned. The devkit `changes/*/` probing
    below is retained as a fallback for that format's older layouts.
    """
    base = wt / spec_rel.strip("/")
    try:
        cand = Path(wt) / taskformats.task_brief_ref_for(base, task_id)[0]
    except (OSError, ValueError, AttributeError):
        cand = base / "tasks" / f"{task_id}.md"
    if cand.exists():
        return cand
    changes = base / "changes"
    if changes.is_dir():
        for d in sorted(changes.iterdir()):
            c = d / f"{task_id}.md"
            if c.exists():
                return c
    return cand


def _require_task_file(wt: Path, spec_rel: str, task_id: str) -> None:
    """Fail loud at dispatch time if a freshly created task worktree is missing
    its own task file.

    Defense-in-depth backstop for the pipeline-level commit-discipline guard
    (PR #248): if some other call site still forks a worktree before its
    tasks/ commit lands on the branch point, the IMPLEMENT worker must never
    silently receive an unreadable brief.
    """
    tf = _task_file_in_worktree(wt, spec_rel, task_id)
    if not tf.exists():
        raise WorktreeMissingTaskFileError(
            f"task {task_id} worktree {wt} has no task file at "
            f"{tf.relative_to(wt)} -- the worktree branched before its "
            "tasks/ commit landed on the base branch (see PR #248)"
        )


def _require_dependency_files(wt: Path, task: dict, by_id: dict) -> "list[dict]":
    """Fail loud at dispatch time if a dependency's declared files are missing
    from the stacked worktree after the dependency's branch was merged in.

    Structural counterpart to PR #295's prompt-level fix (`build_worker_prompt`
    surfaces a dependency's `files:` in the IMPLEMENT worker's "Read first"
    list and instructs it to `ls`-verify before reporting insufficient
    context): that fix relies on the worker actually reading and following the
    instruction. This runs deterministically before the worker is ever
    spawned, so a missing dependency file quarantines the task with a
    forensically specific error instead of silently spawning a worker that has
    to notice the gap itself.

    A dependency's declared `files:` frontmatter can legitimately drift from
    what it actually committed (e.g. an alembic migration renamed to dodge a
    revision-slot collision). Before failing, reconcile against the
    dependency's actual commit (`task["head_sha"]`, set by
    `dispatch.apply_report` for both live and resumed runs): if that commit is
    an ancestor of the stacked worktree's HEAD, the dependency's *content* did
    land even though a declared path didn't, so this downgrades to a WARN
    naming both paths instead of crashing the dispatch.

    Returns the structured `dependency_file_drift` events fired (empty when no
    drift occurred) so callers can journal them for cross-run aggregation
    (`safety_net_report.py`) -- the print above is only visible in a single
    run's transcript.
    """
    events: "list[dict]" = []
    for dep_id in task.get("deps", []):
        dep = by_id.get(dep_id)
        if not dep:
            continue
        for f in dep.get("files", []) or []:
            if (wt / f).exists():
                continue
            dep_head = dep.get("head_sha")
            if (
                dep_head
                and _git(
                    wt, "merge-base", "--is-ancestor", dep_head, "HEAD", check=False
                ).returncode
                == 0
            ):
                print(
                    f"{_ts()} WARN: task {task['id']} worktree {wt} is missing "
                    f"{dep_id}'s declared file {f!r}, but {dep_id}'s commit "
                    f"{dep_head[:8]} is an ancestor of this worktree's HEAD -- "
                    "likely declared-vs-actual filename drift (the dependency "
                    f"renamed a file). Correct {dep_id}'s task frontmatter "
                    "`files:` to match what it actually committed."
                )
                events.append(
                    {
                        "event": "dependency_file_drift",
                        "task": task["id"],
                        "dep_id": dep_id,
                        "declared_path": f,
                        "dep_head_sha": dep_head,
                        "at": round(time.time(), 3),
                    }
                )
                continue
            raise WorktreeMissingDependencyFileError(
                f"task {task['id']} worktree {wt} is missing dependency "
                f"{dep_id}'s declared file {f!r} -- the stacked worktree "
                "merge (add_stacked_worktree) did not carry it (see PR "
                "#295, whose prompt-only fix this guard structurally backs up)"
            )
    return events


# The devkit frontmatter contract owns the surgical status write. Re-exported
# here under its historical name because this module's helpers are part of the
# orchestrator's tested public surface.
set_task_status_completed = _devkit_schema.set_status_completed


def cleanup_task_in_python(wt: Path, task_id: str) -> dict:
    """Deterministic replacement for the cleanup SPAWN -- a pure state transition:
    no filesystem write, no commit.

    The task's ``completed`` status is recorded in the run journal (by the
    caller's ``_commit_step`` -> ``dispatch.apply_report``), which is what
    actually drives ``deliverable_subset`` and the post-run dashboard. It is
    deliberately NOT written into the task file on this branch: doing that put a
    ``docs/specs/**`` diff on every one of N task branches, which is the entire
    reason ``integrate._strip_spec_folder_to_base()`` had to exist. The artifact
    write now happens exactly once per group, on the group branch, at integrate
    time -- see ``integrate._write_group_task_status()``.
    """
    head = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
    return {
        "task": task_id,
        "step": "cleanup",
        "status": "success",
        "head_sha": head[:8],
        "tests": "none",
        "notes": "deterministic cleanup (journal-only status, no spawn)",
    }


def _fire_notify(cmd: str, payload: dict) -> None:
    """Best-effort: run *cmd* (shell) with JSON payload on stdin.

    Called on every task-state transition and phase boundary so callers can push
    progress (desktop notification, Slack, conductor relay). Never raises — a
    broken notify command must never abort a run.
    """
    try:
        subprocess.run(
            cmd,
            shell=True,
            input=json.dumps(payload),
            text=True,
            timeout=10,
            capture_output=True,
        )
    except Exception:
        pass


def live_run_real(
    repo: Path,
    spec_rel: str,
    max_workers: int = 2,
    out_cassette: str | None = None,
    only: list | None = None,
    with_tail: bool = False,
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    timeout: int = WORKER_TIMEOUT_DEFAULT,
    resume: bool = False,
    run_id: str | None = None,
    role_models: dict | None = None,
    role_agents: dict | None = None,
    fallback_agent: str | None = None,
    tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    run_budget: float | None = None,
    spawn=None,
    git_lock=None,
    notify_cmd: str | None = None,
    progress_interval: int | None = None,
    bootstrap_cmd: str | None = None,
) -> dict:
    """Like live_run but operates on a REAL repo — no instantiate/copy.

    repo     — absolute path to the existing git repo (already on the base branch).
    spec_rel — spec folder relative to repo root, e.g. 'docs/specs/008-image-route'.
    Worktrees land under <repo.parent>/<repo.name>-worktrees/<spec_id>-<task_id>.
    resume   — if True and `out_cassette` already exists, replay it to skip tasks
               that already finished and continue mid-flight ones, so an
               interrupted run picks up where it left off instead of re-spawning
               (and re-paying for) completed work.
    run_id   — stable run identity; passed by full_real to ensure persistence
               in the run journal across invocations.
    role_models — optional {role: model} overrides (e.g. review on a stronger
               model than implement); falls back to `model` per role.
    role_agents — optional {role: agent} overrides (e.g. review="claude" while
               implement/fix stay on a cheaper `agent`); falls back to `agent` per role.
    tier_map — optional {(complexity, domain): agent-entry} routing table match
               (TASK-001/006), threaded straight into LiveSpawn's construction.
    fallback_chain — optional ordered fallback agent list; wins over the legacy
               single `fallback_agent` when configured (REQ-018).
    run_budget — optional whole-run wall-clock cap (s); once exceeded the fan-out
               stops dispatching NEW tasks. None -> RUN_BUDGET_DEFAULT (0 = off).

    The dependency-independent tasks of each frontier batch run CONCURRENTLY (each
    in its own worktree), bounded by max_workers -- the headline speed-up over the
    old one-at-a-time loop. Shared state (the journal, task statuses, the
    heartbeat) is mutated only under a lock; the slow `claude -p` spawns run
    outside it.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    repo = repo.resolve()
    role_models = _effective_role_models(agent, role_models)
    spec_id, tasks = taskformats.load_spec(str(repo / spec_rel))
    tasks = apply_run_plan(repo, spec_rel, spec_id, tasks)
    for t in tasks:
        t["retry_count"] = 0
        if t.get("status") in coordinator.DONE:
            # Successfully-terminal in the spec folder: pre-mark done. Treated as a
            # satisfied dependency for dependents, excluded from the frontier and the
            # fan-out -- never reset to pending, never re-implemented (FR-1).
            continue
        # Everything else (non-terminal, failed, escalated) gets a clean run.
        t["status"] = "pending"
    if only:
        keep = set(only)
        # Keep dependency tasks in the list so runnable_frontier sees their deps
        # as satisfied. Dropping them (the old behaviour) made every dependent
        # un-runnable because its deps were no longer present/done.
        if "completed" not in coordinator.DONE:
            raise RuntimeError("coordinator.DONE missing 'completed'; --only pre-mark unsafe")
        for t in tasks:
            if t["id"] not in keep:
                # Mark as "completed" (not "done") so deliverable_subset excludes these —
                # their worktree branches may no longer exist from a prior run.
                t["status"] = "completed"

    by_id = {t["id"]: t for t in tasks}
    # `spawn` is injectable so the concurrent fan-out is unit-testable with a fake
    # worker (no real claude -p); production builds the headless LiveSpawn.
    spawn = spawn or LiveSpawn(
        spec_id,
        spec_rel.rstrip("/") + "/",
        timeout=timeout,
        agent=agent,
        model=model,
        role_models=role_models,
        role_agents=role_agents,
        fallback_agent=fallback_agent,
        tier_map=tier_map,
        fallback_chain=fallback_chain,
    )
    # Set unconditionally (default-constructed or caller-injected, e.g. the
    # --fork-research spawn built in _full_real_inner) so ROLE_IMPLEMENT prompts
    # can surface a dependency's delivered files (see dispatch.build_worker_prompt).
    if hasattr(spawn, "by_id"):
        spawn.by_id = by_id
    if hasattr(spawn, "external_deps_by_ref"):
        spawn.external_deps_by_ref = build_external_deps_by_ref(repo, tasks)
    wt_base = repo.parent / f"{repo.name}-worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    run_budget = RUN_BUDGET_DEFAULT if run_budget is None else run_budget

    # Concurrency: a batch's tasks run in parallel threads. `state_lock` guards the
    # shared journal/task-state/heartbeat mutations; `git_lock` serialises the only
    # contended git op (worktree add touches the shared .git registry). Per-task
    # commits happen in distinct worktrees and need no lock.
    # In pipeline mode the caller injects a shared process-wide lock (AC-012 / TASK-005).
    state_lock = threading.Lock()
    git_lock = git_lock or threading.Lock()
    actives: dict = {}  # task_id -> {task, role, started_at, pid} of in-flight workers
    # Paused-time accumulator for the run-budget clock: each spawn reports back how
    # many seconds it spent sleeping on Anthropic session-limit waits. Sum is
    # subtracted from wall-clock elapsed so a 4h reset window never consumes a 4h
    # budget. Mutated under state_lock (each drive() appends after every spawn).
    _budget_pauses: list = []

    def _publish_actives() -> None:
        if out_cassette:
            progress.write_actives(
                out_cassette, run_id=run_id, spec_id=spec_id, actives=list(actives.values())
            )

    # Resume: replay an existing journal so completed roles aren't re-spawned.
    # `entries` is SEEDED from the journal (not reset), so record() keeps appending
    # to the same history rather than truncating it.
    entries: list = []
    journaled_heads: dict[str, str] = {}
    if resume and out_cassette and Path(out_cassette).exists():
        try:
            journal = json.loads(Path(out_cassette).read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"{_ts()} RESUME: journal {out_cassette} unreadable ({e}); starting fresh")
        else:
            entries = reconcile_from_journal(tasks, journal)
            journaled_heads.update(_journaled_task_heads(entries))
            skipped = [t["id"] for t in tasks if t["status"] in coordinator.DONE]
            inflight = [t["id"] for t in tasks if t["status"] in coordinator.IN_FLIGHT]
            print(
                f"{_ts()} RESUME: {len(entries)} journal entries replayed -- "
                f"terminal (skipped): {', '.join(skipped) or '-'}; "
                f"mid-flight (continuing): {', '.join(inflight) or '-'}"
            )

    # Validate AFTER reconcile: a resume replays the journal first, so only tasks
    # still `pending` (genuinely undispatched) are checked. Validating earlier
    # tripped on already-completed tasks that lack `files` frontmatter, aborting
    # every resume of an existing run.
    validate_task_metadata(tasks)

    _budget_stopped_at: list[float | None] = [None]  # list so nested def can mutate; None = not set

    def record() -> None:
        if out_cassette:
            journal_dict = {"spec_id": spec_id, "entries": entries}
            if run_id is not None:
                journal_dict["run_id"] = run_id
            if _budget_stopped_at[0] is not None:
                journal_dict["budget_stopped_at"] = _budget_stopped_at[0]
            progress.atomic_write_text(
                out_cassette, json.dumps(journal_dict, indent=2, sort_keys=True) + "\n"
            )

    def ensure_wt(task: dict) -> Path:
        wt = wt_base / f"{spec_id}-{task['id'].lower()}"
        expected_head_sha = journaled_heads.get(task["id"])
        if not wt.exists():
            with git_lock:  # `git worktree add` mutates the shared .git registry
                if not wt.exists():
                    if expected_head_sha:
                        add_stacked_worktree(
                            repo, spec_id, task, by_id, wt, expected_head_sha=expected_head_sha
                        )
                    else:
                        add_stacked_worktree(repo, spec_id, task, by_id, wt)
                    drift_events = _require_dependency_files(wt, task, by_id)
                    if drift_events:
                        with state_lock:
                            entries.extend(drift_events)
                            record()
                    _require_task_file(wt, spec_rel, task["id"])
            # Install deps into the fresh worktree OUTSIDE git_lock so a slow
            # `npm ci` never serializes other tasks' worktree creation.
            bootstrap_worktree(wt, bootstrap_cmd)
        else:
            start, _ = dependency_start_ref(repo, spec_id, task, by_id)
            branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
            expected_branch = f"{spec_id}/{task['id'].lower()}"
            if branch != expected_branch:
                raise WorktreeAddError(
                    f"retained worktree {wt} is on {branch or 'detached HEAD'}, expected "
                    f"{expected_branch}; repair it explicitly before resuming"
                )
            _validate_retained_task_branch(repo, branch, start, expected_head_sha)
            drift_events = _require_dependency_files(wt, task, by_id)
            if drift_events:
                with state_lock:
                    entries.extend(drift_events)
                    record()
            _require_task_file(wt, spec_rel, task["id"])
        return wt

    def _commit_step(
        task: dict,
        role: str,
        rep: dict,
        t0: float,
        t1: float,
        usage: dict | None = None,
        tools_used: list | None = None,
        skills_used: list | None = None,
        agent: str | None = None,
    ) -> tuple:
        """Append the journal entry, persist, drop the heartbeat, apply the state
        transition -- all under the lock so concurrent workers never race."""
        with state_lock:
            entry: dict = {
                "task": rep["task"],
                "role": rep["step"],
                "report": {k: rep.get(k) for k in orchestrate._REPORT_FIELDS},
                # Per-step timing -> the journal is auditable after the fact.
                # Extra keys; reconcile_from_journal/ReplaySpawn ignore them.
                "started_at": round(t0, 3),
                "ended_at": round(t1, 3),
                "duration_s": round(t1 - t0, 1),
            }
            if usage:
                entry["usage"] = usage
            if tools_used:
                entry["tools_used"] = tools_used
            if skills_used:
                entry["skills_used"] = skills_used
            if task.get("_scope_added_files"):
                entry["scope_escalated"] = True
                entry["scope_added_files"] = list(task["_scope_added_files"])
            # TASK-007 (REQ-027, AC-026): best-effort agent label -- a lookup
            # problem here must never fail the run, only omit the key.
            try:
                if agent:
                    entry["agent"] = agent
            except Exception:
                pass
            entries.append(entry)
            task.pop("_scope_added_files", None)
            record()
            actives.pop(task["id"], None)
            _publish_actives()
            old, new = dispatch.apply_report(tasks, rep, role)
            if notify_cmd:
                done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
                _fire_notify(
                    notify_cmd,
                    {
                        "spec": spec_id,
                        "run_id": run_id,
                        "phase": "fanout",
                        "tasks_done": done_ct,
                        "tasks_total": len(tasks),
                        "current_task": task["id"],
                        "role": role,
                        "old_status": old,
                        "new_status": new,
                    },
                )
            return old, new

    def drive(task: dict) -> None:
        wt = ensure_wt(task)
        # Only claim a fresh task. A RESUMED task carries its replayed mid-flight
        # status (reviewing/fixing/cleaning) and must continue from there, not be
        # reset to "claimed" (which would re-run implement and clobber progress).
        if task["status"] == "pending":
            task["status"] = "claimed"
        while task["status"] not in orchestrate.TERMINAL:
            role = orchestrate.ROLE_BY_STATUS[task["status"]]

            # Fast path (#16): a review-exempt task skips the review SPAWN entirely
            # (reviewing -> cleaning) -- opt-in via frontmatter, recorded so resume
            # sees it as a passed review.
            if role == dispatch.ROLE_REVIEW and _review_exempt(task):
                t0 = time.time()
                rep = {
                    "task": task["id"],
                    "step": "review",
                    "status": "success",
                    "review_status": "PASSED",
                    "notes": "review skipped (exempt)",
                }
                _, new = _commit_step(task, role, rep, t0, t0)
                print(f"{_ts()}   ⏭ {task['id']} review    skipped (exempt) -> {new}")
                continue

            # Deterministic cleanup (#14): status write-back + commit, no spawn.
            if role == dispatch.ROLE_CLEANUP:
                t0 = time.time()
                rep = cleanup_task_in_python(wt, task["id"])
                t1 = time.time()
                _, new = _commit_step(task, role, rep, t0, t1)
                print(
                    f"{_ts()}   ✓ {task['id']} cleanup   (python) -> {new}  {progress._fmt_dur(t1 - t0)}"
                )
                continue

            # Spawned step (implement | review | fix).
            t0 = time.time()
            pre_sha = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
            with state_lock:
                actives[task["id"]] = {
                    "task": task["id"],
                    "role": role,
                    "started_at": t0,
                    "pid": os.getpid(),
                }
                _publish_actives()
            _eff_timeout = task.get("timeout") or getattr(spawn, "timeout", "?")
            _override_marker = (
                " [task-override]"
                if isinstance(_eff_timeout, int) and _eff_timeout != getattr(spawn, "timeout", None)
                else ""
            )
            print(
                f"{_ts()}   ▶ {task['id']} {role:9} started  "
                f"(worker running; timeout {_eff_timeout}s{_override_marker})"
            )
            try:
                _spawn_result = spawn(role, task, wt)
                raw = _spawn_result.text
                _usage = _spawn_result.usage
                _tools_used = _spawn_result.tools_used
                _skills_used = _spawn_result.skills_used
                # Accumulate session-limit sleep so the budget clock excludes it.
                if _spawn_result.paused_s:
                    with state_lock:
                        _budget_pauses.append(_spawn_result.paused_s)
            except subprocess.TimeoutExpired:
                limit = task.get("timeout") or getattr(spawn, "timeout", "?")
                t1 = time.time()
                with state_lock:
                    entries.append(
                        _journal_failure_entry(
                            task, role, f"{task['id']}/{role} timed out after {limit}s", t0, t1
                        )
                    )
                    record()
                    actives.pop(task["id"], None)
                    _publish_actives()
                task["status"] = "failed"
                print(
                    f"{_ts()}   !! {task['id']}/{role} TIMED OUT after {limit}s -- marking failed"
                )
                break
            try:
                rep = dispatch.parse_report_back(raw)
            except Exception as e:
                # #11: a good commit shouldn't be lost to an unparseable report-back.
                rep = salvage_report(role, task, wt, pre_sha)
                if rep is None:
                    t1 = time.time()
                    terminal_status = "retryable" if role == dispatch.ROLE_IMPLEMENT else "failed"
                    with state_lock:
                        entries.append(
                            _journal_failure_entry(
                                task,
                                role,
                                f"{task['id']}/{role} report parse failed: {e}",
                                t0,
                                t1,
                                terminal_status=terminal_status,
                            )
                        )
                        record()
                        actives.pop(task["id"], None)
                        _publish_actives()
                    print(f"{_ts()}   !! {task['id']}/{role} report parse FAILED: {e}")
                    print(f"     raw tail: {raw[-220:]!r}")
                    task["status"] = "failed"
                    break
                print(
                    f"{_ts()}   ~ {task['id']}/{role} report unparseable -- salvaged from git commit"
                )
            # Adaptive read-widening: when review reports insufficient context, stage
            # the missing items so the next fix dispatch gets them in its prompt.
            # Never widen on sufficient/too_much — too_much means trim, not add.
            if role == dispatch.ROLE_REVIEW and rep.get("context_quality") == "insufficient":
                mr = rep.get("missing_context") or []
                if mr:
                    task["_extra_reads"] = [str(p) for p in mr]
            scope_files = (
                _scope_escalation_files(task, rep, wt, by_id) if role == dispatch.ROLE_FIX else []
            )
            if scope_files:
                task["_scope_escalated"] = True
                task["_scope_added_files"] = scope_files
                task["_scope_escalation_files"] = scope_files
                task["files"] = list(task.get("files") or []) + scope_files
                task["_extra_reads"] = scope_files
            t1 = time.time()
            old, new = _commit_step(
                task,
                role,
                rep,
                t0,
                t1,
                usage=_usage,
                tools_used=_tools_used,
                skills_used=_skills_used,
                agent=getattr(spawn, "last_agent", None),
            )
            if scope_files:
                task["status"] = new = "fixing"
                print(
                    f"{_ts()}   ↻ {task['id']} fix scope widened once: " f"{', '.join(scope_files)}"
                )
            extra = f" [{rep.get('review_status')}]" if role == dispatch.ROLE_REVIEW else ""
            print(
                f"{_ts()}   ✓ {task['id']} {role:9} {old:12} -> {new}{extra}  "
                f"{progress._fmt_dur(t1 - t0)}  (sha {str(rep.get('head_sha',''))[:8]})"
            )

    def _safe_drive(task: dict) -> None:
        """drive() wrapper for the thread pool: a single task crashing must not
        abort the whole batch (the executor would otherwise re-raise on result())."""
        try:
            drive(task)
        except Exception as e:  # noqa: BLE001 -- isolate one worker's failure
            now = time.time()
            print(f"{_ts()}   !! {task['id']} drive crashed: {e!r} -- marking failed")
            with state_lock:
                task["status"] = "failed"
                entries.append(
                    _journal_failure_entry(task, "drive", f"drive crashed: {e!r}", now, now)
                )
                record()
                actives.pop(task["id"], None)
                _publish_actives()

    # Resumed mid-flight tasks (status in IN_FLIGHT after journal replay) are not
    # "pending", so runnable_frontier won't surface them -- finish them (from whatever
    # role they were interrupted at) before the fan-out. They are re-dispatched in
    # parallel when their file scopes are disjoint (each runs in its own worktree),
    # mirroring the fan-out's file-disjoint batching; same-file ones fall back to serial.
    midflight = [t for t in tasks if t.get("status") in coordinator.IN_FLIGHT]
    for batch in coordinator.disjoint_batches(midflight, max_workers):
        if len(batch) == 1:
            t = batch[0]
            print(f"{_ts()} RESUME: continuing {t['id']} from '{t['status']}'")
            _safe_drive(by_id[t["id"]])
        else:
            ids = ", ".join(f"{t['id']}({t['status']})" for t in batch)
            print(f"{_ts()} RESUME [parallel x{len(batch)}]: continuing {ids}")
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                futures = [ex.submit(_safe_drive, by_id[t["id"]]) for t in batch]
                for fut in futures:
                    fut.result()

    tick = 0
    run_start = time.time()

    _stop_emitter = threading.Event()
    if progress_interval and progress_interval > 0:

        def _emit_progress() -> None:
            while not _stop_emitter.wait(progress_interval):
                try:
                    with state_lock:
                        done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
                        in_flight = list(actives.values())
                    current = ", ".join(a["task"] for a in in_flight) if in_flight else "—"
                    elapsed = progress._fmt_dur(time.time() - run_start)
                    print(
                        f"{_ts()} PROGRESS: {done_ct}/{len(tasks)} done"
                        f" · {current}"
                        f" · {elapsed} elapsed"
                    )
                except Exception:
                    pass

        _emitter = threading.Thread(target=_emit_progress, daemon=True, name="progress-emitter")
        _emitter.start()

    while True:
        if run_budget:
            now = time.time()
            with state_lock:
                total_pauses = sum(_budget_pauses)
            effective = (now - run_start) - total_pauses
            if effective > run_budget:
                # Budget exhausted: record the stop for audit, but DO NOT journal
                # pending (or mid-flight) tasks as failed. They stay in their
                # current state so the next resume continues the fan-out from
                # where it stopped, instead of treating fan-out as complete and
                # SPLIT-quarantining everything.
                _budget_stopped_at[0] = now
                with state_lock:
                    record()
                print(
                    f"{_ts()} RUN BUDGET {run_budget}s exceeded after {tick} tick(s) "
                    f"(effective {effective:.0f}s; {total_pauses:.0f}s session-limit "
                    f"sleeps excluded) -- pending tasks left for resume"
                )
                break
        _annotate_external_deps(repo, tasks)
        frontier = coordinator.runnable_frontier(tasks, max_workers)
        if not frontier:
            break
        tick += 1
        ids = ", ".join(t["id"] for t in frontier)
        if len(frontier) == 1:
            print(f"{_ts()} TICK {tick}: {ids}")
            _safe_drive(by_id[frontier[0]["id"]])
        else:
            # Run the batch CONCURRENTLY -- file-disjoint by construction, each in
            # its own worktree. This is the parallelism the orchestrator promised.
            print(f"{_ts()} TICK {tick} [parallel x{len(frontier)}]: {ids}")
            with ThreadPoolExecutor(max_workers=len(frontier)) as ex:
                futures = [ex.submit(_safe_drive, by_id[ft["id"]]) for ft in frontier]
                for fut in futures:
                    fut.result()
    _stop_emitter.set()
    if with_tail:
        # Tail tasks are serialized, but still form a dependency DAG. Never
        # start a cleanup/e2e task until every prerequisite is terminal-success;
        # failed prerequisites are journaled as blocked failures so the run cannot
        # silently advance past a failed acceptance gate.
        pending_tail = [t for t in tasks if t.get("kind") in ("e2e", "cleanup")]
        while pending_tail:
            progressed = False
            for task in list(pending_tail):
                unmet = [
                    dep
                    for dep in task.get("deps", [])
                    if dep in by_id and by_id[dep].get("status") not in coordinator.DONE
                ]
                failed = [
                    dep for dep in unmet if by_id[dep].get("status") in coordinator.FAILED_STATUSES
                ]
                if unmet and not failed:
                    continue
                if failed:
                    reason = f"blocked by failed prerequisite(s): {', '.join(failed)}"
                    now = time.time()
                    with state_lock:
                        task["status"] = "failed"
                        entries.append(
                            _journal_failure_entry(task, "dependency-gate", reason, now, now)
                        )
                        record()
                    print(f"{_ts()}   !! {task['id']} TAIL BLOCKED -- {reason}")
                else:
                    print(f"{_ts()} TAIL: {task['id']}")
                    drive(task)
                pending_tail.remove(task)
                progressed = True
            if not progressed:
                unresolved = ", ".join(t["id"] for t in pending_tail)
                raise RuntimeError(
                    f"tail dependency gate stalled with unresolved tasks: {unresolved}"
                )

    done = sum(1 for t in tasks if t["status"] in coordinator.DONE)
    print(f"{_ts()} LIVE RUN DONE: {done}/{len(tasks)} tasks done; {len(entries)} cassette entries")
    if out_cassette:
        print(f"cassette: {out_cassette}")
    return {
        "done": done,
        "total": len(tasks),
        "entries": len(entries),
        "spec_id": spec_id,
        "tasks": tasks,
    }


def full_real(
    repo_path: str,
    spec_rel: str,
    remote: str = "origin",
    base: str = "dev",
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_workers: int = 3,
    timeout: int = WORKER_TIMEOUT_DEFAULT,
    resume: bool = True,
    from_verify: bool = False,
    only: list | None = None,
    role_models: dict | None = None,
    role_agents: dict | None = None,
    fallback_agent: str | None = None,
    tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    run_budget: int | None = None,
    pipeline: bool = False,
    re_integrate: bool = False,
    smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    fork_research: bool = False,
    notify_cmd: str | None = None,
    progress_interval: int | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
) -> dict:
    """End-to-end on a REAL repo (the public entry); see `_full_real_inner` for the
    full pipeline doc.

    Wrapped in a single-owner RunLock so a second concurrent run for the SAME spec
    can't interleave journal/branch writes and corrupt the first. The lock releases
    on process exit even if this returns early; a held lock aborts with a clear
    message rather than racing.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    repo = Path(repo_path).resolve()
    role_models = _effective_role_models(agent, role_models)
    journal_path = str(journal_path_for(repo, spec_rel))
    try:
        _lock = RunLock(journal_path).acquire()
    except RunLockHeld as e:
        print(f"{_ts()} ABORT (run lock held by another run for this spec): {e}")
        return {"group_prs": [], "final": None, "quarantined": {}, "merged": [], "aborted": str(e)}
    try:
        return _full_real_inner(
            repo_path,
            spec_rel,
            remote,
            base,
            agent,
            model,
            max_workers,
            timeout,
            resume,
            from_verify,
            only,
            role_models,
            run_budget,
            role_agents=role_agents,
            fallback_agent=fallback_agent,
            tier_map=tier_map,
            fallback_chain=fallback_chain,
            pipeline=pipeline,
            re_integrate=re_integrate,
            smoke_cmd=smoke_cmd,
            bootstrap_cmd=bootstrap_cmd,
            fork_research=fork_research,
            notify_cmd=notify_cmd,
            progress_interval=progress_interval,
            merge_method=merge_method,
            pr_labels=pr_labels,
            pr_pacing_wait=pr_pacing_wait,
        )
    finally:
        _lock.release()


def _pipeline_scheduler(
    repo: Path,
    spec_rel: str,
    remote: str,
    base: str,
    model: str | None,
    max_workers: int,
    timeout: int,
    resume: bool,
    only: list | None,
    role_models: dict | None,
    run_budget: int | None,
    journal_path: str,
    run_id: str,
    smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
    agent: str = DEFAULT_AGENT,
    role_agents: dict | None = None,
    fallback_agent: str | None = None,
    tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    re_integrate: bool = False,
    # Injectable seams (default to production implementations)
    _spawn=None,
    _integrate_one=None,
    _make_verifier=None,
    _assembly_resolve_spawn=None,
    # TASK-005: inject a shared process-wide registry lock here to serialize all
    # .git registry mutations (worktree add/remove, branch delete, prune) across
    # the overlapping fan-out and verify phases. Today fan-out uses git_lock and
    # verify uses Verifier._git_lock; they are distinct threading.Lock() objects
    # safe only because the phases are strictly sequential in the non-pipeline path.
    _registry_lock=None,
) -> dict:
    """Pipeline scheduler: integrate+verify each group as its tasks finish,
    overlapping with the continued fan-out of later groups.

    Group completion detection: after each fan-out tick, any group whose tasks
    are all terminal (done | failed | escalated) is submitted to a background
    ThreadPoolExecutor for integrate+verify.

    Base-before-dependent: each dependent group's IV thread waits on the base
    group's done event before starting integrate, so the ordering invariant holds
    even when multiple groups are processed concurrently.

    Exception isolation: any exception inside a group's IV is caught, quarantines
    that group (and cascades to its dependents via the done-event mechanism), and
    never aborts the run (REQ-018 / REQ-NR004).

    TASK-006 note: journal writes inside integrate_one (via _write_group_journal)
    should be made atomic under concurrent interleaved writes. Either update
    _write_group_journal to use progress.atomic_write_text, or pass an atomic-write
    callback through the integrate_one seam.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    from . import integrate as integrate_module
    from . import verify as verify_module

    role_models = _effective_role_models(agent, role_models)
    spec_id, tasks = taskformats.load_spec(str(repo / spec_rel))
    tasks = apply_run_plan(repo, spec_rel, spec_id, tasks)
    for t in tasks:
        t.setdefault("retry_count", 0)
        if t.get("status") in coordinator.DONE:
            continue
        t["status"] = "pending"
    if only:
        keep = set(only)
        for t in tasks:
            if t["id"] not in keep:
                t["status"] = "completed"

    groups = coordinator.plan_groups(tasks)
    by_id = {t["id"]: t for t in tasks}

    # Spec-folder ownership (matches finish_real logic): only the first independent
    # group carries docs/specs/<spec_id>/; all other independent siblings strip it.
    _spec_carrier = next((g["name"] for g in groups if not g.get("depends_on")), None)
    if _spec_carrier is None and groups:
        _spec_carrier = groups[0]["name"]

    # --- Injectable seam bindings ---
    spawn_fn = _spawn or LiveSpawn(
        spec_id,
        spec_rel.rstrip("/") + "/",
        timeout=timeout,
        agent=agent,
        model=model,
        role_models=role_models,
        role_agents=role_agents,
        fallback_agent=fallback_agent,
        tier_map=tier_map,
        fallback_chain=fallback_chain,
    )
    # See live_run_real's identical guard: surfaces dependency files to
    # ROLE_IMPLEMENT prompts, default-constructed or caller-injected alike.
    if hasattr(spawn_fn, "by_id"):
        spawn_fn.by_id = by_id
    if hasattr(spawn_fn, "external_deps_by_ref"):
        spawn_fn.external_deps_by_ref = build_external_deps_by_ref(repo, tasks)
    integrate_one_fn = (
        _integrate_one if _integrate_one is not None else integrate_module.integrate_one
    )
    if pr_pacing_wait > 0:
        # PR pacing (mirrors finish_real's sequential pacing): serialize the
        # integrate+PR-open step across the concurrent per-group IV threads and,
        # between PR creations, wait (bounded) for the previous group PR's
        # checks to resolve so sibling PRs don't hit a shared CI runner pool
        # simultaneously. Only integrate is serialized — verify still overlaps.
        # No deadlock risk: dependency ordering uses group_done_events, which
        # are fired by IV threads after verify and never while waiting here,
        # and the wait itself is bounded by pr_pacing_wait.
        _pace_lock = threading.Lock()
        _paced_prev: list = []
        _unpaced_integrate_one = integrate_one_fn

        def _paced_integrate_one(*a, **kw):
            with _pace_lock:
                if _paced_prev:
                    prev_url = _paced_prev[-1]
                    print(
                        f"{_ts()} PACE waiting on PR checks " f"(max {pr_pacing_wait}s): {prev_url}"
                    )
                    outcome = integrate_module._wait_for_pr_checks(repo, prev_url, pr_pacing_wait)
                    print(f"{_ts()} PACE {outcome}: {prev_url}")
                result = _unpaced_integrate_one(*a, **kw)
                if result is not None:
                    _paced_prev.append(result[2])
                return result

        integrate_one_fn = _paced_integrate_one
    _ar_agent, _ar_model = _role_agent_model(
        dispatch.ROLE_ASSEMBLY_RESOLVE, agent, model, role_agents, role_models
    )
    assembly_resolve_spawn_fn = (
        _assembly_resolve_spawn
        if _assembly_resolve_spawn is not None
        else verify_module._make_live_spawn(_ar_model, timeout, agent=_ar_agent)
    )

    def _default_make_verifier() -> "verify_module.Verifier":
        resolve_spawn, ci_fix_spawn = _verifier_role_spawns(
            agent, model, timeout, role_agents, role_models
        )
        return verify_module.Verifier(
            repo,
            remote,
            base,
            spec_id,
            spawn=resolve_spawn,
            ci_fix_spawn=ci_fix_spawn,
            git_lock=iv_lock,  # shared registry lock (AC-012 / TASK-005)
            merge_method=merge_method,
            spec_rel=spec_rel,  # tells the deny-list which spec root to guard
        )

    make_verifier_fn = _make_verifier if _make_verifier is not None else _default_make_verifier

    # iv_lock: serializes all shared mutable state (merged/quarantined/group_branch)
    # AND shared .git registry mutations (worktree add/remove, branch delete, prune)
    # across the overlapping fan-out and IV background threads. In pipeline mode a
    # caller-injected lock (_registry_lock) is shared with fan-out's git_lock and the
    # Verifier's _git_lock so all registry mutations serialize on one object (AC-012).
    iv_lock = _registry_lock or threading.Lock()

    group_branch: dict = {}
    quarantined: dict = {}
    merged: list = []
    armed: dict = {}
    prs: list = []

    # One event per group: fired when the group reaches MERGED or quarantined so
    # dependent groups can unblock and observe the quarantined state before starting.
    group_done_events = {g["name"]: threading.Event() for g in groups}
    dispatched_groups: set = set()

    # Per-group phase tracking for concurrent-phase progress (AC-016/AC-017).
    # Mutated under iv_lock; read (snapshotted) by _emit_group_phases (best-effort).
    _group_phase_map: dict = {g["name"]: "fanout" for g in groups}

    def _emit_group_phases() -> None:
        """Best-effort: update heartbeat with current per-group phases."""
        try:
            progress.set_group_phases(journal_path, dict(_group_phase_map))
        except Exception:
            pass

    terminal_statuses = coordinator.DONE | coordinator.FAILED_STATUSES

    def _group_is_terminal(g: dict) -> bool:
        return all(
            by_id[tid].get("status") in terminal_statuses for tid in g["tasks"] if tid in by_id
        )

    def _integrate_verify_group(g: dict) -> None:
        """Background IV worker: integrate then verify one group.
        Always fires the group's done event so dependents never deadlock.
        """
        name = g["name"]
        try:
            # Base-before-dependent gate: wait for each dependency to finish
            for dep in g.get("depends_on", []):
                group_done_events[dep].wait()
                if dep in quarantined:
                    with iv_lock:
                        quarantined[name] = f"base group '{dep}' quarantined"
                    _record_group_fn(name, "", f"{run_id}/{name}", "QUARANTINED")
                    return

            # Resume: skip already-completed IV phases for this group (AC-013, REQ-017)
            journal_rec = groups_journal.get(name, {})
            if journal_rec.get("state") == "MERGED":
                # Terminal: group already merged on a prior interrupted run.
                with iv_lock:
                    prs.append((name, base, journal_rec.get("pr_url")))
                return
            _skip_integrate = bool(journal_rec.get("pr_url"))
            if _skip_integrate:
                # Already integrated but not merged; restore branch ref for verify.
                with iv_lock:
                    if name not in group_branch:
                        group_branch[name] = journal_rec.get("head_branch", f"{run_id}/{name}")
                    prs.append((name, base, journal_rec.get("pr_url")))

            if not _skip_integrate:
                # Integrate
                status = {t["id"]: t.get("status", "pending") for t in tasks}
                try:
                    integrate_kwargs = {
                        "_record_group": _record_group_fn,
                        "git_lock": iv_lock,
                        "strip_spec_folder": not g.get("depends_on") and g["name"] != _spec_carrier,
                        "smoke_cmd": smoke_cmd,
                        "assembly_resolve_spawn": assembly_resolve_spawn_fn,
                    }
                    if pr_labels is not None:
                        integrate_kwargs["pr_labels"] = pr_labels
                    pr_tuple = integrate_one_fn(
                        g,
                        repo,
                        spec_id,
                        tasks,
                        remote,
                        run_id,
                        base,
                        journal_path,
                        status,
                        group_branch,
                        quarantined,
                        **integrate_kwargs,
                    )
                    if pr_tuple is not None:
                        with iv_lock:
                            prs.append(pr_tuple)
                except Exception as exc:
                    with iv_lock:
                        quarantined[name] = f"integrate exception: {exc!r}"
                    _record_group_fn(name, "", f"{run_id}/{name}", "QUARANTINED")
                    print(
                        f"{_ts()}   !! GROUP [{name}] integrate raised: {exc!r} -- quarantined, run continues"
                    )
                    return

                if name in quarantined:
                    return  # quarantined by integrate_one (empty subset, conflict, etc.)

                if name not in group_branch:
                    return  # MERGED on a prior run: integrate_one returned None

            # Verify
            with iv_lock:
                _group_phase_map[name] = "verifying"
            _emit_group_phases()
            status2 = {t["id"]: t.get("status", "pending") for t in tasks}
            delivered = {name: coordinator.deliverable_subset(g["tasks"], tasks, status2)[0]}
            verifier = make_verifier_fn()
            try:
                verifier.verify_one(
                    g,
                    group_branch[name],
                    delivered,
                    merged,
                    quarantined,
                    iv_lock,
                    armed=armed,
                )
            except Exception as exc:
                with iv_lock:
                    quarantined[name] = f"verify exception: {exc!r}"
                _record_group_fn(
                    name, "", group_branch.get(name, f"{run_id}/{name}"), "QUARANTINED"
                )
                print(
                    f"{_ts()}   !! GROUP [{name}] verify raised: {exc!r} -- quarantined, run continues"
                )
            else:
                # Stamp MERGED in the journal so a resume skips re-verification of this
                # group. Without this, a group stays state:OPEN in the journal even after
                # its PR auto-merges, causing the next resume to re-verify an already-merged PR.
                #
                # A group only queued for auto-merge (`auto_merge()` returned
                # (True, "queued")) is NOT a confirmed merge -- GitHub arms it but the
                # PR can still sit OPEN/BLOCKED indefinitely (e.g. a required check
                # stuck red). Stamping "MERGED" for that case let a run's journal claim
                # a group was done while `gh pr view` showed it OPEN. Stamp the distinct
                # "AUTOMERGE_ARMED" state instead so a resume re-verifies it (verify_one
                # re-checks live PR state first and promotes to real MERGED once GitHub
                # actually completes the merge).
                if name in merged:
                    with state_lock:
                        pr_url = groups_journal.get(name, {}).get("pr_url", "")
                    _record_group_fn(
                        name, pr_url, group_branch.get(name, f"{run_id}/{name}"), "MERGED"
                    )
                elif name in armed:
                    with state_lock:
                        pr_url = groups_journal.get(name, {}).get("pr_url", "")
                    _record_group_fn(
                        name,
                        pr_url,
                        group_branch.get(name, f"{run_id}/{name}"),
                        "AUTOMERGE_ARMED",
                    )

        finally:
            # Remove from active phase map (best-effort), then fire done event.
            with iv_lock:
                _group_phase_map.pop(name, None)
            _emit_group_phases()
            group_done_events[name].set()

    # --- Fan-out infrastructure (mirrors live_run_real's tick loop) ---
    wt_base = repo.parent / f"{repo.name}-worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    state_lock = threading.Lock()
    git_lock = iv_lock  # shared registry lock: same object as iv_lock (AC-012 / TASK-005)
    actives: dict = {}
    entries: list = []
    journaled_heads: dict[str, str] = {}
    groups_journal: dict = {}  # in-memory mirror of journal["groups"]; updated under state_lock
    # Paused-time accumulator: same purpose as in live_run_real -- each spawn reports
    # session-limit sleep back here so the run-budget clock excludes it.
    _budget_pauses: list = []
    _budget_stopped_at: list[float | None] = [None]  # list so nested def can mutate

    # --re-integrate: clear the persisted integrate_complete marker + per-group
    # integrate records BEFORE the resume-read below so it reconstructs
    # `groups_journal` from a genuinely reset journal. Previously this run-mode
    # entirely bypassed the sequential path's identical clear step (that code
    # lives further down _full_real_inner, past the `if pipeline: return
    # _pipeline_scheduler(...)` early return) so `--re-integrate --pipeline`
    # silently no-op'd: `_integrate_verify_group`'s own journal_rec read below
    # treats any truthy pr_url as "already integrated" and skips straight to
    # verify (brief 20260723-102500, Bug 2). MERGED records are preserved by
    # `_clear_integration_state`, same as the sequential path.
    if re_integrate and Path(journal_path).exists():
        try:
            _reset_journal = json.loads(Path(journal_path).read_text())
        except (OSError, json.JSONDecodeError):
            _reset_journal = {}
        if _clear_integration_state(_reset_journal):
            progress.atomic_write_text(
                journal_path, json.dumps(_reset_journal, indent=2, sort_keys=True) + "\n"
            )
            print(f"{_ts()} RE-INTEGRATE: cleared integrate_complete + group records; rebuilding")

    if resume and Path(journal_path).exists():
        try:
            jdata = json.loads(Path(journal_path).read_text())
            entries = reconcile_from_journal(tasks, jdata)
            journaled_heads.update(_journaled_task_heads(entries))
            groups_journal.update(jdata.get("groups", {}))
            done_ids = [t["id"] for t in tasks if t["status"] in coordinator.DONE]
            inflight_ids = [t["id"] for t in tasks if t["status"] in coordinator.IN_FLIGHT]
            print(
                f"{_ts()} PIPELINE RESUME: {len(entries)} journal entries -- "
                f"done: {', '.join(done_ids) or '-'}; "
                f"in-flight: {', '.join(inflight_ids) or '-'}"
            )
            _resume_drift_report(repo, base, spec_id, tasks)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{_ts()} PIPELINE RESUME: journal unreadable ({exc}); starting fresh")

    # Validate AFTER reconcile so only genuinely-undispatched (`pending`) tasks are
    # checked -- a resume replays the journal first (see live_run_real for rationale).
    validate_task_metadata(tasks)

    def _record() -> None:
        jdict: dict = {"spec_id": spec_id, "entries": entries}
        if run_id:
            jdict["run_id"] = run_id
        if groups_journal:
            jdict["groups"] = groups_journal
        if _budget_stopped_at[0] is not None:
            jdict["budget_stopped_at"] = _budget_stopped_at[0]
        progress.atomic_write_text(journal_path, json.dumps(jdict, indent=2, sort_keys=True) + "\n")

    def _record_group_fn(name: str, pr_url: str, head_branch: str, state: str) -> None:
        """Serialize one group's integrate result under state_lock (AC-015 / TASK-006)."""
        with state_lock:
            groups_journal[name] = {"pr_url": pr_url, "head_branch": head_branch, "state": state}
            _record()

    def _ensure_wt(task: dict) -> Path:
        wt = wt_base / f"{spec_id}-{task['id'].lower()}"
        expected_head_sha = journaled_heads.get(task["id"])
        if not wt.exists():
            with git_lock:
                if not wt.exists():
                    if expected_head_sha:
                        add_stacked_worktree(
                            repo, spec_id, task, by_id, wt, expected_head_sha=expected_head_sha
                        )
                    else:
                        add_stacked_worktree(repo, spec_id, task, by_id, wt)
                    drift_events = _require_dependency_files(wt, task, by_id)
                    if drift_events:
                        with state_lock:
                            entries.extend(drift_events)
                            _record()
                    _require_task_file(wt, spec_rel, task["id"])
            # Install deps into the fresh worktree OUTSIDE git_lock so a slow
            # `npm ci` never serializes other tasks' worktree creation.
            bootstrap_worktree(wt, bootstrap_cmd)
        else:
            start, _ = dependency_start_ref(repo, spec_id, task, by_id)
            branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
            expected_branch = f"{spec_id}/{task['id'].lower()}"
            if branch != expected_branch:
                raise WorktreeAddError(
                    f"retained worktree {wt} is on {branch or 'detached HEAD'}, expected "
                    f"{expected_branch}; repair it explicitly before resuming"
                )
            _validate_retained_task_branch(repo, branch, start, expected_head_sha)
            drift_events = _require_dependency_files(wt, task, by_id)
            if drift_events:
                with state_lock:
                    entries.extend(drift_events)
                    _record()
            _require_task_file(wt, spec_rel, task["id"])
        return wt

    def _commit_step(
        task: dict,
        role: str,
        rep: dict,
        t0: float,
        t1: float,
        usage: dict | None = None,
        tools_used: list | None = None,
        skills_used: list | None = None,
        agent: str | None = None,
    ) -> tuple:
        with state_lock:
            entry: dict = {
                "task": rep["task"],
                "role": rep["step"],
                "report": {k: rep.get(k) for k in orchestrate._REPORT_FIELDS},
                "started_at": round(t0, 3),
                "ended_at": round(t1, 3),
                "duration_s": round(t1 - t0, 1),
            }
            if usage:
                entry["usage"] = usage
            if tools_used:
                entry["tools_used"] = tools_used
            if skills_used:
                entry["skills_used"] = skills_used
            if task.get("_scope_added_files"):
                entry["scope_escalated"] = True
                entry["scope_added_files"] = list(task["_scope_added_files"])
            # TASK-007 (REQ-027, AC-026): best-effort agent label -- a lookup
            # problem here must never fail the run, only omit the key.
            try:
                if agent:
                    entry["agent"] = agent
            except Exception:
                pass
            entries.append(entry)
            task.pop("_scope_added_files", None)
            _record()
            actives.pop(task["id"], None)
            return dispatch.apply_report(tasks, rep, role)

    def _drive(task: dict) -> None:
        wt = _ensure_wt(task)
        if task["status"] == "pending":
            task["status"] = "claimed"
        while task["status"] not in orchestrate.TERMINAL:
            role = orchestrate.ROLE_BY_STATUS[task["status"]]
            if role == dispatch.ROLE_REVIEW and _review_exempt(task):
                t0 = time.time()
                rep = {
                    "task": task["id"],
                    "step": "review",
                    "status": "success",
                    "review_status": "PASSED",
                    "notes": "review skipped (exempt)",
                }
                _commit_step(task, role, rep, t0, t0)
                continue
            if role == dispatch.ROLE_CLEANUP:
                t0 = time.time()
                rep = cleanup_task_in_python(wt, task["id"])
                t1 = time.time()
                _commit_step(task, role, rep, t0, t1)
                continue
            t0 = time.time()
            pre_sha = _git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
            with state_lock:
                actives[task["id"]] = {
                    "task": task["id"],
                    "role": role,
                    "started_at": t0,
                    "pid": os.getpid(),
                }
            try:
                _spawn_result = spawn_fn(role, task, wt)
                raw = _spawn_result.text
                _usage = _spawn_result.usage
                _tools_used = _spawn_result.tools_used
                _skills_used = _spawn_result.skills_used
                if _spawn_result.paused_s:
                    with state_lock:
                        _budget_pauses.append(_spawn_result.paused_s)
            except subprocess.TimeoutExpired:
                t1 = time.time()
                with state_lock:
                    entries.append(
                        _journal_failure_entry(task, role, f"{task['id']}/{role} timed out", t0, t1)
                    )
                    _record()
                    task["status"] = "failed"
                    actives.pop(task["id"], None)
                print(f"{_ts()}   !! {task['id']}/{role} TIMED OUT -- marking failed")
                break
            try:
                rep = dispatch.parse_report_back(raw)
            except Exception as exc:
                rep = salvage_report(role, task, wt, pre_sha)
                if rep is None:
                    t1 = time.time()
                    terminal_status = "retryable" if role == dispatch.ROLE_IMPLEMENT else "failed"
                    with state_lock:
                        entries.append(
                            _journal_failure_entry(
                                task,
                                role,
                                f"{task['id']}/{role} report parse failed: {exc}",
                                t0,
                                t1,
                                terminal_status=terminal_status,
                            )
                        )
                        _record()
                        task["status"] = "failed"
                        actives.pop(task["id"], None)
                    print(f"{_ts()}   !! {task['id']}/{role} report parse FAILED: {exc}")
                    break
            # Adaptive read-widening: when review reports insufficient context, stage
            # the missing items so the next fix dispatch gets them in its prompt.
            if role == dispatch.ROLE_REVIEW and rep.get("context_quality") == "insufficient":
                mr = rep.get("missing_context") or []
                if mr:
                    task["_extra_reads"] = [str(p) for p in mr]
            scope_files = (
                _scope_escalation_files(task, rep, wt, by_id) if role == dispatch.ROLE_FIX else []
            )
            if scope_files:
                task["_scope_escalated"] = True
                task["_scope_added_files"] = scope_files
                task["_scope_escalation_files"] = scope_files
                task["files"] = list(task.get("files") or []) + scope_files
                task["_extra_reads"] = scope_files
            _commit_step(
                task,
                role,
                rep,
                t0,
                time.time(),
                usage=_usage,
                tools_used=_tools_used,
                skills_used=_skills_used,
                agent=getattr(spawn_fn, "last_agent", None),
            )
            if scope_files:
                task["status"] = "fixing"
                print(
                    f"{_ts()}   ↻ {task['id']} fix scope widened once: " f"{', '.join(scope_files)}"
                )

    def _safe_drive(task: dict) -> None:
        try:
            _drive(task)
        except Exception as exc:
            now = time.time()
            print(f"{_ts()}   !! {task['id']} drive crashed: {exc!r} -- marking failed")
            with state_lock:
                task["status"] = "failed"
                entries.append(
                    _journal_failure_entry(task, "drive", f"drive crashed: {exc!r}", now, now)
                )
                _record()
                actives.pop(task["id"], None)

    # Resume any in-flight tasks (replayed mid-flight status from journal)
    for t in tasks:
        if t.get("status") in coordinator.IN_FLIGHT:
            print(f"{_ts()} PIPELINE RESUME: continuing {t['id']} from '{t['status']}'")
            _safe_drive(t)

    # Background pool for integrate+verify: one thread per group prevents deadlock
    # when dependent groups block waiting on their base group's done event.
    iv_pool = ThreadPoolExecutor(max_workers=max(1, len(groups)))

    run_budget_eff = RUN_BUDGET_DEFAULT if run_budget is None else run_budget
    run_start = time.time()
    tick = 0
    budget_exceeded = False
    progress.set_phase(journal_path, "fanout")
    _emit_group_phases()
    print(f"{_ts()} === PIPELINE: FAN-OUT + CONCURRENT INTEGRATE+VERIFY ===")

    while True:
        if run_budget_eff:
            now = time.time()
            with state_lock:
                total_pauses = sum(_budget_pauses)
            effective = (now - run_start) - total_pauses
            if effective > run_budget_eff:
                # Budget exceeded: stop dispatching new tasks but leave pending
                # (and mid-flight) tasks in their current state so the next
                # resume continues the fan-out. Do NOT journal them as failed.
                budget_exceeded = True
                _budget_stopped_at[0] = now
                with state_lock:
                    _record()
                print(
                    f"{_ts()} PIPELINE RUN BUDGET {run_budget_eff}s exceeded after {tick} tick(s) "
                    f"(effective {effective:.0f}s; {total_pauses:.0f}s session-limit "
                    f"sleeps excluded) -- pending tasks left for resume"
                )
                break
        _annotate_external_deps(repo, tasks)
        frontier = coordinator.runnable_frontier(tasks, max_workers)
        if not frontier:
            break
        tick += 1
        ids_str = ", ".join(t["id"] for t in frontier)
        if len(frontier) == 1:
            print(f"{_ts()} PIPELINE TICK {tick}: {ids_str}")
            _safe_drive(by_id[frontier[0]["id"]])
        else:
            print(f"{_ts()} PIPELINE TICK {tick} [parallel x{len(frontier)}]: {ids_str}")
            with ThreadPoolExecutor(max_workers=len(frontier)) as ex:
                futs = [ex.submit(_safe_drive, by_id[ft["id"]]) for ft in frontier]
                for fut in futs:
                    fut.result()

        # After this tick: detect newly-complete groups and submit them to the
        # IV pool (non-blocking submit allows continued fan-out while IV runs).
        for g in groups:
            gname = g["name"]
            if gname not in dispatched_groups and _group_is_terminal(g):
                dispatched_groups.add(gname)
                with iv_lock:
                    _group_phase_map[gname] = "integrating"
                _emit_group_phases()
                iv_pool.submit(_integrate_verify_group, g)

    # Post-fanout: dispatch any groups that became terminal after the last tick
    # (e.g. the final frontier emptied but a budget break left groups undispatched).
    for g in groups:
        gname = g["name"]
        if gname not in dispatched_groups:
            if budget_exceeded:
                with iv_lock:
                    quarantined[gname] = "fan-out incomplete (run budget exceeded)"
                    _group_phase_map.pop(gname, None)
                _record_group_fn(gname, "", f"{run_id}/{gname}", "QUARANTINED")
                group_done_events[gname].set()
                dispatched_groups.add(gname)
            elif _group_is_terminal(g):
                dispatched_groups.add(gname)
                with iv_lock:
                    _group_phase_map[gname] = "integrating"
                _emit_group_phases()
                iv_pool.submit(_integrate_verify_group, g)
            else:
                # Non-terminal: fan-out never completed their tasks. Quarantine and
                # fire the event so any dependent IV threads don't deadlock.
                with iv_lock:
                    quarantined[gname] = "fan-out incomplete (run budget or error)"
                    _group_phase_map.pop(gname, None)
                _record_group_fn(gname, "", f"{run_id}/{gname}", "QUARANTINED")
                group_done_events[gname].set()
                dispatched_groups.add(gname)

    iv_pool.shutdown(wait=True)

    done_tasks = sum(1 for t in tasks if t["status"] in coordinator.DONE)
    print(f"{_ts()} PIPELINE FAN-OUT DONE: {done_tasks}/{len(tasks)} tasks terminal")

    if quarantined:
        print(
            f"{_ts()} NOTE: {len(quarantined)} group(s) quarantined for human review "
            f"(worktrees kept): {', '.join(quarantined)}"
        )
    integrate_module._mark_integrate_complete_if_terminal(journal_path, groups, tasks)
    progress.set_phase(journal_path, "done")
    _print_usage_report(journal_path)
    print(f"{_ts()} === PIPELINE RUN COMPLETE ===")
    return {
        "group_prs": prs,
        "final": None,
        "quarantined": quarantined,
        "merged": merged,
    }


def _full_real_inner(
    repo_path: str,
    spec_rel: str,
    remote: str = "origin",
    base: str = "dev",
    agent: str = DEFAULT_AGENT,
    model: str | None = None,
    max_workers: int = 3,
    timeout: int = WORKER_TIMEOUT_DEFAULT,
    resume: bool = True,
    from_verify: bool = False,
    only: list | None = None,
    role_models: dict | None = None,
    run_budget: int | None = None,
    role_agents: dict | None = None,
    fallback_agent: str | None = None,
    tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    pipeline: bool = False,
    re_integrate: bool = False,
    smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    fork_research: bool = False,
    notify_cmd: str | None = None,
    progress_interval: int | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
) -> dict:
    """End-to-end on a REAL repo: fan-out -> integrate -> grouped PRs into base branch.

    repo_path — path to the real git repo (must already be on base branch).
    spec_rel  — spec folder relative to repo root, e.g. 'docs/specs/008-image-route'.
    remote    — git remote to push group branches and open PRs against (default: origin).
    base      — base branch for PRs (default: dev).
    resume    — default True: the fan-out records an incremental run journal next
                to the worktrees, and a re-invocation replays it to skip work that
                already finished. This is what makes the long-running run survive a
                harness/process kill -- re-run the SAME command to continue. Pass
                resume=False (CLI: --fresh) to discard any journal and start over.
    from_verify — if True, skip fan-out and integrate entirely, read per-group
                  integrate records from the journal, and run only verify_and_cleanup.
                  Requires an existing journal with integrate_complete set.
    role_models — optional {role: model} overrides threaded to the fan-out workers.
    role_agents — optional {role: agent} overrides threaded to the fan-out workers
                  (e.g. review="claude" for an independent reviewer while
                  implement/fix stay on a cheaper `agent`).
    run_budget  — optional whole-run wall-clock cap (s) for the fan-out (0 = off).

    Run this DETACHED (background), never as a blocking foreground call: it fans
    out N tasks then blocks on each PR's CI, routinely exceeding a 10-minute
    foreground timeout. If killed, the journal makes the next run resumable.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    from . import integrate
    from . import verify

    repo = Path(repo_path).resolve()
    current = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current != base:
        print(f"{_ts()} WARNING: repo is on '{current}', expected '{base}'. Proceeding anyway.")

    # Pre-integrate freshness: fetch + ff-only the local base ref before the
    # first integrate_one of this run/resume, for BOTH the pipeline and
    # sequential paths below (brief 20260723-171500).
    _refresh_base_branch(repo, remote, base)

    # Journal lives beside the worktrees (OUTSIDE the repo, so the base checkout is
    # never dirtied) and is keyed by spec folder name so re-runs find it.
    wt_base = repo.parent / f"{repo.name}-worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    journal_path = str(journal_path_for(repo, spec_rel))
    if not resume and Path(journal_path).exists():
        Path(journal_path).unlink()  # --fresh: discard prior progress
        print(f"{_ts()} FRESH: discarded prior journal {journal_path}")

    # --from-verify entry point: skip fan-out and integrate, run only verify_and_cleanup
    if from_verify:
        journal_file = Path(journal_path)
        if not journal_file.exists():
            print(f"{_ts()} ERROR: --from-verify requires an existing journal at {journal_path}")
            return {"group_prs": [], "final": None, "quarantined": {}, "merged": []}
        try:
            journal = json.loads(journal_file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"{_ts()} ERROR: Failed to read journal: {e}")
            return {"group_prs": [], "final": None, "quarantined": {}, "merged": []}

        spec_id, tasks = taskformats.load_spec(str(repo / spec_rel))
        for t in tasks:
            t["status"], t["retry_count"] = "done", 0

        # Reconstruct group_branch from journal's per-group integrate records
        journal_groups = journal.get("groups", {})
        group_branch = {
            name: rec.get("head_branch", f"{journal.get('run_id', 'unknown')}/{name}")
            for name, rec in journal_groups.items()
        }

        progress.set_phase(journal_path, "verify")
        print("=== VERIFY (from journal, skipping fan-out + integrate) ===")
        groups = coordinator.plan_groups(tasks)
        status = {t["id"]: t.get("status", "done") for t in tasks}
        delivered = {
            g["name"]: coordinator.deliverable_subset(g["tasks"], tasks, status)[0] for g in groups
        }
        resolve_spawn, ci_fix_spawn = _verifier_role_spawns(
            agent, model, timeout, role_agents, role_models
        )
        vres = verify.verify_and_cleanup(
            repo,
            remote,
            base,
            spec_id,
            groups,
            group_branch,
            delivered,
            spawn=resolve_spawn,
            ci_fix_spawn=ci_fix_spawn,
            merge_method=merge_method,
            # Without these the deny-list falls back to devkit's spec root,
            # leaving an OpenSpec run's own tree unguarded.
            spec_rel=spec_rel,
            declared_files=coordinator.declared_files_by_group(groups, tasks),
        )
        quarantined = vres.get("quarantined", {})
        self_merged = vres.get("self_merged", {})
        automerge_evidence = vres.get("automerge_evidence", {})
        progress.append_safety_net_events(
            journal_path,
            _safety_net_events_from_preflight_fallbacks(vres.get("preflight_fallbacks", {})),
        )

        if quarantined:
            print(
                f"NOTE: {len(quarantined)} group(s) quarantined for human review "
                f"(worktrees kept): {', '.join(quarantined)}"
            )
        if self_merged:
            print(
                f"!! SELF-MERGE VIOLATION: {len(self_merged)} group(s) merged by a worker "
                f"itself, not the orchestrator (worktrees kept): {', '.join(self_merged)}"
            )
        note = _format_automerge_evidence_note(automerge_evidence)
        if note:
            print(note)
        progress.set_phase(journal_path, "done")
        _print_usage_report(journal_path)
        print("=== FULL RUN COMPLETE ===")
        return {
            "group_prs": [
                (name, base, journal_groups.get(name, {}).get("pr_url")) for name in journal_groups
            ],
            "final": None,
            "quarantined": quarantined,
            "merged": vres["merged"],
            "automerge_armed": vres.get("automerge_armed", {}),
            "self_merged": self_merged,
            "automerge_evidence": automerge_evidence,
        }

    # --pipeline: route to the pipeline scheduler.
    if pipeline:
        run_id = read_or_create_run_id(Path(journal_path))
        return _pipeline_scheduler(
            re_integrate=re_integrate,
            repo=repo,
            spec_rel=spec_rel,
            remote=remote,
            base=base,
            agent=agent,
            model=model,
            max_workers=max_workers,
            timeout=timeout,
            resume=resume,
            only=only,
            role_models=role_models,
            role_agents=role_agents,
            fallback_agent=fallback_agent,
            tier_map=tier_map,
            fallback_chain=fallback_chain,
            run_budget=run_budget,
            journal_path=journal_path,
            run_id=run_id,
            smoke_cmd=smoke_cmd,
            bootstrap_cmd=bootstrap_cmd,
            merge_method=merge_method,
            pr_labels=pr_labels,
            pr_pacing_wait=pr_pacing_wait,
        )

    # Read or generate a stable run_id (persists in journal across invocations).
    run_id = read_or_create_run_id(Path(journal_path))

    # --fork-research: pre-load shared source files once; workers fork from that session.
    research_spawn = None
    if fork_research:
        spec_folder = repo / spec_rel
        sid = run_research_session(spec_folder, agent=agent, model=model, timeout=timeout)
        if sid:
            spec_id_tmp, _ = taskformats.load_spec(str(spec_folder))
            research_spawn = LiveSpawn(
                spec_id_tmp,
                spec_rel.rstrip("/") + "/",
                timeout=timeout,
                agent=agent,
                model=model,
                role_models=role_models,
                role_agents=role_agents,
                fallback_agent=fallback_agent,
                tier_map=tier_map,
                fallback_chain=fallback_chain,
            )
            research_spawn.research_session_id = sid

    res = live_run_real(
        repo,
        spec_rel,
        max_workers=max_workers,
        out_cassette=journal_path,
        only=only,
        with_tail=False,
        agent=agent,
        model=model,
        timeout=timeout,
        resume=resume,
        run_id=run_id,
        role_models=role_models,
        role_agents=role_agents,
        fallback_agent=fallback_agent,
        tier_map=tier_map,
        fallback_chain=fallback_chain,
        run_budget=run_budget,
        spawn=research_spawn,
        notify_cmd=notify_cmd,
        progress_interval=progress_interval,
        bootstrap_cmd=bootstrap_cmd,
    )
    spec_id, tasks = res["spec_id"], res["tasks"]
    for t in tasks:
        t.setdefault("retry_count", 0)
    # `_full_real_inner` dispatches the fan-out with with_tail=False (see the
    # live_run_real call above), so tail-kind tasks (e2e, cleanup) are never run
    # and must be excluded from the completeness check and the diagnostic list --
    # otherwise the run reports "FAN-OUT INCOMPLETE" forever and never integrates.
    if not _fanout_complete(tasks, with_tail=False):
        incomplete = _fanout_incomplete_detail(tasks, with_tail=False)
        print(
            f"{_ts()} FAN-OUT INCOMPLETE -- stopping before integrate: "
            f"{', '.join(incomplete['summary']) or 'unknown'}"
        )
        if incomplete["failed_tasks"]:
            print(
                f"{_ts()} Retry hint: remove the failed task entries from the cassette "
                f"and re-run to retry: {', '.join(t['id'] for t in incomplete['failed_tasks'])}"
            )
        progress.set_phase(
            journal_path,
            "fanout_failed",
            detail={
                "failed_tasks": incomplete["failed_tasks"],
                "blocked_tasks": incomplete["blocked_tasks"],
            },
        )
        if notify_cmd:
            done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
            _fire_notify(
                notify_cmd,
                {
                    "spec": spec_id,
                    "run_id": run_id,
                    "phase": "fanout_failed",
                    "tasks_done": done_ct,
                    "tasks_total": len(tasks),
                },
            )
        return {
            "group_prs": [],
            "final": None,
            "quarantined": {"fanout": "incomplete task terminal state"},
            "merged": [],
        }
    progress.set_phase(journal_path, "integrate")
    if notify_cmd:
        done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
        _fire_notify(
            notify_cmd,
            {
                "spec": spec_id,
                "run_id": run_id,
                "phase": "integrate",
                "tasks_done": done_ct,
                "tasks_total": len(tasks),
            },
        )

    # Check if integrate_complete is set and we can skip finish_real
    journal_file = Path(journal_path)
    journal = {}
    if journal_file.exists():
        try:
            journal = json.loads(journal_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    # --re-integrate: a prior pass set integrate_complete (and per-group records) but
    # the integration was wrong/incomplete. Clear both so the skip-path below is
    # bypassed and finish_real rebuilds the group branches/PRs (it stays reconcile-safe
    # against any branches/PRs that still exist on the remote). Persist the cleared
    # marker so a later resume doesn't see the stale integrate_complete again.
    if re_integrate and _clear_integration_state(journal):
        if journal_file.exists():
            progress.atomic_write_text(
                journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n"
            )
        print(f"{_ts()} RE-INTEGRATE: cleared integrate_complete + group records; rebuilding")

    integrate_complete = journal.get("integrate_complete", False)
    journal_groups = journal.get("groups", {})

    # Skip finish_real only when the journal shows EVERY group already integrated
    # (each has a pr_url). Otherwise call finish_real unconditionally: it is
    # reconcile-safe (reuses existing remote branches + OPEN PRs, skips MERGED,
    # creates only what's missing) and returns a COMPLETE group_branch map so the
    # verify stage covers every group -- including ones already integrated on a
    # prior pass. (Replaces a broken partial-re-entry that passed an unsupported
    # only_groups= kwarg to finish_real -> TypeError, and that also dropped the
    # already-complete groups out of group_branch so verify never merged them.)
    all_integrated = bool(
        integrate_complete
        and journal_groups
        and all(rec.get("pr_url") for rec in journal_groups.values())
    )
    if all_integrated:
        print("=== INTEGRATE (skipped; all groups already integrated in journal) ===")
        stranded = _stranded_after_integrate(tasks, journal_groups)
        if stranded:
            print(
                f"{_ts()} WARNING: integrate skipped (journal marks every recorded group "
                f"integrated), but these fanned-out task(s) belong to NO integrated group "
                f"and were delivered to no PR: {', '.join(stranded)}. Re-run with "
                f"--re-integrate to rebuild the group branches/PRs and deliver them."
            )
        group_branch = {
            name: rec.get("head_branch", f"{run_id}/{name}") for name, rec in journal_groups.items()
        }
        prs = [(name, base, rec.get("pr_url")) for name, rec in journal_groups.items()]
        quarantined = {}
    else:
        print("=== INTEGRATE ===")
        # Same role-aware assembly-resolve worker the pipeline path gets: without
        # it, any assembly-time merge conflict here quarantined the group
        # immediately (only the deterministic add/add init auto-resolve ran).
        _ar_agent, _ar_model = _role_agent_model(
            dispatch.ROLE_ASSEMBLY_RESOLVE, agent, model, role_agents, role_models
        )
        prs, group_branch, quarantined = integrate.finish_real(
            repo,
            spec_id,
            tasks,
            remote,
            run_id,
            base,
            cleanup=False,
            journal_path=journal_path,
            smoke_cmd=smoke_cmd,
            assembly_resolve_spawn=verify._make_live_spawn(_ar_model, timeout, agent=_ar_agent),
            pr_labels=pr_labels,
            pr_pacing_wait=pr_pacing_wait,
        )

    if not group_branch:
        print("No groups integrated cleanly -- nothing to assemble; see quarantine above.")
        progress.set_phase(journal_path, "done")
        _print_usage_report(journal_path)
        print("=== FULL RUN COMPLETE (quarantined only) ===")
        return {"group_prs": prs, "final": None, "quarantined": quarantined, "merged": []}

    # Verify each group PR in dependency order: ensure mergeable (resolve loop),
    # block on CI (ci-fix loop, 3-strikes), auto-merge on green, then tear down
    # the merged group's task worktrees + branches. Quarantined groups keep theirs.
    progress.set_phase(journal_path, "verify")
    if notify_cmd:
        done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
        _fire_notify(
            notify_cmd,
            {
                "spec": spec_id,
                "run_id": run_id,
                "phase": "verify",
                "tasks_done": done_ct,
                "tasks_total": len(tasks),
            },
        )
    print("=== VERIFY (mergeability + CI -> auto-merge -> gated cleanup) ===")
    groups = coordinator.plan_groups(tasks)
    # Which tasks each group actually delivered (defect B: a split group ships its
    # healthy subset; the dropped task's worktree must survive cleanup). Recompute
    # from the same deliverable_subset finish_real used -- single source of truth.
    status = {t["id"]: t.get("status", "done") for t in tasks}
    delivered = {
        g["name"]: coordinator.deliverable_subset(g["tasks"], tasks, status)[0] for g in groups
    }
    resolve_spawn, ci_fix_spawn = _verifier_role_spawns(
        agent, model, timeout, role_agents, role_models
    )
    vres = verify.verify_and_cleanup(
        repo,
        remote,
        base,
        spec_id,
        groups,
        group_branch,
        delivered,
        spawn=resolve_spawn,
        ci_fix_spawn=ci_fix_spawn,
        merge_method=merge_method,
        # Without these the deny-list falls back to devkit's spec root,
        # leaving an OpenSpec run's own tree unguarded.
        spec_rel=spec_rel,
        declared_files=coordinator.declared_files_by_group(groups, tasks),
    )
    quarantined = {**quarantined, **vres["quarantined"]}
    self_merged = vres.get("self_merged", {})
    automerge_evidence = vres.get("automerge_evidence", {})
    progress.append_safety_net_events(
        journal_path,
        _safety_net_events_from_preflight_fallbacks(vres.get("preflight_fallbacks", {})),
    )

    if quarantined:
        print(
            f"NOTE: {len(quarantined)} group(s) quarantined for human review "
            f"(worktrees kept): {', '.join(quarantined)}"
        )
    if self_merged:
        print(
            f"!! SELF-MERGE VIOLATION: {len(self_merged)} group(s) merged by a worker "
            f"itself, not the orchestrator (worktrees kept): {', '.join(self_merged)}"
        )
    note = _format_automerge_evidence_note(automerge_evidence)
    if note:
        print(note)
    progress.set_phase(journal_path, "done")
    if notify_cmd:
        done_ct = sum(1 for t in tasks if t.get("status") in coordinator.DONE)
        _fire_notify(
            notify_cmd,
            {
                "spec": spec_id,
                "run_id": run_id,
                "phase": "done",
                "tasks_done": done_ct,
                "tasks_total": len(tasks),
                "quarantined": list(quarantined.keys()),
                "merged": vres["merged"],
                "self_merged": list(self_merged.keys()),
                "automerge_evidence": automerge_evidence,
            },
        )
    _print_usage_report(journal_path)
    print("=== FULL RUN COMPLETE ===")
    return {
        "group_prs": prs,
        "final": None,
        "quarantined": quarantined,
        "merged": vres["merged"],
        "automerge_armed": vres.get("automerge_armed", {}),
        "self_merged": self_merged,
        "automerge_evidence": automerge_evidence,
    }


def smoke(agent: str = DEFAULT_AGENT, model: str | None = None) -> bool:
    model = model or spawnlib.default_model_for_agent(agent)
    result = spawnlib.spawn_agent(
        "Reply with exactly: PONG",
        Path.cwd(),
        agent=agent,
        model=model,
        timeout=120,
        retries=0,
    )
    out = result.text.strip()
    ok = "PONG" in out
    print(f"{agent} smoke -> {out!r}  [{'OK' if ok else 'UNEXPECTED'}]")
    return ok


def _parse_model_map(spec: str | None) -> dict | None:
    """Parse a `role=model,role=model` string into a {role: model} dict (#20)."""
    out: dict = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition("=")
        key, val = key.strip(), val.strip()
        if key and val:
            out[key] = val
    return out or None


def _parse_tier_map(spec: str | None) -> dict | None:
    """Parse `--tier-map`'s `complexity:domain=agent[:model],...` string into the
    `{(complexity, domain): {"agent_cli":.., "agent_model":..}}` shape
    dispatch.agent_for's tier_map expects (TASK-006).

    Fed in production by Phase 7's CLI-string round-trip of `policy.py`'s
    `resolve_tier_map(policy)` output (TASK-CHG-002) -- see `--tier-map`'s
    help text for the domain-less-tier caveat that string round-trip carries."""
    out: dict = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, val = part.partition("=")
        key, val = key.strip(), val.strip()
        if not key or not val:
            continue
        complexity, _, domain = key.partition(":")
        agent_cli, _, agent_model = val.partition(":")
        out[(complexity.strip(), domain.strip())] = {
            "agent_cli": agent_cli.strip(),
            "agent_model": agent_model.strip() or None,
        }
    return out or None


def _parse_fallback_chain(spec: str | None) -> "list[str] | None":
    """Parse `--fallback-chain`'s ordered comma-separated agent list."""
    chain = [a.strip() for a in (spec or "").split(",") if a.strip()]
    return chain or None


def _role_agent_model(
    role: str,
    agent: str,
    model: str,
    role_agents: dict | None,
    role_models: dict | None,
) -> tuple[str, str]:
    """Resolve the (agent, model) pair for a role the same way LiveSpawn.__call__
    does: --role-agent-map wins for the agent; an explicit --model-map entry wins
    for the model; a role pinned to a different agent than the run's default falls
    back to that agent's OWN default model, not the run's (which would be an
    invalid model id for the other CLI)."""
    role_agent = (role_agents or {}).get(role, agent)
    role_model = (role_models or {}).get(
        role,
        model if role_agent == agent else spawnlib.default_model_for_agent(role_agent),
    )
    return role_agent, role_model


def _verifier_role_spawns(
    agent: str,
    model: str,
    timeout: int,
    role_agents: dict | None,
    role_models: dict | None,
) -> tuple:
    """Build the (resolve, ci-fix) spawn pair for a Verifier, honoring
    --role-agent-map / --model-map for the group-level verify roles. Unmapped
    roles keep the pipeline path's historical defaults: resolve on the run
    agent/model with the worker timeout; ci-fix on its role-model default
    (--model-map ci-fix=..., else verify.DEFAULT_MODEL) with the shorter
    CI_FIX_TIMEOUT."""
    from . import verify

    rs_agent, rs_model = _role_agent_model(
        dispatch.ROLE_RESOLVE, agent, model, role_agents, role_models
    )
    cf_agent, cf_model = _role_agent_model(
        dispatch.ROLE_CI_FIX, agent, verify.DEFAULT_MODEL, role_agents, role_models
    )
    return (
        verify._make_live_spawn(rs_model, timeout, agent=rs_agent),
        verify._make_live_spawn(cf_model, verify.CI_FIX_TIMEOUT, agent=cf_agent),
    )


def _effective_role_models(agent: str, role_models: dict | None) -> dict | None:
    if role_models is not None:
        return role_models
    if agent == "codex":
        return dict(CODEX_DEFAULT_ROLE_MODELS)
    return None


def main(argv=None) -> int:
    # full-real runs in the background with stdout redirected to a log file. A
    # redirected (non-TTY) stdout is block-buffered by default, so progress
    # prints would not reach the log until ~4-8KB accumulated or the process
    # exited -- making the log look empty (0 lines) while the run is healthy.
    # Force line buffering so every print is observable in the polled log.
    try:
        getattr(sys.stdout, "reconfigure")(line_buffering=True)
        getattr(sys.stderr, "reconfigure")(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(description="Live spawn via a headless agent CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("smoke")
    sm.add_argument("--agent", choices=sorted(spawnlib.SUPPORTED_AGENTS), default=DEFAULT_AGENT)
    sm.add_argument("--model", default=None)
    ip = sub.add_parser("instantiate")
    ip.add_argument("--dest", default=str(DEFAULT_DEST))
    so = sub.add_parser("spawn-one")
    so.add_argument("--task", required=True)
    so.add_argument("--role", default="implement", choices=list(dispatch.ROLES))
    so.add_argument("--dest", default=str(DEFAULT_DEST))
    so.add_argument("--keep", action="store_true")
    lr = sub.add_parser("live-run")
    lr.add_argument("--dest", default=str(DEFAULT_DEST))
    lr.add_argument(
        "--out", default=str(_SKILL / ".fixtures" / "sample-spec" / "cassette.live.json")
    )
    lr.add_argument("--max-workers", type=int, default=2)
    lr.add_argument("--only", default=None, help="comma-separated task IDs subset")
    lr.add_argument("--with-tail", action="store_true")
    lr.add_argument("--agent", choices=sorted(spawnlib.SUPPORTED_AGENTS), default=DEFAULT_AGENT)
    lr.add_argument("--model", default=None)
    fu = sub.add_parser("full", help="End-to-end: fan-out -> integrate -> grouped PRs -> tail")
    fu.add_argument("--agent", choices=sorted(spawnlib.SUPPORTED_AGENTS), default=DEFAULT_AGENT)
    fu.add_argument("--model", default=None)
    fu.add_argument("--sandbox", default="behindthedash/orch-sandbox")
    fu.add_argument("--keep-prs", action="store_true")
    fu.add_argument("--max-workers", type=int, default=3)
    lrr = sub.add_parser("live-run-real", help="Live fan-out on a real repo (no copy)")
    lrr.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    lrr.add_argument(
        "--spec",
        required=True,
        help="Spec folder relative to repo root, e.g. docs/specs/008-image-route",
    )
    lrr.add_argument("--out", default=None, help="Path to write cassette JSON")
    lrr.add_argument("--max-workers", type=int, default=2)
    lrr.add_argument("--only", default=None, help="Comma-separated task IDs subset")
    lrr.add_argument("--with-tail", action="store_true")
    lrr.add_argument("--agent", choices=sorted(spawnlib.SUPPORTED_AGENTS), default=DEFAULT_AGENT)
    lrr.add_argument("--model", default=None)
    lrr.add_argument(
        "--timeout",
        type=int,
        default=WORKER_TIMEOUT_DEFAULT,
        help="Per-worker claude -p timeout in seconds " "(default: $ORCH_WORKER_TIMEOUT or 1800)",
    )
    lrr.add_argument(
        "--resume",
        action="store_true",
        help="Replay an existing --out journal to skip completed "
        "tasks and continue an interrupted fan-out",
    )
    lrr.add_argument(
        "--fork-research",
        action="store_true",
        dest="fork_research",
        default=False,
        help="Pre-load shared source files in a single research session and fork each "
        "worker from it, reducing cache-miss token cost for overlapping file reads (opt-in).",
    )
    stt = sub.add_parser(
        "status",
        help="Render a live checklist (per-step elapsed time) for a run journal",
    )
    stt.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    stt.add_argument(
        "--spec", required=True, help="Spec folder relative to repo root, e.g. docs/specs/008-foo"
    )
    usg = sub.add_parser(
        "usage",
        help="Report per-role token + cost totals from a run journal (where the tokens went)",
    )
    usg.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    usg.add_argument(
        "--spec", required=True, help="Spec folder relative to repo root, e.g. docs/specs/008-foo"
    )
    fr = sub.add_parser(
        "full-real", help="End-to-end on a real repo: fan-out -> integrate -> grouped PRs"
    )
    fr.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    fr.add_argument(
        "--spec",
        required=True,
        help="Spec folder relative to repo root, e.g. docs/specs/008-image-route",
    )
    fr.add_argument(
        "--remote", default="origin", help="Git remote to push branches and open PRs against"
    )
    fr.add_argument("--base", default="dev", help="Base branch for PRs (default: dev)")
    fr.add_argument("--agent", choices=sorted(spawnlib.SUPPORTED_AGENTS), default=DEFAULT_AGENT)
    fr.add_argument(
        "--fallback-agent",
        choices=sorted(spawnlib.SUPPORTED_AGENTS),
        default=None,
        help="Switch once to this worker CLI when the primary reports a session limit. "
        "Covers every --agent choice, including claude (spawn_claude_p threads this "
        "through to spawn_agent's fallback machinery).",
    )
    fr.add_argument("--model", default=None)
    fr.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Fan-out width: concurrent task worktrees with live agent workers. "
        "Sourced from go-policy.yaml's max_workers by the sdd-workflow conductor "
        "when that key is set; an explicit invocation value wins.",
    )
    fr.add_argument(
        "--timeout",
        type=int,
        default=WORKER_TIMEOUT_DEFAULT,
        help="Per-worker claude -p timeout in seconds " "(default: $ORCH_WORKER_TIMEOUT or 1800)",
    )
    fr.add_argument(
        "--fresh",
        action="store_true",
        help="Discard any existing run journal and start over "
        "(default: resume from the journal beside the worktrees)",
    )
    fr.add_argument(
        "--from-verify",
        action="store_true",
        dest="from_verify",
        help="Skip fan-out and integrate; run only verify_and_cleanup "
        "against journal's per-group integrate records",
    )
    fr.add_argument(
        "--only",
        default=None,
        help="Comma-separated task IDs to run; all others are pre-marked done.",
    )
    fr.add_argument(
        "--model-map",
        default=None,
        help="Per-role model overrides, e.g. 'implement=sonnet,review=haiku,fix=sonnet,ci-fix=sonnet'. "
        "Roles not listed fall back to --model (ci-fix defaults to sonnet regardless).",
    )
    fr.add_argument(
        "--role-agent-map",
        default=None,
        help="Per-role agent CLI overrides, e.g. 'review=claude,implement=opencode,fix=opencode'. "
        "Roles not listed fall back to --agent. Lets the reviewer run on a genuinely "
        "different/independent headless CLI (its own subscription/model) than the one "
        "writing the code -- e.g. review on claude/sonnet while implement/fix stay on "
        "a cheaper opencode model. Covered roles: implement, review, fix, cleanup "
        "(task-level workers), assembly-resolve (merge-conflict resolution during "
        "group integration), and the group-level verify workers resolve and ci-fix "
        "(pipeline and non-pipeline paths alike).",
    )
    fr.add_argument(
        "--tier-map",
        default=None,
        help="Resolved routing-table tier matches, e.g. "
        "'hard:backend=codex:gpt-tier,standard:frontend=opencode'. Each entry is "
        "'complexity:domain=agent[:model]'; matched tasks route implement/fix/cleanup "
        "spawns to that agent (its own default model unless one is given here). "
        "Never consulted for review/resolve/ci-fix/assembly-resolve (DEC-003). "
        "Sourced from go-policy.yaml's routing.tiers by the sdd-workflow conductor "
        "(policy.py's resolve_tier_map()); a domain-less tier (resolve_tier_map()'s "
        "(complexity, None) key) has no CLI-string representation -- _parse_tier_map() "
        "always yields an empty-string domain, never None -- so domain-less tiers only "
        "reach dispatch.agent_for via a native tier_map dict, not this flag (TASK-CHG-002). "
        "Omit for pre-spec behavior (REQ-016).",
    )
    fr.add_argument(
        "--fallback-chain",
        default=None,
        dest="fallback_chain",
        help="Ordered comma-separated fallback agent chain, e.g. 'codex,opencode'; "
        "walked in order when the primary agent is capacity-gated (REQ-018). Wins "
        "over --fallback-agent when both are given; omit to keep the legacy "
        "single-fallback behavior.",
    )
    fr.add_argument(
        "--run-budget",
        type=float,
        default=RUN_BUDGET_DEFAULT_MINUTES,
        help="Whole-run wall-clock cap in MINUTES (not seconds); the fan-out stops "
        "dispatching NEW tasks once exceeded (0 = off; default $ORCH_RUN_BUDGET "
        "minutes, or 0).",
    )
    fr.add_argument(
        "--pipeline",
        action="store_true",
        default=False,
        help="Opt-in pipelined run: integrate and verify each group as its tasks finish, "
        "overlapping with continued fan-out of later groups (default: sequential).",
    )
    fr.add_argument(
        "--re-integrate",
        action="store_true",
        dest="re_integrate",
        help="Force re-integration: clear the journal's integrate_complete marker and "
        "per-group records so finish_real runs again (reconcile-safe). Use after a failed "
        "integration instead of hand-editing the journal JSON.",
    )
    fr.add_argument(
        "--smoke-cmd",
        default=None,
        dest="smoke_cmd",
        help="Shell command run on each group's integration branch before its PR opens "
        "(e.g. 'pytest -q' or 'cd app && npm ci && npm test'); a non-zero exit quarantines "
        "the group. Sourced from go-policy.yaml's integrate_smoke_cmd by the sdd-workflow conductor; "
        "omit to skip (repos without a wired command are never blocked).",
    )
    fr.add_argument(
        "--bootstrap-cmd",
        default=None,
        dest="bootstrap_cmd",
        help="Shell command run in each freshly-created task worktree right after it is "
        "created, before a worker is spawned into it, to install local dependencies "
        "(e.g. 'npm ci' or 'cd app && npm ci'). Task worktrees branch off the base commit "
        "and start without the base checkout's node_modules. Sourced from go-policy.yaml's "
        "worktree_bootstrap_cmd by the sdd-workflow conductor; omit to skip. Non-fatal: a "
        "failed install is logged and the worker still self-installs.",
    )
    fr.add_argument(
        "--pr-pacing-wait",
        type=int,
        default=0,
        dest="pr_pacing_wait",
        help="Seconds to wait (bounded) for the previous group PR's checks to resolve "
        "before opening the next group's PR, so sibling PRs don't hit a shared CI "
        "runner pool simultaneously (0 = off, the default). Best-effort: a red, "
        "stuck, or check-less PR never blocks integration beyond this bound. Paces "
        "both the sequential integrate path and --pipeline (where it serializes "
        "only the integrate+PR-open step; verify still overlaps). Sourced from "
        "go-policy.yaml's pr_pacing_wait_s by the sdd-workflow conductor.",
    )
    fr.add_argument(
        "--merge-method",
        default=None,
        dest="merge_method",
        choices=("merge", "squash", "rebase"),
        help="Merge method for auto_merge() to use for THIS base branch, overriding "
        "verify.py's repo-wide GitHub-settings detection. Sourced from go-policy.yaml's "
        "merge_method_by_base by the sdd-workflow conductor (policy.py "
        "--merge-method-for-branch); omit to keep repo-wide auto-detection.",
    )
    fr.add_argument(
        "--pr-label",
        action="append",
        dest="pr_labels",
        default=None,
        help="Exact PR label resolved by the GO pre-PR gate; may be repeated.",
    )
    fr.add_argument(
        "--fork-research",
        action="store_true",
        dest="fork_research",
        default=False,
        help="Pre-load shared source files in a single research session and fork each "
        "worker from it, reducing cache-miss token cost for overlapping file reads (opt-in).",
    )
    fr.add_argument(
        "--notify-cmd",
        default=None,
        dest="notify_cmd",
        help="Shell command invoked on each task-state transition and phase boundary "
        "(integrate, verify, done). JSON payload is written to its stdin: "
        "{spec, run_id, phase, tasks_done, tasks_total, current_task, role, "
        "old_status, new_status}. Best-effort: errors are silently ignored and "
        "never block a run. Omit to disable.",
    )
    fr.add_argument(
        "--progress-interval",
        type=int,
        default=None,
        dest="progress_interval",
        metavar="N",
        help="Emit a compact one-line progress summary to stdout every N seconds "
        "(e.g. '[HH:MM:SS] PROGRESS: 4/7 done · TASK-005 · 12m elapsed'), "
        "independent of transition cadence. Useful when a single step runs long. "
        "Omit to disable.",
    )
    pc = sub.add_parser(
        "precheck",
        help="Check whether pending impl tasks have their declared files already present",
    )
    pc.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    pc.add_argument("spec", help="Spec folder relative to repo root, e.g. docs/specs/008-foo")
    sk = sub.add_parser(
        "skip",
        help="Mark stuck task(s) escalated in the run journal so the next full-real "
        "resume stops waiting on them and proceeds to integrate the rest",
    )
    sk.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    sk.add_argument(
        "--spec", required=True, help="Spec folder relative to repo root, e.g. docs/specs/008-foo"
    )
    sk.add_argument("--tasks", required=True, help="Comma-separated task IDs to skip")
    sk.add_argument("--reason", default="manually skipped", help="Note recorded on each skip")

    args = p.parse_args(argv)
    if getattr(args, "model", None) is None:
        args.model = spawnlib.default_model_for_agent(getattr(args, "agent", DEFAULT_AGENT))
    role_models = _effective_role_models(
        getattr(args, "agent", DEFAULT_AGENT), _parse_model_map(getattr(args, "model_map", None))
    )
    role_agents = _parse_model_map(getattr(args, "role_agent_map", None))
    tier_map = _parse_tier_map(getattr(args, "tier_map", None))
    fallback_chain = _parse_fallback_chain(getattr(args, "fallback_chain", None))
    if args.cmd == "smoke":
        return 0 if smoke(args.agent, args.model) else 1
    if args.cmd == "instantiate":
        print(instantiate(SAMPLE_TEMPLATE, Path(args.dest)))
        return 0
    if args.cmd == "spawn-one":
        spawn_one(args.task, args.role, Path(args.dest), keep=args.keep)
        return 0
    if args.cmd == "live-run":
        only = args.only.split(",") if args.only else None
        live_run(
            Path(args.dest),
            args.max_workers,
            args.out,
            only,
            args.with_tail,
            args.agent,
            args.model,
        )
        return 0
    if args.cmd == "full":
        full(args.agent, args.model, args.sandbox, args.keep_prs, args.max_workers)
        return 0
    if args.cmd == "live-run-real":
        only = args.only.split(",") if args.only else None
        spawn = None
        if getattr(args, "fork_research", False):
            spec_folder = Path(args.repo) / args.spec
            sid = run_research_session(
                spec_folder, agent=args.agent, model=args.model, timeout=args.timeout
            )
            if sid:
                spec_id_tmp, _ = taskformats.load_spec(str(spec_folder))
                spawn = LiveSpawn(
                    spec_id_tmp,
                    args.spec.rstrip("/") + "/",
                    timeout=args.timeout,
                    agent=args.agent,
                    model=args.model,
                )
                spawn.research_session_id = sid
        live_run_real(
            Path(args.repo),
            args.spec,
            max_workers=args.max_workers,
            out_cassette=args.out,
            only=only,
            with_tail=args.with_tail,
            agent=args.agent,
            model=args.model,
            timeout=args.timeout,
            resume=args.resume,
            spawn=spawn,
        )
        return 0
    if args.cmd == "status":
        repo = Path(args.repo).resolve()
        jp = journal_path_for(repo, args.spec)
        if not jp.exists():
            print(f"no run journal at {jp} -- has a run started for this spec?")
            return 1
        # Reconcile the spec's tasks against the journal so pending/not-yet-started
        # tasks also appear, and each task's marker reflects its current status.
        tasks: list = []
        try:
            _, tasks = taskformats.load_spec(str(repo / args.spec))
            for t in tasks:
                t["retry_count"] = 0
            journal = json.loads(jp.read_text())
            reconcile_from_journal(tasks, journal)
        except Exception as e:  # render is still useful from the journal alone
            print(f"(note: could not reconcile spec tasks: {e})")
            tasks = []
        sys.stdout.write(progress.render(jp, tasks=tasks or None))
        return 0
    if args.cmd == "usage":
        repo = Path(args.repo).resolve()
        jp = journal_path_for(repo, args.spec)
        if not jp.exists():
            print(f"no run journal at {jp} -- has a run started for this spec?")
            return 1
        try:
            journal = json.loads(jp.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"could not read journal {jp}: {e}")
            return 1
        print(progress.render_usage(journal))
        print(progress.render_tools_used(journal))
        print(progress.render_context_quality(journal))
        return 0
    if args.cmd == "full-real":
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        full_real(
            args.repo,
            args.spec,
            args.remote,
            args.base,
            args.agent,
            args.model,
            args.max_workers,
            args.timeout,
            resume=not args.fresh,
            from_verify=args.from_verify,
            only=only,
            role_models=role_models,
            role_agents=role_agents,
            fallback_agent=args.fallback_agent,
            tier_map=tier_map,
            fallback_chain=fallback_chain,
            run_budget=args.run_budget * 60 if args.run_budget else args.run_budget,
            pipeline=args.pipeline,
            re_integrate=args.re_integrate,
            smoke_cmd=args.smoke_cmd,
            bootstrap_cmd=args.bootstrap_cmd,
            fork_research=args.fork_research,
            notify_cmd=getattr(args, "notify_cmd", None),
            progress_interval=getattr(args, "progress_interval", None),
            merge_method=args.merge_method,
            pr_labels=args.pr_labels,
            pr_pacing_wait=args.pr_pacing_wait,
        )
        return 0
    if args.cmd == "precheck":
        return precheck(Path(args.repo).resolve(), args.spec)
    if args.cmd == "skip":
        task_ids = [s.strip() for s in args.tasks.split(",") if s.strip()]
        if not task_ids:
            print("skip: --tasks must list at least one task ID")
            return 1
        return skip_tasks(Path(args.repo).resolve(), args.spec, task_ids, args.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())

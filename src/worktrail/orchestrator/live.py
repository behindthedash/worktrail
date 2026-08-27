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
import copy
import inspect
import json
import os
import re
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
from ..router import invocation_context
from ..taskformats import resolve as taskformats


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
    """Resolve the default worker CLI via invocation_context.resolve() -- the
    single implementation of the provider precedence chain (GO_AGENT_CLI >
    ORCH_AGENT > OPENCODE_PARENT > CODEX_CI/CODEX_THREAD_ID > claude) -- so
    this module can no longer drift from the front door's resolver (the same
    drift class PR #338/#348 eliminated for invocation_context.py/go_seed.py).

    An unsupported provider name in GO_AGENT_CLI/ORCH_AGENT now surfaces a
    warning and falls back to "claude" instead of leaking into DEFAULT_AGENT
    unvalidated. DEFAULT_AGENT is resolved at import time (see below) and used
    as the default for many function signatures in this module, so letting
    resolve()'s ValueError propagate would crash every live.py invocation over
    one bad env var -- a worse regression than the unvalidated value this
    replaces.
    """
    try:
        return invocation_context.resolve().agent_cli
    except ValueError as exc:
        print(f"warning: {exc}; falling back to 'claude'", file=sys.stderr)
        return "claude"


def _default_smoke_cmd(repo: Path) -> "str | None":
    """Auto-resolve the pre-PR gate command from policy when `--smoke-cmd` was
    not passed explicitly.

    `--smoke-cmd` is opt-in and easy for the calling agent to forget -- it
    happened in the same session this fix was authored in, on a repo (this
    one) that has `pre_pr_cmd` configured the whole time. `gh pr create` in
    `integrate.py` is a raw subprocess call; it is never reachable by a
    Claude Code hook (headless or interactive), so the only place this can be
    enforced is here, in code, before a group PR ever opens.

    Mirrors `pre_pr_gate.resolve_cmd()`'s own precedence (`pre_pr_cmd`, then
    `integrate_smoke_cmd`) and its `pre_pr_cmd: skip` opt-out -- but does NOT
    mirror its default-deny-when-unconfigured behavior: a repo with neither
    key set resolves to None here too, identical to the pre-existing "omit
    `--smoke-cmd` to skip" contract. Failing closed on every unconfigured
    repo would be more consistent with the interactive gate, but it changes
    behavior for every repo and test fixture that has never configured
    either key -- out of scope for this fix, which closes the "forgot to
    pass a flag on an already-configured repo" gap, not the separate
    "repos with no gate configured at all" gap.
    """
    from ..router.policy import load_policy
    from ..router.pre_pr_gate import SKIP_VALUES, resolve_cmd

    cmd = resolve_cmd(load_policy(repo))
    if cmd is None or cmd.lower() in SKIP_VALUES:
        return None
    return cmd


def _default_post_merge_smoke_cmd(repo: Path) -> "str | None":
    """Auto-resolve verify.py's cumulative post-merge gate command from policy
    when `--post-merge-smoke-cmd` was not passed explicitly.

    Mirrors `_default_smoke_cmd` above: `post_merge_smoke_cmd` wins,
    `integrate_smoke_cmd` is the fallback (policy.resolve_post_merge_smoke_cmd),
    a repo with neither key set resolves to None (gate skipped, no behavior
    change), and explicit `--post-merge-smoke-cmd` always overrides this.
    """
    from ..router.policy import load_policy, resolve_post_merge_smoke_cmd

    return resolve_post_merge_smoke_cmd(load_policy(repo))


# DEFAULT_AGENT is resolved from the launching host via _detect_default_agent()
# so that Claude Code, Codex, and OpenCode each use their own headless CLI
# without an invocation-wide env var or a per-call --agent flag. Explicit
# --agent, policy agent_cli, and GO_AGENT_CLI env var all override this.
DEFAULT_AGENT = _detect_default_agent()

# Every codex role defaults to the SAME model (spawnlib.default_model_for_agent
# resolved fresh per call, not a frozen snapshot -- a stale frozen copy of
# spawnlib's default here is exactly the staleness bug spawnlib.py itself was
# just fixed for; see _effective_role_models below).
_CODEX_DEFAULT_ROLES = ("implement", "review", "fix", "cleanup", "ci-fix")

# Reviewer independence (locked decision 13.3): the headless review worker is the
# same binary as the implementer, so we enforce independence with an appended
# system prompt (a real behavioural change) rather than only a prompt line. Kept
# to the review role so implement/fix/cleanup keep the DEFAULT system prompt and
# its prompt-cache reuse across the many cold workers of a run.
_REVIEWER_SYSTEM_PROMPT = (
    "You are an INDEPENDENT code reviewer. You did NOT write this code. Be "
    "skeptical: verify the diff against the task's Acceptance Criteria, look for "
    "bugs, missing tests, and scope drift, and do not rubber-stamp. Do not modify "
    "source. If the diff takes a different approach than the task literally "
    "describes, that is a FAILED-worthy finding on its own -- a plausible "
    "justification for the deviation does not substitute for flagging it."
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


class WorktreeStackConflictError(WorktreeAddError):
    """A sibling dependency branch could not be merged into a stacked worktree
    (add/add or content conflict) -- the worktree would silently be missing
    that dependency's commits if the run continued on it."""


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


def apply_run_plan(
    repo: "Path", spec_rel: str, spec_id: str, tasks: list, *, spawn=None
) -> list:
    """Enrich freshly-loaded tasks with a RunPlan, reusing this run's pinned plan
    (or compiling and pinning one, if this is the run's first compile).

    A run pins its plan the first time `_record_plan_fingerprint` stamps
    `plan_fingerprint` into the journal; every later call for the same
    (repo, spec) -- a resume, or a later phase of the same run -- resolves
    that pin from the cache instead of recompiling, so the plan a run
    executes under can no longer drift mid-run the way it did in
    full-1786812908 (see `_record_plan_fingerprint`). A pin that no longer
    resolves (its cache entry deleted) fails the run rather than silently
    recompiling a possibly-different plan out from under in-progress work;
    the error names the re-plan escape hatch (clear `plan_fingerprint` from
    the journal) for a deliberate re-plan. The same posture applies when the
    pin *does* resolve but its task set no longer matches the tasks just
    read from the artifact (e.g. `tasks.md` was hand-edited between phases
    or across a resume): `runplan.apply_to_tasks()` would otherwise reject
    the mismatched plan and silently fall back to each task's own baseline
    deps/file-scope for the *whole* run -- exactly the "format's own
    deps/files" fallback DEC-003 of `run-scoped-plan-pinning` rejected as an
    alternative, because it changes group membership just as much as a
    recompile while also hiding that it happened. For OpenSpec tasks (no
    native file scope) that fallback starves every task of both a file scope
    and a dependency edge, which `validate_task_metadata` then refuses to
    fan out with an unrelated-looking "missing required frontmatter files"
    error several frames later. This function catches the mismatch itself
    and fails the same way as an unresolvable pin, before ever calling
    `apply_to_tasks`.

    When no pin is present yet, `compile_run_plan` pays for a model call only
    when it has to: a cache hit is free, and a format that already declares
    file scope for every task (devkit frontmatter) takes the free seed path --
    no model call, same as before this changed. Only a format that carries no
    per-task file scope at all (OpenSpec's `tasks.md`) triggers a real
    compile, and only on the first run of that exact content version; every
    subsequent run/resume hits the pin (or, failing that, the cache) this
    call just populated. That one-time cost is exactly what running
    `worktrail-compile` by hand beforehand would have paid -- this makes it
    automatic instead of a required, easy-to-forget separate step (see
    `docs/design/history/` P3b and the incident that motivated this: a first
    `full-real` launch against a fresh OpenSpec change used to hard-fail with
    `RuntimeError: implementation task(s) missing required frontmatter files`
    unless the operator remembered to compile first).

    `spawn` is the same injectable compile seam `compile_run_plan` exposes,
    threaded through for tests; production callers never pass it (falls back to
    the real headless-agent spawn).
    """
    from ..conductor import compile as conductor_compile
    from ..conductor import runplan as _runplan

    pinned = _pinned_plan_fingerprint(repo, spec_rel)
    if pinned is not None:
        plan = _runplan.load_cached(conductor_compile.default_cache_dir(repo), spec_id, pinned)
        if plan is None:
            jp = journal_path_for(repo, spec_rel)
            raise RuntimeError(
                f"run plan: pinned plan {pinned[:12]} for {spec_id} is no longer cached "
                "and cannot be resolved; refusing to recompile a possibly-different plan "
                f"mid-run. To deliberately re-plan, clear plan_fingerprint from {jp}."
            )
        current_ids = {t["id"] for t in tasks}
        planned_ids = set(plan.by_id())
        if current_ids != planned_ids:
            jp = journal_path_for(repo, spec_rel)
            missing = sorted(current_ids - planned_ids)
            extra = sorted(planned_ids - current_ids)
            raise RuntimeError(
                f"run plan: pinned plan {pinned[:12]} for {spec_id} no longer matches "
                f"the current task set (missing={missing or '-'}, unknown={extra or '-'}); "
                "refusing to silently fall back to unscoped deps mid-run. To deliberately "
                f"re-plan, clear plan_fingerprint from {jp}."
            )
        print(f"{_ts()} run plan: reusing pinned plan {pinned[:12]}")
    else:
        try:
            spec_dir = repo / spec_rel
            plan = conductor_compile.compile_run_plan(
                spec_dir,
                tasks,
                spec_id=spec_id,
                repo=repo,
                spawn=spawn,
                log=lambda m: print(f"{_ts()} {m}"),
            )
        except OSError as exc:  # an unreadable/unwritable cache must never take a run down
            print(f"{_ts()} run plan: cache unreadable ({exc}); using the spec's own deps")
            return tasks

    merged, notes = _runplan.apply_to_tasks(tasks, plan)
    for n in notes:
        print(f"{_ts()} {n}")
    _record_plan_fingerprint(repo, spec_rel, plan)
    return merged


def _pinned_plan_fingerprint(repo: "Path", spec_rel: str) -> "str | None":
    """This run's pinned plan fingerprint, if `_record_plan_fingerprint` has
    already stamped one into the journal -- None on a fresh run, or on any
    journal I/O failure (DEC-004: journal I/O never takes a run down; same
    best-effort contract `_record_plan_fingerprint` already applies to its own
    journal read/write)."""
    try:
        jp = journal_path_for(repo, spec_rel)
        journal = json.loads(jp.read_text()) if jp.exists() else {}
        if not isinstance(journal, dict):
            return None
        fp = journal.get("plan_fingerprint")
        return fp if isinstance(fp, str) and fp else None
    except (OSError, ValueError, TypeError):
        return None


PLAN_PIN_KEYS = ("plan_fingerprint", "plan_fingerprints")


def _preserve_plan_pin(path: "str | Path", jdict: dict) -> dict:
    """Carry the run's plan pin across a wholesale journal rewrite.

    Both schedulers' `record()` build their journal dict from scratch (spec_id,
    entries, groups, ...) and write it with `atomic_write_text`, so anything
    written to the journal by a *different* writer is destroyed on the next
    write. `_record_plan_fingerprint` is exactly such a writer: it does its own
    read-modify-write of `plan_fingerprint`/`plan_fingerprints`.

    Without this, the pin never survived to be read back. Observed directly in
    run full-1786825958, whose journal ended with keys
    `[entries, gitnexus_capability, groups, run_id, spec_id,
    unreconciled_tail_evidence]` -- precisely `_record()`'s own set -- despite
    `_record_plan_fingerprint` having logged `fingerprint=214b79dd6933` three
    times during that run. That silently disabled BOTH the PLAN DRIFT warning
    (the list was wiped between phases, so a second distinct fingerprint never
    saw the first) and `apply_run_plan`'s run-scoped pin (`plan_fingerprint`
    was always absent, so every phase recompiled).

    Only the pin keys are carried over -- this is deliberately not a general
    merge of the on-disk journal, which would resurrect stale state the
    rebuilding writer intends to drop.
    """
    try:
        existing = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return jdict
    if not isinstance(existing, dict):
        return jdict
    for key in PLAN_PIN_KEYS:
        if key in existing and key not in jdict:
            jdict[key] = existing[key]
    return jdict


def _record_plan_fingerprint(repo: "Path", spec_rel: str, plan) -> None:
    """Stamp which compiled RunPlan this run is executing, into the journal.

    Group membership is derived from each task's `deps`/`files`
    (`coordinator.plan_groups`), so two compiles of the same change that infer
    different values produce different groups for what is nominally the same
    run. That happened in run full-1786812908: two plans 108s apart disagreed on
    `deps`, `files`, and `kind`, giving `base = [1.1, 1.2, 1.3, 1.4, 2.1, ...]`
    under one and `base = [1.1, 1.2, 1.3]` under the other.

    Nothing recorded which plan any phase actually used, so the drift was only
    reconstructable by noticing two files in `runplans/` after the fact. Stamping
    the fingerprint makes it a first-class journal fact instead -- and, since
    `apply_run_plan` now reads that same stamp back as this run's pin
    (`_pinned_plan_fingerprint`) before ever recompiling, the drift this
    docstring describes can no longer happen for a single run's phases;
    `plan_fingerprints` growing past one entry (and the PLAN DRIFT log below)
    is retained purely as defense-in-depth against a future caller that
    bypasses the pin. Best-effort: observability must never take a run down.
    """
    fp = getattr(plan, "fingerprint", None)
    if not fp:
        return
    print(f"{_ts()} run plan: fingerprint={fp[:12]} source={getattr(plan, 'source', '?')}")
    try:
        jp = journal_path_for(repo, spec_rel)
        journal = json.loads(jp.read_text()) if jp.exists() else {}
        seen = journal.get("plan_fingerprints") or []
        if fp not in seen:
            seen.append(fp)
        journal["plan_fingerprints"] = seen
        journal["plan_fingerprint"] = fp
        if len(seen) > 1:
            print(
                f"{_ts()}   !! PLAN DRIFT: this spec has compiled to "
                f"{len(seen)} distinct run plans in this journal "
                f"({', '.join(f[:12] for f in seen)}) -- group membership may "
                f"differ between phases; see brief 20260815-115257"
            )
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(journal, indent=2))
    except Exception as exc:  # best-effort: observability never takes a run down
        print(f"{_ts()} run plan: could not record fingerprint ({exc})")


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


def _format_unreconciled_tail_note(findings: "list[dict]") -> "str | None":
    """Human-readable warning for `integrate.detect_unreconciled_tail_evidence`'s
    findings -- terminal tail-kind (e2e/cleanup) tasks whose own commits never
    got merged onto base, so the run must not report unqualified success.
    Returns None for empty findings so callers can `if note:`.

    When findings carry `reconcile_state`/`reconcile_pr_url` (i.e. they went
    through `integrate.reconcile_unreconciled_tail_evidence`), each entry is
    annotated with that outcome so the console log doesn't read as still
    purely manual -- the fuller per-state wording lives in
    `journal_selfcheck.py`'s dashboard finding, not here.
    """
    if not findings:
        return None

    def _entry(f: dict) -> str:
        state = f.get("reconcile_state")
        suffix = f" reconcile={state}" if state else ""
        pr_url = f.get("reconcile_pr_url")
        if pr_url and state in ("opened", "already-open"):
            suffix += f" {pr_url}"
        elif state == "superseded":
            suffix += f" by {f.get('reconcile_superseded_by', '?')}"
        return f"{f['task']} (sha {f['head_sha']} @ {f['worktree']}{suffix})"

    return (
        f"!! {len(findings)} tail task(s) completed with unreconciled evidence "
        f"(commits never merged onto base -- reconcile before worktree cleanup, "
        f"see journal `unreconciled_tail_evidence`): "
        + ", ".join(_entry(f) for f in findings)
    )


def _format_migration_quarantine_warning(
    groups: "list[dict]",
    tasks: "list[dict]",
    migration_patterns: "Sequence[str] | None",
    quarantined: "dict[str, str]",
) -> "str | None":
    """Explicit warning when a migration-touching group is quarantined while
    other groups still proceeded to PR without it.

    `plan_groups(migration_patterns=...)` folds any migration-touching task into
    BASE specifically so a migration quarantined there blocks dependent work (see
    that function's "Why migration tasks are forced into BASE" docstring) -- but
    the cascade only quarantines a group that declares a dependency edge (deps or
    a shared file) on the quarantined group. A group whose code merely consumes
    the new schema, with no such declared edge, is not caught: its PR can land
    with the migration missing from its branch ancestry, silently defeating the
    folding guarantee (reproduced live: datalena run go-20260817-162424, spec
    099, 10 of 11 group PRs referenced a new column with no migration in their
    ancestry, and nothing warned). Returns None when there is nothing to warn
    about, so callers can `if note:`.
    """
    if not migration_patterns or not quarantined:
        return None
    migration_groups = [
        g["name"]
        for g in groups
        if g["name"] in quarantined
        and coordinator.group_contains_migration_task(g, tasks, migration_patterns)
    ]
    if not migration_groups:
        return None
    proceeded = sorted(g["name"] for g in groups if g["name"] not in quarantined)
    if not proceeded:
        return None
    reasons = "; ".join(f"{name}: {quarantined[name]}" for name in migration_groups)
    return (
        f"!! MIGRATION SAFETY: {', '.join(migration_groups)} carries a schema "
        f"migration and is quarantined ({reasons}), but {len(proceeded)} other "
        f"group(s) still opened PR(s) with no declared dependency on it: "
        f"{', '.join(proceeded)}. worktrail cannot verify these don't reference "
        f"the pending migration's schema -- manually confirm merge order (the "
        f"migration group first) before merging any of them."
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


def _resolve_journaled_head_branch(name: str, rec: dict, run_id: str) -> tuple[str, str | None]:
    """Validate one group's journaled head_branch before trusting it for VERIFY.

    Returns ``(branch, quarantine_reason)``. ``quarantine_reason`` is ``None``
    when the journal's ``head_branch`` is unset or matches this run's own
    orchestrator-owned integration branch (``f"{run_id}/{name}"`` -- the only
    value ``integrate.py`` ever pushes a group's commits to). A mismatch means
    PR discovery recorded some other PR's real ``headRefName`` (its matching
    heuristic found the wrong PR); trusting that value would let VERIFY check
    out and ci-fix/merge a real, unrelated branch (e.g. "stg", a live
    stg->prd promotion branch) under a fabricated justification, so it is
    rejected outright rather than substituted or guessed at.
    """
    owned = f"{run_id}/{name}"
    candidate = rec.get("head_branch")
    if candidate and candidate != owned:
        return candidate, (
            f"resumed head_branch {candidate!r} does not match this run's owned "
            f"branch {owned!r} -- refusing to VERIFY a possibly-unrelated real branch"
        )
    return candidate or owned, None


def _group_superseded_by_tail_prs(task_ids: "list[str]", journal_groups: dict) -> str | None:
    """A quarantine reason when every task originally bundled into a group has
    independently reached a terminal (merged or PR-opened) state through its own
    `tail-<task-id>` group record instead of the parent's own PR.

    `reconcile_unreconciled_tail_evidence` (integrate.py) can ship a task that a
    group dropped (or that the group itself never delivered) through its own
    synthetic `tail-<task-id>` group across a later `full-real` invocation. When
    that has happened for every task the parent group ever bundled, the parent's
    own journal record is stale: it names a branch/PR that no longer represents
    live, unmerged work, so re-VERIFYing it chases a dead group with no
    corresponding open PR left on GitHub (brief 20260820-134348, observed on
    datalena run full-1787247442: group 'feature-1' re-verified via
    --from-verify long after tasks 2.2/4.1/4.2 had each independently merged
    through tail-2.2/tail-4.1/tail-4.2's own PRs).

    Returns `None` when `task_ids` is empty (nothing to check -- e.g. a `tail-*`
    group itself, which has no parent task list of its own) or when any task
    lacks a terminal `tail-<task-id>` record -- the parent might still be the
    group actually shipping that task.
    """
    if not task_ids:
        return None
    tail_names: "list[str]" = []
    for task_id in task_ids:
        tail_name = f"tail-{task_id.lower()}"
        rec = journal_groups.get(tail_name)
        if not rec or rec.get("state") not in ("MERGED", "OPEN"):
            return None
        tail_names.append(tail_name)
    return (
        f"superseded: every task ({', '.join(task_ids)}) independently reached "
        f"a terminal state via its own {', '.join(tail_names)} -- no live PR to verify"
    )


def _group_branch_from_journal(
    journal_groups: dict, run_id: str, groups: "list[dict] | None" = None
) -> tuple[dict, dict]:
    """Build a resumed group_branch map from journal records, quarantining any
    group whose head_branch fails `_resolve_journaled_head_branch`, or whose every
    originally-bundled task has since independently merged/opened-PR through its
    own `tail-<task-id>` group (`_group_superseded_by_tail_prs`), instead of
    trusting either for VERIFY.

    `groups` (optional): the current run's `coordinator.plan_groups(tasks)`
    output, used only to look up each journal group's original task ids for the
    supersession check. Omit when that check does not apply (e.g. existing
    callers/tests exercising only the head_branch validation).
    """
    group_branch: dict = {}
    quarantined: dict = {}
    tasks_by_group = {g["name"]: g.get("tasks", []) for g in (groups or [])}
    for name, rec in journal_groups.items():
        superseded_reason = _group_superseded_by_tail_prs(
            tasks_by_group.get(name, []), journal_groups
        )
        if superseded_reason:
            quarantined[name] = superseded_reason
            print(f"{_ts()}   !! GROUP [{name}] {superseded_reason}")
            continue
        branch, reason = _resolve_journaled_head_branch(name, rec, run_id)
        if reason:
            quarantined[name] = reason
            print(f"{_ts()}   !! GROUP [{name}] {reason}")
        else:
            group_branch[name] = branch
    return group_branch, quarantined


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


def journal_foreign_task_ids(entries: list, tasks: list) -> set[str]:
    """Return journal entry task ids that have no match in the currently loaded *tasks*.

    A journal recorded against a different `--spec` path (or a stale one from before
    tasks.md was edited) still parses and replays cleanly through `reconcile_from_journal`
    -- `dispatch.apply_report` just swallows the `KeyError` for any id it can't find and
    resume proceeds as if that task's history never happened. Observability-only
    `{"event": ...}` markers carry no `task` id tied to the current run's task set and are
    skipped here, matching `reconcile_from_journal`'s own skip.
    """
    task_ids = {t["id"] for t in tasks}
    foreign: set[str] = set()
    for e in entries:
        if e.get("event"):
            continue
        task_id = e.get("task")
        if task_id and task_id not in task_ids:
            foreign.add(task_id)
    return foreign


def group_is_terminal(g: dict, by_id: dict, terminal_statuses: set) -> bool:
    """True only when EVERY task this group claims has reached a terminal status.

    Fails **closed** on membership drift. The previous form filtered the
    comprehension with `if tid in by_id`, so a group member absent from `by_id`
    was silently skipped -- and `all()` over the surviving subset returns True.
    A group could therefore be declared terminal, and handed to the
    integrate/verify pool, while tasks it nominally owns were still running;
    their commits then never reach the group PR and nothing reports it
    (brief 20260815-115257).

    Membership drift is real, not theoretical: `plan_groups()` derives grouping
    from each task's `deps`/`files`, and compiling the same spec twice can yield
    different values for both (see `conductor/compile.py`'s inference pass), so
    `groups` and `by_id` can legitimately disagree about who owns what. Treat an
    unknown member as not-terminal and say so, rather than quietly shipping the
    group without it.
    """
    unknown = [tid for tid in g["tasks"] if tid not in by_id]
    if unknown:
        print(
            f"{_ts()}   !! GROUP [{g.get('name', '?')}] not terminal: "
            f"{len(unknown)} member(s) missing from the run's task table "
            f"({', '.join(sorted(unknown))}) -- plan drift between grouping and "
            f"fan-out; refusing to integrate a group whose membership is unresolved"
        )
        return False
    return all(by_id[tid].get("status") in terminal_statuses for tid in g["tasks"])


def diagnose_stuck_group(g: dict, by_id: dict, terminal_statuses: set) -> str:
    """Explain WHY a non-terminal group's fan-out stalled, for the "fan-out
    incomplete (run budget or error)" quarantine message.

    The bare message gives no signal about which task/edge blocked the
    frontier -- confirmed to cost long manual investigations (worktrail-go
    brief 20260824-084313: a tail-kind (e2e/cleanup) predecessor blocking its
    same-group dependent, since a tail task only runs in the separate tail
    dispatch phase *after* the main fan-out has already given up on it).
    Walk each stuck task's unmet-dependency chain to its root(s) and name
    them, flagging tail-kind roots specifically since they are the deadlock
    case a human can't fix by waiting.
    """
    missing = sorted(tid for tid in g["tasks"] if tid not in by_id)
    stuck = sorted(
        tid for tid in g["tasks"]
        if tid in by_id and by_id[tid].get("status") not in terminal_statuses
    )
    if not missing and not stuck:
        return ""

    lines = []
    if missing:
        lines.append(
            f"{len(missing)} member(s) missing from the run's task table "
            f"({', '.join(missing)})"
        )
    if not stuck:
        return "; ".join(lines)

    def _unmet_deps(tid: str) -> list:
        task = by_id.get(tid)
        if task is None:
            return []
        return [
            d for d in task.get("deps", [])
            if by_id.get(d, {}).get("status") not in coordinator.DONE
        ]

    def _root_blockers(tid: str, seen: set) -> set:
        if tid in seen:
            return set()
        seen.add(tid)
        unmet = _unmet_deps(tid)
        if not unmet:
            return {tid}
        roots: set = set()
        for d in unmet:
            roots |= _root_blockers(d, seen)
        return roots

    for tid in stuck:
        unmet = _unmet_deps(tid)
        if not unmet:
            lines.append(
                f"task {tid} blocked: no eligible frontier and no unmet deps "
                f"recorded -- status stuck at '{by_id[tid].get('status')}'"
            )
            continue
        roots: set = set()
        for d in unmet:
            roots |= _root_blockers(d, {tid})
        tail_roots = sorted(r for r in roots if by_id.get(r, {}).get("kind") in coordinator.TAIL_KINDS)
        other_roots = sorted(r for r in roots - set(tail_roots))
        parts = []
        if tail_roots:
            parts.append(
                f"depends on tail task(s) {', '.join(tail_roots)} which only "
                f"run in the tail phase after fan-out"
            )
        if other_roots:
            statuses = ", ".join(f"{r}={by_id.get(r, {}).get('status', '?')}" for r in other_roots)
            parts.append(f"depends on {', '.join(other_roots)} which never reached done ({statuses})")
        lines.append(f"task {tid} blocked: " + "; ".join(parts))
    return "; ".join(lines)


def _quarantine_reason_with_diagnosis(base_reason: str, g: dict, by_id: dict, terminal_statuses: set) -> str:
    """`base_reason`, with `diagnose_stuck_group()`'s diagnosis appended when non-empty.

    Shared by every post-fanout quarantine branch (budget-exceeded, non-terminal) so
    the diagnosis wiring lives in one place instead of being copy-pasted per branch.
    """
    diagnosis = diagnose_stuck_group(g, by_id, terminal_statuses)
    return f"{base_reason} -- {diagnosis}" if diagnosis else base_reason


def validate_task_metadata(tasks: list) -> None:
    """Refuse live fan-out when implementation tasks have no scope AND no serialization
    boundary. `conductor/compile.py`'s own prompt tells the model that an empty `files`
    list is "the safe answer" because `runplan.apply_to_tasks` keeps the task's baseline
    dependency edge whenever either endpoint lacks file scope -- serialising it behind
    its neighbour instead of leaving it to race unbounded. A task that still has that
    edge (non-empty `deps`) is exactly the case the prompt promises is safe, so it is not
    flagged here. A task with neither files nor a dependency boundary has no such
    guarantee -- it would enter the frontier immediately and `runnable_frontier` reads
    its empty file set as "collides with nothing", so it is the genuinely unbounded case
    this check exists to catch.

    Also refuses fan-out when a still-pending task declares the same file as another
    task with no dependency order between them (go-20260805-172326). `runnable_frontier`'s
    per-tick file lock happens to serialise same-file writers anyway, but this is a
    defense-in-depth backstop, not the primary enforcement: `runplan.apply_to_tasks` now
    auto-repairs this same gap by adding an ordering edge whenever both endpoints go
    through it, but the live `tasks` list checked here can be assembled without ever
    calling `apply_to_tasks` (e.g. resumed from a journal straight off the task source),
    so nothing upstream guarantees the invariant for it. This is the live-run enforcement
    point for `runplan.unordered_file_collisions`, the same check `worktrail-compile` runs
    standalone against a freshly compiled plan -- also now mostly a backstop there, since
    `apply_to_tasks` closes the gap before that check ever sees it. A pair where both
    tasks are already done is not reported: they already ran, so there is nothing left
    here to protect."""
    missing = [
        t["id"]
        for t in tasks
        if t.get("status") == "pending"
        and t.get("kind", "impl") not in ("docs", "e2e", "cleanup")
        and not t.get("files")
        and not t.get("deps")
    ]
    if missing:
        raise RuntimeError(
            "implementation task(s) missing required frontmatter files and have no "
            "dependency to serialize behind: " + ", ".join(missing)
        )

    from ..conductor import runplan as _runplan

    status_by_id = {t["id"]: t.get("status") for t in tasks}
    collisions = [
        (f, a, b)
        for f, a, b in _runplan.unordered_file_collisions(tasks)
        if status_by_id.get(a) == "pending" or status_by_id.get(b) == "pending"
    ]
    if collisions:
        detail = "; ".join(f"{f}: {a} <-> {b}" for f, a, b in collisions)
        raise RuntimeError(
            "task(s) declare the same file with no dependency order between them: " + detail
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

    Also runs the loaded `TaskSource`'s `validate_dependencies()` (when the
    adapter implements it -- e.g. Spec Kit does not) once for the whole task
    set -- unresolved same-spec `deps` and (where the format supports it)
    unsettled `decision-refs:` -- and WARNs on each diagnostic it returns.

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

    spec_id, tasks = taskformats.load_spec(str(repo / spec_rel))
    warn_count = 0

    validate_dependencies = getattr(
        taskformats.task_source_for(repo / spec_rel), "validate_dependencies", None
    )
    if validate_dependencies is not None:
        for diagnostic in validate_dependencies(spec_id, tasks):
            print(f"WARN: {diagnostic}")
            warn_count += 1

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
                result = taskformats.resolve_external_dependency(repo / spec_rel, ref)
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
                create_files, modify_files = taskformats.file_sections_for(task_path, body_text)
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
    blocked_by: list | None = None,
) -> dict:
    entry = {
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
    if blocked_by:
        # Structured blocker ids for dependency-gate entries, so `clear_tasks`'
        # cascade never has to parse them back out of the free-text notes.
        entry["blocked_by"] = [str(b) for b in blocked_by]
    return entry


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


_NON_RETRYABLE_TERMINAL = ("failed", "escalated")
_GATE_NOTES_PREFIX = "blocked by failed prerequisite(s): "


def _gate_blockers(entry: dict) -> list:
    """Blocker task ids recorded on a `dependency-gate` journal entry.

    Prefers the structured `blocked_by` field (written by `_journal_failure_entry`
    since clear-task landed); falls back to parsing the known notes format for
    journals written before the field existed."""
    structured = entry.get("blocked_by")
    if structured:
        return [str(b) for b in structured]
    notes = (entry.get("report") or {}).get("notes") or ""
    if _GATE_NOTES_PREFIX in notes:
        tail = notes.split(_GATE_NOTES_PREFIX, 1)[1]
        return [part.strip() for part in tail.split(",") if part.strip()]
    return []


def clear_tasks(repo: Path, spec_rel: str, task_ids: list) -> int:
    """Surgically remove failed/escalated journal entries for *task_ids* so the next
    `full-real` resume re-dispatches just those tasks as pending.

    This is the targeted alternative to `--fresh` for a hand-fixed task: replay bakes
    any non-"retryable" terminal_status back into task status on every resume, and
    `--fresh` discards the ENTIRE journal -- forcing every other task, including
    already-merged work, to re-run. Semantics:

    - Only entries whose `report.terminal_status` is non-retryable ("failed" /
      "escalated") are removed. A task's earlier successful role entries
      (implement/review) are kept, so a task that broke mid-flight resumes from
      where it broke rather than from scratch.
    - Cascades to `dependency-gate` entries blocked by a cleared task (see
      `_gate_blockers`), transitively, so downstream tasks that only failed by
      inheritance also become pending again.
    - REFUSES (exit 1, journal untouched) when a targeted task has a completion
      entry -- a successful cleanup is the per-task record that carried it to
      "done" (`dispatch.apply_report`), and a merged group PR implies exactly that
      record for each of its tasks -- or when a targeted task has nothing to clear
      (guards against id typos silently no-opping).

    Returns 0 on success, 1 on refusal or when there is no journal.
    """
    journal_path = journal_path_for(repo, spec_rel)
    if not journal_path.exists():
        print(f"{_ts()} CLEAR-TASK: no run journal at {journal_path} -- nothing to clear")
        return 1
    journal = json.loads(journal_path.read_text())
    entries = journal.get("entries", [])
    targets = set(task_ids)

    def _terminal_failure(entry: dict) -> bool:
        return (
            not entry.get("event")
            and (entry.get("report") or {}).get("terminal_status") in _NON_RETRYABLE_TERMINAL
        )

    # Guardrail: never discard completed work. Refuse the whole operation (zero
    # file mutation) if any targeted task has a completion record.
    for entry in entries:
        if entry.get("event") or entry.get("task") not in targets:
            continue
        report = entry.get("report") or {}
        completed = report.get("terminal_status") == "done" or (
            entry.get("role") == dispatch.ROLE_CLEANUP
            and report.get("status") != "failed"
            and report.get("terminal_status") not in _NON_RETRYABLE_TERMINAL
        )
        if completed:
            print(
                f"{_ts()} CLEAR-TASK: refusing -- {entry.get('task')} has a "
                f"success/completion entry (role {entry.get('role')!r}); clearing it "
                f"would discard completed work. Journal left unchanged."
            )
            return 1
    uncleared = sorted(
        t for t in targets if not any(_terminal_failure(e) and e.get("task") == t for e in entries)
    )
    if uncleared:
        print(
            f"{_ts()} CLEAR-TASK: refusing -- no failed/escalated journal entries for "
            f"{', '.join(uncleared)}; nothing to clear. Journal left unchanged."
        )
        return 1

    cleared = set(targets)
    remove = {id(e) for e in entries if _terminal_failure(e) and e.get("task") in targets}
    # Cascade to fixpoint: a gate blocked by a cleared task is removed, and ITS
    # task then counts as cleared for gates further downstream.
    changed = True
    while changed:
        changed = False
        for entry in entries:
            if id(entry) in remove or not _terminal_failure(entry):
                continue
            if entry.get("role") != "dependency-gate":
                continue
            if cleared.intersection(_gate_blockers(entry)):
                remove.add(id(entry))
                cleared.add(entry.get("task"))
                changed = True

    direct = [e for e in entries if id(e) in remove and e.get("task") in targets]
    cascaded = [e for e in entries if id(e) in remove and e.get("task") not in targets]
    journal["entries"] = [e for e in entries if id(e) not in remove]
    progress.atomic_write_text(
        str(journal_path), json.dumps(journal, indent=2, sort_keys=True) + "\n"
    )
    msg = (
        f"CLEAR-TASK: removed {len(direct)} entr{'y' if len(direct) == 1 else 'ies'} "
        f"for task(s) {', '.join(sorted(targets))}"
    )
    if cascaded:
        casc_ids = sorted({e.get("task") for e in cascaded})
        msg += (
            f" (cascaded: {len(cascaded)} dependency-gate "
            f"entr{'y' if len(cascaded) == 1 else 'ies'} for {', '.join(casc_ids)})"
        )
    print(f"{_ts()} {msg}")
    return 0


def _annotate_external_deps(repo: Path, tasks: list, spec_rel: str | None = None) -> None:
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
            result = (
                taskformats.resolve_external_dependency(repo / spec_rel, ref)
                if spec_rel
                else taskformats.resolve_external_dependency_for_repo(repo, ref)
            )
            if result["satisfied"]:
                continue
            if result["resolved"]:
                blockers.append(f"{ref} {result['status']}")
            else:
                blockers.append(f"{ref} unresolved ({result['reason']})")
        t["external_deps_ok"] = not blockers
        t["external_deps_blockers"] = blockers


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _branch_exists(repo: Path, name: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", name, check=False).returncode == 0


def _is_ancestor(git_dir: Path, ancestor: str, descendant: str) -> bool:
    """True if `ancestor`'s content is already present in `descendant` (`git merge-base
    --is-ancestor`) -- the one correct freshness/staleness primitive for this module.

    A branch/file *existence* check cannot tell a stale copy from a fresh one -- it was
    the root cause of two independently-discovered incidents (`_carry_squash_merged_
    dependencies`'s file-existence gate, and `integrate_one`'s dependency-branch-gone
    fallback reconstructing a stale merge-base) before both were fixed to use this
    ancestry test instead. Every call site in this module that needs to know whether a
    ref's content is already caught up with another ref should go through this, not a
    hand-written `_git(..., "merge-base", "--is-ancestor", ...)` call.
    """
    return (
        _git(git_dir, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode
        == 0
    )


def _worktree_checkouts_on_branch(repo: Path, base: str) -> list[Path]:
    """Every linked worktree (this repo's own checkout included) that has
    `refs/heads/<base>` checked out right now. Worktrees share one ref store,
    so a ref move in any one of them is visible in all the others."""
    listing = _git(repo, "worktree", "list", "--porcelain", check=False)
    if listing.returncode != 0:
        return []
    paths: list[Path] = []
    current_path: Path | None = None
    for line in listing.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            if line[len("branch ") :].strip() == f"refs/heads/{base}":
                paths.append(current_path)
            current_path = None
    return paths


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
    Uses `update-ref` on the shared `refs/heads/<base>` ref rather than
    `git pull`/`merge` run from a specific checkout, because `repo` (the
    --repo argument) may itself be a linked worktree distinct from whichever
    checkout has `base` checked out (brief 20260806-215026). `update-ref`
    alone never touches any checkout's index/workdir though -- including
    `repo`'s own, when `repo` IS that checkout -- so this refuses the ref
    move entirely whenever any worktree with `base` checked out is dirty
    (mirroring git's own "cannot force update branch checked out in
    worktree" protection, which `update-ref` bypasses), and otherwise syncs
    every such checkout via `reset --hard` right after moving the ref. This
    avoids the exact bug this function exists to prevent: a checkout whose
    HEAD silently outruns its own index/workdir, surfacing as fabricated (or
    inflated) staged changes in `git status`.
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
    if not _is_ancestor(repo, base, f"{remote}/{base}"):
        print(
            f"{_ts()} BASE REFRESH: local '{base}' ({old_sha[:8]}) has diverged from "
            f"{remote}/{base} ({remote_sha[:8]}) -- not fast-forwardable; leaving the "
            "local ref untouched (it may carry local-only commits; resolve manually)."
        )
        return

    # Every checkout sharing this ref must be clean BEFORE moving it -- the
    # ref move is what would desync a checkout's HEAD from its index/workdir,
    # so a dirty checkout makes the whole move unsafe, not just that
    # checkout's own sync.
    checkouts = _worktree_checkouts_on_branch(repo, base)
    dirty_checkouts = [
        path
        for path in checkouts
        if _git(path, "status", "--porcelain", check=False).stdout.strip()
    ]
    if dirty_checkouts:
        dirty_list = ", ".join(str(p) for p in dirty_checkouts)
        print(
            f"{_ts()} BASE REFRESH: '{base}' is checked out with uncommitted local changes "
            f"at {dirty_list} -- leaving the ref untouched (resolve manually, then re-run)."
        )
        return

    upd = _git(repo, "update-ref", f"refs/heads/{base}", remote_sha, check=False)
    if upd.returncode != 0:
        print(f"{_ts()} BASE REFRESH: update-ref failed for '{base}'; leaving local ref untouched")
        return
    print(f"{_ts()} BASE REFRESH: {base} {old_sha[:8]} -> {remote_sha[:8]} ({remote}/{base})")

    for path in checkouts:
        sync = _git(path, "reset", "--hard", check=False)
        if sync.returncode == 0:
            print(f"{_ts()} BASE REFRESH: synced checkout at {path} to {remote_sha[:8]}")
        else:
            print(
                f"{_ts()} BASE REFRESH: failed to sync checkout at {path}: "
                f"{sync.stderr.strip()}"
            )


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


def _resume_quarantine_staleness_warning(
    repo: Path, base: str, spec_id: str, groups: list, groups_journal: dict
) -> None:
    """Best-effort: on a pipeline resume, warn per-group when a QUARANTINED
    group's task branches have fallen behind `base` -- a resume with no
    --fresh replays that group's cached quarantine verdict as-is, even if a
    fix has since landed on `base`. Unlike `_resume_drift_report` (a single
    generic heads-up scoped to the first task branch found), this iterates
    every QUARANTINED group and uses the MAX drift across that group's own
    task branches, since drift on an unrelated non-quarantined group's
    branch says nothing about whether this group's verdict is stale.

    Never raises: any branch that doesn't exist or any failing git call is
    skipped for that group (silently, matching `_resume_drift_report`'s own
    failure posture) rather than blocking or failing the resume.
    """
    for g in groups:
        if groups_journal.get(g["name"], {}).get("state") != "QUARANTINED":
            continue
        max_count = 0
        for tid in g["tasks"]:
            branch = f"{spec_id}/{tid.lower()}"
            if not _branch_exists(repo, branch):
                continue
            mb = _git(repo, "merge-base", branch, base, check=False)
            if mb.returncode != 0 or not mb.stdout.strip():
                continue
            merge_base_sha = mb.stdout.strip()
            count = _git(repo, "rev-list", "--count", f"{merge_base_sha}..{base}", check=False)
            if count.returncode != 0 or not count.stdout.strip().isdigit():
                continue
            max_count = max(max_count, int(count.stdout.strip()))
        if max_count != 0:
            print(
                f"{_ts()} PIPELINE RESUME WARNING: group '{g['name']}' is QUARANTINED in "
                f"the resumed journal, and base '{base}' has moved {max_count} commit(s) "
                "since that group's task branch was forked. This resume will replay the "
                "prior quarantine verdict as-is. If the blocker may already be fixed on "
                f"{base}, re-run with --fresh to re-evaluate instead of trusting the "
                "cached result."
            )


def build_external_deps_by_ref(repo: Path, tasks: list, spec_rel: str | None = None) -> dict:
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
            resolved = taskformats.resolve_external_dependency_for_repo(repo, ref)
            if not resolved.get("satisfied"):
                continue
            spec_id, _, task_id = ref.partition("/")
            sibling_candidates = (
                repo / "openspec" / "changes" / spec_id,
                repo / "docs" / "specs" / spec_id,
                repo / ".specify" / "specs" / spec_id,
            )
            sibling_spec = next(
                (candidate for candidate in sibling_candidates if candidate.is_dir()),
                sibling_candidates[-1],
            )
            sibling_task = taskformats.task_for(sibling_spec, task_id)
            if sibling_task is None:
                continue
            external_deps_by_ref[ref] = {"id": task_id, "files": sibling_task.get("files", [])}
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
        result = taskformats.resolve_external_dependency_for_repo(repo_root, ref)
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

    if not _is_ancestor(repo, start_ref, branch):
        raise WorktreeAddError(
            f"retained task branch {branch} is stale: {start_ref} is not an ancestor of "
            f"{branch}. Repair the branch or choose an explicit fresh run before retrying."
        )

    if expected_head_sha:
        expected = _git(
            repo, "rev-parse", "--verify", f"{expected_head_sha}^{{commit}}", check=False
        )
        if expected.returncode != 0 or not _is_ancestor(repo, expected_head_sha, branch):
            raise WorktreeAddError(
                f"retained task branch {branch} does not contain journaled task head "
                f"{expected_head_sha}. Repair the branch or clear the stale run explicitly."
            )


ASSEMBLY_RESOLVE_STRIKES = 1
"""Bounds a sibling-merge resolve attempt to a single try before giving up.

Mirrors `integrate.ASSEMBLY_RESOLVE_STRIKES`. Kept as a separate constant
rather than imported -- `integrate` imports `live`, so importing back would
cycle -- but must stay numerically in sync with it.
"""


def _stack_resolve_verify(wt: Path, conflicted_files: list) -> bool:
    """Git-state check for whether a sibling-merge conflict was actually resolved.

    Mirrors `integrate._assembly_resolve_salvage`: a resolve worker's report-back
    is trusted only as far as the git state backs it up. The merge must be
    concluded (no `MERGE_HEAD`), the tree must be clean (`git status
    --porcelain`), and none of the files that were conflicted may still carry a
    `<<<<<<<` marker. Any failure here means the resolution is rejected outright,
    regardless of what the worker claimed.
    """
    if _git(wt, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        return False  # merge still in progress
    if _git(wt, "status", "--porcelain", check=False).stdout.strip():
        return False  # dirty tree
    for f in conflicted_files:
        p = Path(wt) / f
        if p.is_file() and "<<<<<<<" in p.read_text(errors="replace"):
            return False
    return True


def _stack_resolve_attempt(
    wt: Path, spec_id: str, task: dict, conflicting_branch: str, assembly_resolve_spawn
) -> bool:
    """Dispatch a resolve worker to fix a sibling-dependency merge conflict.

    Called with the conflicted merge state (conflict markers) still in place.
    Mirrors `integrate._attempt_assembly_resolve`'s bounded strike loop (see
    `ASSEMBLY_RESOLVE_STRIKES`): a worker's report-back is trusted only as far
    as `_stack_resolve_verify` backs it up. Returns True once a strike's
    resolution verifies clean; False once strikes are exhausted, leaving the
    merge aborted so the caller can raise.
    """
    conflicted_files = [
        ln.strip()
        for ln in _git(
            wt, "diff", "--name-only", "--diff-filter=U", check=False
        ).stdout.splitlines()
        if ln.strip()
    ]
    prompt = dispatch.build_stack_conflict_prompt(spec_id, task, conflicting_branch, wt)
    for strike in range(ASSEMBLY_RESOLVE_STRIKES):
        explicit_failure = False
        try:
            raw = assembly_resolve_spawn(prompt, wt)
            rep = dispatch.parse_report_back(raw)
            if rep.get("status") != "success":
                explicit_failure = True  # worker explicitly reported failure; trust it
        except Exception:
            pass  # spawn crash or unparseable report-back: let git state decide
        if not explicit_failure and _stack_resolve_verify(wt, conflicted_files):
            return True
        _git(wt, "merge", "--abort", check=False)
        if strike < ASSEMBLY_RESOLVE_STRIKES - 1:
            # Re-issue the conflicting merge to restore conflict state for the next strike.
            m = _git(wt, "merge", "--no-edit", conflicting_branch, check=False)
            if m.returncode == 0:
                return True  # Merged cleanly on the retry (unlikely but safe)
    return False


_CHECKLIST_LINE_RE = re.compile(r"^(?P<prefix>\s*-\s*\[)(?P<mark>[ xX])(?P<suffix>\]\s*)(?P<text>.*)$")


def _union_merge_checklist(ours: str, theirs: str) -> str:
    """Line-union two versions of a checklist file: a task line is checked if
    EITHER side checked it, keyed by its post-checkbox text. A checklist line
    present on only one side is kept as-is (from `ours`, or appended from
    `theirs` if `ours` lacks it); non-checklist lines are taken from `ours`
    unchanged. Never removes a checkmark either side already had, so no
    completed work is ever hidden by the merge.
    """
    def parse(text: str) -> tuple[dict, list]:
        checked: dict = {}
        order: list = []
        for line in text.splitlines():
            m = _CHECKLIST_LINE_RE.match(line)
            if m:
                key = m.group("text")
                checked[key] = m.group("mark") in ("x", "X")
                order.append((key, line))
            else:
                order.append((None, line))
        return checked, order

    ours_checked, ours_order = parse(ours)
    theirs_checked, theirs_order = parse(theirs)

    merged_lines = []
    seen = set()
    for key, line in ours_order:
        if key is None:
            merged_lines.append(line)
            continue
        seen.add(key)
        m = _CHECKLIST_LINE_RE.match(line)
        mark = "x" if (ours_checked.get(key, False) or theirs_checked.get(key, False)) else " "
        merged_lines.append(f"{m.group('prefix')}{mark}{m.group('suffix')}{key}")
    for key, line in theirs_order:
        if key is not None and key not in seen:
            merged_lines.append(line)

    result = "\n".join(merged_lines)
    if ours.endswith("\n") or theirs.endswith("\n"):
        result += "\n"
    return result


def _resolve_tasks_md_checklist_conflict(wt: Path, spec_id: str) -> bool:
    """If the in-progress conflicted merge in `wt` is confined entirely to this
    change's own `openspec/changes/<spec_id>/tasks.md`, resolve it deterministically
    by taking the union of checked task lines from both sides and conclude the
    merge. Returns True if resolved (merge committed); False if the conflict is
    not this narrow case -- caller aborts exactly as before this change.
    """
    unmerged = _git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
    expected = f"openspec/changes/{spec_id}/tasks.md"
    if unmerged != [expected]:
        return False
    ours = _git(wt, "show", f":2:{expected}", check=False)
    theirs = _git(wt, "show", f":3:{expected}", check=False)
    if ours.returncode != 0 or theirs.returncode != 0:
        return False
    (wt / expected).write_text(_union_merge_checklist(ours.stdout, theirs.stdout))
    _git(wt, "add", expected, check=False)
    return _git(wt, "commit", "--no-edit", check=False).returncode == 0


def _carry_squash_merged_dependencies(
    repo: Path, spec_id: str, task: dict, by_id: dict, wt: Path, remote: str, base: str
) -> "dict | None":
    """Carry a DONE dependency's content into `wt` across a squash-merge boundary.

    `dependency_start_ref` falls back to bare `HEAD` for a dependency whose task
    branch was already deleted (squash-merged + `verify.cleanup_group`) --
    deterministic for tail e2e/cleanup tasks, which `_dispatch_pending_tail`
    only dispatches after every group is integrated/verified/merged. `HEAD` is
    the run-start local base, which predates those merges, so the stacked
    worktree can be missing the dependency's declared files even though the
    dependency is DONE.

    Only engages for a dependency that is both DONE and branch-gone -- one this
    run stacked onto (branch still present) already carries the content via the
    normal sibling-merge above. For each such dependency, fetch the freshest
    base ref (`<remote>/<base>`, falling back to local `<base>`) and merge it
    into `wt` with a normal (unbiased) merge -- deliberately NOT `-X ours`: an
    `-X` strategy auto-resolves every content-level conflict in the merge, not
    just ones touching this dependency's own files, silently discarding real
    live-base content on any file the worktree's stale start point happens to
    also touch (`docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`,
    the same risk class root-caused and fixed for `integrate_one`'s
    dependency-branch-gone fallback in PR #475). A merge failure -- whether from a
    genuine conflict or any other git error -- aborts and falls through with a
    WARN: `_require_dependency_files` stays the fail-loud backstop when the
    content genuinely isn't available. One narrow, deterministic exception: a
    conflict confined entirely to this change's own `openspec/changes/<id>/tasks.md`
    (each concurrently-merged group independently checks off its own tasks in that
    shared checklist, so squash-merge history loses the common ancestor and produces
    a routine add/add conflict there) resolves via `_resolve_tasks_md_checklist_conflict`
    by taking the union of checked boxes, instead of aborting.

    Deliberately does NOT gate on `_dependency_file_declared_path_exists` --
    that is a bare path-existence check, which is a broken proxy for "content
    is missing" whenever a dependency's declared file already existed in the
    repo before the dependency's own change (any edit to an established file,
    not just a newly-created one): the path exists in every worktree
    regardless of which commit it forked from, so the check can never detect
    staleness (brief 20260817-120332, reproduced live on a tail-kind task
    depending on an edit to a long-lived doc). The `merge-base --is-ancestor`
    check right below is the actual, exact freshness test and already
    short-circuits into a no-op for a dependency whose content is genuinely
    already present -- so gating on it alone is both correct and sufficient;
    the extra file-existence pre-filter only introduced false negatives.

    Returns a `checklist_conflict_resolved` event dict when the tasks.md
    conflict exception engaged; `None` from every other return point.
    """
    stale_deps = [
        dep_id
        for dep_id in task.get("deps", [])
        if (dep := by_id.get(dep_id)) is not None
        and dep.get("status") in coordinator.DONE
        and not _branch_exists(repo, f"{spec_id}/{dep_id.lower()}")
    ]
    if not stale_deps:
        return

    _git(repo, "fetch", "-q", remote, base, check=False)  # best-effort; offline stays a no-op
    ref = f"{remote}/{base}" if _branch_exists(repo, f"{remote}/{base}") else base
    if not _branch_exists(repo, ref):
        return  # neither ref resolvable; _require_dependency_files raises with forensics

    if _is_ancestor(wt, ref, "HEAD"):
        return  # already carried (e.g. a prior carry, or the worktree started past it)

    m = _git(wt, "merge", "--no-edit", ref, check=False)
    if m.returncode != 0:
        if _resolve_tasks_md_checklist_conflict(wt, spec_id):
            return {
                "event": "checklist_conflict_resolved",
                "task": task["id"],
                "at": round(time.time(), 3),
            }
        _git(wt, "merge", "--abort", check=False)
        print(
            f"{_ts()} WARN: task {task['id']} worktree {wt} squash-merge carry from "
            f"{ref} failed for dependenc{'y' if len(stale_deps) == 1 else 'ies'} "
            f"{', '.join(stale_deps)}: {(m.stderr or '').strip()[:300]}"
        )


def add_stacked_worktree(
    repo: Path,
    spec_id: str,
    task: dict,
    by_id: dict,
    wt: Path,
    expected_head_sha: str | None = None,
    assembly_resolve_spawn=None,
    remote: str | None = None,
    base: str | None = None,
) -> None:
    """Create `wt` on a fresh task branch, stacked on the task's dependencies.

    Branches off the first dependency (dependency_start_ref) and merges any
    sibling dependencies into the new worktree so it carries ALL dependency
    commits. A merge conflict between sibling deps is aborted and logged (rare --
    deps are usually a chain), leaving the worktree on the primary dependency.

    `assembly_resolve_spawn`, when provided, is used to attempt an automated
    resolve-and-retry of a sibling merge conflict before giving up: on a
    conflicted merge, `_stack_resolve_attempt` dispatches a resolve-worker
    (bounded to `ASSEMBLY_RESOLVE_STRIKES` tries) and only accepts the
    resolution once `_stack_resolve_verify` confirms the git state actually
    backs it up. If the attempt is exhausted or `assembly_resolve_spawn` is
    not provided, the merge is aborted and `WorktreeStackConflictError` is
    raised as before.

    `remote`/`base`, when both provided, run a post-stack carry
    (`_carry_squash_merged_dependencies`) for dependencies already squash-merged
    into base with their task branch deleted. Absent (the default), the carry
    is skipped entirely -- no behavior change for the cassette/demo path or any
    caller that doesn't pass them.
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
            if assembly_resolve_spawn is not None and _stack_resolve_attempt(
                wt, spec_id, task, mb, assembly_resolve_spawn
            ):
                continue
            _git(wt, "merge", "--abort", check=False)
            # Continuing here would silently leave `wt` missing `mb`'s commits --
            # `_require_dependency_files` would be the next line to notice, but only
            # for files it happens to check, and only well after this cheap check
            # could have caught it. Raise now: `_safe_drive` isolates this to just
            # `task['id']` (marked failed, journaled), so the run continues on every
            # OTHER task; only this task's dependents stay blocked until a human
            # resolves the conflict and resumes.
            raise WorktreeStackConflictError(
                f"{task['id']}: could not stack dependency branch {mb} onto {start} "
                f"(merge conflict) -- {task['id']}'s worktree would be missing {mb}'s "
                f"commits. Resolve the conflict between {start} and {mb} manually, "
                f"then resume the run."
            )

    if remote and base:
        _carry_squash_merged_dependencies(repo, spec_id, task, by_id, wt, remote, base)


def _add_stacked_worktree_kwargs(target, kwargs: dict) -> dict:
    """Drop entries `target` doesn't declare, so newly threaded optional kwargs
    (e.g. `assembly_resolve_spawn`) don't break callers that monkeypatch
    `add_stacked_worktree` with an older, narrower-signature double. Unwraps a
    `unittest.mock.MagicMock(side_effect=...)`, whose own `__call__` signature
    is always `(*args, **kwargs)` and would otherwise hide the double's real
    (narrower) signature from `inspect.signature`.
    """
    fn = getattr(target, "side_effect", None) or target
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def bootstrap_worktree(
    wt: Path, bootstrap_cmd: str | None, log=print, *, required: bool = False,
) -> bool:
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
    "cd app && npm ci"), sourced from worktrail-go-policy.yaml's `worktree_bootstrap_cmd`.
    None/empty -> skip entirely, so repos without a wired command are unaffected.
    For Node repos, `worktrail-bootstrap-node-modules` (bootstrap_node_modules.py)
    is a drop-in `bootstrap_cmd` that hardlink-clones this fan-out's spec worktree
    node_modules instead of paying a full install per task worktree.

    By default a failed install is logged and the caller may let the worker
    self-recover. Orchestrated task creation passes ``required=True`` so a
    configured bootstrap failure stops before an agent is spawned into an
    incomplete worktree.

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
        log(
            f"{_ts()} BOOTSTRAP: !! could not launch ({e}); "
            f"{'stopping before worker spawn' if required else 'worker will self-install'}"
        )
        if required:
            raise WorktreeAddError(f"required worktree bootstrap could not launch in {wt}: {e}") from e
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        log(
            f"{_ts()} BOOTSTRAP: !! failed (rc={proc.returncode}); "
            f"{'stopping before worker spawn' if required else 'worker will self-install'}. {tail}"
        )
        if required:
            raise WorktreeAddError(
                f"required worktree bootstrap failed in {wt}: {tail or 'exit status ' + str(proc.returncode)}"
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
    effort: str | None = None,
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
                task = taskformats.task_for(spec_folder, tf.stem)
                for f in (task or {}).get("files", []) or []:
                    file_counts[f] += 1
            except Exception:
                pass

    # --- pre-load top-N most-referenced source files ---
    repo_root = taskformats.task_source_for(spec_folder).repo_root
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
        effort=effort,
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
    candidates = [(agent, model)]
    for hop in hops:
        if not hop or hop == agent:
            continue
        try:
            candidates.append((hop, spawnlib.default_model_for_agent(hop)))
        except Exception:
            continue
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
        purpose_tier_map: dict | None = None,
        fallback_chain: "list[str] | None" = None,
        effort: str | None = None,
    ) -> None:
        self.agent = agent
        self.label = f"LIVE {agent}"
        self.spec_id = spec_id
        self.spec_folder_rel = spec_folder_rel.rstrip("/") + "/"
        self.timeout = timeout
        self.model = model or spawnlib.default_model_for_agent(agent)
        # Run-level default effort (model-tier-routing 3.3). Unlike `model`, effort
        # has no per-agent default to fall back to -- omitting it is always a valid,
        # common state (spawnlib.build_cmd only adds the flag `if effort:`), so no
        # `spawnlib.default_effort_for_agent()` equivalent exists or is needed.
        self.effort = effort
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
        # {purpose: tier} -- routing.purpose_tiers, consulted by dispatch.agent_for
        # ahead of task.get("complexity") when resolving self.tier_map's key
        # (task-purpose-classification 4.2/5.1). Defaults to "off" like tier_map.
        self.purpose_tier_map = purpose_tier_map or {}
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
            purpose_tier_map=self.purpose_tier_map,
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
        # Effort mirrors the model precedence above, minus the cross-agent default
        # fallback: there's no `default_effort_for_agent()` (no agent requires one),
        # so a role/tier pinned to a different agent than the run's default only
        # gets an effort when the resolution itself carried one (AC-011 parity).
        effort = resolved["effort"] or (self.effort if agent == self.agent else None)
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
        # legacy single --fallback-agent when configured (REQ-018). It applies
        # to every spawn EXCEPT judgment roles pinned to a non-default agent:
        # a role/tier override on implement/fix/cleanup still deserves the
        # run's configured recovery path (a pinned tier model going unavailable
        # for any reason should not leave the task with zero automatic
        # recovery), but a JUDGMENT_ROLES spawn (review is the only one that
        # reaches this call -- resolve/ci-fix/assembly-resolve never do, see
        # dispatch.JUDGMENT_ROLES) deliberately pinned to a different reviewer
        # agent keeps the old no-fallback gating so a silent fallback can never
        # erode the independent-reviewer guarantee (13.3, DEC-003). spawn_agent/
        # spawn_claude_p already drop any hop equal to the spawned `agent`
        # itself (spawnlib._normalize_fallback_chain), so passing the run-level
        # chain through unchanged for a tier-resolved agent is safe as-is.
        fallback = self.fallback_chain if self.fallback_chain else self.fallback_agent
        judgment_pinned = role in dispatch.JUDGMENT_ROLES and agent != self.agent
        effective_fallback = None if judgment_pinned else fallback
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
                effort=effort,
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
            effort=effort,
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
    spawn=None,
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
    spawn = spawn or LiveSpawn(spec_id, SAMPLE_SPEC_REL, agent=agent, model=model)
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
            # No `assembly_resolve_spawn` seam here, deliberately: this is the
            # cassette/demo recording path (`live_run`), not a production run
            # path -- see design.md's Non-Goals for stacked-worktree-conflict-auto-resolve.
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
            old, new = dispatch.apply_report(tasks, rep, role)
            report_fields = {k: rep.get(k) for k in orchestrate._REPORT_FIELDS}
            if new in ("escalated", "failed"):
                # dispatch.transition computes this status in-memory only -- stamp it
                # onto the entry that actually produced it, not only onto downstream
                # dependency-gate entries it blocks, so clear_tasks()'s terminal_status
                # match can find it. Covers both the review 3-strikes circuit breaker
                # ("escalated") and a normal role's terminal "failed" report (e.g. a
                # fix-role worker that legitimately declines an out-of-scope change) --
                # previously only "escalated" was stamped here, so clear_tasks()
                # refused a "failed" entry even though task status already read
                # "failed". Same fix as _apply_step_commit() (#496), applied here to
                # live_run's own separate (pre-#498) entry-construction path.
                report_fields["terminal_status"] = new
            entries.append(
                {
                    "task": rep["task"],
                    "role": rep["step"],
                    "report": report_fields,
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
            extra = f" [{rep.get('review_status')}]" if role == dispatch.ROLE_REVIEW else ""
            print(
                f"{_ts()}   {task['id']} {role:9} {old:12} -> {new}{extra}  (sha {str(rep.get('head_sha',''))[:8]})"
            )

    tick = 0
    while True:
        _annotate_external_deps(repo, tasks, SAMPLE_SPEC_REL)
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
        reasons = ", ".join(f"{k}: {v}" for k, v in quarantined.items())
        print(f"NOTE: {len(quarantined)} group(s) quarantined for human review: {reasons}")
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


def _dependency_file_declared_path_exists(wt: Path, declared: str) -> bool:
    """True if `declared` resolves under `wt` -- either literally, or, for a
    glob-style entry (e.g. `api/tests/**`, legitimate shorthand for many
    files), if the pattern matches at least one path. A literal
    `Path.exists()` check can never match a wildcard entry -- no file is
    literally named `**` -- so glob metacharacters route through
    `Path.glob()` instead.
    """
    if any(ch in declared for ch in "*?["):
        try:
            return any(wt.glob(declared))
        except ValueError:
            return False
    return (wt / declared).exists()


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

    A `--fresh` run has no journal, so a dependency the run did not itself
    drive never gets a `head_sha` populated (frontmatter carries no commit
    SHAs) and the ancestor check above could never engage. But a dependency
    already in a DONE-like status (`coordinator.DONE`) was, by construction,
    merged before this run started -- `dependency_start_ref` falls back to
    bare HEAD for it precisely because no branch exists to stack -- so the
    stacked worktree's HEAD already IS whatever that dependency actually
    shipped. A still-missing declared file for such a dependency downgrades to
    the same WARN, keyed off its DONE status instead of an unavailable
    `head_sha`. A dependency this run is actively driving (no DONE-like status
    yet) carries no such guarantee, so the guard stays strict for it.

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
            if _dependency_file_declared_path_exists(wt, f):
                continue
            dep_head = dep.get("head_sha")
            if dep_head and _is_ancestor(wt, dep_head, "HEAD"):
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
            if not dep_head and dep.get("status") in coordinator.DONE:
                print(
                    f"{_ts()} WARN: task {task['id']} worktree {wt} is missing "
                    f"{dep_id}'s declared file {f!r}, and no head_sha is "
                    f"available to verify (fresh run, no journal) -- but "
                    f"{dep_id} is already {dep.get('status')!r}, which means "
                    "it was merged before this run started, so this is "
                    f"treated as declared-vs-actual drift. Correct {dep_id}'s "
                    "task frontmatter `files:` to match what it actually "
                    "committed."
                )
                events.append(
                    {
                        "event": "dependency_file_drift",
                        "task": task["id"],
                        "dep_id": dep_id,
                        "declared_path": f,
                        "dep_head_sha": None,
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


def _require_dependency_files_with_repair(
    wt: Path,
    task: dict,
    by_id: dict,
    repo: Path,
    spec_id: str,
    remote: str | None,
    base: str | None,
) -> "list[dict]":
    """`_require_dependency_files`, but retries the squash-merge carry once
    before re-raising on `WorktreeMissingDependencyFileError`.

    Used by both `ensure_wt`/`_ensure_wt` branches -- fresh creation and
    RETAINED (already-existing) worktree on resume:

    - Retained: a dependency may have squash-merged (and had its branch
      deleted) after this worktree was created, so it never got carried at
      creation time -- every subsequent resume would otherwise re-hit the
      identical error forever, since the retained-worktree path previously
      only re-validated, never repaired.
    - Fresh creation: `add_stacked_worktree` already attempts the carry once
      at creation time; if that single attempt fails for any transient
      reason (e.g. a git fetch/lock error), the worktree is stuck missing the
      dependency's content with no second chance, crashing the whole run
      (brief 20260822-115008 -- gap 2 of the same defect class fixed for the
      retained-worktree branch by brief 20260817-223443's gap 1, below).

    `_carry_squash_merged_dependencies` is idempotent (no-ops via its own
    `merge-base --is-ancestor` check when nothing changed) and repairs the
    worktree in place via `git merge` -- no worktree recreation, so any
    in-progress work is preserved. Re-raises unchanged if the repair doesn't
    resolve it: the fail-loud backstop is unchanged for a genuinely
    unresolvable drift.

    On a successful repair, the returned list is prefixed with a
    `worktree_drift_repaired` event (plus a `checklist_conflict_resolved`
    event if the carry itself resolved a tasks.md conflict), followed by any
    `dependency_file_drift` events the re-check fires. If the re-check still
    raises, the exception propagates unchanged and no event is journaled for
    this attempt.
    """
    try:
        return _require_dependency_files(wt, task, by_id)
    except WorktreeMissingDependencyFileError:
        checklist_event = None
        if remote and base:
            checklist_event = _carry_squash_merged_dependencies(
                repo, spec_id, task, by_id, wt, remote, base
            )
        drift_events = _require_dependency_files(wt, task, by_id)
        repair_events = [
            {
                "event": "worktree_drift_repaired",
                "task": task["id"],
                "at": round(time.time(), 3),
            }
        ]
        if checklist_event is not None:
            repair_events.append(checklist_event)
        return repair_events + drift_events


def set_task_status_completed(path: Path) -> bool:
    """Compatibility wrapper for callers that still pass a legacy task file."""
    return taskformats.mark_status_completed(path)


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


def _apply_step_commit(
    *,
    tasks: list,
    entries: list,
    actives: dict,
    record_fn,
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
    transition. Caller must hold state_lock for the duration."""
    old, new = dispatch.apply_report(tasks, rep, role)
    report_fields = {k: rep.get(k) for k in orchestrate._REPORT_FIELDS}
    if new in ("escalated", "failed"):
        # dispatch.transition computes this status in-memory only -- stamp it onto
        # the entry that actually produced it, not only onto downstream
        # dependency-gate entries it blocks, so clear_tasks()'s terminal_status
        # match can find it. Covers both the review 3-strikes circuit breaker
        # ("escalated") and a normal role's terminal "failed" report (e.g. a
        # fix-role worker that legitimately declines an out-of-scope change) --
        # previously only "escalated" was stamped here, so clear_tasks() refused a
        # "failed" entry even though task status already read "failed".
        report_fields["terminal_status"] = new
    entry: dict = {
        "task": rep["task"],
        "role": rep["step"],
        "report": report_fields,
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
    record_fn()
    actives.pop(task["id"], None)
    return old, new


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
    purpose_tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    effort: str | None = None,
    run_budget: float | None = None,
    spawn=None,
    git_lock=None,
    notify_cmd: str | None = None,
    progress_interval: int | None = None,
    bootstrap_cmd: str | None = None,
    remote: str | None = None,
    base: str | None = None,
) -> dict:
    """Like live_run but operates on a REAL repo — no instantiate/copy.

    repo     — absolute path to the existing git repo (already on the base branch).
    spec_rel — spec folder relative to repo root, e.g. 'docs/specs/008-image-route'.
    remote/base — optional; when both given, threaded to `add_stacked_worktree` so
               a stacked worktree carries a DONE dependency's content across a
               squash-merge boundary (dependency's branch deleted). Absent
               (default) -> no carry, no behavior change.
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
    purpose_tier_map — optional {purpose: tier} table (routing.purpose_tiers),
               threaded straight into LiveSpawn's construction alongside tier_map
               (task-purpose-classification 5.1).
    fallback_chain — optional ordered fallback agent list; wins over the legacy
               single `fallback_agent` when configured (REQ-018).
    effort — optional run-level default effort, threaded straight into LiveSpawn's
               construction (model-tier-routing 3.3); a configured tier's own
               `effort` still wins per dispatch.agent_for's precedence.
    run_budget — optional whole-run wall-clock cap (s); once exceeded the fan-out
               stops dispatching NEW tasks. None -> RUN_BUDGET_DEFAULT (0 = off).

    The dependency-independent tasks of each frontier batch run CONCURRENTLY (each
    in its own worktree), bounded by max_workers -- the headline speed-up over the
    old one-at-a-time loop. Shared state (the journal, task statuses, the
    heartbeat) is mutated only under a lock; the slow `claude -p` spawns run
    outside it.
    """
    model = model or spawnlib.default_model_for_agent(agent)
    from . import verify as verify_module

    repo = repo.resolve()
    from ..router.gitnexus_preflight import check as gitnexus_check

    gitnexus_capability = gitnexus_check(repo)
    role_models = _effective_role_models(agent, role_models)
    _ar_agent, _ar_model = _role_agent_model(
        dispatch.ROLE_ASSEMBLY_RESOLVE, agent, model, role_agents, role_models
    )
    assembly_resolve_spawn_fn = verify_module._make_live_spawn(_ar_model, timeout, agent=_ar_agent)
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
        purpose_tier_map=purpose_tier_map,
        fallback_chain=fallback_chain,
        effort=effort,
    )
    # Set unconditionally (default-constructed or caller-injected, e.g. the
    # --fork-research spawn built by the live-run-real CLI handler) so
    # ROLE_IMPLEMENT prompts can surface a dependency's delivered files (see
    # dispatch.build_worker_prompt).
    if hasattr(spawn, "by_id"):
        spawn.by_id = by_id
    if hasattr(spawn, "external_deps_by_ref"):
        spawn.external_deps_by_ref = build_external_deps_by_ref(repo, tasks, spec_rel)
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
            foreign_ids = journal_foreign_task_ids(entries, tasks)
            if foreign_ids:
                raise RuntimeError(
                    f"Journal at {out_cassette} contains task id(s) "
                    f"{sorted(foreign_ids)} not present in --spec {spec_rel!r}. This "
                    f"journal likely belongs to a different spec/change whose trailing "
                    f"path name collides with this one. Re-run with --fresh to discard "
                    f"this journal and start clean."
                )
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
            journal_dict = {
                "spec_id": spec_id,
                "entries": entries,
                "gitnexus_capability": gitnexus_capability,
            }
            if run_id is not None:
                journal_dict["run_id"] = run_id
            if _budget_stopped_at[0] is not None:
                journal_dict["budget_stopped_at"] = _budget_stopped_at[0]
            _preserve_plan_pin(out_cassette, journal_dict)
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
                            repo,
                            spec_id,
                            task,
                            by_id,
                            wt,
                            **_add_stacked_worktree_kwargs(
                                add_stacked_worktree,
                                {
                                    "expected_head_sha": expected_head_sha,
                                    "assembly_resolve_spawn": assembly_resolve_spawn_fn,
                                    "remote": remote,
                                    "base": base,
                                },
                            ),
                        )
                    else:
                        add_stacked_worktree(
                            repo,
                            spec_id,
                            task,
                            by_id,
                            wt,
                            **_add_stacked_worktree_kwargs(
                                add_stacked_worktree,
                                {
                                    "assembly_resolve_spawn": assembly_resolve_spawn_fn,
                                    "remote": remote,
                                    "base": base,
                                },
                            ),
                        )
                    drift_events = _require_dependency_files_with_repair(
                        wt, task, by_id, repo, spec_id, remote, base
                    )
                    if drift_events:
                        with state_lock:
                            entries.extend(drift_events)
                            record()
                    _require_task_file(wt, spec_rel, task["id"])
            # Install deps into the fresh worktree OUTSIDE git_lock so a slow
            # `npm ci` never serializes other tasks' worktree creation.
            bootstrap_worktree(wt, bootstrap_cmd, required=bool(bootstrap_cmd))
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
            drift_events = _require_dependency_files_with_repair(
                wt, task, by_id, repo, spec_id, remote, base
            )
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
            old, new = _apply_step_commit(
                tasks=tasks,
                entries=entries,
                actives=actives,
                record_fn=record,
                task=task,
                role=role,
                rep=rep,
                t0=t0,
                t1=t1,
                usage=usage,
                tools_used=tools_used,
                skills_used=skills_used,
                agent=agent,
            )
            _publish_actives()
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
        _annotate_external_deps(repo, tasks, spec_rel)
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
    if with_tail and _budget_stopped_at[0] is None:
        # Tail tasks are serialized, but still form a dependency DAG. Never
        # start a cleanup/e2e task until every prerequisite is terminal-success;
        # failed prerequisites are journaled as blocked failures so the run cannot
        # silently advance past a failed acceptance gate.
        #
        # Gated on _budget_stopped_at[0] is None (i.e. the main loop exited
        # because the frontier genuinely emptied, not because run_budget cut
        # it off): below, any non-tail dep not yet in coordinator.DONE is
        # treated as a permanent failure on the premise that "the fan-out is
        # over, so a never-dispatched dep's status will never advance past
        # its initial value." That premise is false when run_budget stopped
        # the main loop early with real fan-out tasks still pending -- they
        # will run on the next resume, not never. Entering this loop anyway
        # would journal a `dependency-gate` failure with terminal_status
        # "failed" for any tail task depending on one of those tasks; on
        # every future resume, reconcile_from_journal replays that
        # terminal_status onto the tail task unconditionally (with no
        # re-check of whether the blocker has since completed), and drive()
        # then no-ops on it (its while-loop is guarded on status not in
        # orchestrate.TERMINAL) -- so the tail task is permanently stuck
        # failed even after its real blocker succeeds, recoverable only via
        # an explicit `clear-task` (handoff 20260824-023938). Skipping tail
        # evaluation here instead leaves pending tail tasks untouched, so the
        # next resume re-evaluates them once the fan-out actually completes.
        pending_tail = [t for t in tasks if t.get("kind") in ("e2e", "cleanup")]
        while pending_tail:
            progressed = False
            pending_tail_ids = {t["id"] for t in pending_tail}
            for task in list(pending_tail):
                unmet = [
                    dep
                    for dep in task.get("deps", [])
                    if dep in by_id and by_id[dep].get("status") not in coordinator.DONE
                ]
                # An unmet dep still queued in this tail loop may resolve on a
                # later iteration. Any other unmet dep -- explicitly failed/
                # escalated, or a non-tail task the main fan-out never
                # dispatched because ITS OWN prerequisite failed -- has
                # already had its final say: the fan-out is over, so a
                # never-dispatched dep's status will never advance past its
                # initial value. Treat both the same as a blocking failure
                # instead of waiting forever on a status that cannot change.
                blocked_by = [dep for dep in unmet if dep not in pending_tail_ids]
                waiting_on = [dep for dep in unmet if dep in pending_tail_ids]
                if waiting_on and not blocked_by:
                    continue
                if blocked_by:
                    reason = f"blocked by failed prerequisite(s): {', '.join(blocked_by)}"
                    now = time.time()
                    with state_lock:
                        task["status"] = "failed"
                        entries.append(
                            _journal_failure_entry(
                                task, "dependency-gate", reason, now, now, blocked_by=blocked_by
                            )
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
    elif with_tail:
        print(
            f"{_ts()} TAIL: deferred to next resume -- run budget stopped the fan-out "
            f"before it genuinely completed"
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
    purpose_tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    effort: str | None = None,
    run_budget: int | None = None,
    re_integrate: bool = False,
    smoke_cmd: str | None = None,
    post_merge_smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
    route: str | None = None,
    gates: str = "",
    migration_patterns: "list[str] | None" = None,
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
            purpose_tier_map=purpose_tier_map,
            fallback_chain=fallback_chain,
            effort=effort,
            re_integrate=re_integrate,
            smoke_cmd=smoke_cmd,
            post_merge_smoke_cmd=post_merge_smoke_cmd,
            bootstrap_cmd=bootstrap_cmd,
            merge_method=merge_method,
            pr_labels=pr_labels,
            pr_pacing_wait=pr_pacing_wait,
            route=route,
            gates=gates,
            migration_patterns=migration_patterns,
        )
    finally:
        _lock.release()


def _dispatch_pending_tail(
    repo: Path,
    spec_rel: str,
    journal_path: str,
    run_id: str,
    tasks: list,
    max_workers: int,
    agent: str,
    model: str | None,
    timeout: int,
    role_models: dict | None,
    role_agents: dict | None,
    fallback_agent: str | None,
    tier_map: dict | None,
    purpose_tier_map: dict | None,
    fallback_chain: "list[str] | None",
    effort: str | None,
    run_budget: int | None,
    bootstrap_cmd: str | None = None,
    spawn=None,
    remote: str | None = None,
    base: str | None = None,
    only: list | None = None,
) -> dict | None:
    """Dispatch pending e2e/cleanup tail tasks once the impl-group fan-out is
    integrated (`_pipeline_scheduler` reaches this point with every non-tail
    task already terminal). The scheduler never threads `with_tail=True`
    through to `live_run_real` on its own -- tail-kind tasks are excluded from
    `runnable_frontier` unconditionally, so without this call they are never
    dispatched by `full-real`, first run or resume alike (the journal's
    `pending_tail_tasks`/`pending_tail_reason` fields were bookkeeping with no
    consumer). A second `live_run_real` call is level-triggered by
    construction: it reloads task status fresh, and `drive()` no-ops on any
    task already terminal, so repeat calls (including a resume in a fresh
    process) are safe and idempotent.

    `remote`/`base` pass straight through to `live_run_real` (see its docstring):
    tail tasks are exactly the deterministic squash-merged-dependency case
    (`_dispatch_pending_tail` only fires once every group is integrated/verified/
    merged and cleaned up), so the call site supplies them.

    `only` — the scheduler's own `--only` restriction (list of kept task ids),
    threaded straight through to `live_run_real`'s pre-mark block. Without this,
    `live_run_real` reloads every task fresh from the TaskSource with no `only`
    filter, silently re-driving the FULL fan-out for every non-terminal task
    (including ones already merged via an earlier group's PR) instead of
    respecting the run's own scope restriction.

    Returns None when there is no tail work outstanding (no-op); otherwise the
    dict `live_run_real` returns for its `with_tail=True` pass.
    """
    if not coordinator.tail_held_out_task_ids(tasks):
        return None
    print(f"{_ts()} === TAIL: dispatching e2e/cleanup task(s) ===")
    return live_run_real(
        repo,
        spec_rel,
        max_workers=max_workers,
        out_cassette=journal_path,
        only=only,
        with_tail=True,
        agent=agent,
        model=model,
        timeout=timeout,
        resume=True,
        run_id=run_id,
        role_models=role_models,
        role_agents=role_agents,
        fallback_agent=fallback_agent,
        tier_map=tier_map,
        purpose_tier_map=purpose_tier_map,
        fallback_chain=fallback_chain,
        effort=effort,
        run_budget=run_budget,
        bootstrap_cmd=bootstrap_cmd,
        spawn=spawn,
        remote=remote,
        base=base,
    )


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
    post_merge_smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
    route: str | None = None,
    gates: str = "",
    agent: str = DEFAULT_AGENT,
    role_agents: dict | None = None,
    fallback_agent: str | None = None,
    tier_map: dict | None = None,
    purpose_tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    effort: str | None = None,
    re_integrate: bool = False,
    migration_patterns: "list[str] | None" = None,
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
    from ..router.gitnexus_preflight import check as gitnexus_check

    gitnexus_capability = gitnexus_check(repo)
    role_models = _effective_role_models(agent, role_models)
    spec_id, tasks = taskformats.load_spec(str(repo / spec_rel))
    tasks = apply_run_plan(repo, spec_rel, spec_id, tasks)
    pre_only_done = {t["id"] for t in tasks if t.get("status") in coordinator.DONE}
    if only and resume and Path(journal_path).exists():
        # A task can be genuinely already-done purely via a PRIOR run's journal
        # (frontmatter status alone won't show it -- frontmatter isn't rewritten
        # by run completion). Peek the journal the same way the real resume
        # block below does (reconcile_from_journal), but on a throwaway copy of
        # `tasks` so this doesn't affect or duplicate the real reconciliation
        # that happens later once the fan-out infrastructure is set up.
        try:
            _peek_journal = json.loads(Path(journal_path).read_text())
            _peek_tasks = copy.deepcopy(tasks)
            reconcile_from_journal(_peek_tasks, _peek_journal)
            pre_only_done |= {t["id"] for t in _peek_tasks if t.get("status") in coordinator.DONE}
        except (OSError, json.JSONDecodeError):
            pass  # best-effort; the real resume block's own error handling still applies
    for t in tasks:
        t.setdefault("retry_count", 0)
        if t.get("status") in coordinator.DONE:
            continue
        t["status"] = "pending"

    # Groups are computed from deps/files only (plan_groups never reads status),
    # so this is safe to call before the --only status override below and reused
    # unchanged afterward -- no behavior change for callers without --only.
    groups = coordinator.plan_groups(tasks, migration_patterns=migration_patterns or ())

    if only:
        keep = set(only)
        # --only's "mark excluded tasks completed" is meant for tasks ALREADY
        # integrated on a prior partial run (their worktree branch may no
        # longer exist -- see the identical live_run_real comment at ~2636).
        # Silently applying the same fake-"completed" to a task that never
        # actually ran is unsafe when that task shares a GROUP with an
        # --only-included task: deliverable_subset() treats "completed" as
        # ALREADY_INTEGRATED and drops it from the group's PR, so the group
        # gets judged integration-ready and merged/re-attempted with that
        # task's real, still-needed work silently missing -- no error, no
        # quarantine, just an incomplete PR (confirmed via
        # tests/orchestrator/test_pipeline_budget_partial_group.py,
        # ResumeWithOnlyExcludingPendingSiblingTest). Refuse instead: a task
        # that genuinely already finished before this invocation (in
        # `pre_only_done`) is still safely excludable either way.
        task_group = {tid: g["name"] for g in groups for tid in g["tasks"]}
        included_groups = {task_group[tid] for tid in keep if tid in task_group}
        for t in tasks:
            tid = t["id"]
            if tid in keep:
                continue
            if tid in pre_only_done:
                t["status"] = "completed"
                continue
            if task_group.get(tid) in included_groups:
                raise RuntimeError(
                    f"--only excludes task {tid!r}, which has not completed and "
                    f"shares group {task_group[tid]!r} with a task named in --only. "
                    "Faking it as already-integrated would silently drop its real "
                    "work from that group's PR. Add it to --only too, or wait for "
                    "it to finish on a plain resume first."
                )
            t["status"] = "completed"

    by_id = {t["id"]: t for t in tasks}

    # Spec-folder ownership: only the first independent
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
        purpose_tier_map=purpose_tier_map,
        fallback_chain=fallback_chain,
        effort=effort,
    )
    # See live_run_real's identical guard: surfaces dependency files to
    # ROLE_IMPLEMENT prompts, default-constructed or caller-injected alike.
    if hasattr(spawn_fn, "by_id"):
        spawn_fn.by_id = by_id
    if hasattr(spawn_fn, "external_deps_by_ref"):
        spawn_fn.external_deps_by_ref = build_external_deps_by_ref(repo, tasks, spec_rel)
    integrate_one_fn = (
        _integrate_one if _integrate_one is not None else integrate_module.integrate_one
    )
    if pr_pacing_wait > 0:
        # PR pacing: serialize the
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
            post_merge_smoke_cmd=post_merge_smoke_cmd,
            merge_lock=pm_merge_lock,  # shared across every per-group Verifier
            cumulative_regression=pm_cumulative_regression,  # ditto
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
    post_merge_regressed: dict = {}
    prs: list = []

    # Cumulative post-merge gate state (verify.py's `_merge_lock`/
    # `_cumulative_regression`): `_default_make_verifier` builds a FRESH
    # Verifier per group below, so this lock+dict must be constructed ONCE
    # here and injected into every one of them -- otherwise each group's
    # Verifier would carry its own isolated lock/flag and cross-group
    # serialization would silently do nothing in pipeline mode.
    pm_merge_lock = threading.Lock()
    pm_cumulative_regression: dict = {}

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
        return group_is_terminal(g, by_id, terminal_statuses)

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
                    _record_group_fn(
                        name, "", f"{run_id}/{name}", "QUARANTINED",
                        integrate_module.QUARANTINE_DEPENDENCY_QUARANTINED,
                    )
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
                        candidate, reason = _resolve_journaled_head_branch(name, journal_rec, run_id)
                        if reason:
                            quarantined[name] = reason
                        else:
                            group_branch[name] = candidate
                    prs.append((name, base, journal_rec.get("pr_url")))
                if name in quarantined:
                    _record_group_fn(
                        name, "", journal_rec.get("head_branch", ""), "QUARANTINED",
                        integrate_module.QUARANTINE_INTEGRATION_ERROR,
                    )
                    print(f"{_ts()}   !! GROUP [{name}] {quarantined[name]}")

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
                    if route is not None:
                        integrate_kwargs["route"] = route
                    if gates:
                        integrate_kwargs["gates"] = gates
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
                    _record_group_fn(
                        name, "", f"{run_id}/{name}", "QUARANTINED",
                        integrate_module.QUARANTINE_INTEGRATION_ERROR,
                        quarantine_detail=quarantined[name],
                    )
                    print(
                        f"{_ts()}   !! GROUP [{name}] integrate raised: {exc!r} -- quarantined, run continues"
                    )
                    return

            if name in quarantined:
                return  # quarantined by integrate_one, or resumed head_branch validation above
            if name not in group_branch:
                return  # resumed head_branch failed validation above
            if groups_journal.get(name, {}).get("state") == "MERGED":
                # integrate_one's own implicit-merge path (every task in this group was
                # already ALREADY_INTEGRATED) journals MERGED via _record_group_fn -- which
                # mutates this same groups_journal dict in place -- and sets group_branch so
                # dependents can stack on it, but opens no PR. Without this check, the group
                # fell through to verify_one against a branch that was never pushed/opened,
                # which always fails "no pull requests found" and wrongly quarantines an
                # already-fully-merged group (and cascades to every dependent group).
                with iv_lock:
                    prs.append((name, base, groups_journal[name].get("pr_url", "")))
                return

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
                    post_merge_regressed=post_merge_regressed,
                )
            except Exception as exc:
                with iv_lock:
                    quarantined[name] = f"verify exception: {exc!r}"
                _record_group_fn(
                    name, "", group_branch.get(name, f"{run_id}/{name}"), "QUARANTINED",
                    integrate_module.QUARANTINE_INTEGRATION_ERROR,
                    quarantine_detail=quarantined[name],
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
                elif name in quarantined:
                    # Ordinary (non-exception) verify-stage quarantine: verify_one()
                    # set quarantined[name] directly and returned (real CI failure or
                    # merge conflict), unlike the `except Exception` branch above which
                    # only catches a raised exception. Persist it the same way, so a
                    # pipeline-mode resume sees QUARANTINED instead of replaying the
                    # stale pre-verify journal record.
                    _record_group_fn(
                        name, "", group_branch.get(name, f"{run_id}/{name}"), "QUARANTINED",
                        integrate_module.QUARANTINE_INTEGRATION_ERROR,
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
            foreign_ids = journal_foreign_task_ids(entries, tasks)
            if foreign_ids:
                raise RuntimeError(
                    f"Journal at {journal_path} contains task id(s) "
                    f"{sorted(foreign_ids)} not present in --spec {spec_rel!r}. This "
                    f"journal likely belongs to a different spec/change whose trailing "
                    f"path name collides with this one. Re-run with --fresh to discard "
                    f"this journal and start clean."
                )
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
            _resume_quarantine_staleness_warning(repo, base, spec_id, groups, groups_journal)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{_ts()} PIPELINE RESUME: journal unreadable ({exc}); starting fresh")

    # Validate AFTER reconcile so only genuinely-undispatched (`pending`) tasks are
    # checked -- a resume replays the journal first (see live_run_real for rationale).
    validate_task_metadata(tasks)

    def _record() -> None:
        jdict: dict = {
            "spec_id": spec_id,
            "entries": entries,
            "gitnexus_capability": gitnexus_capability,
        }
        if run_id:
            jdict["run_id"] = run_id
        if groups_journal:
            jdict["groups"] = groups_journal
        if _budget_stopped_at[0] is not None:
            jdict["budget_stopped_at"] = _budget_stopped_at[0]
        _preserve_plan_pin(journal_path, jdict)
        progress.atomic_write_text(journal_path, json.dumps(jdict, indent=2, sort_keys=True) + "\n")

    def _record_group_fn(
        name: str, pr_url: str, head_branch: str, state: str, quarantine_reason: str = "",
        quarantine_detail: str = "",
    ) -> None:
        """Serialize one group's integrate result under state_lock (AC-015 / TASK-006)."""
        with state_lock:
            record = {"pr_url": pr_url, "head_branch": head_branch, "state": state}
            if quarantine_reason:
                record["quarantine_reason"] = quarantine_reason
            if quarantine_detail:
                record["quarantine_detail"] = quarantine_detail
            groups_journal[name] = record
            _record()

    def _ensure_wt(task: dict) -> Path:
        wt = wt_base / f"{spec_id}-{task['id'].lower()}"
        expected_head_sha = journaled_heads.get(task["id"])
        if not wt.exists():
            with git_lock:
                if not wt.exists():
                    # Same `assembly_resolve_spawn_fn` already constructed above for
                    # the integrate step (`integrate_kwargs["assembly_resolve_spawn"]`)
                    # -- one worker, reused here instead of building a second one.
                    if expected_head_sha:
                        add_stacked_worktree(
                            repo,
                            spec_id,
                            task,
                            by_id,
                            wt,
                            expected_head_sha=expected_head_sha,
                            assembly_resolve_spawn=assembly_resolve_spawn_fn,
                            remote=remote,
                            base=base,
                        )
                    else:
                        add_stacked_worktree(
                            repo,
                            spec_id,
                            task,
                            by_id,
                            wt,
                            assembly_resolve_spawn=assembly_resolve_spawn_fn,
                            remote=remote,
                            base=base,
                        )
                    drift_events = _require_dependency_files_with_repair(
                        wt, task, by_id, repo, spec_id, remote, base
                    )
                    if drift_events:
                        with state_lock:
                            entries.extend(drift_events)
                            _record()
                    _require_task_file(wt, spec_rel, task["id"])
            # Install deps into the fresh worktree OUTSIDE git_lock so a slow
            # `npm ci` never serializes other tasks' worktree creation.
            bootstrap_worktree(wt, bootstrap_cmd, required=bool(bootstrap_cmd))
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
            drift_events = _require_dependency_files_with_repair(
                wt, task, by_id, repo, spec_id, remote, base
            )
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
            return _apply_step_commit(
                tasks=tasks,
                entries=entries,
                actives=actives,
                record_fn=_record,
                task=task,
                role=role,
                rep=rep,
                t0=t0,
                t1=t1,
                usage=usage,
                tools_used=tools_used,
                skills_used=skills_used,
                agent=agent,
            )

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
        _annotate_external_deps(repo, tasks, spec_rel)
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
                reason = _quarantine_reason_with_diagnosis(
                    "fan-out incomplete (run budget exceeded)", g, by_id, terminal_statuses
                )
                with iv_lock:
                    quarantined[gname] = reason
                    _group_phase_map.pop(gname, None)
                _record_group_fn(
                    gname, "", f"{run_id}/{gname}", "QUARANTINED",
                    integrate_module.QUARANTINE_BUDGET_EXHAUSTED,
                )
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
                reason = _quarantine_reason_with_diagnosis(
                    "fan-out incomplete (run budget or error)", g, by_id, terminal_statuses
                )
                with iv_lock:
                    quarantined[gname] = reason
                    _group_phase_map.pop(gname, None)
                _record_group_fn(
                    gname, "", f"{run_id}/{gname}", "QUARANTINED",
                    integrate_module.QUARANTINE_TASK_FAILURE,
                )
                group_done_events[gname].set()
                dispatched_groups.add(gname)

    iv_pool.shutdown(wait=True)

    done_tasks = sum(1 for t in tasks if t["status"] in coordinator.DONE)
    print(f"{_ts()} PIPELINE FAN-OUT DONE: {done_tasks}/{len(tasks)} tasks terminal")

    if quarantined:
        reasons = ", ".join(f"{k}: {v}" for k, v in quarantined.items())
        print(f"{_ts()} NOTE: {len(quarantined)} group(s) quarantined for human review: {reasons}")
    migration_warning = _format_migration_quarantine_warning(
        groups, tasks, migration_patterns, quarantined
    )
    if migration_warning:
        print(f"{_ts()} {migration_warning}")
    if post_merge_regressed:
        print(
            f"{_ts()} !! POST-MERGE REGRESSION: {len(post_merge_regressed)} group(s) "
            f"merged, then failed the cumulative post-merge smoke check against the "
            f"updated base -- human review required, no automatic revert: "
            f"{', '.join(post_merge_regressed)}"
        )
    integrate_complete = integrate_module._mark_integrate_complete_if_terminal(
        journal_path, groups, tasks
    )
    tail_res = None
    if integrate_complete:
        progress.set_phase(journal_path, "tail")
        tail_res = _dispatch_pending_tail(
            repo,
            spec_rel,
            journal_path,
            run_id,
            tasks,
            max_workers,
            agent,
            model,
            timeout,
            role_models,
            role_agents,
            fallback_agent,
            tier_map,
            purpose_tier_map,
            fallback_chain,
            effort,
            run_budget,
            bootstrap_cmd=bootstrap_cmd,
            spawn=spawn_fn,
            remote=remote,
            base=base,
            only=only,
        )
        if tail_res is not None:
            integrate_module._mark_integrate_complete_if_terminal(
                journal_path, groups, tail_res["tasks"]
            )
    unreconciled_tail = integrate_module.detect_unreconciled_evidence(
        repo, remote, base, spec_id, wt_base, (tail_res or {}).get("tasks", tasks)
    )
    if unreconciled_tail:
        unreconciled_tail = integrate_module.reconcile_unreconciled_tail_evidence(
            unreconciled_tail,
            repo,
            spec_id,
            (tail_res or {}).get("tasks", tasks),
            remote,
            run_id,
            base,
            journal_path,
            pr_labels=pr_labels,
            route=route,
            gates=gates,
        )
    integrate_module._record_unreconciled_tail_evidence(journal_path, unreconciled_tail)
    unreconciled_note = _format_unreconciled_tail_note(unreconciled_tail)
    if unreconciled_note:
        print(f"{_ts()} {unreconciled_note}")
    progress.set_phase(journal_path, "done")
    _print_usage_report(journal_path)
    print(f"{_ts()} === PIPELINE RUN COMPLETE ===")
    return {
        "group_prs": prs,
        "final": None,
        "quarantined": quarantined,
        "merged": merged,
        "post_merge_regressed": post_merge_regressed,
        "unreconciled_tail_evidence": unreconciled_tail,
    }


def _record_verify_outcomes(
    journal_path: str, vres: dict, group_branch: dict, run_id: str
) -> None:
    """Persist verify's merge outcomes into the journal on the --from-verify path.

    The pipeline scheduler already stamps MERGED / AUTOMERGE_ARMED per group
    right after verify (see _integrate_verify_group); the --from-verify entry
    point calls verify_and_cleanup outside that flow, so without this a
    fully-merged --from-verify run's journal permanently claimed every group
    was still OPEN (originally found on the deleted serial scheduler by the
    lifecycle harness's first real-verify run). Same confirmed-merge semantics
    as the pipeline path: only vres["merged"] gets MERGED; a group only queued
    for auto-merge gets AUTOMERGE_ARMED (non-terminal, so a resume re-verifies
    it).
    """
    from . import integrate

    try:
        groups_j = json.loads(Path(journal_path).read_text()).get("groups", {})
    except (OSError, json.JSONDecodeError):
        groups_j = {}

    def _stamp(name: str, state: str) -> None:
        rec = groups_j.get(name, {})
        integrate._write_group_journal(
            journal_path,
            name,
            rec.get("pr_url", ""),
            group_branch.get(name) or rec.get("head_branch", f"{run_id}/{name}"),
            state,
        )

    for name in vres.get("merged", []):
        _stamp(name, "MERGED")
    for name in vres.get("automerge_armed", []):
        _stamp(name, "AUTOMERGE_ARMED")


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
    purpose_tier_map: dict | None = None,
    fallback_chain: "list[str] | None" = None,
    effort: str | None = None,
    re_integrate: bool = False,
    smoke_cmd: str | None = None,
    post_merge_smoke_cmd: str | None = None,
    bootstrap_cmd: str | None = None,
    merge_method: str | None = None,
    pr_labels: list[str] | None = None,
    pr_pacing_wait: int = 0,
    route: str | None = None,
    gates: str = "",
    migration_patterns: "list[str] | None" = None,
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
    effort      — optional run-level default effort, threaded to every LiveSpawn
                  this run constructs.
    purpose_tier_map — optional {purpose: tier} table (routing.purpose_tiers),
                  threaded to every LiveSpawn this run constructs alongside
                  tier_map (task-purpose-classification 5.1).
    migration_patterns — optional fnmatch glob list (worktrail-go-policy.yaml's
                  migration_path_patterns) identifying schema-migration file paths;
                  any task touching one is folded into coordinator.plan_groups()'s
                  BASE group so it can't be quarantined independently of code that
                  depends on the tables it creates. `()`/None = no behavior change.
    post_merge_smoke_cmd — optional shell command re-run against the ACTUAL
                  updated base HEAD immediately after each group's PR CONFIRMS
                  merged, before the next independent group in this run is
                  allowed to merge (verify.py's cumulative post-merge gate).
                  None = gate disabled, no behavior change. See
                  policy.resolve_post_merge_smoke_cmd() for the worktrail-go-policy.yaml
                  fallback to integrate_smoke_cmd.

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
    # first integrate_one of this run/resume (brief 20260723-171500).
    _refresh_base_branch(repo, remote, base)

    # Journal lives beside the worktrees (OUTSIDE the repo, so the base checkout is
    # never dirtied) and is keyed by spec folder name so re-runs find it.
    wt_base = repo.parent / f"{repo.name}-worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    journal_path = str(journal_path_for(repo, spec_rel))
    if not resume and Path(journal_path).exists():
        Path(journal_path).unlink()  # --fresh: discard prior progress
        print(f"{_ts()} FRESH: discarded prior journal {journal_path}")

    def _persist_newly_quarantined(quarantined_names, run_id_fallback: str) -> None:
        """Persist newly-quarantined groups so a later resume sees QUARANTINED
        instead of replaying the pre-verify journal state (matches the pipeline
        path's _record_group_fn behavior in _integrate_verify_group). Serves the
        --from-verify branch below, which runs verify outside the pipeline flow.
        """
        for _qname in quarantined_names:
            integrate._write_group_journal(
                journal_path, _qname, "", group_branch.get(_qname, f"{run_id_fallback}/{_qname}"),
                "QUARANTINED", integrate.QUARANTINE_INTEGRATION_ERROR,
            )

    # Foreign-journal guard, fired directly in _full_real_inner's own resume
    # block (spec-path-task-crosscheck 1.2) so it covers both paths below --
    # --from-verify and the pipeline scheduler -- before either reconciles a
    # journal from a different --spec onto this run's tasks. live_run_real /
    # _pipeline_scheduler carry the identical guard on their own
    # reconcile_from_journal() call too, for callers that reach those
    # functions directly (e.g. the `live-run-real` CLI command) without going
    # through this function.
    if resume and Path(journal_path).exists():
        try:
            _resume_journal = json.loads(Path(journal_path).read_text())
        except (OSError, json.JSONDecodeError):
            _resume_journal = None
        if _resume_journal is not None:
            _, _resume_tasks = taskformats.load_spec(str(repo / spec_rel))
            reconcile_from_journal(_resume_tasks, _resume_journal)
            foreign_ids = journal_foreign_task_ids(
                _resume_journal.get("entries", []), _resume_tasks
            )
            if foreign_ids:
                raise RuntimeError(
                    f"Journal at {journal_path} contains task id(s) "
                    f"{sorted(foreign_ids)} not present in --spec {spec_rel!r}. This "
                    f"journal likely belongs to a different spec/change whose trailing "
                    f"path name collides with this one. Re-run with --fresh to discard "
                    f"this journal and start clean."
                )

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

        groups = coordinator.plan_groups(tasks, migration_patterns=migration_patterns or ())

        # Reconstruct group_branch from journal's per-group integrate records.
        # Passing `groups` lets a stale parent group -- every task it ever
        # bundled already merged/PR-opened individually via its own tail-<id>
        # group -- get pruned here instead of chased as if it still had live
        # work to verify.
        journal_groups = journal.get("groups", {})
        group_branch, from_verify_quarantined = _group_branch_from_journal(
            journal_groups, journal.get("run_id", "unknown"), groups=groups
        )

        progress.set_phase(journal_path, "verify")
        print("=== VERIFY (from journal, skipping fan-out + integrate) ===")
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
            post_merge_smoke_cmd=post_merge_smoke_cmd,
            # Without these the deny-list falls back to devkit's spec root,
            # leaving an OpenSpec run's own tree unguarded.
            spec_rel=spec_rel,
            declared_files=coordinator.declared_files_by_group(groups, tasks),
        )
        quarantined = {**from_verify_quarantined, **vres.get("quarantined", {})}
        _persist_newly_quarantined(vres.get("quarantined", {}), journal.get("run_id", "unknown"))
        _record_verify_outcomes(
            journal_path, vres, group_branch, journal.get("run_id", "unknown")
        )
        self_merged = vres.get("self_merged", {})
        post_merge_regressed = vres.get("post_merge_regressed", {})
        automerge_evidence = vres.get("automerge_evidence", {})
        progress.append_safety_net_events(
            journal_path,
            _safety_net_events_from_preflight_fallbacks(vres.get("preflight_fallbacks", {})),
        )

        if quarantined:
            reasons = ", ".join(f"{k}: {v}" for k, v in quarantined.items())
            print(f"NOTE: {len(quarantined)} group(s) quarantined for human review: {reasons}")
        migration_warning = _format_migration_quarantine_warning(
            groups, tasks, migration_patterns, quarantined
        )
        if migration_warning:
            print(migration_warning)
        if self_merged:
            print(
                f"!! SELF-MERGE VIOLATION: {len(self_merged)} group(s) merged by a worker "
                f"itself, not the orchestrator (worktrees kept): {', '.join(self_merged)}"
            )
        if post_merge_regressed:
            print(
                f"!! POST-MERGE REGRESSION: {len(post_merge_regressed)} group(s) merged, "
                f"then failed the cumulative post-merge smoke check against the updated "
                f"base -- human review required, no automatic revert: "
                f"{', '.join(post_merge_regressed)}"
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
            "post_merge_regressed": post_merge_regressed,
            "automerge_evidence": automerge_evidence,
        }

    # Single scheduler: the pipelined engine. scheduler-consolidation stage 2
    # deleted the legacy serial path (full fan-out, then INTEGRATE, then
    # VERIFY); every full-real run now routes here after the --from-verify
    # branch above.
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
        purpose_tier_map=purpose_tier_map,
        fallback_chain=fallback_chain,
        effort=effort,
        run_budget=run_budget,
        journal_path=journal_path,
        run_id=run_id,
        smoke_cmd=smoke_cmd,
        post_merge_smoke_cmd=post_merge_smoke_cmd,
        bootstrap_cmd=bootstrap_cmd,
        merge_method=merge_method,
        pr_labels=pr_labels,
        pr_pacing_wait=pr_pacing_wait,
        route=route,
        gates=gates,
        migration_patterns=migration_patterns,
    )


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
    # model-tier-routing 3.3: this function has no `effort` equivalent, and none
    # is needed -- verified, not assumed. Its whole job is auto-populating a
    # PER-ROLE MODEL override for codex (every codex role otherwise shares one
    # model, see the module comment above LiveSpawn) when the caller passed
    # none, working around a gap in `role_models`'s own precedence (it has no
    # per-agent default to fall back to, unlike `self.model`). A per-role
    # EFFORT override already exists through a different, already-shipped path:
    # `role_agents` (routing.roles / --role-agent-map) entries carry their own
    # `effort` key, resolved by `dispatch.agent_for()` into `resolved["effort"]`
    # and consumed directly in `LiveSpawn.spawn()` -- see
    # `test_role_override_effort_reaches_spawn_agent` in test_live_extras.py.
    # `role_models`/`_effective_role_models()` is a separate, narrower {role:
    # model} convenience surface with no `role_agents`-equivalent precedence
    # gap for effort to fill, so there is nothing for this function to thread.
    if role_models is not None:
        return role_models
    if agent == "codex":
        model = spawnlib.default_model_for_agent("codex")
        return {role: model for role in _CODEX_DEFAULT_ROLES}
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
        "--effort",
        default=None,
        help="Run-level default reasoning effort (e.g. 'high'), threaded to every "
        "LiveSpawn this run constructs; a configured tier's own effort still wins "
        "per dispatch.agent_for's precedence (model-tier-routing 3.3). Omit for "
        "no effort flag (pre-spec behavior).",
    )
    fr.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Fan-out width: concurrent task worktrees with live agent workers. "
        "Sourced from worktrail-go-policy.yaml's max_workers by the sdd-workflow conductor "
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
        "Sourced from worktrail-go-policy.yaml's routing.tiers by the sdd-workflow conductor "
        "(policy.py's resolve_tier_map()); a domain-less tier (resolve_tier_map()'s "
        "(complexity, None) key) has no CLI-string representation -- _parse_tier_map() "
        "always yields an empty-string domain, never None -- so domain-less tiers only "
        "reach dispatch.agent_for via a native tier_map dict, not this flag (TASK-CHG-002). "
        "Omit for pre-spec behavior (REQ-016).",
    )
    fr.add_argument(
        "--purpose-tier-map",
        dest="purpose_tier_map",
        default=None,
        help="Resolved purpose-to-tier table, e.g. 'security-review=t1,bulk-mechanical=t4'. "
        "Each entry is 'purpose=tier'; a matched task's tier (looked up in --tier-map) is "
        "the mapped tier instead of its complexity, for implement/fix/cleanup spawns only "
        "(task.get('purpose') wins over task.get('complexity') when it resolves here). "
        "Never consulted for review/resolve/ci-fix/assembly-resolve (DEC-003). Sourced from "
        "worktrail-go-policy.yaml's routing.purpose_tiers via policy.py's resolve_routing() "
        "(task-purpose-classification 3.2/5.1). Omit for pre-spec behavior (REQ-016).",
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
        default=True,
        help="Pipelined run (the ONLY scheduler since the serial path's removal): "
        "integrate and verify each group as its tasks finish, overlapping with "
        "continued fan-out of later groups. The flag is kept for compatibility; "
        "it is a no-op affirmation.",
    )
    fr.add_argument(
        "--sequential",
        action="store_true",
        default=False,
        help="REMOVED: the legacy serial scheduler was deleted "
        "(openspec/changes/scheduler-consolidation/). Passing this flag is a hard "
        "error; the pipelined engine is the only scheduler.",
    )
    fr.add_argument(
        "--re-integrate",
        action="store_true",
        dest="re_integrate",
        help="Force re-integration: clear the journal's integrate_complete marker and "
        "per-group records so integration runs again (reconcile-safe). Use after a failed "
        "integration instead of hand-editing the journal JSON.",
    )
    fr.add_argument(
        "--smoke-cmd",
        default=None,
        dest="smoke_cmd",
        help="Shell command run on each group's integration branch before its PR opens "
        "(e.g. 'pytest -q' or 'cd app && npm ci && npm test'); a non-zero exit quarantines "
        "the group. Omit to auto-resolve from worktrail-go-policy.yaml (pre_pr_cmd, falling back to "
        "integrate_smoke_cmd -- same precedence as pre_pr_gate.py); explicit --smoke-cmd "
        "always wins over policy. Repos with neither key configured are unaffected "
        "(never blocked) -- this only closes the gap of forgetting to pass the flag on "
        "an already-configured repo, not repos with no gate configured at all.",
    )
    fr.add_argument(
        "--post-merge-smoke-cmd",
        default=None,
        dest="post_merge_smoke_cmd",
        help="Shell command re-run against the ACTUAL updated base HEAD immediately "
        "after each group's PR CONFIRMS merged, before the next independent group in "
        "this run is allowed to merge; a non-zero exit blocks every remaining group "
        "from merging (the merge that triggered it already landed and is not reverted "
        "automatically). Omit to auto-resolve from worktrail-go-policy.yaml (post_merge_smoke_cmd, "
        "falling back to integrate_smoke_cmd); explicit --post-merge-smoke-cmd always "
        "wins over policy. Repos with neither key configured are unaffected (gate "
        "skipped entirely, identical to pre-existing behavior).",
    )
    fr.add_argument(
        "--bootstrap-cmd",
        default=None,
        dest="bootstrap_cmd",
        help="Shell command run in each freshly-created task worktree right after it is "
        "created, before a worker is spawned into it, to install local dependencies "
        "(e.g. 'npm ci' or 'cd app && npm ci'). Task worktrees branch off the base commit "
        "and start without the base checkout's node_modules. Sourced from worktrail-go-policy.yaml's "
        "worktree_bootstrap_cmd by the sdd-workflow conductor; omit to skip. Non-fatal: a "
        "failed install is logged and the worker still self-installs.",
    )
    fr.add_argument(
        "--migration-pattern",
        action="append",
        dest="migration_patterns",
        default=None,
        help="fnmatch glob (repeatable) identifying a schema-migration file path for this "
        "repo (e.g. 'api/migrations/versions/*.py'). Any task whose declared files match "
        "one is always folded into the parallel-orchestrator's BASE integration group, "
        "even if its dependency graph would otherwise place it in an independent feature "
        "group -- a migration and the code that depends on the tables it creates rarely "
        "share a files entry, so a migration quarantined on its own can silently leave "
        "consumer code merged against tables that don't exist. Sourced from "
        "worktrail-go-policy.yaml's migration_path_patterns by the sdd-workflow conductor; omit to "
        "skip (no behavior change).",
    )
    fr.add_argument(
        "--pr-pacing-wait",
        type=int,
        default=0,
        dest="pr_pacing_wait",
        help="Seconds to wait (bounded) for the previous group PR's checks to resolve "
        "before opening the next group's PR, so sibling PRs don't hit a shared CI "
        "runner pool simultaneously (0 = off, the default). Best-effort: a red, "
        "stuck, or check-less PR never blocks integration beyond this bound. "
        "Serializes only the integrate+PR-open step across the concurrent "
        "per-group IV threads; verify still overlaps. Sourced from "
        "worktrail-go-policy.yaml's pr_pacing_wait_s by the sdd-workflow conductor.",
    )
    fr.add_argument(
        "--merge-method",
        default=None,
        dest="merge_method",
        choices=("merge", "squash", "rebase"),
        help="Merge method for auto_merge() to use for THIS base branch, overriding "
        "verify.py's repo-wide GitHub-settings detection. Sourced from worktrail-go-policy.yaml's "
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
        "--route",
        default=None,
        help="Classified route letter for this run, forwarded to the group-PR label "
        "refresh's --route so policy's require_human_routes check applies to "
        "orchestrator-created PRs. Sourced from the GO run record by the sdd-workflow "
        "conductor; omit when unknown.",
    )
    fr.add_argument(
        "--gates",
        default="",
        help="Comma-separated classifier gates for this run, forwarded to the group-PR "
        "label refresh's --gates for the same eligibility check as the one-off PR path.",
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
    ct = sub.add_parser(
        "clear-task",
        help="Surgically remove a task's failed/escalated journal entries (cascading to "
        "dependency-gate entries it blocked) so the next full-real resume re-dispatches "
        "just that task -- without --fresh discarding every other task's completed work",
    )
    ct.add_argument("--repo", required=True, help="Absolute path to the real git repo")
    ct.add_argument(
        "--spec", required=True, help="Spec folder relative to repo root, e.g. docs/specs/008-foo"
    )
    ct.add_argument("--tasks", required=True, help="Comma-separated task IDs to clear")

    args = p.parse_args(argv)
    if getattr(args, "model", None) is None:
        args.model = spawnlib.default_model_for_agent(getattr(args, "agent", DEFAULT_AGENT))
    role_models = _effective_role_models(
        getattr(args, "agent", DEFAULT_AGENT), _parse_model_map(getattr(args, "model_map", None))
    )
    role_agents = _parse_model_map(getattr(args, "role_agent_map", None))
    tier_map = _parse_tier_map(getattr(args, "tier_map", None))
    # {purpose: tier} is a plain string-to-string map, the same "key=value,..." shape
    # _parse_model_map already parses (task-purpose-classification 5.1) -- no
    # dedicated parser needed, unlike --tier-map's agent-entry-shaped value.
    purpose_tier_map = _parse_model_map(getattr(args, "purpose_tier_map", None))
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
        if args.sequential:
            print(
                f"{_ts()} ERROR: --sequential was removed; the legacy serial scheduler "
                "no longer exists (openspec/changes/scheduler-consolidation/). The "
                "pipelined engine is the only scheduler -- re-run without --sequential."
            )
            return 2
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        smoke_cmd = args.smoke_cmd
        if smoke_cmd is None:
            smoke_cmd = _default_smoke_cmd(Path(args.repo))
        post_merge_smoke_cmd = args.post_merge_smoke_cmd
        if post_merge_smoke_cmd is None:
            post_merge_smoke_cmd = _default_post_merge_smoke_cmd(Path(args.repo))
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
            purpose_tier_map=purpose_tier_map,
            fallback_chain=fallback_chain,
            effort=args.effort,
            run_budget=args.run_budget * 60 if args.run_budget else args.run_budget,
            re_integrate=args.re_integrate,
            smoke_cmd=smoke_cmd,
            post_merge_smoke_cmd=post_merge_smoke_cmd,
            bootstrap_cmd=args.bootstrap_cmd,
            merge_method=args.merge_method,
            pr_labels=args.pr_labels,
            pr_pacing_wait=args.pr_pacing_wait,
            route=args.route,
            gates=args.gates,
            migration_patterns=args.migration_patterns,
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
    if args.cmd == "clear-task":
        task_ids = [s.strip() for s in args.tasks.split(",") if s.strip()]
        if not task_ids:
            print("clear-task: --tasks must list at least one task ID")
            return 1
        return clear_tasks(Path(args.repo).resolve(), args.spec, task_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())

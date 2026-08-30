#!/usr/bin/env python3
"""
`sdd-workflow` conductor -- resume dashboard / state detector.

Pure file inspection: scan `docs/specs/*/`, compute each spec's lifecycle stage
and the next action, emit a table (human) or JSON (machine). No git, no network,
no agents -- deterministic and unit-testable against fixtures.

Grounding (verified against the real repos under ~/projects, not assumed -- the
on-disk specs use several artifact conventions across naming eras, so detection
is by EXCLUSION + CONTENT, never a single strict filename pattern):
  - spec doc:               any top-level `*.md` that is not a known auxiliary file.
                            Canonical is `YYYY-MM-DD--<name>.md` (specs.brainstorm),
                            but legacy/manual folders use `spec.md`, `SPEC.md`,
                            `brainstorm.md`, or `<name>-specs.md`. find_spec_file()
                            recognizes all of them (dated wins, newest first).
  - auxiliary (NOT spec):   user-request.md, brainstorming-notes.md, decision-log.md,
                            traceability-matrix.md, data-model.md, technical-plan.md,
                            spec-check.md, *--tasks.md, *-review.md, knowledge-graph.json.
  - unspec'd backlog:       a folder with `user-request.md` but no spec doc -- a feature
                            seeded by brainstorm (which writes user-request.md first) but
                            never carried through to a spec. ~44 such folders exist in
                            datalena; they are the "features not spec'd" input.
  - task DAG:               docs/specs/[id]/tasks/TASK-*.md (status: pending|completed),
                            UNIONED with every docs/specs/[id]/changes/<name>/tasks/TASK-*.md
                            (a change-spec's own tail tasks must not be shadowed just
                            because the parent spec's top-level tasks/ is already
                            complete -- see brief 20260713-201900).
  - clarification gate:     unresolved `[NEEDS CLARIFICATION: ...]` markers in the spec
                            doc -- the thing specs.spec-check actually consumes. A
                            `## Clarifications` HEADING is NOT a reliable signal (only
                            ~22/46 real dated specs carry one; spec-check resolves markers
                            in place and small-scope specs skip it entirely), so the gate
                            keys on markers, not the heading.
  - Backfill convention:    a `Status: Backfill` / `**Status**: Backfill` line in the
                            spec file. NOTE: `Status:` is NOT part of the upstream
                            brainstorm template -- it's a project convention, so it is
                            treated as *optional*; absence falls through to artifact
                            detection.

State precedence (task existence dominates everything below the backfill check -- a
tasked folder is past the spec stage even if its spec doc has an unrecognized name,
and a small-scope spec may have tasks with no clarification markers, so it must not
be bounced back to spec-check):

    1. Status: Backfill                         -> done       (never flag spec-to-tasks)
    2. has tasks, real pending impl             -> orchestrator (+ verify/tail/sync/
                                                                  complete sub-states)
    2a. pending impl tasks whose files are ALL  -> stale-bookkeeping (confirm & close;
        already merged on the base branch           files shipped, status never flipped --
        (stale status, not real work)               re-running the orchestrator would
                                                     re-implement merged code)
    3. has tasks, all completed                 -> complete    (-> open PR / sync; merge
                                                                 state is git, not files)
    4. no tasks, no spec doc, has user-request  -> unspecd     (brainstorm; backlog)
    5. no tasks, no spec doc, no user-request   -> empty       (brainstorm)
    6. no tasks, [NEEDS CLARIFICATION] markers  -> spec-check
    7. no tasks, no markers                     -> spec-to-tasks (technical-plan optional)
"""

from __future__ import annotations

import argparse
import datetime
import functools
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent

# Reuse the orchestrator's task loader (same package) so we don't duplicate
# the changes/<name>/ resolution + review-file skipping.
from ..taskformats import resolve as _taskformats
from ..taskformats.devkit.source import parse_frontmatter as _parse_fm

try:
    from ..conductor import compile as _compile
    from ..conductor import runplan as _runplan
except ImportError:  # pragma: no cover - defensive degradation for partial installs
    _compile = None
    _runplan = None

_HAVE_LOADER = True

# resolve_repo is a sibling module; reused so the multi-repo overview
# (`--repos`) detects candidate repos exactly as Step 0 does.
from ..orchestrator.agent_capacity import (
    gate_snapshot as _capacity_gate_snapshot,
)
from ..orchestrator.agent_capacity import (
    provider_key as _capacity_provider_key,
)

# routing_candidates is the pure targets/tiers -> candidate-catalog projection
# (task 2.2); the capacity gate's provider-key set and the capacity line's
# per-cell labels are both derived from it, never from the removed
# `routing.agents` table.
from ..runtime.routing_source import routing_candidates as _routing_candidates

# select_cell resolves the "planned agent" annotation's default-tier winner
# (task 2.1); route/risk no longer select routing (routing.defaults was
# retired by the target/tier/role redesign), so this reports the operator's
# configured default tier's winning target, not a route/risk-keyed lookup.
from ..runtime.selection import select_cell as _select_cell
from ..shared.homedir import env_setting, worktrail_home

# cluster_detect is a sibling module (spec 018).
# cluster_telemetry is a sibling module (spec 018 change: cluster-precision-telemetry).
from . import cluster_detect, cluster_telemetry

# audit_postmerge is a sibling module (spec post-merge-reconciliation-audit):
# its dashboard_snapshot() is a pure state-file read (no `gh` calls), reused
# here rather than re-reading the persisted state a second way.
from .audit_postmerge import (
    dashboard_snapshot as _postmerge_dashboard_snapshot,
)
from .audit_postmerge import (
    resolve_state_dir as _postmerge_resolve_state_dir,
)

# automerge_selfcheck is a sibling module (route:J automerge-label-gate audit).
from .automerge_selfcheck import check_repo as _automerge_check_repo

# check_cache_freshness's ancestor-walk is reused by _dashboard_repo_root() to
# find the true repo root across install topologies.
from .check_cache_freshness import _find_git_root

# check_repo_freshness is a sibling module (route:J go-dashboard-local-only-
# git-staleness). NOT called from scan()/detect_stage()/scan_repos() -- those
# stay pure file inspection per this module's own docstring ("No git, no
# network"). Only main() calls it, and only when --check-freshness is passed
# (opt-in: a `git fetch` per displayed repo is real network cost that a bare
# `/go` render should not pay by default).
from .check_repo_freshness import check as _check_repo_freshness

# journal_selfcheck is a sibling module (brief 20260808-210929): stranded-run
# invariants (integrate_complete with undispatched tail; malformed journals).
from .journal_selfcheck import check_repo as _journal_check_repo

# Policy routing is used to annotate picker items and, via _routing_configured_providers,
# to derive the provider set fed to the capacity gate snapshot below.
from .policy import DEFAULTS as _POLICY_DEFAULTS
from .policy import load_policy as _load_policy
from .policy import resolve_routing as _resolve_routing

# policy_drift_selfcheck is a sibling module (route:A go-policy-drift-guard).
from .policy_drift_selfcheck import check_repo as _policy_drift_check_repo

# policy_selfcheck is a sibling module (route:J go-policy-integrity-guards audit).
from .policy_selfcheck import check_repo as _policy_check_repo
from .policy_selfcheck import discover_repo_names as _discover_repo_names

# quarantine_selfcheck is a sibling module (spec quarantined-group-visibility).
from .quarantine_selfcheck import check_repo as _quarantine_check_repo
from .resolve_repo import is_git_repo as _is_git_repo
from .resolve_repo import list_candidate_repos as _list_candidate_repos

# run_record is a sibling module (route:J go-dashboard-run-record-history):
# its YAML loader is reused so recent /go run outcomes can be surfaced
# without duplicating the parser.
from .run_record import _load as _load_run_record

# --- spec file discovery -----------------------------------------------------

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}--")

# Files that live in a spec folder but are NOT the spec doc. Matched
# case-insensitively against the bare filename. The brainstorm/spec-check/
# spec-to-tasks commands all write these alongside the spec; treating any of
# them as "the spec" is the bug that mislabels tasked folders as empty.
_AUX_EXACT = {
    "user-request.md",
    "brainstorming-notes.md",
    "decision-log.md",
    "traceability-matrix.md",
    "data-model.md",
    "technical-plan.md",
    "spec-check.md",
    "knowledge-graph.json",
    "architecture.md",
    "ontology.md",
    "readme.md",
    "caching-guardrails.md",
    "coverage-matrix.md",
    "work-plan.md",
    "tasks.md",
}
# Stem SUFFIXES that mark a generated/auxiliary doc even with a date prefix:
# `2026-06-09--x--tasks.md`, `2026--x--review.md`, `2026-05-30--technical-plan.md`.
# These MUST be end-anchored, not substring -- a spec named
# `…--orchestrator-review-writer-path` legitimately contains "-review" mid-name and
# must NOT be rejected (this exact case mislabeled spec 004 as empty).
_AUX_SUFFIX = (
    "--tasks",
    "--review",
    "-review",
    "technical-plan",
    "brainstorming-notes",
    "decision-log",
    "traceability-matrix",
)


def _is_spec_doc(name: str) -> bool:
    """A spec-doc candidate: a `.md` that is not a known auxiliary artifact."""
    low = name.lower()
    if not low.endswith(".md") or low in _AUX_EXACT:
        return False
    stem = low[:-3]
    if stem == "tasks":
        return False
    return not any(stem.endswith(s) for s in _AUX_SUFFIX)


def find_spec_file(spec_dir: Path) -> Path | None:
    """The brainstorm spec doc, recognized by EXCLUSION so every naming era is
    covered: canonical `YYYY-MM-DD--<name>.md`, plus legacy/manual `spec.md`,
    `SPEC.md`, `brainstorm.md`, and `<name>-specs.md`. Known auxiliary files
    (user-request, notes, data-model, tasks, review, technical-plan, ...) are
    excluded. A dated spec wins (newest first); otherwise a deterministic name
    preference, then lexicographic order, picks a single file."""
    cands = [f for f in spec_dir.glob("*.md") if _is_spec_doc(f.name)]
    if not cands:
        return None
    dated = sorted(f for f in cands if _DATE_PREFIX.match(f.name))
    if dated:
        return dated[-1]

    def _rank(f: Path):
        n = f.name.lower()
        if n == "spec.md":
            return (0, f.name)
        if n.endswith(("-specs.md", "-spec.md")):
            return (1, f.name)
        if n == "brainstorm.md":
            return (2, f.name)
        return (3, f.name)

    ranked = sorted(cands, key=_rank)
    best = ranked[0]
    # A rank-3 file has no naming-convention evidence of being the spec doc --
    # a single one is still trusted (the whole reason detection is by
    # exclusion, not a strict pattern, is to cover bespoke legacy names), but
    # 2+ rank-3 files tied means several arbitrarily-named prose docs are
    # equally (un)plausible. Picking one by alphabetical order was the bug
    # that misidentified a reference-doc dump (architecture diagram,
    # investigation summaries, ...) as "the spec" and misrouted a docs-only
    # backlog stub to spec-to-tasks instead of unspecd. Refuse to guess.
    if _rank(best)[0] == 3 and sum(1 for f in cands if _rank(f)[0] == 3) > 1:
        return None
    return best


# --- task counting -----------------------------------------------------------

# Tail-kind tasks (global E2E + final cleanup) are held out of the parallel fan-out
# by design and stay `pending` in frontmatter even after the impl groups merge. They
# must not feed the "ready-to-implement → orchestrator" branch (which re-dispatches a
# fan-out that skips them every time); detect_stage handles them via pending_tail.
_TAIL_KINDS = ("e2e", "cleanup")

# Task statuses that count as "done" (terminal) for counting purposes.
# Mirror of check_spec_sync.py's TERMINAL_STATUSES — a superseded task's target
# was intentionally removed/made irrelevant and should not count as pending work.
_TERMINAL_STATUSES = {"completed", "superseded", "optional"}


def _task_files(d: Path) -> list[Path]:
    """TASK-*.md files directly in `d`, skipping generated/aux files whose stem
    contains "--" (e.g. TASK-001--review.md)."""
    return [f for f in d.glob("TASK-*.md") if "--" not in f.stem] if d.is_dir() else []


def _task_dirs(spec_dir: Path) -> list[Path]:
    """Every directory holding this spec's TASK-*.md files: the original
    top-level tasks/ (if present) UNIONED with every changes/<slug>/tasks/ dir
    that has task files -- never a single pick. Picking only one (the historic
    behavior: tasks/ wins whenever non-empty, else the newest changes/<slug>/)
    permanently hides a change-spec's own pending tail tasks once the parent
    spec's original tasks/ is fully completed, which is the common case for any
    mature spec (brief 20260713-201900). Change dirs are sorted by slug name
    (date-prefixed, so chronological) for deterministic ordering."""
    dirs: list[Path] = []
    main = spec_dir / "tasks"
    if _task_files(main):
        dirs.append(main)
    changes = spec_dir / "changes"
    if changes.is_dir():
        for c in sorted(d for d in changes.iterdir() if d.is_dir()):
            ctasks = c / "tasks"
            if _task_files(ctasks):
                dirs.append(ctasks)
    return dirs


def _load_tasks(spec_dir: Path) -> list[dict[str, Any]] | None:
    """Union of task rows across every task-bearing dir under spec_dir (see
    _task_dirs), or None when the spec has no task DAG at all. Parsed directly
    via _parse_fm rather than the orchestrator's loader.load_spec: that loader
    correctly resolves exactly one changeset to fan out per orchestrator run,
    but the dashboard's stage detection must see every changeset's task state
    at once. Loaded once per spec and threaded through detect_stage so
    _count_tasks / _pending_impl_stale / _pending_tail_stale don't each
    re-read and re-parse every TASK-*.md."""
    rows: list[dict[str, Any]] = []
    for d in _task_dirs(spec_dir):
        for f in _task_files(d):
            try:
                fm = _parse_fm(f.read_text(errors="ignore"))
            except OSError:
                fm = {}
            rows.append(
                {
                    "id": fm.get("id", f.stem),
                    "status": fm.get("status"),
                    "kind": fm.get("kind", "impl") or "impl",
                    "files": fm.get("files", []),
                    "deps": fm.get("dependencies", []),
                }
            )
    return rows or None


def _count_tasks(
    spec_dir: Path, tasks: list[dict[str, Any]] | None = None
) -> dict[str, int] | None:
    """Return task counts or None if the spec has no task DAG. A backfill spec
    legitimately has no tasks (the code is the truth).

    Keys: 'total', 'completed', 'pending', and the pending split 'pending_impl' /
    'pending_tail' (tail = e2e/cleanup kind). The split lets detect_stage tell a
    spec that still needs implementation work apart from one whose only remaining
    pending tasks are the held-out tail."""
    if tasks is None:
        tasks = _load_tasks(spec_dir)
    if not tasks:
        return None
    completed = sum(1 for t in tasks if t.get("status") in _TERMINAL_STATUSES)
    pending_tail = sum(
        1
        for t in tasks
        if t.get("status") not in _TERMINAL_STATUSES
        and t.get("kind", "impl") in _TAIL_KINDS
    )
    pending = len(tasks) - completed
    return {
        "total": len(tasks),
        "completed": completed,
        "pending": pending,
        "pending_impl": pending - pending_tail,
        "pending_tail": pending_tail,
    }


def _dashboard_repo_root() -> Path:
    """Find the Worktrail checkout root across installed and source layouts."""
    git_root = _find_git_root(_HERE) if _find_git_root is not None else None
    if git_root is not None:
        return git_root
    return _HERE.parents[4]


@functools.lru_cache(maxsize=32)
def _load_dashboard_policy(repo_root: str) -> dict[str, Any] | None:
    if _load_policy is None:
        return None
    try:
        return _load_policy(Path(repo_root))
    except Exception:  # noqa: BLE001 — policy annotation is best-effort only
        return None


def _item_repo_root(item: dict[str, Any], fallback: Path) -> Path:
    repo_path = item.get("path") or item.get("repo_path") or item.get("repo")
    if isinstance(repo_path, str) and repo_path.strip():
        try:
            return Path(repo_path).expanduser().resolve()
        except OSError:
            return fallback
    return fallback


def _planned_agent_for_item(repo_root: Path | None = None) -> str | None:
    """The routing target that would serve `repo_root`'s configured
    `default_tier` -- the dashboard's best-effort "planned agent" annotation.
    Route/risk no longer select routing (`routing.defaults` was retired by
    the target/tier/role redesign, so an item's own route/risk classifier is
    not a routing input any more), which is why this takes no `item`."""
    default_agent = _POLICY_DEFAULTS.get("agent_cli")
    root = repo_root or _dashboard_repo_root()
    policy = _load_dashboard_policy(str(root))
    if not policy:
        return default_agent
    if _resolve_routing is None or _select_cell is None:
        return policy.get("agent_cli") or default_agent
    resolved = _resolve_routing(policy)
    tier = resolved.get("default_tier")
    if not tier:
        return policy.get("agent_cli") or default_agent
    try:
        cell = _select_cell(resolved, tier)
    except Exception:  # noqa: BLE001 — dashboard annotations must not fail rendering
        return policy.get("agent_cli") or default_agent
    return cell.target


def _routing_configured_providers(policy: dict[str, Any] | None) -> list[str]:
    """The routing-derived `target:model` provider-key set fed to
    `agent_capacity.gate_snapshot()` -- every `(target, model)` cell
    `routing_candidates()` (task 2.2) yields from `routing.targets`/
    `routing.tiers`, replacing the removed `configure()`-written
    `configured_providers` cache key that `gate_snapshot()` no longer reads."""
    if not policy:
        return []
    routing = policy.get("routing")
    return sorted(
        _capacity_provider_key(c["target"], c["model"])
        for c in _routing_candidates(routing)
    )


# --- stale status bookkeeping (files merged, status never flipped) -----------


def _git_tracked(repo: Path, files: list[str]) -> set:
    """The subset of `files` git tracks at the base checkout's HEAD. One
    `git ls-files` call (the dashboard scans the base branch, so the index ==
    the committed base tree). Returns an empty set on any git failure -- callers
    treat 'cannot confirm tracking' conservatively (the file is not stale)."""
    if files and repo and (repo / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "-z", "--"] + list(files),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if result.returncode == 0:
                return {p for p in result.stdout.split("\0") if p}
        except (subprocess.SubprocessError, OSError):
            pass
    return set()


@functools.lru_cache(maxsize=32)
def _rename_destinations(repo_value: str) -> dict[str, str]:
    """Build one explicit-rename map per repo instead of scanning per task path."""
    repo = Path(repo_value)
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--all",
                "--diff-filter=R",
                "--name-status",
                "--format=",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return {}
    if result.returncode != 0:
        return {}
    renames: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[1]] = parts[2]
    return renames


def _moved_tracked_path(repo: Path, old_path: str) -> str | None:
    """Return a current destination reached only through explicit git renames."""
    renames = _rename_destinations(str(repo.resolve()))
    destination = old_path
    visited: set = set()
    while destination in renames and destination not in visited:
        visited.add(destination)
        destination = renames[destination]
    if destination == old_path:
        return None
    if (repo / destination).is_file() and destination in _git_tracked(
        repo, [destination]
    ):
        return destination
    return None


def _declared_file_targets(repo: Path, declared: str) -> list[tuple[Path, str]]:
    """Resolve a task's file declaration to candidate repository-relative paths.

    Task artifacts are commonly copied between repositories and may retain a
    repository-directory prefix (for example ``datalena/.github/...``). A
    change spec can also own tasks for a sibling repository. Keep the
    unqualified path as a fallback, but prefer an exact repository prefix or a
    sibling checkout when one is present.
    """
    path = Path(str(declared))
    parts = path.parts
    targets: list[tuple[Path, str]] = []

    def add(root: Path, relative: Path) -> None:
        candidate = (root, relative.as_posix())
        if candidate not in targets:
            targets.append(candidate)

    if parts and parts[0] == repo.name:
        add(repo, Path(*parts[1:]))
    elif len(parts) > 1:
        sibling = repo.parent / parts[0]
        if sibling.exists() and (sibling / ".git").exists():
            add(sibling, Path(*parts[1:]))

    add(repo, path)
    return targets


@functools.lru_cache(maxsize=32)
def _dir_creation_timestamp(repo_value: str, directory_value: str) -> int | None:
    """Epoch timestamp of the oldest commit touching `directory_value`, the
    bookkeeping baseline a "shipped" file's own last commit must postdate."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_value,
                "log",
                "--format=%ct",
                "--reverse",
                "--",
                directory_value,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            try:
                return int(line)
            except ValueError:
                return None
    return None


@functools.lru_cache(maxsize=32)
def _latest_commit_timestamp(repo_value: str, relative_value: str) -> int | None:
    """Epoch timestamp of the most recent commit touching `relative_value`."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_value,
                "log",
                "-1",
                "--format=%ct",
                "--",
                relative_value,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    if not line:
        return None
    try:
        return int(line)
    except ValueError:
        return None


def _task_files_are_shipped(repo: Path, files: list[str], tracked: set) -> bool:
    for declared in files:
        shipped = False
        for target_repo, relative in _declared_file_targets(repo, declared):
            target_tracked = (
                tracked
                if target_repo == repo and relative == str(declared)
                else _git_tracked(target_repo, [relative])
            )
            if relative in target_tracked and (target_repo / relative).is_file():
                shipped = True
                break
            if _moved_tracked_path(target_repo, relative) is not None:
                shipped = True
                break
        if not shipped:
            return False
    return True


def _pending_impl_stale(
    spec_dir: Path, tasks: list[dict[str, Any]] | None = None
) -> list[str]:
    """Ids of pending `kind: impl` tasks whose `files:` are ALL already present on
    disk AND git-tracked on the base checkout -- i.e. the code shipped (e.g. via a
    merged PR) but the task's `status:` was never flipped to completed (stale
    bookkeeping). These must NOT feed the orchestrator branch: a fan-out would
    re-implement already-merged code.

    Conservative on every uncertainty (returns [] / drops the task) so a genuinely
    unimplemented task always keeps the orchestrator path:
      - no task loader importable    -> [] (cannot read `files:` reliably)
      - task carries no `files:` list -> dropped (nothing to verify against)
      - any listed file missing or untracked -> dropped
    """
    if not _HAVE_LOADER:
        return []
    spec_dir = Path(spec_dir)
    repo = spec_dir.parent.parent.parent
    if tasks is None:
        tasks = _load_tasks(spec_dir)
    if not tasks:
        return []
    candidates = [
        t
        for t in tasks
        if t.get("status") != "completed"
        and t.get("kind", "impl") not in _TAIL_KINDS
        and t.get("files")
    ]
    if not candidates:
        return []
    all_files = sorted({f for t in candidates for f in t["files"]})
    tracked = _git_tracked(repo, all_files)
    stale: list[str] = []
    for t in candidates:
        files = t["files"]
        if _task_files_are_shipped(repo, files, tracked):
            stale.append(t["id"])
    return stale


def _pending_openspec_stale(
    change_dir: Path, spec_id: str, tasks: list[dict[str, Any]]
) -> list[str]:
    """Ids of pending OpenSpec impl tasks whose cached plan files shipped.

    OpenSpec task artifacts do not carry file scope, so this check only trusts a
    RunPlan cached for the change's current fingerprint. A cache miss or a
    rejected plan is deliberately indistinguishable from today's behavior.
    """
    if _compile is None or _runplan is None or not tasks:
        return []

    change_dir = Path(change_dir)
    repo = change_dir.parent.parent.parent
    fp = _runplan.fingerprint(change_dir, tasks)
    plan = _runplan.load_cached(_compile.default_cache_dir(repo), spec_id, fp)
    if plan is None:
        return []

    merged, _notes = _runplan.apply_to_tasks(tasks, plan)
    candidates = [
        task
        for task in merged
        if task.get("status") != "completed"
        and task.get("kind", "impl") not in _TAIL_KINDS
        and task.get("files")
    ]
    if not candidates:
        return []

    all_files = sorted({file for task in candidates for file in task["files"]})
    tracked = _git_tracked(repo, all_files)
    return [
        task["id"]
        for task in candidates
        if _task_files_are_shipped(repo, task["files"], tracked)
    ]


def _pending_tail_stale(
    spec_dir: Path, tasks: list[dict[str, Any]] | None = None
) -> list[str]:
    """Same check as `_pending_impl_stale`, for tail-kind (e2e/cleanup) tasks
    instead of impl tasks: ids of pending tail tasks whose `files:` are ALL
    already git-tracked on the base checkout -- i.e. the tail work landed (e.g.
    via an out-of-band merge) but the task's `status:` was never flipped to
    completed. Without this, a spec whose tail work merges outside the
    orchestrator's own integrate path reports stage=tail-pending forever."""
    if not _HAVE_LOADER:
        return []
    spec_dir = Path(spec_dir)
    repo = spec_dir.parent.parent.parent
    if tasks is None:
        tasks = _load_tasks(spec_dir)
    if not tasks:
        return []
    candidates = [
        t
        for t in tasks
        if t.get("status") != "completed"
        and t.get("kind", "impl") in _TAIL_KINDS
        and t.get("files")
    ]
    status_by_id: dict[str, list[Any]] = {}
    for row in tasks:
        status_by_id.setdefault(str(row.get("id")), []).append(row.get("status"))
    empty_cleanup = [
        t
        for t in tasks
        if t.get("status") != "completed"
        and t.get("kind", "impl") == "cleanup"
        and not t.get("files")
        and t.get("deps")
        and all(
            statuses and all(status == "completed" for status in statuses)
            for dep in t.get("deps", [])
            for statuses in [status_by_id.get(str(dep), [])]
        )
    ]
    if not candidates:
        return [t["id"] for t in empty_cleanup]
    all_files = sorted({f for t in candidates for f in t["files"]})
    tracked = _git_tracked(repo, all_files)
    stale: list[str] = [t["id"] for t in empty_cleanup]
    for t in candidates:
        files = t["files"]
        if _task_files_are_shipped(repo, files, tracked):
            stale.append(t["id"])
    return stale


# --- header / marker probes --------------------------------------------------

_STATUS_RE = re.compile(
    r"^\**\s*status\s*\**\s*:\s*\**\s*([A-Za-z][\w -]*)", re.IGNORECASE | re.MULTILINE
)
_CLARIFICATIONS_RE = re.compile(
    r"^#{1,6}\s+clarifications\b", re.IGNORECASE | re.MULTILINE
)
# The authoritative "needs spec-check" signal: an unresolved marker specs.spec-check
# consumes. Far more reliable than the heading, which most specs never emit.
_CLAR_MARKER_RE = re.compile(r"\[NEEDS CLARIFICATION", re.IGNORECASE)
_FEATURE_SUMMARY_RE = re.compile(
    r"^\*{0,2}Feature Summary\*{0,2}\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def _status_header(spec_text: str) -> str | None:
    m = _STATUS_RE.search(spec_text)
    return m.group(1).strip() if m else None


def _feature_summary(spec_text: str) -> str | None:
    m = _FEATURE_SUMMARY_RE.search(spec_text)
    if m:
        val = m.group(1).strip()
        if val and val != "${FEATURE_SUMMARY}":
            return val
    return None


# --- the state machine -------------------------------------------------------


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _pr_number(pr_url: str | None) -> str:
    """Extract the `#N` PR number from a GitHub PR URL for compact rendering."""
    m = _PR_NUMBER_RE.search(pr_url or "")
    return m.group(1) if m else "?"


def _group_merged_on_base(repo: Path, pr_url: str) -> bool:
    """True if a merge commit referencing this PR number already exists in the
    base checkout's history -- i.e. the group actually landed (e.g. a manual or
    out-of-band merge) even though the run journal's per-group `state` was never
    stamped MERGED. Squash/merge commits from GitHub carry a "(#N)" suffix, so
    this is a pure git-log check: no network call, consistent with
    `_pending_impl_stale`'s local-only philosophy."""
    m = _PR_NUMBER_RE.search(pr_url or "")
    if not m or not repo or not (repo / ".git").exists():
        return False
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--oneline",
                "--fixed-strings",
                "--grep",
                f"(#{m.group(1)})",
                "-1",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def _journal_verify_pending(spec_dir: Path) -> bool:
    """Returns True if the run journal for spec_dir has integrate_complete: true
    and at least one group record with state != 'MERGED' whose PR has not
    actually landed on the base branch either. Returns False if the journal
    does not exist, is unreadable, all groups are MERGED, or every non-MERGED
    group's merge commit is already present in the base branch's history
    (stale journal bookkeeping, not real pending work)."""
    try:
        spec_dir = Path(spec_dir)
        repo = spec_dir.parent.parent.parent
        journal_path = (
            repo.parent / f"{repo.name}-worktrees" / f"run-{spec_dir.name}.json"
        )
        if not journal_path.is_file():
            return False
        journal = json.loads(journal_path.read_text())
        if not journal.get("integrate_complete"):
            return False
        groups = journal.get("groups", {})
        if not groups:
            return False
        pending = [g for g in groups.values() if g.get("state") != "MERGED"]
        if not pending:
            return False
        return any(
            not _group_merged_on_base(repo, g.get("pr_url", "")) for g in pending
        )
    except (json.JSONDecodeError, OSError, KeyError, TypeError):
        return False


def _status_phase(spec_dir: Path) -> str | None:
    """Best-effort read of the orchestrator heartbeat sidecar phase."""
    try:
        spec_dir = Path(spec_dir)
        repo = spec_dir.parent.parent.parent
        status_path = (
            repo.parent / f"{repo.name}-worktrees" / f"run-{spec_dir.name}.status.json"
        )
        if not status_path.is_file():
            return None
        return json.loads(status_path.read_text()).get("phase")
    except (json.JSONDecodeError, OSError, KeyError, TypeError, AttributeError):
        return None


def _sync_pending(spec_dir: Path) -> bool:
    """True when a code-complete spec has NOT yet been reconciled by `sync` -- so its
    knowledge-graph.json is missing or was never written by the spec-sync agent and is
    drifting from the merged code. Used to surface sync as the next action before a
    completed spec is closed out (otherwise the cache silently goes stale).

    Conservative: only the unambiguous "never synced" case is flagged. A KG carrying a
    `spec-sync` analysis source is treated as synced (re-sync-after-later-merge drift is
    not inferred from artifacts here).
    """
    try:
        kg = Path(spec_dir) / "knowledge-graph.json"
        if not kg.is_file():
            return True
        meta = (json.loads(kg.read_text()) or {}).get("metadata", {})
        sources = meta.get("analysis_sources", []) or []
        return not any("sync" in str(s.get("agent", "")).lower() for s in sources)
    except (json.JSONDecodeError, OSError, AttributeError, TypeError):
        # Unreadable KG -> can't confirm a sync ran; surfacing sync is the safe default.
        return True


def detect_stage(spec_dir: Path, probe_stale: bool = True) -> dict[str, Any]:
    """Stage-detect one spec folder. probe_stale=False skips the per-spec
    `git ls-files` stale-bookkeeping probe — callers that only need the stage
    label for display (e.g. the overlap gate) avoid a subprocess per spec."""
    spec_dir = Path(spec_dir)
    spec_file = find_spec_file(spec_dir)
    try:
        spec_text = spec_file.read_text(errors="ignore") if spec_file else ""
    except OSError:
        spec_text = ""
    status = _status_header(spec_text)
    has_clar = bool(_CLARIFICATIONS_RE.search(spec_text))
    has_markers = bool(_CLAR_MARKER_RE.search(spec_text))
    has_plan = (spec_dir / "technical-plan.md").is_file()
    has_request = (spec_dir / "user-request.md").is_file()
    tasks = _load_tasks(spec_dir)
    counts = _count_tasks(spec_dir, tasks)

    info: dict[str, Any] = {
        "id": spec_dir.name,
        "spec_file": spec_file.name if spec_file else None,
        "status_header": status,
        "has_clarifications": has_clar,
        "clarification_markers": has_markers,
        "has_user_request": has_request,
        "technical_plan": "present" if has_plan else "missing",
        "tasks": counts,
        "feature_summary": _feature_summary(spec_text),
    }

    # 1. Backfill or Implemented -- done. "Implemented" is stamped by the sync agent
    # only after all PRs land on the base branch, so it is authoritative. Without
    # this check, a stale run journal (groups still at state:OPEN from a pre-MERGED-
    # stamp orchestrator run) overrides the spec's own completion signal.
    #
    # This fast path only applies when `counts` (now unioned across tasks/ AND every
    # changes/<slug>/tasks/) has no pending work at all. The header is stamped once,
    # against the spec as it stood at that time -- a LATER change-spec's own pending
    # tail/impl tasks must still surface (fall through to the task-based branch below)
    # rather than being permanently masked by a stamp that predates them (brief
    # 20260713-201900).
    if (
        status
        and status.lower() in ("backfill", "implemented")
        and (not counts or counts.get("pending", 0) == 0)
    ):
        info.update(stage="done", next_action="none (backfill)")
        return info
    # 2. Tasks dominate everything below: a folder with a task DAG is past the spec
    # stage even if its spec doc carries an unrecognized name (so it is never
    # mislabeled empty/unspecd). Also dominates the clarification check (small-scope
    # specs may skip spec-check).
    if counts:
        # Tasks still needing implementation (excludes held-out e2e/cleanup tail) put
        # the spec on the orchestrator path. When only tail-kind tasks remain pending,
        # the impl groups are done — fall through to the integration/tail/sync states
        # rather than re-dispatching a fan-out that skips the tail every time.
        pending_impl = counts.get("pending_impl", counts["pending"])
        # A pending impl task whose files are all already merged on the base branch
        # is stale bookkeeping, not real work — exclude it so the spec is not bounced
        # onto the orchestrator path (which would re-implement merged code). Only
        # probed when impl work appears outstanding (keeps the common path git-free).
        stale_ids = (
            _pending_impl_stale(spec_dir, tasks)
            if (probe_stale and pending_impl > 0)
            else []
        )
        pending_impl_real = pending_impl - len(stale_ids)
        if pending_impl_real > 0:
            if _status_phase(spec_dir) == "fanout_failed":
                info.update(
                    stage="orchestrator-stuck",
                    next_action="manual recovery — prior orchestrator run is stuck (fanout_failed)",
                )
            else:
                info.update(stage="ready-to-implement", next_action="orchestrator")
        elif stale_ids:
            # All remaining pending impl tasks shipped already; the only outstanding
            # work is flipping their status. Surface a cheap closeout, not orchestrator.
            suffix = f" ({', '.join(stale_ids)})" if stale_ids else ""
            info.update(
                stage="stale-bookkeeping",
                next_action=f"confirm & close{suffix} (files already merged on base; "
                "flip task status → completed, no orchestrator)",
                stale_task_ids=stale_ids,
            )
        elif _journal_verify_pending(spec_dir):
            # Impl complete but group PRs still need verify → merge → cleanup.
            info.update(
                stage="verify-pending",
                next_action="resume full-real (verify → merge → cleanup)",
            )
        elif counts.get("pending_tail", 0) > 0:
            # Impl groups merged; tail-kind (e2e/cleanup) work is outstanding by
            # design. A pending tail task whose files are all already merged on the
            # base branch is stale bookkeeping, not real work -- same drift class
            # PR #245 fixed for _journal_verify_pending, applied here to the tail
            # tasks' own `files:`/status instead of a frozen run-journal snapshot
            # (which may not even exist for out-of-band merges).
            pending_tail = counts["pending_tail"]
            stale_tail_ids = _pending_tail_stale(spec_dir, tasks) if probe_stale else []
            pending_tail_real = pending_tail - len(stale_tail_ids)
            if pending_tail_real > 0:
                tail_ids = [
                    t["id"]
                    for t in (tasks or [])
                    if t.get("kind", "impl") in _TAIL_KINDS
                    and t.get("status") != "completed"
                    and t["id"] not in stale_tail_ids
                ]
                suffix = f" ({', '.join(tail_ids)})" if tail_ids else ""
                info.update(
                    stage="tail-pending",
                    next_action=f"run E2E + cleanup tail{suffix}, "
                    "or mark them completed/backfill if not applicable",
                )
            else:
                suffix = f" ({', '.join(stale_tail_ids)})" if stale_tail_ids else ""
                info.update(
                    stage="stale-bookkeeping",
                    next_action=f"confirm & close{suffix} (files already merged on base; "
                    "flip task status → completed, no orchestrator)",
                    stale_task_ids=stale_tail_ids,
                )
        elif _sync_pending(spec_dir):
            info.update(
                stage="sync-pending",
                next_action="sync (reconcile spec ↔ code; update knowledge-graph.json)",
            )
        else:
            info.update(
                stage="complete", next_action="open PR / sync (verify merge state)"
            )
        return info
    # 3 & 4. No tasks and no spec doc. A folder carrying only user-request.md is an
    # unspec'd feature (brainstorm seeded the request but never produced the spec) --
    # the "features not spec'd" backlog. A truly bare folder is `empty`. Both -> brainstorm.
    if not spec_file:
        if has_request:
            info.update(stage="unspecd", next_action="brainstorm (unspec'd backlog)")
        else:
            info.update(stage="empty", next_action="brainstorm")
        return info
    # 6. Spec exists with unresolved [NEEDS CLARIFICATION] markers -- spec-check
    # consumes exactly these. (Absence of a `## Clarifications` heading does NOT
    # imply unclarified: most specs never emit one, and small-scope specs skip
    # spec-check, so keying on the heading bounces them back wrongly.)
    if has_markers:
        info.update(stage="needs-clarification", next_action="spec-check")
        return info
    # 7. No markers, no tasks -- ready for the DAG (technical-plan is optional).
    info.update(stage="needs-tasks", next_action="spec-to-tasks")
    return info


# Sibling directories under docs/specs/ that are NOT per-spec folders: shared
# addenda, research spikes, archived specs, the epics index, scaffolding. They
# can hold loose `.md` files that would otherwise look like a spec doc.
_NON_SPEC_DIRS = {
    "addenda",
    "research",
    "archived",
    "epics",
    "templates",
    "_ralph_loop",
    "reviews",
    "contracts",
    "tasks",
    "changes",
}


def _is_spec_folder(d: Path) -> bool:
    """A spec folder carries a spec doc, a task DAG, a change set, OR just a
    `user-request.md` (an unspec'd-backlog stub -- brainstorm seeded the request but
    never produced the spec; these are the ~44 datalena folders and must be scanned,
    not dropped). Skips shared siblings (architecture.md / ontology.md /
    knowledge-graph.json) and the known non-spec directories (addenda/research/...)."""
    if not d.is_dir() or d.name.lower() in _NON_SPEC_DIRS:
        return False
    return bool(
        find_spec_file(d)
        or (d / "tasks").is_dir()
        or (d / "changes").is_dir()
        or (d / "user-request.md").is_file()
    )


def _safe_detect_stage(spec_dir: Path) -> dict[str, Any]:
    """Per-spec isolation: one unreadable/broken spec folder degrades to an
    `error` row instead of killing the whole dashboard (the /go front door)."""
    try:
        return detect_stage(spec_dir)
    except Exception as e:  # noqa: BLE001 — degrade, never crash orientation
        return {
            "id": Path(spec_dir).name,
            "spec_file": None,
            "status_header": None,
            "has_clarifications": False,
            "clarification_markers": False,
            "has_user_request": False,
            "technical_plan": "missing",
            "tasks": None,
            "feature_summary": None,
            "stage": "error",
            "next_action": f"inspect manually ({type(e).__name__}: {e})",
        }


_OPENSPEC_DELTA_SECTION = re.compile(
    r"^## (ADDED|MODIFIED|REMOVED|RENAMED) Requirements\s*$", re.MULTILINE
)
_OPENSPEC_REQUIREMENT = re.compile(r"^### Requirement:\s*(.+?)\s*$", re.MULTILINE)
_OPENSPEC_SCENARIO = re.compile(r"^#### Scenario:\s*(.+?)\s*$", re.MULTILINE)
_OPENSPEC_RENAME = re.compile(r"^-\s*(FROM|TO):\s*`?(.+?)`?\s*$", re.MULTILINE)


def _openspec_headings(text: str) -> tuple[set[str], set[str]]:
    """Return stable requirement and scenario names from an OpenSpec spec."""
    return (
        {match.strip() for match in _OPENSPEC_REQUIREMENT.findall(text)},
        {match.strip() for match in _OPENSPEC_SCENARIO.findall(text)},
    )


def _rename_requirement_name(value: str) -> str:
    value = value.strip().strip("`").strip()
    prefix = "### Requirement:"
    return value[len(prefix) :].strip() if value.startswith(prefix) else value


def _iter_openspec_delta_sections(text: str) -> Iterator[tuple[str, str]]:
    """Yield (kind, body) per `## ADDED|MODIFIED|REMOVED|RENAMED Requirements` section."""
    sections = list(_OPENSPEC_DELTA_SECTION.finditer(text))
    for index, section in enumerate(sections):
        end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
        yield section.group(1), text[section.end() : end]


def _openspec_delta_current_requirements(kind: str, body: str) -> set[str]:
    """Return the requirement names a delta `(kind, body)` section declares as current.

    `ADDED`/`MODIFIED` bodies declare current requirements by heading; `RENAMED` bodies
    declare them via their `TO:` values. `REMOVED` bodies declare none.
    """
    if kind in {"ADDED", "MODIFIED"}:
        return {match.strip() for match in _OPENSPEC_REQUIREMENT.findall(body)}
    if kind == "RENAMED":
        return {
            _rename_requirement_name(value)
            for direction, value in _OPENSPEC_RENAME.findall(body)
            if direction == "TO"
        }
    return set()


def _git_last_commit_time(repo: Path, path: Path) -> int | None:
    """Unix timestamp of `path`'s most recent commit, or None if it has none
    (e.g. an uncommitted-only file)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        return int(output) if output else None
    except (subprocess.SubprocessError, OSError):
        return None


def _git_added_commit_time(repo: Path, path: Path) -> int | None:
    """Unix timestamp of `path`'s add commit, or None if it has none.

    Prefers the commit that first added `path` under its current name
    (`--diff-filter=A`); falls back to the earliest entry of its rename/move
    history (`--follow`) when the add-filtered query finds nothing, e.g.
    because `path` arrived via a rename `git log --diff-filter=A` does not
    attribute to it.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--diff-filter=A",
                "-1",
                "--format=%ct",
                "--",
                str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())

        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "log",
                "--follow",
                "--format=%ct",
                "--",
                str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode != 0:
            return None
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        return int(lines[-1]) if lines else None
    except (subprocess.SubprocessError, OSError):
        return None


def _openspec_delta_drift(change_dir: Path, repo: Path) -> list[dict[str, str]]:
    """Requirement names an open change's delta MODIFIED/RENAMED that an archived
    sibling touched more recently.

    Parses on-disk delta headings and compares local `git log` commit ordering only
    -- never a model call.
    """
    try:
        drift: list[dict[str, str]] = []
        for delta_file in sorted((change_dir / "specs").glob("**/spec.md")):
            capability = delta_file.relative_to(change_dir / "specs").parent
            text = delta_file.read_text(errors="ignore")
            open_names: set[str] = set()
            for kind, body in _iter_openspec_delta_sections(text):
                if kind in {"MODIFIED", "RENAMED"}:
                    open_names |= _openspec_delta_current_requirements(kind, body)
            if not open_names:
                continue
            open_time = _git_last_commit_time(repo, delta_file)
            if open_time is None:
                continue

            archive_root = repo / "openspec" / "changes" / "archive"
            for archived_spec in sorted(
                repo.glob(f"openspec/changes/archive/*/specs/{capability}/spec.md")
            ):
                archived_change_id = archived_spec.relative_to(archive_root).parts[0]
                archived_text = archived_spec.read_text(errors="ignore")
                archived_names: set[str] = set()
                for kind, body in _iter_openspec_delta_sections(archived_text):
                    if kind in {"ADDED", "MODIFIED", "RENAMED"}:
                        archived_names |= _openspec_delta_current_requirements(
                            kind, body
                        )

                for name in open_names & archived_names:
                    archived_time = _git_added_commit_time(repo, archived_spec)
                    if archived_time is None or archived_time <= open_time:
                        continue
                    drift.append(
                        {
                            "requirement": name,
                            "capability": str(capability),
                            "archived_change_id": archived_change_id,
                        }
                    )
        return drift
    except Exception:  # noqa: BLE001
        return []


def _openspec_delta_reconciled(change_dir: Path) -> bool:
    """Whether every structural declaration in an OpenSpec delta is canonical.

    This deliberately compares headings only. It is a read-only workflow signal,
    not an attempt to decide whether requirement prose is semantically equivalent.
    """
    delta_files = sorted((change_dir / "specs").glob("**/spec.md"))
    if not delta_files:
        return True

    repo = change_dir.parent.parent.parent
    for delta_file in delta_files:
        relative = delta_file.relative_to(change_dir / "specs")
        canonical_file = repo / "openspec" / "specs" / relative
        if not canonical_file.is_file():
            return False

        canonical_requirements, canonical_scenarios = _openspec_headings(
            canonical_file.read_text(errors="ignore")
        )
        delta_text = delta_file.read_text(errors="ignore")
        sections = list(_iter_openspec_delta_sections(delta_text))
        if not sections:
            raise ValueError(f"no recognized delta sections in {delta_file}")
        for kind, body in sections:
            requirements, scenarios = _openspec_headings(body)
            if kind in {"ADDED", "MODIFIED"}:
                if not requirements.issubset(canonical_requirements):
                    return False
                if not scenarios.issubset(canonical_scenarios):
                    return False
            elif kind == "REMOVED":
                if requirements & canonical_requirements:
                    return False
            else:
                rename_values: dict[str, list[str]] = {"FROM": [], "TO": []}
                for direction, value in _OPENSPEC_RENAME.findall(body):
                    rename_values[direction].append(_rename_requirement_name(value))
                if not rename_values["FROM"] or len(rename_values["FROM"]) != len(
                    rename_values["TO"]
                ):
                    raise ValueError(f"malformed RENAMED Requirements in {delta_file}")
                if any(
                    name in canonical_requirements for name in rename_values["FROM"]
                ):
                    return False
                if any(
                    name not in canonical_requirements for name in rename_values["TO"]
                ):
                    return False
    return True


def _safe_detect_openspec(change_dir: Path) -> dict[str, Any]:
    """Project an OpenSpec change into the dashboard's common stage shape."""
    try:
        spec_id, tasks = _taskformats.load_spec(change_dir)
        pending = [t for t in tasks if t.get("status") != "completed"]
        stale_ids: list[str] = []
        if not tasks:
            stage, next_action = "needs-tasks", "create tasks"
        elif pending:
            stale_ids = _pending_openspec_stale(change_dir, spec_id, tasks)
            pending_impl = [
                t for t in pending if t.get("kind", "impl") not in _TAIL_KINDS
            ]
            pending_impl_real = len(pending_impl) - len(
                set(stale_ids) & {t["id"] for t in pending_impl}
            )
            if pending_impl and pending_impl_real == 0:
                suffix = f" ({', '.join(stale_ids)})" if stale_ids else ""
                stage, next_action = (
                    "stale-bookkeeping",
                    (
                        f"confirm & close{suffix} (files already merged on base; "
                        "flip task status → completed, no orchestrator)"
                    ),
                )
            else:
                stage, next_action = "ready-to-implement", "orchestrator"
        elif _journal_verify_pending(change_dir):
            # Same run-journal lookup the devkit branch uses (journal_path_for
            # keys purely on repo root + spec_dir.name, both format-agnostic) --
            # an OpenSpec change with all tasks checked but an unmerged/OPEN
            # group PR is still verify-pending, not complete.
            stage, next_action = (
                "verify-pending",
                "resume full-real (verify → merge → cleanup)",
            )
        elif not _openspec_delta_reconciled(change_dir):
            stage, next_action = "sync-pending", "sync change"
        else:
            stage, next_action = "complete", "archive"
        info = {
            "id": spec_id,
            "format": "openspec",
            "path": str(change_dir),
            "spec_file": str(change_dir / "proposal.md")
            if (change_dir / "proposal.md").is_file()
            else None,
            "status_header": None,
            "has_clarifications": False,
            "clarification_markers": False,
            "has_user_request": False,
            "technical_plan": "present"
            if (change_dir / "design.md").is_file()
            else "missing",
            "tasks": tasks,
            "feature_summary": None,
            "stage": stage,
            "next_action": next_action,
        }
        if pending and stale_ids:
            info["stale_task_ids"] = stale_ids
        repo = change_dir.parent.parent.parent
        delta_drift = _openspec_delta_drift(change_dir, repo)
        if delta_drift:
            info["delta_drift"] = delta_drift
        return info
    except Exception as e:  # noqa: BLE001 — one malformed change must not kill /go
        return {
            "id": change_dir.name,
            "format": "openspec",
            "path": str(change_dir),
            "spec_file": None,
            "status_header": None,
            "has_clarifications": False,
            "clarification_markers": False,
            "has_user_request": False,
            "technical_plan": "missing",
            "tasks": None,
            "feature_summary": None,
            "stage": "error",
            "next_action": f"inspect manually ({type(e).__name__}: {e})",
        }


def _openspec_change_dirs(repo: Path) -> list[Path]:
    changes = Path(repo) / "openspec" / "changes"
    if not changes.is_dir():
        return []
    return sorted(d for d in changes.iterdir() if d.is_dir() and d.name != "archive")


def scan(specs_root: Path) -> list[dict[str, Any]]:
    specs_root = Path(specs_root)
    if not specs_root.is_dir():
        repo_root = (
            specs_root.parent.parent
            if specs_root.name == "specs" and specs_root.parent.name == "docs"
            else None
        )
        return (
            [_safe_detect_openspec(d) for d in _openspec_change_dirs(repo_root)]
            if repo_root
            else []
        )
    spec_dirs = sorted(d for d in specs_root.iterdir() if _is_spec_folder(d))
    repo_root = (
        specs_root.parent.parent
        if specs_root.name == "specs" and specs_root.parent.name == "docs"
        else None
    )
    if spec_dirs:
        with ThreadPoolExecutor() as ex:
            rows = list(ex.map(_safe_detect_stage, spec_dirs))
    else:
        rows = []
    if repo_root is not None:
        with ThreadPoolExecutor() as ex:
            rows.extend(ex.map(_safe_detect_openspec, _openspec_change_dirs(repo_root)))
    return rows


# --- epic stage detection (spec epic-dashboard-visibility) -------------------
#
# `docs/specs/epics/*.md` decomposition documents are a third artifact
# category alongside devkit spec folders and OpenSpec changes: each one lists
# `### Feature N` headings and is "done" once every feature has a citing spec
# (or its own `**Status:**` line is stamped terminal). This is the shared
# detector both this module's own dashboard rendering and
# `workqueue/seed_backlog.py`'s `find_epic_gaps()` project onto -- ported
# 1:1 from seed_backlog's original `_epic_status` / `_count_features` /
# `_epic_citation_patterns` / `_citing_spec_ids`, so seeding behavior does not
# shift when that module is refactored to call this instead of its own copy.

# Canonical epic-id filename convention: `NNN-<slug>.md` under
# `docs/specs/epics/`. Anything else in that directory (an index/README, a
# stray note) is not an epic and must be skipped, not misclassified.
EPIC_ID_RE = re.compile(r"^\d{3}-[a-z0-9][a-z0-9-]*$")

# An epic whose `**Status:**` line matches one of these (case-insensitive) is
# finished as a planning artifact -- every feature is either specced,
# delivered, or intentionally dropped, so it stops surfacing as a gap
# regardless of citation count.
TERMINAL_EPIC_STATUSES = frozenset(
    {
        "completed",
        "complete",
        "delivered",
        "done",
        "closed",
        "superseded",
        "cancelled",
        "abandoned",
    }
)

_EPIC_STATUS_LINE_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)
_EPIC_FEATURE_HEADING_RE = re.compile(r"^###\s+Feature\b", re.MULTILINE)
_EPIC_NUMBER_RE = re.compile(r"^(\d{3})-")
_EPIC_FUTURE_SPEC_ID_RE = re.compile(
    r"\*\*Future spec id:\*\*\s*`([a-z][a-z0-9-]*)`", re.IGNORECASE
)


def _epic_status_header(text: str) -> str | None:
    match = _EPIC_STATUS_LINE_RE.search(text)
    return match.group(1).strip() if match else None


def _count_epic_features(text: str) -> int:
    return len(_EPIC_FEATURE_HEADING_RE.findall(text))


def _epic_citation_patterns(epic_id: str, epic_text: str) -> list[re.Pattern]:
    """Signals that count a spec/change folder as citing this epic.

    The literal epic id string is the strongest signal, but real citations
    often reference the epic by prose instead -- an OpenSpec change's
    proposal.md typically reads "Epic 002 Feature 2 adds..." rather than
    spelling out the full `002-safe-work-queue-dependency-references` id. A
    feature's own documented `**Future spec id:**` is an equally strong,
    unambiguous signal once a citing file names it. Matching only the literal
    epic id string undercounts real citations.
    """
    patterns: list[re.Pattern] = [re.compile(re.escape(epic_id))]
    number_match = _EPIC_NUMBER_RE.match(epic_id)
    if number_match:
        patterns.append(
            re.compile(
                rf"\bEpic\s+{number_match.group(1)}\s+Feature\s+\d+\b", re.IGNORECASE
            )
        )
    for future_spec_id in _EPIC_FUTURE_SPEC_ID_RE.findall(epic_text):
        patterns.append(re.compile(re.escape(future_spec_id)))
    return patterns


def _epic_citing_spec_ids(repo: Path, patterns: list[re.Pattern]) -> list[str]:
    """Spec/change folders whose markdown matches any epic citation pattern.

    Scans both supported spec formats, since an epic's features can ship as
    either: `docs/specs/<id>/*.md` (devkit, top-level files only) and
    `openspec/{specs,changes}/**/*.md` (OpenSpec, recursive -- covers
    `changes/archive/<date>-<slug>/` and its nested `specs/<slug>/spec.md`
    copy, both of which sit one level deeper than a live change).
    """
    citing: list[str] = []
    seen: set = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            citing.append(name)

    def _matches(text: str) -> bool:
        return any(pattern.search(text) for pattern in patterns)

    docs_specs_root = repo / "docs" / "specs"
    if docs_specs_root.is_dir():
        for spec_dir in sorted(docs_specs_root.iterdir()):
            if not spec_dir.is_dir() or spec_dir.name.lower() in {"epics"}:
                continue
            for md in spec_dir.glob("*.md"):
                try:
                    if _matches(md.read_text(encoding="utf-8")):
                        _add(spec_dir.name)
                        break
                except OSError:
                    continue

    openspec_root = repo / "openspec"
    for sub in ("specs", "changes"):
        sub_root = openspec_root / sub
        if not sub_root.is_dir():
            continue
        for md in sorted(sub_root.glob("**/*.md")):
            rel_parts = md.relative_to(sub_root).parts
            if sub == "changes" and rel_parts[0] == "archive":
                rel_parts = rel_parts[1:]
            if len(rel_parts) < 2:
                continue  # loose file directly under specs/ or changes/, not a change/spec folder
            try:
                if _matches(md.read_text(encoding="utf-8")):
                    _add(rel_parts[0])
            except OSError:
                continue

    return citing


def detect_epic_stage(epic_file: Path, repo: Path) -> dict[str, Any]:
    """Stage-detect one `docs/specs/epics/*.md` decomposition document.

    Returns a dict with `stage` one of:
      - `epic-complete`    -- terminal `**Status:**`, or every `### Feature`
                              has a citing spec/change.
      - `epic-gap`         -- fewer citing specs than decomposed features.
      - `epic-unparseable` -- no `### Feature` headings found at all (with no
                              feature count there is no terminal condition,
                              so this must never be conflated with epic-gap).
    """
    epic_file = Path(epic_file)
    repo = Path(repo)
    epic_id = epic_file.stem
    text = epic_file.read_text(encoding="utf-8")
    status = _epic_status_header(text)

    info: dict[str, Any] = {
        "id": epic_id,
        "epic_file": epic_file.name,
        "status_header": status,
    }

    if status and status.lower() in TERMINAL_EPIC_STATUSES:
        info.update(
            stage="epic-complete",
            next_action=f"none (terminal status: {status})",
            features=None,
            cited=None,
            citing_specs=[],
        )
        return info

    features = _count_epic_features(text)
    if features == 0:
        info.update(
            stage="epic-unparseable",
            next_action="inspect manually (no ### Feature headings found)",
            features=0,
            cited=None,
            citing_specs=[],
        )
        return info

    patterns = _epic_citation_patterns(epic_id, text)
    citing = _epic_citing_spec_ids(repo, patterns)
    info["features"] = features
    info["citing_specs"] = citing
    info["cited"] = len(citing)

    if len(citing) >= features:
        info.update(
            stage="epic-complete",
            next_action=f"none (fully cited: {len(citing)}/{features} features)",
        )
    else:
        info.update(
            stage="epic-gap",
            next_action=(
                f"spec the next unspecced feature from its decomposition "
                f"(docs/specs/epics/{epic_id}.md)"
            ),
        )
    return info


def _safe_detect_epic_stage(epic_file: Path, repo: Path) -> dict[str, Any]:
    """Per-epic-file isolation: one unreadable/unparseable epic file degrades
    to an `error` row instead of killing the whole epic scan."""
    try:
        return detect_epic_stage(epic_file, repo)
    except Exception as e:  # noqa: BLE001 — degrade, never crash the scan
        return {
            "id": Path(epic_file).stem,
            "epic_file": Path(epic_file).name,
            "status_header": None,
            "features": None,
            "cited": None,
            "citing_specs": [],
            "stage": "error",
            "next_action": f"inspect manually ({type(e).__name__}: {e})",
        }


def scan_epics(repo: Path) -> list[dict[str, Any]]:
    """Sibling to `scan()`: one row per `docs/specs/epics/*.md` decomposition
    document whose stem matches `EPIC_ID_RE`. Non-matching files in that
    directory (an index/README, a stray note) are skipped, not misclassified.
    Returns an empty list, not an error, when the repo has no epics directory.
    """
    repo = Path(repo)
    epics_dir = repo / "docs" / "specs" / "epics"
    if not epics_dir.is_dir():
        return []
    epic_files = sorted(f for f in epics_dir.glob("*.md") if EPIC_ID_RE.match(f.stem))
    if not epic_files:
        return []
    with ThreadPoolExecutor() as ex:
        return list(ex.map(lambda f: _safe_detect_epic_stage(f, repo), epic_files))


def constitution_status(specs_root: Path) -> dict[str, bool]:
    """Phase 0 architectural DNA lives beside the specs: docs/specs/architecture.md
    + ontology.md (verified: constitution skill writes them there). Advisory only --
    the lifecycle works without it, so `go` suggests, never gates."""
    specs_root = Path(specs_root)
    return {
        "architecture": (specs_root / "architecture.md").is_file(),
        "ontology": (specs_root / "ontology.md").is_file(),
    }


# --- auto mode (spec 017): deterministic next-brief pick with collision guards ---


def _runlock_held(lock_path: Path) -> bool:
    """True if another live process holds an exclusive flock on lock_path.

    The orchestrator's RunLock (parallel-orchestrator/scripts/live.py) holds the
    flock only for the run's duration and the kernel releases it on process
    death, so held == a live run RIGHT NOW. Stale lock files (which persist
    after runs finish) probe as free. The probe acquires non-blocking, releases
    immediately, and writes nothing. No fcntl (non-POSIX) -> False, matching
    RunLock's own no-op degradation.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return False
    try:
        fh = open(lock_path, "a")  # noqa: SIM115 -- held across the surrounding scope as a lock file
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def _resolve_repo_dir(repo: Any, repos_root: Any | None) -> Path | None:
    """Resolve a brief's `repo:` frontmatter value to an on-disk directory.

    Absolute/HOME-relative paths (the normal, intended case) resolve
    directly. A bare name or `owner/name`-style value (e.g. 'devops',
    'behindthedash/devops') has no filesystem meaning by itself -- such
    values can still reach here because `create_handoff.py` writes `repo:`
    verbatim with no normalization. Resolve it by basename against
    `repos_root`, mirroring the basename match `auto_pick_brief`'s
    `repo_filter` already applies just above this check, instead of failing
    `is_dir()` on the literal string. Returns None when unresolvable.
    """
    if not repo:
        return None
    p = Path(str(repo)).expanduser()
    if p.is_dir():
        return p
    if repos_root:
        candidate = Path(str(repos_root)).expanduser() / p.name
        if candidate.is_dir():
            return candidate
    return None


def _repo_busy_reason(repo: Any, repos_root: Any | None = None) -> str | None:
    """Skip-reason string when a brief's repo can't be safely auto-claimed.

    - `no-repo`: brief has no repo frontmatter — no collision surface to verify.
    - `repo-missing`: the checkout doesn't exist on this machine (checked
      directly, and by basename under `repos_root` when given — see
      `_resolve_repo_dir`).
    - `orchestrator-run-active:<lock>`: a live orchestrator run holds a RunLock
      somewhere under the repo's sibling `<repo>-worktrees/` dir (another agent
      is actively working this repo, possibly with no corresponding queue
      brief). `live.py`'s `journal_path_for(repo, spec_rel)` derives the lock's
      parent from whatever path was passed as `repo` — when that's itself a
      nested worktree (worktree dependency stacking), the lock lands one or
      more levels deeper than `<repo>-worktrees/*.lock`, so this recurses
      (`rglob`) rather than checking only the immediate level. Only `live.py`
      ever creates `.lock` files in this tree (always `run-<spec>.lock` beside
      its journal), so a recursive scan carries no false-positive risk from
      unrelated lock files.
    Returns None when the repo is safe to target.
    """
    if not repo or str(repo) in ("null", "~"):
        return "no-repo"
    p = _resolve_repo_dir(repo, repos_root)
    if p is None:
        return "repo-missing"
    worktrees = p.parent / f"{p.name}-worktrees"
    if worktrees.is_dir():
        for lock in sorted(worktrees.rglob("*.lock")):
            if _runlock_held(lock):
                return f"orchestrator-run-active:{lock.name}"
    return None


def _remote_spec_branch(
    repo: Any, target_spec: Any, repos_root: Any | None = None
) -> str | None:
    """Return a matching remote branch ref for a queued target spec, if any."""
    if not repo or not target_spec:
        return None
    repo_path = _resolve_repo_dir(repo, repos_root)
    if repo_path is None:
        return None
    spec_id = Path(str(target_spec).rstrip("/")).name
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "ls-remote",
                "--heads",
                "origin",
                f"spec/{spec_id}*",
                f"chg/{spec_id}-*",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    refs = sorted(
        line.split("\t", 1)[1]
        for line in result.stdout.splitlines()
        if "\trefs/heads/" in line
    )
    return refs[0] if refs else None


def _dashboard_task_candidates(
    repo: Any, target_spec: Any, *, repos_root: Any | None = None
) -> list[dict[str, Any]]:
    """`cluster_detect.compute_clusters()`'s `task_candidates_fn` adapter
    (Dashboard Advisory Surfaces Brief-vs-Task Matches).

    Resolves a brief's `repo` frontmatter to an on-disk directory via
    `_resolve_repo_dir` (same basename-under-`repos_root` resolution
    `auto_pick_brief`/`_remote_spec_branch` already use), picks that repo's
    `openspec/` root when it looks OpenSpec-shaped -- the only format with
    per-task granularity (`overlap_check._is_openspec_root`) -- else falls
    back to its devkit `docs/specs/` root, and delegates to
    `overlap_check.task_candidates()`.

    `overlap_check` is imported here rather than at module scope: it imports
    `find_spec_file`/`detect_stage` back from this module, so a top-level
    import here would be circular. Deferring it to call time (after this
    module has finished loading) avoids that without restructuring either
    module.

    Returns `[]` when `repo` doesn't resolve to a directory, matching this
    module's fail-open posture elsewhere -- `cluster_detect` also wraps this
    call in its own per-pair `try/except`, so any other failure here already
    degrades to no task edges for that `(repo, target_spec)` pair.
    """
    repo_dir = _resolve_repo_dir(repo, repos_root)
    if repo_dir is None:
        return []
    from . import overlap_check as _overlap_check

    openspec_root = repo_dir / "openspec"
    is_openspec = (openspec_root / "changes").is_dir() or (
        openspec_root / "specs"
    ).is_dir()
    specs_root = openspec_root if is_openspec else repo_dir / "docs" / "specs"
    return _overlap_check.task_candidates(specs_root, str(target_spec))


def auto_pick_brief(
    queue_briefs: list[dict[str, Any]],
    repo_filter: str | None = None,
    repos_root: Any | None = None,
) -> dict[str, Any]:
    """Deterministically pick the next brief for /go auto (spec 017 REQ-002/003).

    Ranking is FIFO oldest-first by the YYYYMMDD-HHMMSS filename prefix
    (lexicographic == chronological) — the feature exists to drain backlog, and
    newest-first would starve old briefs forever under steady inflow. A brief is
    skipped (recorded with a reason) when its frontmatter doesn't parse (every
    other gate reads frontmatter, so a corrupt brief would otherwise rank as
    maximally eligible — see work_queue.py's `_frontmatter_unparsable`), it is
    blocked, not yet due for recheck
    (`next-check-after` hasn't arrived — see work_queue.py's `_is_not_yet_due`),
    was released back to the queue within the last 20 minutes (another live
    session's claim/release race or a considered not-yet-actionable judgment —
    see work_queue.py's `_recently_released_info`), its repo is busy/missing/
    absent (_repo_busy_reason), or it doesn't match repo_filter. repo_filter
    matches the brief's repo by full path or basename.

    `repos_root`, when given, is also where a bare or `owner/name`-style
    `repo:` value (e.g. 'devops', 'behindthedash/devops') resolves by
    basename — see `_resolve_repo_dir`. Without it, such values are only
    checked as a literal (almost always nonexistent) path, same as before.

    Release scoping (feat/release-triage): briefs are ranked blocker-first
    (`triage: blocker` < untriaged < `triage: deferred`), FIFO within each
    tier. When a brief's repo policy sets `release_gate` (the repo is in a
    release freeze), a non-blocker brief for that repo is skipped entirely with
    reason `release-gate:<name>` — capture stays open during a freeze, but
    unattended scheduling is blockers-only. Interactive selection is unaffected.

    Returns {"pick": {...}|None, "skipped": [{"id", "reason"}, ...]}.
    """
    skipped: list[dict[str, str]] = []
    _TRIAGE_RANK = {"blocker": 0, None: 1, "deferred": 2}
    policy_cache: dict[str, dict[str, Any] | None] = {}

    def _release_gate_for(repo: Any) -> str | None:
        resolved = _resolve_repo_dir(repo, repos_root)
        if resolved is None:
            return None
        key = str(resolved)
        if key not in policy_cache:
            policy_cache[key] = _load_dashboard_policy(key)
        pol = policy_cache[key]
        gate = (pol or {}).get("release_gate")
        return str(gate) if gate else None

    def _rank_key(x: dict[str, Any]):
        triage = x.get("triage") if x.get("triage") in ("blocker", "deferred") else None
        return (_TRIAGE_RANK[triage], x.get("filename") or "")

    for b in sorted(queue_briefs, key=_rank_key):
        stem = (b.get("filename") or "").replace(".md", "")
        if b.get("unparsable"):
            # Every gate below reads frontmatter. When the block doesn't parse
            # they all read as absent, so an unparsable brief would rank as
            # maximally eligible -- the exact inversion that let /go auto pick
            # a brief carrying `next-check-after: 2026-08-31` on 2026-08-10.
            # Unattended scheduling fails closed; interactive selection can
            # still reach it (a human can see and repair the file).
            skipped.append({"id": stem, "reason": "unparsable-frontmatter"})
            continue
        if b.get("blocked"):
            skipped.append({"id": stem, "reason": "blocked"})
            continue
        if b.get("not_yet_due"):
            skipped.append({"id": stem, "reason": "not-yet-due"})
            continue
        if b.get("recently_released"):
            skipped.append({"id": stem, "reason": "recently-released"})
            continue
        path = b.get("path")
        fm = (
            _parse_fm(Path(path).read_text(errors="ignore"))
            if path and Path(path).is_file()
            else {}
        )
        repo = fm.get("repo")
        if repo_filter:
            rf = str(repo_filter).rstrip("/")
            r = str(repo or "").rstrip("/")
            if r != rf and Path(r).name != Path(rf).name:
                skipped.append({"id": stem, "reason": "repo-filter"})
                continue
        gate = _release_gate_for(repo)
        if gate and b.get("triage") != "blocker":
            skipped.append({"id": stem, "reason": f"release-gate:{gate}"})
            continue
        busy = _repo_busy_reason(repo, repos_root)
        if busy:
            skipped.append({"id": stem, "reason": busy})
            continue
        remote_branch = _remote_spec_branch(repo, fm.get("target-spec"), repos_root)
        if remote_branch:
            skipped.append(
                {"id": stem, "reason": f"remote-spec-branch:{remote_branch}"}
            )
            continue
        return {
            "pick": {
                "id": stem,
                "filename": b.get("filename"),
                "path": path,
                "focus": b.get("focus"),
                "repo": repo,
            },
            "skipped": skipped,
        }
    return {"pick": None, "skipped": skipped}


AUTO_PICK_MISS_LOG_ENV = "WORKTRAIL_AUTO_PICK_MISS_LOG"
MAX_AUTO_PICK_MISS_ENTRIES = 200  # bounded like agent_capacity.py's audit log


def auto_pick_miss_log_path(path: Path | None = None) -> Path:
    if path:
        return path
    override = env_setting(AUTO_PICK_MISS_LOG_ENV)
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "auto-pick-misses.jsonl"


def log_auto_pick_miss(
    auto_pick: dict[str, Any],
    total_briefs: int,
    repo_filter: str | None = None,
    path: Path | None = None,
) -> None:
    """Append a JSONL record of WHY `--auto` found nothing to pick, when it found
    nothing to pick.

    drain.py's own run log only ever recorded the `no_pick` outcome itself --
    not `auto_pick_brief()`'s per-brief skip reasons -- so a "62 briefs looked
    ready, why did no_pick fire" question was unanswerable after the fact
    (confirmed live 2026-08-03: no evidence existed to reconstruct the prior
    night's miss). This closes that gap at the source: auto_pick_brief() is the
    only place that computes the skip reasons, regardless of caller (an
    interactive `/go auto` session or a drain-spawned one-shot), so logging
    here covers both without threading anything through drain.py.

    Best-effort: a write failure (permissions, missing parent, full disk) is
    swallowed rather than breaking the dashboard render this is a side effect
    of -- an unlogged miss is a regression in observability, not correctness.
    """
    if auto_pick.get("pick") is not None:
        return
    log_path = auto_pick_miss_log_path(path)
    skipped = auto_pick.get("skipped") or []
    reasons: dict[str, int] = {}
    for entry in skipped:
        reason = str(entry.get("reason", "")).split(":", 1)[0]
        reasons[reason] = reasons.get(reason, 0) + 1
    record = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "repo_filter": repo_filter,
        "total_briefs": total_briefs,
        "skipped_count": len(skipped),
        "reasons": reasons,
        "skipped": skipped,
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    existing.append(line)
        existing.append(json.dumps(record))
        if len(existing) > MAX_AUTO_PICK_MISS_ENTRIES:
            existing = existing[-MAX_AUTO_PICK_MISS_ENTRIES:]
        log_path.write_text("\n".join(existing) + "\n", encoding="utf-8")
    except OSError:
        pass


def _hours_since_claim(claimed_at: Any) -> float | None:
    """Hours elapsed since an ISO-8601 `claimed-at` stamp; None if absent/unparseable."""
    if not claimed_at:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(claimed_at))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assume local, matching work_queue.py's stamp
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - dt).total_seconds() / 3600.0


INFLIGHT_STALE_HOURS = (
    48.0  # a picked brief younger than this is presumed actively owned
)

# Cluster-detection precision (cluster_telemetry.summarize()) is only worth
# surfacing once there's enough decided (consolidated + declined) outcomes
# for the ratio to mean something; below this it's noise, not a signal.
CLUSTER_PRECISION_MIN_DECIDED = 5


def inflight_briefs(
    picked_dir: Path | None, stale_hours: float = INFLIGHT_STALE_HOURS
) -> list[dict[str, Any]]:
    """Scan picked_dir for briefs with status: picked (not done) that look
    ABANDONED. Returns a list of stalled in-flight briefs with: filename, focus,
    claimed_at, hours_since_claim, repo, status, batched.

    Freshness filter: a picked brief claimed less than `stale_hours` ago is
    presumed actively owned by the session that claimed it and is NOT returned —
    surfacing it would waste a picker slot and invite a second session to
    collide with the owner. A brief claimed >= `stale_hours` ago (or with a
    missing/unparseable `claimed-at`, where active ownership can't be verified)
    is returned so it can be resumed. Pass stale_hours=0 to disable the filter.

    Batch-claimed companions (frontmatter `batch-primary` naming another brief
    that is itself still picked) are folded into their primary's entry — the
    primary carries `batched: N` instead of the companions appearing as N extra
    rows. A companion whose primary is done, released, or missing is listed
    standalone (its stale link is ignored).

    Returns [] if picked_dir is None, doesn't exist, or contains no picked briefs.
    """
    if not picked_dir:
        return []
    picked_dir = Path(picked_dir)
    if not picked_dir.is_dir():
        return []

    picked: list[dict[str, Any]] = []
    for brief_file in sorted(picked_dir.glob("*.md")):
        text = brief_file.read_text(errors="ignore")
        fm = _parse_fm(text)

        # Skip briefs with status != "picked"
        if fm.get("status") != "picked":
            continue

        # Extract focus: first non-empty line after the closing --- delimiter
        parts = text.split("---", 2)
        focus = ""
        if len(parts) >= 3:
            body = parts[2].strip()
            for line in body.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    focus = stripped
                    break

        picked.append(
            {
                "filename": brief_file.name,
                "stem": brief_file.stem,
                "batch_primary": fm.get("batch-primary"),
                "focus": focus,
                "claimed_at": fm.get("claimed-at"),
                "repo": fm.get("repo"),
                "status": "picked",
            }
        )

    picked_stems = {b["stem"] for b in picked}
    companion_counts: dict[str, int] = {}
    for b in picked:
        bp = b["batch_primary"]
        if bp and bp != b["stem"] and bp in picked_stems:
            companion_counts[bp] = companion_counts.get(bp, 0) + 1

    results: list[dict[str, Any]] = []
    for b in picked:
        bp = b["batch_primary"]
        if bp and bp != b["stem"] and bp in picked_stems:
            continue  # folded into its primary's entry
        hours = _hours_since_claim(b["claimed_at"])
        if stale_hours > 0 and hours is not None and hours < stale_hours:
            continue  # freshly claimed -> actively owned elsewhere; hide
        results.append(
            {
                "filename": b["filename"],
                "focus": b["focus"],
                "claimed_at": b["claimed_at"],
                "hours_since_claim": round(hours, 1) if hours is not None else None,
                "repo": b["repo"],
                "status": "picked",
                "batched": companion_counts.get(b["stem"], 0),
            }
        )

    return results


# --- rendering ---------------------------------------------------------------

# Stages that represent in-progress work needing the next SDD step. `empty` and
# `unspecd` are NOT here -- they are pre-spec backlog (need brainstorm) and are
# surfaced separately so a large unspec'd backlog can't drown out active work.
_ACTIVE = {
    "needs-clarification",
    "needs-tasks",
    "orchestrator-stuck",
    "ready-to-implement",
    "stale-bookkeeping",
    "verify-pending",
    "tail-pending",
    "sync-pending",
    "error",  # unreadable spec folder — surfaced so degradation is never silent
}
_BACKLOG = {"unspecd", "empty"}

# Stage subgroups used by the two-level category picker.
_READY_STAGES = {
    "orchestrator-stuck",
    "ready-to-implement",
    "stale-bookkeeping",
    "verify-pending",
    "tail-pending",
    "sync-pending",
}
_TASK_STAGES = {"needs-tasks", "needs-clarification"}

# Display order + header for each active stage, most-actionable first. Drives the
# category-grouped compact dashboard. (`complete` = code done, open-PR -- terminal
# enough that it is not grouped as active work, matching the pick-list priority.)
_CATEGORY_ORDER = [
    ("error", "Unreadable spec folder"),
    ("orchestrator-stuck", "Needs stuck-run recovery"),
    ("ready-to-implement", "Ready to implement"),
    ("stale-bookkeeping", "Needs status closeout"),
    ("verify-pending", "Needs verify / merge"),
    ("tail-pending", "Needs E2E / cleanup tail"),
    ("sync-pending", "Needs sync"),
    ("needs-tasks", "Needs tasking"),
    ("needs-clarification", "Needs clarification"),
]


def _constitution_hint(con: dict[str, bool] | None) -> str | None:
    """One-line nudge, shown only when something's missing (quiet when present)."""
    if not con or (con["architecture"] and con["ontology"]):
        return None
    missing = [n for n in ("architecture", "ontology") if not con[n]]
    return (
        f"Constitution: missing {', '.join(m + '.md' for m in missing)} under "
        f"docs/specs/ -> recommended before new specs (run constitution)."
    )


# --- multi-repo overview -----------------------------------------------------
# `go` is usually launched from a multi-repo parent. Instead of asking the user to
# pick a repo blind, scan every candidate repo's docs/specs and report which carry
# outstanding (in-progress) work. Pure file inspection, same as the single-repo path.


def _find_worktrees(parent: Path, repo_name: str) -> list[Path]:
    """Worktree checkouts live at <parent>/<repo_name>-worktrees/<branch>/.
    Each is a git worktree with a .git FILE (not dir) pointing back to the
    canonical repo. Returns sorted list; empty if the directory doesn't exist
    or is_git_repo is unavailable."""
    if _is_git_repo is None:
        return []
    wt_parent = parent / f"{repo_name}-worktrees"
    if not wt_parent.is_dir():
        return []
    return sorted(d for d in wt_parent.iterdir() if d.is_dir() and _is_git_repo(d))


def _detect_for_repo(args: tuple) -> tuple:
    """Worker for flat parallel detection across repos.
    Returns (repo_path_str, stage_dict) so results can be reassembled per-repo."""
    repo_key, spec_dir = args
    if "openspec" in spec_dir.parts and "changes" in spec_dir.parts:
        return (repo_key, _safe_detect_openspec(spec_dir))
    return (repo_key, _safe_detect_stage(spec_dir))


def scan_repos(
    parent: Path, run_record_dir: Path | None = None
) -> list[dict[str, Any]]:
    """One row per candidate repo under `parent`, repos with active specs first.

    Scans each repo's BASE-BRANCH `docs/specs` only -- worktree spec state is
    deliberately NOT overlaid. The base checkout is the committed reality; a
    worktree from a merged/abandoned branch would otherwise resurface a spec as
    active long after the work landed. Worktrees are instead reported (by name)
    under `worktrees` so the dashboard can offer a stale-worktree cleanup action,
    and the orchestrator run journals (read by detect_stage) still surface
    verify/tail resume state. (See go SKILL.md "stale-worktree cleanup".)

    Each row: {repo, path, has_specs, total, active, active_ids, active_specs,
    backlog, backlog_ids, worktrees, policy_findings, automerge_findings,
    drift_findings, quarantine_findings}.
    Returns [] if the sibling resolver is unavailable or `parent` holds no
    git repos.

    `policy_findings` is `policy_selfcheck.check_repo()`'s cross-repo
    copy-paste signals for this repo's `worktrail-go-policy.yaml` (empty = clean, or
    the sibling module is unavailable — degrades silently, matching every
    other optional import in this file).

    `automerge_findings` is `automerge_selfcheck.check_repo()`'s signals for
    this repo's `.github/workflows/*.yml` auto-merge automation not actually
    gating `gh pr merge --auto` on the `go:no-automerge` PR label (empty =
    clean or no automerge workflow present).

    `drift_findings` is `policy_drift_selfcheck.check_repo()`'s signals for
    this repo's `worktrail-go-policy.yaml` no longer describing repo reality — test files
    no runner reaches, or absence-claims contradicted by the filesystem (empty
    = clean or no policy file present).

    `quarantine_findings` is `quarantine_selfcheck.check_repo()`'s signals for
    this repo's orchestrator run journals recording a `QUARANTINED` group
    that needs human review (empty = clean or no run journals present).
    `quarantine_resumable` is the same check's groups quarantined only because
    a run's `--run-budget` was exhausted mid-fan-out -- the group never
    failed, so these are safely resumable with a plain re-run and are kept
    out of `quarantine_findings`' human-triage list.

    `journal_findings` is `journal_selfcheck.check_repo()`'s signals -- run
    journal invariant violations (stranded-tail, malformed-journal) plus
    malformed `run_record_dir/<repo>/*.yaml` records (empty = clean).
    `run_record_dir` is threaded through so both readers agree on where
    records live (env override, then `worktrail_home()/runs`, same as `load_recent_runs`).

    detect_stage calls are parallelised with a flat ThreadPoolExecutor across
    all spec dirs in all repos -- no nested pools, one thread per spec dir.
    """
    if _list_candidate_repos is None:
        return []
    parent = Path(parent)
    repos = _list_candidate_repos(parent)

    # Collect metadata and spec dirs for all repos in one pass.
    repo_keys: list[str] = []
    repo_info: dict[str, dict[str, Any]] = {}
    all_pairs: list[tuple] = []
    sibling_names = (
        _discover_repo_names(parent) if _discover_repo_names is not None else []
    )
    for repo in repos:
        repo_key = str(repo)
        repo_keys.append(repo_key)
        specs_root = repo / "docs" / "specs"
        policy_findings: list[dict[str, Any]] = []
        if _policy_check_repo is not None:
            policy_findings = _policy_check_repo(repo, sibling_names)["findings"]
        automerge_findings: list[dict[str, Any]] = []
        if _automerge_check_repo is not None:
            automerge_findings = _automerge_check_repo(repo)["findings"]
        drift_findings: list[dict[str, Any]] = []
        if _policy_drift_check_repo is not None:
            drift_findings = _policy_drift_check_repo(repo)["findings"]
        quarantine_findings: list[dict[str, Any]] = []
        quarantine_resumable: list[dict[str, Any]] = []
        if _quarantine_check_repo is not None:
            _qr = _quarantine_check_repo(repo)
            quarantine_findings = _qr["findings"]
            quarantine_resumable = _qr["resumable"]
        journal_findings: list[dict[str, Any]] = []
        if _journal_check_repo is not None:
            journal_findings = _journal_check_repo(repo, run_record_dir=run_record_dir)[
                "findings"
            ]
        repo_info[repo_key] = {
            "name": repo.name,
            "path": str(repo),
            "has_specs": specs_root.is_dir() or bool(_openspec_change_dirs(repo)),
            "worktrees": [wt.name for wt in _find_worktrees(parent, repo.name)],
            "policy_findings": policy_findings,
            "automerge_findings": automerge_findings,
            "drift_findings": drift_findings,
            "quarantine_findings": quarantine_findings,
            "quarantine_resumable": quarantine_resumable,
            "journal_findings": journal_findings,
        }
        if specs_root.is_dir():
            for d in sorted(specs_root.iterdir()):
                if _is_spec_folder(d):
                    all_pairs.append((repo_key, d))
        all_pairs.extend((repo_key, d) for d in _openspec_change_dirs(repo))

    # Flat parallel detect_stage across every spec dir in every repo.
    repo_specs: dict[str, list[dict[str, Any]]] = {k: [] for k in repo_keys}
    if all_pairs:
        with ThreadPoolExecutor() as ex:
            for repo_key, stage in ex.map(_detect_for_repo, all_pairs):
                repo_specs[repo_key].append(stage)

    # Assemble per-repo rows, preserving original repo order before the final sort.
    rows: list[dict[str, Any]] = []
    for repo_key in repo_keys:
        info = repo_info[repo_key]
        specs = repo_specs[repo_key]
        active_specs = [s for s in specs if s["stage"] in _ACTIVE]
        active_ids = sorted(s["id"] for s in active_specs)
        backlog_ids = sorted(s["id"] for s in specs if s["stage"] in _BACKLOG)
        rows.append(
            {
                "repo": info["name"],
                "path": info["path"],
                "has_specs": info["has_specs"] or bool(specs),
                "total": len(specs),
                "active": len(active_ids),
                "active_ids": active_ids,
                "active_specs": [
                    {
                        "id": s["id"],
                        "stage": s["stage"],
                        "next_action": s["next_action"],
                        "feature_summary": s.get("feature_summary"),
                    }
                    for s in active_specs
                ],
                "policy_findings": info["policy_findings"],
                "automerge_findings": info["automerge_findings"],
                "drift_findings": info["drift_findings"],
                "quarantine_findings": info["quarantine_findings"],
                "quarantine_resumable": info["quarantine_resumable"],
                "journal_findings": info["journal_findings"],
                "backlog": len(backlog_ids),
                "backlog_ids": backlog_ids,
                "worktrees": info["worktrees"],
            }
        )
    rows.sort(key=lambda r: (-r["active"], r["repo"].lower()))
    return rows


# --- deterministic pick list -------------------------------------------------
# Built entirely from data returned by this script (+ queue JSON passed in).
# Claude renders it; it does not construct it. That eliminates the LLM
# non-determinism that caused two /go windows to show different numbered lists.

_STAGE_PRIORITY = {
    "orchestrator-stuck": -1,
    "ready-to-implement": 0,
    "verify-pending": 0,
    "tail-pending": 0,
    "stale-bookkeeping": 1,
    "sync-pending": 1,
    "needs-tasks": 2,
    "needs-clarification": 3,
}


def _repo_scope_match(item_repo: str | None, queue_repo: str) -> bool:
    """Same comparison auto_pick_brief applies for its repo_filter: exact
    (trailing-slash-insensitive) or basename equality, so an item whose
    `repo:` is a path still matches a bare repo name and vice versa. An
    item with no repo at all does NOT match a repo-scoped view -- it is
    not this repo's work any more than another repo's is."""
    rf = str(queue_repo).rstrip("/")
    r = str(item_repo or "").rstrip("/")
    return bool(r) and (r == rf or Path(r).name == Path(rf).name)


# --- two-level category picker -----------------------------------------------

_CATEGORY_DESC = {
    "decisions": "Answer a blocked product decision — unblocks the brief automatically.",
    "ready": "Run orchestrator, continue verify, or sync on these specs.",
    "needs-tasks": "Generate task DAG (spec-to-tasks) or resolve clarifications.",
    "workqueue": "Claim a queued brief or resume stalled in-flight work.",
    "new-work": "Brainstorm a new feature or pick from the backlog.",
}


def build_category_actions(
    repo_rows: list[dict[str, Any]] | None,
    spec_rows: list[dict[str, Any]] | None,
    inflight: list[dict[str, Any]],
    queue_briefs: list[dict[str, Any]],
    open_decisions: list[dict[str, Any]] | None = None,
    queue_repo: str | None = None,
) -> list[dict[str, Any]]:
    """Level-1 AskUserQuestion buttons: one per work category (≤4).

    Categories are only included when they have actionable items. The five
    possible categories in priority order: decisions, ready/in-progress,
    needs-tasking, work-queue, new-work. 'New work' is always present as the
    final entry, so it is the first one dropped by the ≤4 truncation.

    `queue_repo`, when set, scopes `inflight`/`queue_briefs`/`open_decisions`
    to that repo the same way `render_dashboard`'s own `queue_repo` filter
    does for its text output — a repo-scoped `/go <repo>` dashboard must not
    count another repo's queued briefs or open decisions toward these
    buttons (see `_repo_scope_match`)."""
    if queue_repo:
        inflight = [b for b in inflight if _repo_scope_match(b.get("repo"), queue_repo)]
        queue_briefs = [
            b for b in queue_briefs if _repo_scope_match(b.get("repo"), queue_repo)
        ]
        open_decisions = [
            d
            for d in (open_decisions or [])
            if _repo_scope_match(d.get("repo"), queue_repo)
        ]

    actives: list[dict[str, Any]] = []
    if repo_rows is not None:
        for repo in repo_rows:
            actives.extend(repo.get("active_specs") or [])
    else:
        actives = [s for s in (spec_rows or []) if s["stage"] in _ACTIVE]

    ready_count = sum(1 for s in actives if s["stage"] in _READY_STAGES)
    tasks_count = sum(1 for s in actives if s["stage"] in _TASK_STAGES)
    # Blocked, not-yet-due, and recently-released briefs are not claimable
    # (build_category_items filters them out of Level 2), so none of them must
    # count toward the Level-1 button — a queue of only such briefs would
    # otherwise produce a category with zero options.
    queue_count = len(inflight) + sum(
        1
        for b in queue_briefs
        if not b.get("blocked")
        and not b.get("not_yet_due")
        and not b.get("recently_released")
    )

    decisions_count = len(open_decisions or [])

    categories: list[dict[str, Any]] = []
    if decisions_count:
        categories.append(
            {
                "label": f"Open decisions ({decisions_count})",
                "description": _CATEGORY_DESC["decisions"],
                "category": "decisions",
            }
        )
    if ready_count:
        categories.append(
            {
                "label": f"Ready / in-progress ({ready_count})",
                "description": _CATEGORY_DESC["ready"],
                "category": "ready",
            }
        )
    if tasks_count:
        categories.append(
            {
                "label": f"Needs tasking ({tasks_count})",
                "description": _CATEGORY_DESC["needs-tasks"],
                "category": "needs-tasks",
            }
        )
    if queue_count:
        categories.append(
            {
                "label": f"Work queue ({queue_count})",
                "description": _CATEGORY_DESC["workqueue"],
                "category": "workqueue",
            }
        )
    categories.append(
        {
            "label": "New work",
            "description": _CATEGORY_DESC["new-work"],
            "category": "new-work",
        }
    )

    for n, cat in enumerate(categories[:4], 1):
        cat["n"] = n
    return categories[:4]


def build_category_items(
    repo_rows: list[dict[str, Any]] | None,
    spec_rows: list[dict[str, Any]] | None,
    inflight: list[dict[str, Any]],
    queue_briefs: list[dict[str, Any]],
    backlog_total: int = 0,
    clusters: list[dict[str, Any]] | None = None,
    open_decisions: list[dict[str, Any]] | None = None,
    queue_repo: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Level-2 items per category for the two-level /go picker.

    Returns a dict keyed by category string. Each value is a list of ≤4 items
    with full dispatch data (action, spec_id, repo, path, next_action). Items
    beyond 4 are reachable via 'Other' in the AskUserQuestion level-2 call.
    'decisions' is capped at 4 with no overflow item (matching every other
    category's own cap) and is omitted entirely when there are no open
    decisions, so callers that never pass `open_decisions` see byte-identical
    output.

    `queue_repo`, when set, scopes `inflight`/`open_decisions` to that repo
    before any items are built from them, and later scopes the individually
    claimable (non-cluster) queue items the same way — see
    `build_category_actions`'s matching parameter and `_repo_scope_match`.
    Cluster membership matching (below) deliberately uses the *unscoped*
    `queue_briefs`: consolidation is a queue-wide concern, matching
    `render_dashboard`'s own `clusters` section, which `queue_repo` never
    filters either."""
    if queue_repo:
        inflight = [b for b in inflight if _repo_scope_match(b.get("repo"), queue_repo)]
        open_decisions = [
            d
            for d in (open_decisions or [])
            if _repo_scope_match(d.get("repo"), queue_repo)
        ]

    default_repo_root = _dashboard_repo_root()

    def _spec_item(
        s: dict[str, Any], repo: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        label = f"{repo['repo']}: {s['id']}" if repo else s["id"]
        # stale-bookkeeping closes out by flipping task status (files already merged),
        # NOT by dispatching the orchestrator -- a distinct action keeps it off route E.
        action = "close-stale" if s["stage"] == "stale-bookkeeping" else "implement"
        repo_root = _item_repo_root(repo or s, _dashboard_repo_root())
        return {
            "type": "spec",
            "label": label,
            "description": s["next_action"].split(" (")[0],
            "action": action,
            "spec_id": s["id"],
            "repo": repo["repo"] if repo else None,
            "path": repo["path"] if repo else None,
            "next_action": s["next_action"],
            "stage": s["stage"],
            "planned-agent": _planned_agent_for_item(repo_root),
        }

    ready_items: list[dict[str, Any]] = []
    tasks_items: list[dict[str, Any]] = []

    # Most-actionable stage first (id is the tiebreaker) — priority must lead the
    # key, or the ≤4-item cap fills in spec-id order and a stuck run can vanish.
    if repo_rows is not None:
        for repo in repo_rows:
            repo_root = _item_repo_root(repo, default_repo_root)
            for s in sorted(
                repo.get("active_specs") or [],
                key=lambda x: (_STAGE_PRIORITY.get(x["stage"], 99), x["id"]),
            ):
                if s["stage"] in _READY_STAGES:
                    item = _spec_item(s, repo)
                    item["planned-agent"] = _planned_agent_for_item(repo_root)
                    ready_items.append(item)
                elif s["stage"] in _TASK_STAGES:
                    item = _spec_item(s, repo)
                    item["planned-agent"] = _planned_agent_for_item(repo_root)
                    tasks_items.append(item)
    else:
        for s in sorted(
            (s for s in (spec_rows or []) if s["stage"] in _ACTIVE),
            key=lambda x: (_STAGE_PRIORITY.get(x["stage"], 99), x["id"]),
        ):
            if s["stage"] in _READY_STAGES:
                ready_items.append(_spec_item(s))
            elif s["stage"] in _TASK_STAGES:
                tasks_items.append(_spec_item(s))

    workqueue_items: list[dict[str, Any]] = []
    for brief in sorted(
        inflight, key=lambda b: b.get("claimed_at") or "", reverse=True
    )[:2]:
        brief_id = brief["filename"].replace(".md", "")
        batched = brief.get("batched") or 0
        desc = (
            f"Resume this stalled batch ({batched + 1} briefs) — claimed long ago with no completion; likely an abandoned session."
            if batched
            else "Resume this stalled brief — claimed long ago with no completion; likely an abandoned session."
        )
        workqueue_items.append(
            {
                "type": "inflight",
                "label": f"Resume: {brief_id}",
                "description": desc,
                "action": "resume",
                "id": brief_id,
                "planned-agent": _planned_agent_for_item(
                    _item_repo_root(brief, default_repo_root)
                ),
            }
        )

    queue_ids = {b["filename"].replace(".md", "") for b in queue_briefs}
    cluster_member_ids: set = set()
    remaining_slots = 4 - len(workqueue_items)
    if remaining_slots > 0:
        for cluster in clusters or []:
            filtered_members = [m for m in cluster.get("members", []) if m in queue_ids]
            if len(filtered_members) < 2:
                continue
            if remaining_slots <= 0:
                break
            signals = cluster.get("signals", [])
            label = f"Consolidate {len(filtered_members)} briefs" + (
                f" ({signals[0]})" if signals else ""
            )
            workqueue_items.append(
                {
                    "type": "cluster",
                    "label": label,
                    "description": "Review this cluster of related briefs for consolidation into one action.",
                    "action": "consolidate-cluster",
                    "members": filtered_members,
                    "signals": signals,
                }
            )
            cluster_member_ids.update(filtered_members)
            remaining_slots -= 1

    # Individually claimable items ARE repo-scoped (unlike cluster matching
    # above): a repo-scoped picker must not offer another repo's brief as a
    # direct claim target.
    if queue_repo:
        queue_briefs = [
            b for b in queue_briefs if _repo_scope_match(b.get("repo"), queue_repo)
        ]

    unblocked_queue = [
        b
        for b in queue_briefs
        if not b.get("blocked")
        and not b.get("not_yet_due")
        and not b.get("recently_released")
        and b["filename"].replace(".md", "") not in cluster_member_ids
    ]
    remaining_slots = 4 - len(workqueue_items)
    if remaining_slots > 0:
        if len(unblocked_queue) > remaining_slots:
            visible_queue = unblocked_queue[: remaining_slots - 1]
            overflow_queue = unblocked_queue[remaining_slots - 1 :]
        else:
            visible_queue = unblocked_queue[:remaining_slots]
            overflow_queue = []
        for brief in visible_queue:
            brief_id = brief["filename"].replace(".md", "")
            workqueue_items.append(
                {
                    "type": "queue",
                    "label": (brief.get("focus") or brief_id)[:60],
                    "description": "Claim and start this queued brief.",
                    "action": "claim",
                    "id": brief_id,
                    "planned-agent": _planned_agent_for_item(
                        _item_repo_root(brief, default_repo_root)
                    ),
                }
            )
        if overflow_queue:
            workqueue_items.append(
                {
                    "type": "see-more",
                    "label": f"Show more ({len(overflow_queue)} remaining)",
                    "description": "See additional queued briefs.",
                    "action": "see-more",
                    "overflow_ids": [
                        b["filename"].replace(".md", "") for b in overflow_queue
                    ],
                }
            )

    new_work_items: list[dict[str, Any]] = [
        {
            "type": "fixed",
            "label": "Start new feature",
            "description": "Brainstorm a brand-new feature.",
            "action": "brainstorm",
        }
    ]
    if backlog_total:
        new_work_items.append(
            {
                "type": "fixed",
                "label": f"Pick from backlog ({backlog_total})",
                "description": "Browse the unspec'd backlog and brainstorm one.",
                "action": "see-backlog",
            }
        )

    decision_items: list[dict[str, Any]] = []
    for decision in (open_decisions or [])[:4]:
        question = decision.get("question")
        item = {
            "type": "decision",
            "action": "answer-decision",
            "id": decision.get("id"),
            "label": (question or decision.get("id"))[:60],
            "description": "Answer this blocked decision to unblock the brief.",
        }
        if decision.get("repo") is not None:
            item["repo"] = decision["repo"]
        if decision.get("brief") is not None:
            item["brief"] = decision["brief"]
        decision_items.append(item)

    result: dict[str, list[dict[str, Any]]] = {}
    for key, items in [
        ("decisions", decision_items),
        ("ready", ready_items),
        ("needs-tasks", tasks_items),
        ("workqueue", workqueue_items),
        ("new-work", new_work_items),
    ]:
        if key == "decisions" and not items:
            continue
        numbered = items[:4]
        for i, item in enumerate(numbered, 1):
            item["n"] = i
        result[key] = numbered
    return result


def load_recent_runs(
    repo: Path, limit: int = 5, runs_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Read the `limit` most-recent go run records for `repo`.

    Records live at `<runs_dir>/<repo-name>/<run-id>.yaml` (run_record.py's own
    layout; `runs_dir` defaults to `worktrail_home()/runs`, overridable via
    `WORKTRAIL_RUN_RECORD_DIR` -- mirrors cluster_telemetry.py's default/override
    pattern). Sorted by `completed_at`, falling back to `started_at` for runs
    still in progress (no `finish` entry yet). Read-only and best-effort: a
    missing directory, unreadable file, or corrupt record is skipped rather
    than crashing the dashboard render.
    """
    if _load_run_record is None:
        return []
    if runs_dir is None:
        override = env_setting("WORKTRAIL_RUN_RECORD_DIR")
        runs_dir = (
            Path(override).expanduser() if override else worktrail_home() / "runs"
        )
    run_dir = runs_dir / Path(repo).resolve().name
    if not run_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in run_dir.glob("*.yaml"):
        try:
            record = _load_run_record(path)
        except Exception:  # noqa: BLE001, S112 - a corrupt record must not break the render
            continue
        records.append(
            {
                "run_id": record.get("run_id"),
                "selected_route": record.get("selected_route"),
                "final_status": record.get("final_status"),
                "pull_request": record.get("pull_request"),
                "started_at": record.get("started_at"),
                "completed_at": record.get("completed_at"),
            }
        )
    records.sort(
        key=lambda r: r.get("completed_at") or r.get("started_at") or "", reverse=True
    )
    return records[:limit]


def render_dashboard(
    repo_rows: list[dict[str, Any]] | None,
    spec_rows: list[dict[str, Any]] | None,
    inflight: list[dict[str, Any]],
    queue_briefs: list[dict[str, Any]],
    worktrees: list[str] | None = None,
    con: dict[str, bool] | None = None,
    clusters: list[dict[str, Any]] | None = None,
    cluster_precision: dict[str, Any] | None = None,
    capacity: dict[str, Any] | None = None,
    postmerge_check_failures: dict[str, Any] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    staleness_warnings: list[dict[str, Any]] | None = None,
    queue_repo: str | None = None,
    epic_rows: list[dict[str, Any]] | None = None,
) -> str:
    """The compact, category-grouped, deterministic dashboard the conductor prints
    verbatim (no LLM rendering). Active specs are grouped by work-category in
    _CATEGORY_ORDER; the unspec'd backlog collapses to a count + the first two; in-
    flight and queued handoffs show the top three; worktrees get a one-line cleanup
    nudge; cross-repo worktrail-go-policy.yaml contamination signals (policy_selfcheck.py) get
    a one-line review nudge; unguarded auto-merge workflow signals
    (automerge_selfcheck.py) get their own one-line review nudge; worktrail-go-policy.yaml
    rationale-vs-reality drift signals (policy_drift_selfcheck.py) get theirs;
    QUARANTINED-group signals (quarantine_selfcheck.py) get their own one-line
    review nudge; post-merge check-failure signals (audit_postmerge.py's
    dashboard_snapshot()) get their own one-line review nudge, only emitted
    when `postmerge_check_failures` carries at least one flagged PR; outstanding
    (`epic-gap` / `epic-unparseable`) `docs/specs/epics/*.md` rows (single-repo
    mode: `epic_rows`; multi-repo mode: each repo row's own `epics`, scan_epics()'s
    per-repo result -- see main()) get their own one-line review nudge, omitted
    entirely when a repo has no outstanding epics; when a cluster
    section is shown and
    `cluster_precision` (cluster_telemetry.summarize()'s result) has at least
    CLUSTER_PRECISION_MIN_DECIDED decided outcomes, an extra precision line is
    appended under it. `recent_runs` (single-repo mode) or each repo row's own
    `recent_runs` (multi-repo mode, tagged with its repo name like worktrees)
    renders a "Recent runs" section, most-recent-first, capped at 5. Empty
    sections are omitted -- with no clusters or recent runs, output is
    byte-for-byte unchanged regardless of `cluster_precision`."""

    def _clip(s: str, n: int = 60) -> str:
        s = (s or "").strip()
        return (s[:n] + "…") if len(s) > n else s

    def _queue_repo_match(brief_repo: str | None) -> bool:
        return _repo_scope_match(brief_repo, queue_repo)

    if queue_repo:
        # A repo-scoped dashboard (e.g. `/go devops`) must not render other
        # repos' queued/in-flight briefs as if they were claimable here
        # (observed live 2026-08-13: `worktrail-go devops` listed datalena
        # briefs). Hidden counts are still surfaced -- never silently.
        inflight_hidden = sum(
            1 for b in inflight if not _queue_repo_match(b.get("repo"))
        )
        inflight = [b for b in inflight if _queue_repo_match(b.get("repo"))]
        queue_hidden = sum(
            1 for b in queue_briefs if not _queue_repo_match(b.get("repo"))
        )
        queue_briefs = [b for b in queue_briefs if _queue_repo_match(b.get("repo"))]
    else:
        inflight_hidden = queue_hidden = 0

    actives: list[dict[str, Any]] = []
    backlog: list[str] = []
    wt: list[str] = list(worktrees or [])
    policy_flags: list[str] = []
    automerge_flags: list[str] = []
    drift_flags: list[str] = []
    quarantine_flags: list[str] = []
    quarantine_resumable_flags: list[str] = []
    stranded_flags: list[str] = []
    outstanding_epics: list[str] = []
    runs: list[dict[str, Any]] = []
    if repo_rows is not None:
        for r in repo_rows:
            for s in r.get("active_specs", []):
                actives.append({"who": r["repo"], **s})
            backlog.extend(f"{r['repo']} {bid}" for bid in r.get("backlog_ids", []))
            wt.extend(f"{r['repo']}/{w}" for w in r.get("worktrees", []))
            policy_flags.extend(
                f"{r['repo']} ({f['signal']})" for f in r.get("policy_findings", [])
            )
            automerge_flags.extend(
                f"{r['repo']} ({f['signal']})" for f in r.get("automerge_findings", [])
            )
            drift_flags.extend(
                f"{r['repo']} ({f['signal']})" for f in r.get("drift_findings", [])
            )
            quarantine_flags.extend(
                f"{r['repo']} ({f['spec_id']}/{f['group']}, {f['age_days']:.0f}d)"
                for f in r.get("quarantine_findings", [])
            )
            quarantine_resumable_flags.extend(
                f"{r['repo']} ({f['spec_id']}/{f['group']}, {f['age_days']:.0f}d)"
                for f in r.get("quarantine_resumable", [])
            )
            stranded_flags.extend(
                f"{r['repo']} ({f['spec_id']}: {f['kind']})"
                for f in r.get("journal_findings", [])
            )
            outstanding_epics.extend(
                f"{r['repo']} ({e['id']}: {e['stage']})"
                for e in r.get("epics", []) or []
                if e.get("stage") in ("epic-gap", "epic-unparseable")
            )
            for run in r.get("recent_runs", []) or []:
                runs.append({"who": r["repo"], **run})
        runs.sort(
            key=lambda x: x.get("completed_at") or x.get("started_at") or "",
            reverse=True,
        )
        runs = runs[:5]
    else:
        for s in spec_rows or []:
            if s["stage"] in _ACTIVE:
                actives.append({"who": None, **s})
            elif s["stage"] in _BACKLOG:
                backlog.append(s["id"])
        runs = list(recent_runs or [])
        outstanding_epics.extend(
            f"{e['id']} ({e['stage']})"
            for e in (epic_rows or [])
            if e.get("stage") in ("epic-gap", "epic-unparseable")
        )

    lines: list[str] = []
    if staleness_warnings:
        for w in staleness_warnings:
            who = f"{w['repo']}: " if w.get("repo") else ""
            lines.append(f"⚠️  Stale checkout — {who}{w['warning']}")
        lines.append("")
    hint = _constitution_hint(con)
    if hint:
        lines += [hint, ""]

    if actives:
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for a in actives:
            by_stage.setdefault(a["stage"], []).append(a)
        lines.append("📋 Active work")
        for stage, header in _CATEGORY_ORDER:
            group = by_stage.get(stage)
            if not group:
                continue
            # The clipped action string is a pure function of stage, so it is
            # hoisted into the header once instead of repeated on every row.
            action = group[0]["next_action"].split(" (")[0].split(";")[0]
            lines.append(f"  {header} ({len(group)}) → {action}")
            for a in sorted(group, key=lambda x: (x.get("who") or "", x["id"])):
                who = f"{a['who']} " if a.get("who") else ""
                lines.append(f"    {who}{a['id']}")

    if inflight:
        ordered = sorted(
            inflight, key=lambda b: b.get("claimed_at") or "", reverse=True
        )
        inflight_note = (
            f", +{inflight_hidden} in other repos" if inflight_hidden else ""
        )
        lines.append(
            f"🛠️  Stalled in-flight ({len(ordered)}{inflight_note}) — claimed long ago, likely abandoned"
        )
        for b in ordered[:3]:
            bid = b["filename"].replace(".md", "")
            batched = b.get("batched") or 0
            batch_tag = f" (+{batched} batched)" if batched else ""
            focus = _clip(b.get("focus", ""))
            lines.append(f"    • {bid}{batch_tag}" + (f" — {focus}" if focus else ""))
        if len(ordered) > 3:
            lines.append(f"    … +{len(ordered) - 3} more")

    if queue_briefs:
        # Claimable briefs first; blocked/not-yet-due/recently-released ones are
        # listed but flagged so the rendered count and the picker's claimable
        # set can't silently disagree.
        ordered_q = sorted(
            queue_briefs,
            key=lambda b: (
                bool(
                    b.get("blocked")
                    or b.get("not_yet_due")
                    or b.get("recently_released")
                ),
                {"blocker": 0, "deferred": 2}.get(str(b.get("triage") or ""), 1),
            ),
        )
        n_blockers = sum(1 for b in queue_briefs if b.get("triage") == "blocker")
        blocker_note = (
            f", {n_blockers} blocker" + ("s" if n_blockers != 1 else "")
            if n_blockers
            else ""
        )
        hidden_note = f", +{queue_hidden} in other repos" if queue_hidden else ""
        lines.append(
            f"📥 Queued handoffs ({len(ordered_q)}{blocker_note}{hidden_note})"
        )
        for b in ordered_q[:3]:
            label = _clip(b.get("focus") or b["filename"].replace(".md", ""))
            if b.get("blocked"):
                tag = " [blocked]"
            elif b.get("not_yet_due"):
                tag = " [watching]"
            elif b.get("recently_released"):
                by = (
                    f" by {b['recently_released_by']}"
                    if b.get("recently_released_by")
                    else ""
                )
                tag = f" [recently released{by}]"
            elif b.get("triage") == "blocker":
                tag = " [blocker]"
            elif b.get("triage") == "deferred":
                tag = " [deferred]"
            else:
                tag = ""
            lines.append(f"    • {label}{tag}")
        if len(ordered_q) > 3:
            lines.append(f"    … +{len(ordered_q) - 3} more")
    elif queue_hidden:
        # Every queued brief belongs to other repos: keep the queue visible as
        # a count so the repo-scoped view never implies the queue is empty.
        lines.append(
            f"📥 Queued handoffs: none for this repo (+{queue_hidden} in other repos)"
        )

    if backlog:
        head = ", ".join(backlog[:2])
        more = f" … +{len(backlog) - 2}" if len(backlog) > 2 else ""
        lines.append(f"📝 Unspec'd backlog ({len(backlog)})")
        lines.append(f"    {head}{more} → brainstorm")

    if wt:
        head = ", ".join(wt[:4])
        more = f" … +{len(wt) - 4}" if len(wt) > 4 else ""
        lines.append(f"⚠️  Worktrees ({len(wt)}): {head}{more} → review/prune")

    if policy_flags:
        head = ", ".join(policy_flags[:4])
        more = f" … +{len(policy_flags) - 4}" if len(policy_flags) > 4 else ""
        lines.append(
            f"🚩 Policy contamination ({len(policy_flags)}): {head}{more} → review worktrail-go-policy.yaml"
        )

    if automerge_flags:
        head = ", ".join(automerge_flags[:4])
        more = f" … +{len(automerge_flags) - 4}" if len(automerge_flags) > 4 else ""
        lines.append(
            f"🚩 Unguarded auto-merge ({len(automerge_flags)}): {head}{more} "
            "→ review .github/workflows/auto-merge.yml"
        )

    if drift_flags:
        head = ", ".join(drift_flags[:4])
        more = f" … +{len(drift_flags) - 4}" if len(drift_flags) > 4 else ""
        lines.append(
            f"🚩 Policy drift ({len(drift_flags)}): {head}{more} "
            "→ worktrail-go-policy.yaml no longer matches repo reality"
        )

    if stranded_flags:
        head = ", ".join(stranded_flags[:4])
        more = f" … +{len(stranded_flags) - 4}" if len(stranded_flags) > 4 else ""
        lines.append(
            f"🚩 Stranded runs ({len(stranded_flags)}): {head}{more} "
            "→ resume the run (stranded-tail) or inspect the journal (malformed-journal)"
        )

    if outstanding_epics:
        head = ", ".join(outstanding_epics[:4])
        more = f" … +{len(outstanding_epics) - 4}" if len(outstanding_epics) > 4 else ""
        lines.append(
            f"🧭 Outstanding epics ({len(outstanding_epics)}): {head}{more} "
            "→ spec the next unspecced feature (docs/specs/epics/*.md)"
        )

    if quarantine_flags:
        head = ", ".join(quarantine_flags[:4])
        more = f" … +{len(quarantine_flags) - 4}" if len(quarantine_flags) > 4 else ""
        lines.append(
            f"🚩 Quarantined groups ({len(quarantine_flags)}): {head}{more} → review"
        )

    if quarantine_resumable_flags:
        head = ", ".join(quarantine_resumable_flags[:4])
        more = (
            f" … +{len(quarantine_resumable_flags) - 4}"
            if len(quarantine_resumable_flags) > 4
            else ""
        )
        lines.append(
            f"🔁 Resumable quarantines ({len(quarantine_resumable_flags)}): {head}{more} "
            "→ just re-run full-real"
        )

    if capacity and capacity.get("gated"):
        entries = ", ".join(
            f"{item['provider']} ({item['failure_class']}, retry {item['retry_after']})"
            for item in capacity["gated"][:4]
        )
        more = f" … +{len(capacity['gated']) - 4}" if len(capacity["gated"]) > 4 else ""
        retry = capacity.get("retry_after") or "unknown"
        if capacity.get("all_gated"):
            lines.append(
                f"🚫 Headless capacity blocked: all configured providers gated ({entries}{more})"
                f" → retry after {retry}"
            )
        else:
            lines.append(
                f"⏳ Headless capacity gates ({len(capacity['gated'])}): {entries}{more}"
                " → fallback may be available"
            )

    if postmerge_check_failures and postmerge_check_failures.get("flagged"):
        pmf_flagged = postmerge_check_failures["flagged"]
        entries = ", ".join(
            f"{item.get('repo', '?')}#{_pr_number(item.get('url'))}"
            for item in pmf_flagged[:4]
        )
        more = f" … +{len(pmf_flagged) - 4}" if len(pmf_flagged) > 4 else ""
        lines.append(
            f"🚨 Post-merge check failures "
            f"({postmerge_check_failures.get('prs_flagged', len(pmf_flagged))} PR(s) across "
            f"{postmerge_check_failures.get('repos_flagged', 0)} repo(s)): {entries}{more}"
            " → review failing checks"
        )

    cl = list(clusters or [])
    if cl:
        lines.append(f"🔗 Consolidatable briefs ({len(cl)})")
        for c in cl:
            members = ", ".join(c.get("members", []))
            signals = ", ".join(c.get("signals", []))
            suffix = f" — {signals}" if signals else ""
            lines.append(f"    • {members}{suffix}")
        lines.append("    → Review for consolidation before dispatching separately.")
        if cluster_precision:
            consolidated = cluster_precision.get("consolidated", 0)
            declined = cluster_precision.get("declined", 0)
            decided = consolidated + declined
            if decided >= CLUSTER_PRECISION_MIN_DECIDED:
                shown = cluster_precision.get("shown", 0)
                pct = round((consolidated / decided) * 100)
                lines.append(
                    f"    Precision so far: {pct}% ({shown} shown, "
                    f"{consolidated} consolidated, {declined} declined)"
                )

    if runs:
        lines.append(f"🕘 Recent runs ({len(runs)})")
        for r in runs:
            who = f"{r['who']} " if r.get("who") else ""
            status = r.get("final_status") or "in progress"
            pr = r.get("pull_request") or "-"
            lines.append(
                f"    {who}{r.get('run_id')} | {r.get('selected_route')} | {status} | {pr}"
            )

    if not (actives or inflight or queue_briefs or backlog):
        lines.append(
            "No active specs, in-flight work, or queued handoffs. "
            "Start with: new feature → brainstorm."
        )
    return "\n".join(lines)


def _staleness_warnings(named_paths: list[tuple]) -> list[dict[str, Any]]:
    """Run `check_repo_freshness.check()` once per (name, path) pair; return
    only the entries found stale, as `{"repo": name, "warning": str}`.

    Best-effort like every other optional signal in this module: an
    unavailable sibling module or a failed check degrades to no warning for
    that repo, never a crashed render.
    """
    if _check_repo_freshness is None:
        return []
    warnings: list[dict[str, Any]] = []
    for name, path in named_paths:
        try:
            result = _check_repo_freshness(Path(path))
        except Exception:  # noqa: BLE001, S112 — never crash the dashboard render
            continue
        if result.get("stale") and result.get("warning"):
            warnings.append({"repo": name, "warning": result["warning"]})
    return warnings


def _resolve_json_arg(inline: str | None, file_path: str | None) -> str | None:
    """Resolve a `--foo-json`/`--foo-json-file` pair to the raw JSON string.

    `file_path` wins when both are given -- it exists specifically to sidestep
    the shell's per-argument size limit (Linux MAX_ARG_STRLEN, ~128KB) that
    `inline` is subject to, so a caller that already switched to the file form
    should never silently fall back to the capped one. `file_path == "-"` reads
    from stdin. Returns None when neither is given.
    """
    if file_path:
        if file_path == "-":
            return sys.stdin.read()
        return Path(file_path).read_text()
    return inline


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="sdd-workflow conductor resume dashboard")
    p.add_argument(
        "--root", default="docs/specs", help="specs root to scan (default: docs/specs)"
    )
    p.add_argument(
        "--picked-dir",
        default=None,
        help="work-queue picked dir to scan for in-flight briefs "
        "(default: $WORK_QUEUE_DIR/picked or ~/work-queue/picked)",
    )
    p.add_argument(
        "--inflight-stale-hours",
        type=float,
        default=INFLIGHT_STALE_HOURS,
        help="only surface picked briefs claimed at least this many hours ago "
        "(fresh claims are presumed actively owned by another session; "
        f"0 disables the filter; default: {INFLIGHT_STALE_HOURS:g})",
    )
    p.add_argument(
        "--auto",
        action="store_true",
        help="compute the /go auto pick (spec 017): add an `auto_pick` field to the "
        "JSON output — FIFO oldest-first unblocked queue brief whose repo exists "
        "and has no live orchestrator RunLock",
    )
    p.add_argument(
        "--auto-repo",
        default=None,
        help="restrict --auto to briefs whose repo matches this path or basename",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument(
        "--spec",
        default=None,
        help="inspect a single spec folder and print its stage JSON",
    )
    p.add_argument(
        "--repos",
        default=None,
        help="multi-repo overview: scan every candidate repo under this "
        "parent dir for outstanding specs (use when launched from a "
        "multi-repo parent)",
    )
    p.add_argument(
        "--queue-json",
        default=None,
        help="JSON string from 'work_queue.py list --json'; fed into the "
        "category picker so /go renders the list from data, not LLM judgment. "
        "Linux caps a single argv string at ~128KB (MAX_ARG_STRLEN) -- once the "
        "queue grows past that, use --queue-json-file instead.",
    )
    p.add_argument(
        "--queue-json-file",
        default=None,
        help="path to a file containing the same JSON as --queue-json (or '-' to "
        "read it from stdin); avoids the shell's per-argument size limit for a "
        "large queue. Takes precedence over --queue-json when both are given.",
    )
    p.add_argument(
        "--decisions-json",
        default=None,
        help="JSON string from 'worktrail-decision list --status open --json'; fed into the "
        "category picker so /go renders open decisions from data, not LLM judgment",
    )
    p.add_argument(
        "--decisions-json-file",
        default=None,
        help="path to a file containing the same JSON as --decisions-json (or '-' to "
        "read it from stdin). Takes precedence over --decisions-json when both are given.",
    )
    p.add_argument(
        "--capacity-cache",
        default=None,
        help="provider capacity cache path (default: WORKTRAIL_AGENT_CAPACITY_CACHE or "
        "~/.worktrail/agent-capacity.json)",
    )
    p.add_argument(
        "--postmerge-audit-state",
        default=None,
        help="post-merge audit state dir (default: WORKTRAIL_POSTMERGE_AUDIT_STATE or "
        "~/.worktrail/postmerge-audit-state)",
    )
    p.add_argument(
        "--run-record-dir",
        default=None,
        help="go run-record root for the 'Recent runs' section "
        "(default: WORKTRAIL_RUN_RECORD_DIR or ~/.worktrail/runs)",
    )
    p.add_argument(
        "--check-freshness",
        action="store_true",
        help="opt-in: run one `git fetch` + ahead/behind check per displayed repo "
        "(check_repo_freshness.py) and surface a staleness_warning when a local "
        "checkout is behind origin -- off by default since this module is "
        "otherwise pure file inspection (no git, no network) and a fetch per "
        "repo is real network cost on every render",
    )
    args = p.parse_args(argv)
    run_record_dir = (
        Path(args.run_record_dir).expanduser() if args.run_record_dir else None
    )

    if args.spec:
        print(json.dumps(detect_stage(Path(args.spec)), indent=2))
        return 0

    # Resolve picked_dir early so every branch (--repos and --root) can use it.
    picked_dir: Path | None = None
    work_queue_env = (
        Path(os.environ.get("WORK_QUEUE_DIR", ""))
        if os.environ.get("WORK_QUEUE_DIR")
        else None
    )
    if args.picked_dir:
        picked_dir = Path(args.picked_dir)
    else:
        if work_queue_env and work_queue_env.is_dir():
            picked_dir = work_queue_env / "picked"
        else:
            default_picked = Path.home() / "work-queue" / "picked"
            if default_picked.is_dir():
                picked_dir = default_picked

    # Resolve queue_dir the same way (env var fallback, then default), mirroring
    # work_queue.py's own queue_dir() resolution. compute_clusters() handles a
    # missing/nonexistent queue_dir on its own (degrades to []).
    if work_queue_env and work_queue_env.is_dir():
        queue_dir = work_queue_env / "queue"
    else:
        queue_dir = Path.home() / "work-queue" / "queue"

    # Defense-in-depth alongside cluster_detect's own internal wrapper (REQ-NR003):
    # a residual failure at this call site still degrades to [] rather than
    # crashing the whole render.
    try:
        clusters = (
            cluster_detect.compute_clusters(
                queue_dir,
                _parse_fm,
                # Dashboard Advisory Surfaces Brief-vs-Task Matches: lets a
                # queued brief's target-spec: also match an open, unchecked
                # task in that change (see _dashboard_task_candidates).
                task_candidates_fn=functools.partial(
                    _dashboard_task_candidates,
                    repos_root=args.repos or str(Path.home() / "projects"),
                ),
            )
            if cluster_detect
            else []
        )
    except Exception:  # noqa: BLE001 — degrade, never crash the dashboard render
        clusters = []

    # Telemetry (spec 018 change: cluster-precision-telemetry) — logged only
    # for --json calls, matching the JSON output's role as the machine-read
    # surface (interactive text-only renders don't log). Best-effort: a
    # telemetry failure must never affect the dashboard's own output.
    if args.json and clusters and cluster_telemetry:
        try:
            cluster_telemetry.log_shown(clusters)
        except Exception:  # noqa: BLE001, S110 — degrade, never crash the dashboard render
            pass

    # Precision summary (spec 018 change: cluster-precision-telemetry, item 6):
    # read-only and cheap, so computed for every render (not gated to --json
    # like log_shown above) -- interactive text-only renders should see the
    # same precision line as a --json caller. Best-effort: never crash the
    # dashboard render over a telemetry read failure.
    cluster_precision: dict[str, Any] | None = None
    if cluster_telemetry:
        try:
            cluster_precision = cluster_telemetry.summarize()
        except Exception:  # noqa: BLE001 — degrade, never crash the dashboard render
            cluster_precision = None

    capacity = None
    if _capacity_gate_snapshot is not None:
        try:
            dashboard_policy = _load_dashboard_policy(str(_dashboard_repo_root()))
            capacity = _capacity_gate_snapshot(
                _routing_configured_providers(dashboard_policy),
                Path(args.capacity_cache) if args.capacity_cache else None,
            )
        except Exception:  # noqa: BLE001 — telemetry must never break the dashboard
            capacity = None

    postmerge_check_failures = None
    if _postmerge_dashboard_snapshot is not None:
        try:
            postmerge_check_failures = _postmerge_dashboard_snapshot(
                _postmerge_resolve_state_dir(args.postmerge_audit_state)
            )
        except Exception:  # noqa: BLE001 — telemetry must never break the dashboard
            postmerge_check_failures = None

    # Parse queue briefs from --queue-json/--queue-json-file if provided.
    queue_briefs: list[dict[str, Any]] = []
    queue_json_raw = _resolve_json_arg(args.queue_json, args.queue_json_file)
    if queue_json_raw:
        try:
            parsed = json.loads(queue_json_raw)
            queue_briefs = parsed.get("briefs", []) if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, AttributeError):
            pass

    # Parse open decisions from --decisions-json/--decisions-json-file if provided.
    open_decisions: list[dict[str, Any]] = []
    decisions_json_raw = _resolve_json_arg(
        args.decisions_json, args.decisions_json_file
    )
    if decisions_json_raw:
        try:
            parsed = json.loads(decisions_json_raw)
            open_decisions = (
                parsed.get("decisions", []) if isinstance(parsed, dict) else []
            )
        except (json.JSONDecodeError, AttributeError):
            pass

    # When --root doesn't exist (e.g. launched from a workspace root like ~/
    # that has no docs/specs of its own), auto-detect a projects/ sibling and
    # fall back to multi-repo mode rather than returning empty specs.
    if not args.repos and not Path(args.root).is_dir():
        repo_root = Path(args.root).parent.parent  # <repo>/docs/specs -> <repo>
        fallback = repo_root / "projects"
        if (
            fallback.is_dir()
            and _list_candidate_repos is not None
            and _list_candidate_repos(fallback)
        ):
            args.repos = str(fallback)

    # classify.py reads these two ints from --state; without them every request
    # was scored as "no active specs, empty queue" regardless of reality.
    unblocked_queue_total = sum(1 for b in queue_briefs if not b.get("blocked"))

    auto_pick = (
        auto_pick_brief(
            queue_briefs,
            repo_filter=args.auto_repo,
            # Bare/`owner/name` repo: values (create_handoff.py writes them
            # verbatim, no normalization) resolve by basename against this
            # dir. --repos when given; otherwise the same ~/projects default
            # the /go front door itself uses for multi-repo resolution.
            repos_root=args.repos or str(Path.home() / "projects"),
        )
        if args.auto
        else None
    )
    if auto_pick is not None:
        log_auto_pick_miss(auto_pick, len(queue_briefs), repo_filter=args.auto_repo)

    if args.repos:
        repo_rows = scan_repos(Path(args.repos), run_record_dir=run_record_dir)
        for row in repo_rows:
            row["recent_runs"] = load_recent_runs(
                Path(row["path"]), runs_dir=run_record_dir
            )
            row["epics"] = scan_epics(Path(row["path"]))
        backlog_total = sum(r.get("backlog", 0) for r in repo_rows)
        inflight = inflight_briefs(picked_dir, stale_hours=args.inflight_stale_hours)
        staleness_warnings = (
            _staleness_warnings([(r["repo"], r["path"]) for r in repo_rows])
            if args.check_freshness
            else []
        )
        rendered = render_dashboard(
            repo_rows,
            None,
            inflight,
            queue_briefs,
            clusters=clusters,
            cluster_precision=cluster_precision,
            capacity=capacity,
            postmerge_check_failures=postmerge_check_failures,
            staleness_warnings=staleness_warnings,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "repos": repo_rows,
                        "active_specs": sum(r.get("active", 0) for r in repo_rows),
                        "handoff_queue": unblocked_queue_total,
                        "inflight": inflight,
                        "category_actions": build_category_actions(
                            repo_rows,
                            None,
                            inflight,
                            queue_briefs,
                            open_decisions=open_decisions,
                        ),
                        "category_items": build_category_items(
                            repo_rows,
                            None,
                            inflight,
                            queue_briefs,
                            backlog_total,
                            clusters=clusters,
                            open_decisions=open_decisions,
                        ),
                        "auto_pick": auto_pick,
                        "clusters": clusters,
                        "open_decisions": open_decisions,
                        "cluster_precision": cluster_precision,
                        "capacity": capacity,
                        "postmerge_check_failures": postmerge_check_failures,
                        "staleness_warnings": staleness_warnings,
                        "rendered": rendered,
                    },
                    indent=2,
                )
            )
        else:
            print(rendered)
        return 0

    rows = scan(Path(args.root))
    con = constitution_status(Path(args.root))
    # Single-repo worktrees live at <repo>/../<repo>-worktrees/. args.root is
    # <repo>/docs/specs, so the repo is its grandparent.
    repo_dir = Path(args.root).resolve().parent.parent
    # This branch's own summary count must match its repo-scoped view
    # (category_actions' "Work queue" label below) rather than the raw
    # multi-repo total -- same queue_repo scoping as everything else in this
    # branch, applied to the pre-existing "not blocked" formula.
    scoped_handoff_queue = sum(
        1
        for b in queue_briefs
        if not b.get("blocked") and _repo_scope_match(b.get("repo"), repo_dir.name)
    )
    epic_rows = scan_epics(repo_dir)
    worktrees = [wt.name for wt in _find_worktrees(repo_dir.parent, repo_dir.name)]
    backlog_total = sum(1 for r in rows if r["stage"] in _BACKLOG)
    inflight = inflight_briefs(picked_dir, stale_hours=args.inflight_stale_hours)
    recent_runs = load_recent_runs(repo_dir, runs_dir=run_record_dir)
    staleness_warnings = (
        _staleness_warnings([(repo_dir.name, repo_dir)]) if args.check_freshness else []
    )
    rendered = render_dashboard(
        None,
        rows,
        inflight,
        queue_briefs,
        worktrees,
        con,
        clusters=clusters,
        cluster_precision=cluster_precision,
        capacity=capacity,
        postmerge_check_failures=postmerge_check_failures,
        recent_runs=recent_runs,
        staleness_warnings=staleness_warnings,
        # Single-repo mode IS the repo-scoped view: rendering other repos'
        # queued/in-flight briefs here misleads (`/go devops` listed datalena
        # briefs, observed 2026-08-13). The multi-repo overview above stays
        # unfiltered by design.
        queue_repo=repo_dir.name,
        epic_rows=epic_rows,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "constitution": con,
                    "specs": rows,
                    "epics": epic_rows,
                    "active_specs": sum(1 for r in rows if r["stage"] in _ACTIVE),
                    "handoff_queue": scoped_handoff_queue,
                    "inflight": inflight,
                    "worktrees": worktrees,
                    "category_actions": build_category_actions(
                        None,
                        rows,
                        inflight,
                        queue_briefs,
                        open_decisions=open_decisions,
                        queue_repo=repo_dir.name,
                    ),
                    "category_items": build_category_items(
                        None,
                        rows,
                        inflight,
                        queue_briefs,
                        backlog_total,
                        clusters=clusters,
                        open_decisions=open_decisions,
                        queue_repo=repo_dir.name,
                    ),
                    "auto_pick": auto_pick,
                    "clusters": clusters,
                    # Repo-scoped for the same reason as category_actions/items above
                    # (queue_repo) -- a repo-scoped dashboard's raw open_decisions
                    # field must not leak another repo's decision record either.
                    "open_decisions": [
                        d
                        for d in open_decisions
                        if _repo_scope_match(d.get("repo"), repo_dir.name)
                    ],
                    "cluster_precision": cluster_precision,
                    "capacity": capacity,
                    "postmerge_check_failures": postmerge_check_failures,
                    "recent_runs": recent_runs,
                    "staleness_warnings": staleness_warnings,
                    "rendered": rendered,
                },
                indent=2,
            )
        )
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())

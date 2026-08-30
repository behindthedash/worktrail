#!/usr/bin/env python3
"""GO v2 run record — the machine-readable audit log of every front-door run.

One YAML file per run under <dir>/<repo-name>/<run-id>.yaml (default dir
`worktrail_home()/runs`, normally ~/.worktrail/runs — operational telemetry,
deliberately outside the project repo; override via policy `run_record_dir`). Fields follow the assignment's §20
structure. `finish` enforces the ten explicit completion states (§22) so a run
can never end in vague language. It also code-enforces the
`no_implementation_without_approval` gate (routes.md §A): a run whose
`selected_route` is A cannot `finish` on an implementation-completion state
(completed_and_merged/completed_pr_open/completed_awaiting_human_approval)
unless a `decisions` entry was recorded first. It also code-enforces
`pre_pr_gate.py`'s scope-completeness review (`scope_review_failures()`) on
every implementation-completion `finish`, unconditional on route --
previously that check only ran when a caller passed `--run` to
`pre_pr_gate.py` directly, which `integrate.py`'s orchestrator group-PR path
never did (docs/specs/research/go-orchestrator-gate-parity-audit.md).
`finish` runs exactly once per run regardless of how many group PRs the
orchestrator created, so this closes the gap for both paths uniformly.

Subcommands:
  start  --repo R --request "..." --route F --risk medium [--reason "..."]
         [--agent claude|codex|opencode] [--dir DIR]
         [--routing-decision JSON] [--gates "gate_a,gate_b"]
                                               -> prints {run_id, path}
  set    PATH KEY VALUE                       -> set/replace a top-level field
  append PATH KEY VALUE                       -> append VALUE to a list field
  intervention PATH --category C --minutes N --tokens N --note "..."
         -> log a manual rescue (process-friction telemetry for retros)
  capacity-gate PATH --provider AGENT:MODEL --failure-class CLASS
          [--retry-after ISO8601] [--note "..."]
         -> record a sanitized all-provider capacity gate
finish PATH --status completed_pr_open [--pr URL] [--merge-result ...]
         -> release this run's claim (if any). If the claim was staked with
            `claim --remote`, also best-effort deletes the remote claim ref
            on `origin` (failure here never affects `finish`'s exit code or
            JSON output; the claim just expires via its own TTL instead).
            Also applies the `go:risk-*` PR label correction
            (`pr_labels.ensure_pr_risk_label`) whenever the record carries a
            `pull_request`, unconditional on route or completion state --
            code-enforced here instead of relying on every route/dispatch
            surface to call `worktrail-ensure-pr-label` itself. Same
            best-effort posture: a correction failure never affects `finish`'s
            exit code or JSON output. Also code-enforces the merge-state gate
            (`_enforce_merge_state_gate`) and the review-thread gate
            (`check_review_threads.check`) whenever the record carries a
            `pull_request` and `--status` is one of the three implementation-
            completion states -- a still-`BLOCKED` `mergeStateStatus`, or
            `blocking: true`, each raise `SystemExit` (a real block rather
            than a best-effort correction), since ci-watch-loop.md documents
            both gates as meant to stop `finish` the same way a failing check
            does (worktrail PR #393 for the former, datalena PR #2133 for the
            latter). Either check failing to answer (gh unavailable/network)
            still fails open. Also code-enforces `pre_pr_gate.py`'s
            scope-completeness review
            (`scope_review_failures`) whenever `--status` is one of the three
            implementation-completion states, unconditional on route or PR
            presence -- a real block, not fail-open, since it reads only the
            local run record (`_enforce_scope_completeness_gate`).
   scope-review PATH --item "..." --status complete|out-of-scope|blocked
                (--evidence "..." | --reason "...")
   decision PATH --event asked|presented|answered|consumed|superseded
           --decision-id ID [--note "..."]
          -> stamp one pending-user-decision lifecycle hop into the record's
             `pending_decisions` list (idempotent per event+decision-id; see
             DECISION_EVENTS / workqueue/decisions.py's envelope contract)
  active-conflicts --dir DIR --repo REPO --specification SPEC [--exclude PATH]
         -> read-only scan for other non-terminal runs on the same
            repo+specification; prints a JSON array (see contracts/active-conflicts-cli.md)
  claim  RUN_PATH --specification SPEC [--remote] [--remote-ttl-seconds N]
         -> atomically claim repo+specification for the run at RUN_PATH before
            committing to implement it. Closes the TOCTOU gap in the read-only
            active-conflicts scan (two sessions can each pass the scan before
            either's record is visible to the other) by making the
            exclusivity check and the `specification` write one OS-atomic
            step (O_CREAT|O_EXCL on a lock file). A second concurrent claim
            for the same repo+specification fails fast with
            {"status": "already-claimed", ...} instead of racing to the scan.
            `finish` releases the claim automatically.
            With `--remote` (default `--remote-ttl-seconds 86400`), also
            stakes the claim on `origin` via a claim ref
            (refs/worktrail-claims/<spec-slug>) so a second *machine* --
            invisible to this machine's local lock file -- is blocked too.
            A live remote claim fails with
            {"status": "already-claimed", "scope": "remote", ...}; a stale
            one (older than its own TTL) is reclaimed. Without `--remote`,
            behavior and network usage are unchanged from before this layer
            existed.
  prune  [--dir DIR] [--repo REPO] [--keep-count N] [--keep-days N] [--dry-run]
         -> delete old run records under <dir>/<repo-name>/*.yaml. Hybrid
            retention: a record is kept if it is among the --keep-count most
            recent for its repo, OR started within --keep-days, OR is still
            non-terminal (no final_status yet) -- whichever keeps more.
            Omit --repo to prune every repo directory under --dir.
  sweep-orphans --status STATUS [--dir DIR] [--repo REPO]
                [--ttl-seconds N] [--note "..."] [--dry-run]
         -> bulk-close non-terminal run records abandoned mid-dispatch: a
            record with no `final_status` whose `liveness` (same check as the
            `liveness` subcommand below) comes back stale is closed via the
            same path `finish` uses, with the given --status and --merge-result
            (defaults to an auto-reconciled note naming the liveness reason).
            A record still `fresh` per liveness is left untouched -- it may be
            legitimate in-progress work on another machine/session. Unlike
            `reconcile` (which re-checks one record's worktree/base_branch
            staleness), this is heartbeat-based and scoped to zero or more
            entire repo directories, matching `prune`'s --dir/--repo
            semantics. Prints one summary object per repo dir:
            {"repo", "closed": [paths], "skipped_live": [paths], "warnings"}.
            No existing tool covered this before (`reconcile`'s `_is_stale()`
            treats a record with no `base_branch` as live, not stale, which is
            true for most long-orphaned records -- see
            docs/specs/research/dead-dispatch-backlog-investigation.md).
  liveness RUN_PATH [--ttl-seconds N] [--dispatch-id ID]
         -> read-only: is this non-terminal run still actively being worked?
            `updated_at` (stamped by every `_save()`, i.e. every mutating
            subcommand -- a heartbeat, not just an audit field) newer than
            --ttl-seconds (default 1200) ago means "fresh" -- the owning
            process is plausibly still working. Older means "stale" -- no
            activity recently, most likely a crashed/abandoned session. Also
            reports `same_dispatch` when --dispatch-id is given: True only if
            it matches the record's own stamped `dispatch_id` from `start`.
            Used by worktrail-go/SKILL.md's Active-run-resume evidence test
            (docs/specs/research/concurrent-go-dispatch-brief-claim-race.md,
            recommended fix #3) to tell "I am the process that started this
            run" (same_dispatch: true, skip this check entirely) apart from
            "a different, possibly-concurrent dispatch is evaluating someone
            else's still-live run" (same_dispatch: false, fresh: true --
            do NOT resume, a live collision) apart from "a different dispatch
            found an abandoned run" (same_dispatch: false, fresh: false --
            safe to resume via the full Route E reconstruct-before-acting
            procedure). A terminal record (final_status set) is always
            reported fresh: false, same_dispatch: false -- staleness/liveness
            only means anything for a run still in progress.
  find-by-worktree --dir DIR --repo REPO --worktree PATH
         -> read-only: which non-terminal run record (if any) owns this
            worktree path? Scans <dir>/<repo-name>/*.yaml with
            `_load_lenient` (skip and warn on malformed files, same
            tolerance policy as `active-conflicts`), filters to non-terminal
            records whose `worktree` field is exactly PATH -- a flat string
            equality check, not a resolved-path comparison, matching how
            `worktree` is written verbatim by `set "$RUN" worktree "$WT"` at
            creation time. Prints {"found": bool, "path": str|null,
            "run_id": str|null}. If more than one non-terminal record
            matches (shouldn't happen in normal operation, but two records
            could both name the same now-stale path after a
            crash-without-cleanup), resolves to the most recently started
            one -- the same tie-break `active-conflicts` already uses. Feeds
            `liveness` for the deletion liveness guard: resolve a worktree
            path to its owning run record here, then ask `liveness` whether
            that record is still actively being worked.
  worktree-conflict --dir DIR --repo REPO --worktree PATH
          [--dispatch-id ID] [--ttl-seconds N]
         -> read-only, single-call combination of `find-by-worktree` +
            `liveness`: is a DIFFERENT, still-actively-worked dispatch
            claiming this exact worktree path right now? The two-step
            manual version already existed and is individually documented
            above; this exists for an external, cross-repo caller (e.g. a
            PreToolUse hook enforcing worktree safety before an interactive
            edit or git-mutating command, mirroring the existing rename-guard
            hook pattern) that wants one subprocess call and a plain boolean
            rather than reimplementing the same_dispatch/fresh interpretation
            itself. Prints {"conflict": bool, "found": bool, "run_id":
            str|null, "path": str|null, "fresh": bool|null, "same_dispatch":
            bool|null, "age_seconds": float|null, "reason": str|null}.
            `found: false` (nothing tracks this worktree) always means
            `conflict: false`, `reason: "not_tracked"`. When found, `conflict`
            is true only when the owning record is fresh (recently
            heartbeated) AND not the caller's own dispatch (per --dispatch-id,
            same semantics as `liveness`) -- the caller's own worktree, or an
            abandoned/crashed one, is never a conflict. `reason` explains a
            `false` conflict when found: "same_dispatch", "stale", or
            `liveness`'s own no_heartbeat/unparsable_updated_at reason.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..shared.homedir import worktrail_home
from . import invocation_context

ALLOWED_AGENTS = ("claude", "codex", "opencode")

# Kept in lockstep with invocation_context.resolve()'s decision tree so an
# audit record can never claim a dispatch mode the resolver cannot produce.
DISPATCH_MODES = (
    invocation_context.IN_SESSION_RESUME,
    invocation_context.NATIVE_SKILL,
    invocation_context.ADAPTER,
    invocation_context.BLOCKED,
)

COMPLETION_STATES = (
    "completed_and_merged",
    "completed_pr_open",
    "completed_awaiting_human_approval",
    "planned_ready_for_implementation",
    "investigation_complete",
    "blocked_external_dependency",
    "blocked_product_decision",
    "blocked_security_or_safety",
    "failed_recoverable",
    "failed_terminal",
)

# Route A's own completions (routes.md §A) never imply implementation happened.
# Reaching one of these from Route A means the run crossed into building/merging
# without recording the `no_implementation_without_approval` decision first.
IMPLEMENTATION_COMPLETION_STATES = (
    "completed_and_merged",
    "completed_pr_open",
    "completed_awaiting_human_approval",
)

# Run-level phase states (v2-design §2.3); informational, stamped via `set status`.
PHASES = (
    "intake",
    "classified",
    "state_restored",
    "policy_loaded",
    "route_selected",
    "executing",
    "validating",
    "pr_open",
    "merge_gate",
    "done",
)

# Pending-user-decision lifecycle events: the run-record side of the versioned
# decision-envelope contract (workqueue/decisions.py). A guard files an
# envelope, the attended host presents and answers it, and the resuming run
# consumes it -- every hop stamps exactly one entry in the record's
# `pending_decisions` list (via the `decision` subcommand) so a resumed run can
# reconstruct what happened to a decision from the audit trail alone. Kept
# separate from `decisions` (the free-text approval log feeding Route A's
# no_implementation_without_approval gate): recording that a QUESTION was asked
# must never satisfy a gate about recorded APPROVALS.
DECISION_EVENTS = ("asked", "presented", "answered", "consumed", "superseded")

SECRET_PAT = re.compile(
    r"(api[-_ ]?key|secret|token|password|authorization:\s*bearer)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


_BOOL_LITERALS = {"true": True, "false": False}


def _quote(value: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    value = str(value)
    if SECRET_PAT.search(value):
        raise SystemExit("refusing to record what looks like a credential")
    # A string that spells a bool literal must round-trip as a string, so the
    # bare form below is unambiguously a real boolean on the way back in.
    if (
        value.lower() in _BOOL_LITERALS
        or re.search(r"[:#\n\"']", value)
        or value != value.strip()
        or value == ""
    ):
        return json.dumps(value.replace("\n", " "))
    return value


def _render(record: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in record.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_quote(item)}")
        elif isinstance(value, dict):
            lines.append(f"{key}: {_quote(json.dumps(value, sort_keys=True))}")
        else:
            lines.append(f"{key}: {_quote(value) if value is not None else 'null'}")
    return "\n".join(lines) + "\n"


# A run record is written ONLY by this module's line-based renderer. Any key
# that doesn't look like a canonical field name is proof the file was rewritten
# by something else (observed live 2026-08-08: a worker hand-edited a record
# with a generic YAML writer, whose nested `- category: x` lines this parser
# reads as garbage keys like "- category" — parse "succeeds", then the next
# _save re-renders the garbage and the corruption compounds silently).
_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_HAND_EDIT_HINT = (
    "is not in run_record.py's line-based format — it was probably rewritten "
    "by hand or with a generic YAML writer. Never edit run records directly; "
    "every mutation has a subcommand (set/append/intervention/scope-review/"
    "capacity-gate/finish). Recover by starting a fresh record with "
    "`worktrail-run-record start` and continuing there; leave the damaged "
    "file in place as an audit artifact."
)


class RunRecordFormatError(ValueError):
    """The on-disk record was mutated outside run_record.py's own renderer."""


def _load_lenient(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """`_load()` without the raise -- for directory-wide scans where one bad
    file must never abort every other record's read. Returns `(record, None)`
    on success or `(None, warning)` on a malformed file; the caller decides
    whether to skip and surface the warning.
    """
    try:
        return _load(path), None
    except RunRecordFormatError as exc:
        return None, str(exc)


def _load(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {}
    current: str | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if line.startswith("  - ") and current:
                item = line[4:].strip()
                record[current].append(
                    json.loads(item) if item.startswith('"') else item
                )
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value == "":
                record[key] = []
                current = key
            else:
                current = None
                if value == "null":
                    record[key] = None
                elif value in _BOOL_LITERALS:
                    record[key] = _BOOL_LITERALS[value]
                elif value.startswith('"'):
                    parsed = json.loads(value)
                    if isinstance(parsed, str) and parsed.lstrip().startswith("{"):
                        try:
                            parsed = json.loads(parsed)
                        except json.JSONDecodeError:
                            pass
                    record[key] = parsed
                else:
                    record[key] = value
    except (json.JSONDecodeError, AttributeError) as exc:
        raise RunRecordFormatError(
            f"{path} {_HAND_EDIT_HINT} (parse error: {exc})"
        ) from exc
    bad_keys = [k for k in record if not _FIELD_KEY_RE.match(k)]
    if bad_keys:
        raise RunRecordFormatError(
            f"{path} {_HAND_EDIT_HINT} (unrecognized keys: {', '.join(sorted(bad_keys)[:5])})"
        )
    return record


def _save(path: Path, record: dict[str, Any]) -> None:
    # `updated_at` is a heartbeat, not just an audit timestamp: every mutation
    # of a live run record goes through this one function, so stamping it
    # here (rather than in each of the ~8 call sites) guarantees no mutating
    # subcommand can forget it. The Active-run-resume evidence test in
    # worktrail-go/SKILL.md reads its freshness to distinguish "the owning
    # process is still actively working" from "non-terminal status + worktree
    # exists, but nothing has touched this record in a while" -- the gap
    # named in docs/specs/research/concurrent-go-dispatch-brief-claim-race.md
    # (recommended fix #3): today's evidence test can't tell those apart.
    record["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_render(record), encoding="utf-8")
    tmp.replace(path)  # atomic — a crashed write never corrupts the record


def _parse_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return parsed


def cmd_start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    run_id = f"go-{time.strftime('%Y%m%d-%H%M%S')}"
    base = Path(args.dir).expanduser() if args.dir else worktrail_home() / "runs"
    out_dir = base / repo.name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.yaml"
    agent = (args.agent or "").strip().lower()
    if agent and agent not in ALLOWED_AGENTS:
        raise SystemExit(
            f"invalid agent '{args.agent}'; allowed: {', '.join(ALLOWED_AGENTS)}"
        )
    routing_decision = None
    if args.routing_decision is not None:
        routing_decision = _parse_json_object(
            args.routing_decision, "--routing-decision"
        )
    gates = (
        [g for g in (args.gates or "").split(",") if g]
        if args.gates is not None
        else None
    )
    # Two starts in the same second must not overwrite the first audit record.
    serial = 1
    while path.exists():
        serial += 1
        run_id = f"go-{time.strftime('%Y%m%d-%H%M%S')}-{serial}"
        path = out_dir / f"{run_id}.yaml"
    record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now(),
        "completed_at": None,
        "repository": str(repo),
        "base_branch": args.base_branch,
        "base_commit": args.base_commit,
        "worktree": None,
        "request_summary": (args.request or "")[:300],
        "selected_route": args.route,
        "route_reason": args.reason,
        "risk_level": args.risk,
        **({"gates": gates} if gates is not None else {}),
        "agent": agent if agent else None,
        **(
            {"native_skill_available": args.native_skill_available == "true"}
            if args.native_skill_available is not None
            else {}
        ),
        **(
            {"dispatch_mode": args.dispatch_mode}
            if args.dispatch_mode is not None
            else {}
        ),
        **({"dispatch_id": args.dispatch_id} if args.dispatch_id is not None else {}),
        "status": "route_selected",
        **(
            {"routing_decision": routing_decision}
            if routing_decision is not None
            else {}
        ),
        "epic": None,
        "feature": None,
        "specification": None,
        "handoffs_consumed": [],
        "handoffs_created": [],
        "skills_loaded": [],
        "subagents_called": [],
        "files_changed": [],
        "tests_run": [],
        "decisions": [],
        "pending_decisions": [],
        "assumptions": [],
        "deferred_work": [],
        "scope_review": [],
        "validation_evidence": [],
        "failure_recovery": [],
        "interventions": [],
        "capacity_gate": None,
        "pull_request": None,
        "merge_decision": None,
        "merge_result": None,
        "final_status": None,
    }
    _save(path, record)
    print(json.dumps({"run_id": run_id, "path": str(path)}))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    path = Path(args.path)
    record = _load(path)
    if args.key == "status" and args.value not in PHASES:
        raise SystemExit(f"invalid phase '{args.value}'; allowed: {PHASES}")
    record[args.key] = args.value
    _save(path, record)
    return 0


def cmd_append(args: argparse.Namespace) -> int:
    path = Path(args.path)
    record = _load(path)
    cur = record.get(args.key)
    if cur is None:
        record[args.key] = [args.value]
    elif isinstance(cur, list):
        cur.append(args.value)
    else:
        raise SystemExit(f"field '{args.key}' is scalar; use `set`")
    _save(path, record)
    return 0


INTERVENTION_CATEGORIES = (
    "grounding",  # corrected a greenfield-on-brownfield assumption / found existing code
    "integration_repair",  # fixed a cross-task bug that per-task review missed
    "ci_repair",  # repaired red CI on a PR
    "conflict_resolution",  # hand-resolved a merge/integration conflict
    "env_setup",  # built/repaired a verification environment to run tests
    "quota_wait",  # blocked on model/account rate or session limit
    "capacity_gate",  # all configured headless providers are unavailable
    "orchestrator_defect",  # worked around a bug in the orchestrator itself
    "other",
)


def cmd_intervention(args: argparse.Namespace) -> int:
    """Append a structured manual-rescue entry — friction telemetry for process retros.

    Each entry: '<ts> [<category>] <minutes>m ~<tokens>tok — <note>'. The category
    vocabulary is fixed so a future retro can aggregate where the LLM was blocked and
    decide, on data, whether a new tool/service is justified.
    """
    if args.category not in INTERVENTION_CATEGORIES:
        raise SystemExit(
            f"'{args.category}' is not an allowed category.\nAllowed: "
            + ", ".join(INTERVENTION_CATEGORIES)
        )
    path = Path(args.path)
    record = _load(path)
    entry = (
        f"{_now()} [{args.category}] {args.minutes}m ~{args.tokens}tok — {args.note}"
    )
    cur = record.get("interventions")
    if cur is None:
        record["interventions"] = [entry]
    elif isinstance(cur, list):
        cur.append(entry)
    else:
        raise SystemExit("field 'interventions' is scalar; cannot append")
    _save(path, record)
    print(json.dumps({"logged": entry}))
    return 0


def _safe_provider(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:/-]", "_", value or "")
    return value[:120] or "unknown"


def cmd_capacity_gate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    record = _load(path)
    providers = [_safe_provider(value) for value in (args.provider or [])]
    if not providers:
        raise SystemExit("capacity-gate requires at least one --provider")
    failure_class = _safe_provider(args.failure_class)
    retry_after = _safe_provider(args.retry_after) if args.retry_after else None
    record["capacity_gate"] = {
        "status": "blocked",
        "providers": providers,
        "failure_class": failure_class,
        "retry_after": retry_after,
        "recorded_at": _now(),
        "note": (args.note or "all configured headless providers are unavailable")[
            :300
        ],
    }
    record["status"] = "executing"
    _save(path, record)
    print(json.dumps(record["capacity_gate"], sort_keys=True))
    return 0


def cmd_scope_review(args: argparse.Namespace) -> int:
    """Record evidence that requested scope was completed or explicitly excluded."""
    item = args.item.strip()
    if not item:
        raise SystemExit("scope-review requires a non-empty --item")
    if args.status == "complete":
        detail = (args.evidence or "").strip()
        if not detail:
            raise SystemExit("scope-review complete requires --evidence")
    else:
        detail = (args.reason or "").strip()
        if not detail:
            raise SystemExit(f"scope-review {args.status} requires --reason")
    path = Path(args.path)
    record = _load(path)
    record.setdefault("scope_review", []).append(f"{args.status} | {item} | {detail}")
    _save(path, record)
    print(json.dumps({"status": args.status, "item": item}))
    return 0


_DECISION_ENTRY_RE = re.compile(r"\[(?P<event>[a-z_-]+)\]\s+(?P<id>\S+)")


def _decision_entry_matches(entry: Any, event: str, decision_id: str) -> bool:
    """Whether a `pending_decisions` entry is `<ts> [event] <id> ...` for this
    exact (event, decision_id) pair -- token-compared, never substring-matched,
    so `[asked] dec-x` cannot swallow the distinct id `dec-x-extra`."""
    if not isinstance(entry, str):
        return False
    m = _DECISION_ENTRY_RE.search(entry)
    return bool(m) and m.group("event") == event and m.group("id") == decision_id


def record_decision_event(
    run_path: str | Path,
    event: str,
    decision_id: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Stamp one pending-decision lifecycle hop onto a run record.

    Entries land in the record's `pending_decisions` list as
    `<ts> [<event>] <decision-id>` (plus an optional ` — <note>`), the same
    line format as `interventions`. Idempotent on the exact (event,
    decision_id) pair: a retried dispatch re-stamps nothing, it gets
    {"status": "already-recorded"} plus the original entry -- the audit trail
    records that something happened once, not how many times a caller tried.
    """
    if event not in DECISION_EVENTS:
        raise SystemExit(
            f"'{event}' is not an allowed decision event.\nAllowed: "
            + ", ".join(DECISION_EVENTS)
        )
    if not decision_id or not decision_id.strip():
        raise SystemExit("--decision-id is required and must be non-empty")
    decision_id = decision_id.strip()
    path = Path(run_path)
    record = _load(path)
    entries = record.get("pending_decisions")
    if entries is None:
        entries = record["pending_decisions"] = []
    elif not isinstance(entries, list):
        raise SystemExit("field 'pending_decisions' is scalar; cannot append")
    for entry in entries:
        if _decision_entry_matches(entry, event, decision_id):
            return {
                "status": "already-recorded",
                "event": event,
                "decision_id": decision_id,
                "entry": entry,
            }
    new_entry = f"{_now()} [{event}] {decision_id}" + (
        f" — {note.strip()}" if note and note.strip() else ""
    )
    entries.append(new_entry)
    _save(path, record)
    return {
        "status": "recorded",
        "event": event,
        "decision_id": decision_id,
        "entry": new_entry,
    }


def cmd_decision(args: argparse.Namespace) -> int:
    result = record_decision_event(args.path, args.event, args.decision_id, args.note)
    print(json.dumps(result))
    return 0


def _scope_review_worktree_empty_diff(record: dict[str, Any]) -> str | None:
    """Best-effort cross-check: a `scope-review ... --status complete` entry is a
    self-reported string (`--evidence`), never verified against the tree it claims
    to describe -- the same self-report-without-verification gap
    `#post-delegation-verification` closes for a delegate's own completion report,
    now at the run level. When the run record's `worktree` still exists on disk and
    `base_commit` is known, confirm the worktree actually differs from base before
    trusting a "complete" entry.

    Fail-open (returns `None`) whenever the check can't run cleanly: no "complete"
    entry to verify, no `worktree`/`base_commit` recorded, the worktree already
    torn down (the common case by the time `finish` runs), or a git call errors.
    This is a defense-in-depth backstop, not the primary gate -- `git status
    --porcelain` (not `git diff --quiet HEAD`) covers a brand-new untracked file
    the same way `#post-delegation-verification` requires.
    """
    review = record.get("scope_review") or []
    if not any(isinstance(e, str) and e.startswith("complete | ") for e in review):
        return None
    worktree = record.get("worktree")
    base_commit = record.get("base_commit")
    if not worktree or not base_commit:
        return None
    wt_path = Path(worktree)
    if not wt_path.is_dir():
        return None
    try:
        status = subprocess.run(
            ["git", "-C", str(wt_path), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
        )
        diff = subprocess.run(
            ["git", "-C", str(wt_path), "diff", "--quiet", base_commit],
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # `git diff <base_commit>` (no --cached) already reflects uncommitted edits to
    # tracked files; the one thing it never shows is a brand-new untracked file
    # (the same gap `#post-delegation-verification` names), which `status
    # --porcelain` catches via its `??` prefix.
    has_untracked = any(
        line.startswith("??") for line in (status.stdout or "").splitlines()
    )
    if diff.returncode == 0 and not has_untracked:
        return (
            f"scope review records completed work but worktree {wt_path} has an "
            f"empty diff vs base commit {base_commit} -- self-reported evidence "
            "unverified"
        )
    return None


def _enforce_scope_completeness_gate(
    record: dict[str, Any], path: Path, status: str
) -> None:
    """Code-enforced backstop closing the orchestrator group-PR gate-parity gap
    (docs/specs/research/go-orchestrator-gate-parity-audit.md): `pre_pr_gate.py`'s
    `scope_review_failures()` only runs when `--run` is passed, and neither of
    `integrate.py`'s two orchestrator call sites (`--checks-only`,
    `--labels-only`) pass it -- so an orchestrated Route C/D run's
    scope-completeness review was recorded but never actually gated. `finish()`
    runs exactly once per `$RUN` regardless of how many group PRs the
    orchestrator created along the way, so enforcing the check here closes the
    gap uniformly for the one-off and orchestrator paths, mirroring how
    `_enforce_review_thread_gate` below backstops that gate. Unlike the
    review-thread gate, this reads only the local run record with no external
    dependency to fail open against, so any failure here blocks `finish` the
    same way an unresolved `no_implementation_without_approval` decision does.
    """
    from .pre_pr_gate import scope_review_failures

    failures = scope_review_failures(path)
    diff_mismatch = _scope_review_worktree_empty_diff(record)
    if diff_mismatch:
        failures = [*failures, diff_mismatch]
    if failures:
        detail = "; ".join(failures)
        raise SystemExit(
            f"scope_completeness_gate: cannot finish with '{status}' -- "
            f"{detail}. Complete in-scope work before finishing, or record a "
            "different-purpose or user-approved exclusion "
            f'(run_record.py scope-review {path} --item "..." --status '
            'out-of-scope --reason "different purpose: ...").'
        )


def _enforce_review_thread_gate(
    record: dict[str, Any], path: Path, pr_url: str, status: str
) -> None:
    """Code-enforced backstop for `check_review_threads.py`'s own gate.

    That module documents `blocking: true` as "meant to stop finish() the
    same way a failing check does" (ci-watch-loop.md case 1), but nothing
    previously called it from here -- the block existed only as SKILL.md
    prose an agent could skip. Runs unconditional on route, mirroring the PR
    label correction below. `checked: false` (gh unavailable, network
    hiccup, unresolvable owner/repo) is "no signal" and must never block;
    only a definite `blocking: true` does. A crash reaching this call
    (missing `gh`, import failure) is likewise never allowed to block --
    same fail-open posture as the label correction, since ci-watch-loop.md's
    own prior invocation is the primary gate and this is only the backstop
    for an agent that skipped it.
    """
    from .check_review_threads import check as check_review_threads
    from .pr_labels import _owner_repo_number

    repo = str(record.get("repository") or path.parent)
    try:
        parsed = _owner_repo_number(repo, pr_url)
        if parsed is None:
            return
        owner, name, number = parsed
        result = check_review_threads(
            Path(repo), int(number), run_record_path=path, owner=owner, name=name
        )
    except Exception as exc:  # noqa: BLE001 - fail-open, see docstring
        print(
            f"warning: run_record: review-thread gate check failed for {pr_url}: {exc}",
            file=sys.stderr,
        )
        return
    if result.get("checked") and result.get("blocking"):
        unaddressed = result.get("unaddressed") or []
        detail = (
            "; ".join(f"{t.get('path')}:{t.get('line')}" for t in unaddressed)
            or "see PR"
        )
        raise SystemExit(
            f"review_thread_gate: {pr_url} has {len(unaddressed)} unresolved "
            f"review thread(s) with no corresponding commit or run-record "
            f"decision ({detail}). Resolve or record a decision "
            f'(run_record.py append {path} decisions "...") before finishing '
            f"with '{status}'."
        )


def _query_merge_state(
    repo: str, owner: str, name: str, number: str, runner=subprocess.run
) -> dict[str, Any] | None:
    """`gh pr view --json state,mergeStateStatus` for the finish-time merge-state
    backstop below. Returns None on any failure (gh missing, timeout, non-zero
    exit, unparseable JSON) -- the caller treats that as no signal."""
    try:
        result = runner(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                f"{owner}/{name}",
                "--json",
                "state,mergeStateStatus",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _enforce_merge_state_gate(
    record: dict[str, Any], path: Path, pr_url: str, status: str
) -> None:
    """Code-enforced backstop for `ci-watch-loop.md`'s merge-state guard.

    That section documents `mergeStateStatus: BLOCKED` as a hard stop --
    `gh pr checks --watch` reporting all-green, or `autoMergeRequest` being
    armed, says nothing about whether GitHub will actually let the merge
    through (worktrail PR #393, 2026-08-14: a stray `CANCELLED` run alongside
    a later `SUCCESS` run of the same required-check context held branch
    protection at `BLOCKED` with every check green; the loop finished
    `completed_pr_open` anyway and the merge stalled indefinitely until a
    human noticed). Unlike the review-thread gate above, this check was never
    backstopped in code -- nothing here re-queried `mergeStateStatus` before
    `finish`, so an agent under the same time pressure that produced PR #393
    originally can still reproduce it today despite the doc's prose fix.
    `checked` (i.e. the query itself failing -- gh missing, network hiccup,
    unresolvable owner/repo) is "no signal" and must never block; only a
    definite still-`BLOCKED` result on a not-yet-merged PR does. This does
    not attempt the loop's own CANCELLED/SUCCESS rerun remediation or the
    review-thread-gate detour (those stay the primary, doc-driven path) --
    it exists only to catch an agent that skipped the guard entirely, the
    same posture as `_enforce_review_thread_gate`.
    """
    from .pr_labels import _owner_repo_number

    repo = str(record.get("repository") or path.parent)
    try:
        parsed = _owner_repo_number(repo, pr_url)
        if parsed is None:
            return
        owner, name, number = parsed
        data = _query_merge_state(repo, owner, name, number)
    except Exception as exc:  # noqa: BLE001 - fail-open, see docstring
        print(
            f"warning: run_record: merge-state gate check failed for {pr_url}: {exc}",
            file=sys.stderr,
        )
        return
    if data is None:
        return
    if data.get("state") == "MERGED":
        return
    if data.get("mergeStateStatus") == "BLOCKED":
        raise SystemExit(
            f"merge_state_gate: {pr_url} has mergeStateStatus=BLOCKED -- "
            "GitHub will not let this merge through as-is. Run "
            "ci-watch-loop.md's merge-state guard (rerun any stray "
            "CANCELLED check alongside a SUCCESS run of the same context, "
            "then the review-thread gate if still BLOCKED after 2 rounds) "
            f"before finishing with '{status}'."
        )


def cmd_finish(args: argparse.Namespace) -> int:
    if args.status not in COMPLETION_STATES:
        raise SystemExit(
            f"'{args.status}' is not an allowed completion state.\nAllowed: "
            + ", ".join(COMPLETION_STATES)
        )
    path = Path(args.path)
    record = _load(path)
    stored_pr = record.get("pull_request")
    if stored_pr is not None and not isinstance(stored_pr, str):
        # A handful of pre-existing records (written by an older tool/writer,
        # or hand-edited) hold a list here instead of a single string --
        # every downstream consumer (the merge-state gate, the review-thread
        # gate, and the PR risk-label correction below) treats this field as
        # a single URL and crashes with a raw TypeError deep in subprocess
        # argv handling on a list, not the graceful `except (OSError,
        # subprocess.SubprocessError)` this function already documents for a
        # gh/network failure. Sanitize once here instead of guarding each
        # consumer separately.
        print(
            f"warning: run_record: ignoring malformed non-string "
            f"pull_request on {path}: {stored_pr!r}",
            file=sys.stderr,
        )
        record["pull_request"] = None
    if (
        record.get("selected_route") == "A"
        and args.status in IMPLEMENTATION_COMPLETION_STATES
        and not record.get("decisions")
    ):
        raise SystemExit(
            "no_implementation_without_approval: Route A cannot finish with "
            f"'{args.status}' without a recorded decision. Route A's own "
            "completions are investigation_complete or "
            "planned_ready_for_implementation; proceeding to implementation "
            "requires an explicit decision entry first "
            f'(run_record.py append {path} decisions "...").'
        )
    if args.status in IMPLEMENTATION_COMPLETION_STATES:
        _enforce_scope_completeness_gate(record, path, args.status)
    pending_pr_url = args.pr or record.get("pull_request")
    if pending_pr_url and args.status in IMPLEMENTATION_COMPLETION_STATES:
        _enforce_merge_state_gate(record, path, pending_pr_url, args.status)
        _enforce_review_thread_gate(record, path, pending_pr_url, args.status)
    record["completed_at"] = _now()
    record["status"] = "done"
    record["final_status"] = args.status
    if args.pr:
        record["pull_request"] = args.pr
    if args.merge_result:
        record["merge_result"] = args.merge_result
    _save(path, record)
    pr_url = record.get("pull_request")
    if pr_url:
        # Code-enforced, not agent-narrated: every finish carrying a PR gets
        # the go:risk-* correction applied here, so no route/dispatch surface
        # can skip it by omission (6th recurrence of this failure class --
        # see docs/specs/research/go-dispatch-one-shot-pr-label-gap.md).
        # Imported locally: pr_labels.py imports `_load` from this module, so
        # a module-level import here would be circular.
        from .pr_labels import ensure_pr_risk_label

        try:
            ensure_pr_risk_label(
                record.get("repository"), pr_url, record.get("risk_level")
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # Best-effort, same posture as the remote-claim delete below: a
            # failure here (missing `gh`, bad cwd, network) must never affect
            # `finish`'s exit code or JSON output -- reconcile_pr_labels.py's
            # periodic sweep is the safety net for a correction that fails here.
            print(
                f"warning: run_record: pr risk-label correction failed for "
                f"{pr_url}: {exc}",
                file=sys.stderr,
            )
    specification = record.get("specification")
    if specification:
        # Release this run's claim (if any) so a later legitimate claim on
        # the same repo+specification isn't blocked once this run is done.
        # Guarded by run_id so finishing this run never deletes a different
        # run's active claim (e.g. after a stale-lock reclaim elsewhere).
        lock_path = _lock_path(path, specification)
        owner = _load_lock(lock_path)
        if owner.get("run_id") == record.get("run_id"):
            lock_path.unlink(missing_ok=True)
            if owner.get("remote") is True:
                # Best-effort: a failure here must never affect `finish`'s
                # exit code or JSON output, only leave the remote claim to
                # expire via its own TTL.
                project_repo_dir = Path(record.get("repository") or path.parent)
                _delete_remote_claim(project_repo_dir, _claim_ref(specification))
    print(json.dumps({"final_status": args.status, "path": str(path)}))
    return 0


def _extract_path_candidate(entry: str) -> str:
    """Leading whitespace-delimited token of a `files_changed` entry.

    Entries may carry trailing free-text annotation, e.g.
    `"docs/specs/foo/tasks.md (data-model, contracts, KG, 28 tasks)"` — only
    the path itself is a candidate for on-disk/git-tree resolution.
    """
    return entry.strip().split()[0] if entry.strip() else ""


def _is_stale(record: dict[str, Any], repo_dir: Path, base_branch: str) -> bool:
    """Whether `record`'s worktree is gone and its files already landed on `base_branch`.

    Never inferred from `files_changed` alone — a record with no `worktree`
    field is treated as live, since that's the only signal that a worktree
    ever existed to go missing.
    """
    worktree = record.get("worktree")
    if not worktree:
        return False
    if Path(worktree).exists():
        return False
    files_changed = record.get("files_changed") or []
    if not files_changed:
        return False
    for entry in files_changed:
        candidate = _extract_path_candidate(entry)
        if not candidate:
            return False
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "cat-file",
                "-e",
                f"{base_branch}:{candidate}",
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            return False
    return True


def _active_conflicts(
    repo_dir: Path, repo_root: Path, specification: str, exclude: Path | None
) -> dict[str, list[Any]]:
    """Other non-terminal run records under `repo_dir` targeting `specification`,
    partitioned into `{"live": [...], "stale": [...], "warnings": [...]}` via
    `_is_stale()`.

    `repo_root` is the actual git repository (for `_is_stale()`'s `git cat-file`
    check), distinct from `repo_dir` (the run-records directory for this repo).
    Each record's own `base_branch` is used — never a single value shared
    across records, since concurrent runs may target different base branches.
    A record with no `base_branch` is treated as live (can't check staleness).

    A malformed record (hand-edited or written by a generic YAML tool, see
    `RunRecordFormatError`) is skipped rather than aborting the whole scan --
    this is the mandatory `#active-conflicts-scan` hard-stop gate, and one bad
    file must never silently disable it for every other run. Skipped files are
    reported in `warnings`, never dropped without a trace.
    """
    live: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    warnings: list[str] = []
    if repo_dir.is_dir():
        for path in sorted(repo_dir.glob("*.yaml")):
            if exclude and path.resolve() == exclude:
                continue
            record, warning = _load_lenient(path)
            if warning is not None:
                warnings.append(warning)
                continue
            if record.get("final_status") is not None:
                continue
            if record.get("specification") != specification:
                continue
            entry = {
                "run_id": record.get("run_id"),
                "path": str(path),
                "started_at": record.get("started_at"),
                "request_summary": record.get("request_summary"),
                "agent": record.get("agent"),
            }
            base_branch = record.get("base_branch")
            is_stale = bool(base_branch) and _is_stale(record, repo_root, base_branch)
            (stale if is_stale else live).append(entry)
    return {"live": live, "stale": stale, "warnings": warnings}


def cmd_active_conflicts(args: argparse.Namespace) -> int:
    """Read-only scan for other non-terminal runs on the same repo+specification.

    Prints the `{"live": [...], "stale": [...]}` partition from
    `_active_conflicts()`.
    """
    repo = Path(args.repo).resolve()
    repo_dir = Path(args.dir).expanduser() / repo.name
    exclude = Path(args.exclude).resolve() if args.exclude else None
    print(json.dumps(_active_conflicts(repo_dir, repo, args.specification, exclude)))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Re-check one specific run record's staleness and close it if still stale.

    Unlike `active-conflicts` (a fresh scan across all records), this re-runs
    `_is_stale()` against the run record at `args.run` directly, using its own
    `repository`/`base_branch` fields. If still stale, closes it via the same
    finish path `cmd_finish` uses with `--status completed_and_merged`. If no
    longer stale (its worktree reappeared, or a file no longer resolves on its
    base_branch), makes no write.
    """
    run_path = Path(args.run)
    record = _load(run_path)
    repo_root = (
        Path(record["repository"]) if record.get("repository") else run_path.parent
    )
    base_branch = record.get("base_branch")
    if not base_branch or not _is_stale(record, repo_root, base_branch):
        print(
            json.dumps(
                {
                    "status": "not_stale",
                    "run_id": record.get("run_id"),
                    "path": str(run_path),
                }
            )
        )
        return 0
    merge_result = args.note or (
        f"auto-reconciled: staleness reconciler closed run {record.get('run_id')}"
    )
    finish_args = argparse.Namespace(
        path=str(run_path),
        status="completed_and_merged",
        pr=None,
        merge_result=merge_result,
    )
    return cmd_finish(finish_args)


_DEFAULT_LIVENESS_TTL_SECONDS = 1200  # 20 minutes -- matches the harness's own
# idle-wakeup pacing guidance, a reasonable proxy for "an actively working
# session touches its run record at least this often."


def _run_liveness(
    record: dict[str, Any], ttl_seconds: int, caller_dispatch_id: str | None = None
) -> dict[str, Any]:
    """Heartbeat freshness + dispatch-identity match for one run record.

    A terminal record (`final_status` set) is always reported not-fresh and
    not-same-dispatch -- liveness only means anything for a run still in
    progress; a finished run has nothing left to collide with.
    """
    if record.get("final_status") is not None:
        return {
            "fresh": False,
            "same_dispatch": False,
            "age_seconds": None,
            "updated_at": record.get("updated_at"),
            "reason": "terminal",
        }
    same_dispatch = (
        caller_dispatch_id is not None
        and record.get("dispatch_id") is not None
        and record.get("dispatch_id") == caller_dispatch_id
    )
    updated_at = record.get("updated_at")
    if not updated_at:
        # No heartbeat ever recorded (a record predating this field) -- treat
        # as stale rather than guessing fresh, so an old record doesn't block
        # forever on a signal it never had.
        return {
            "fresh": False,
            "same_dispatch": same_dispatch,
            "age_seconds": None,
            "updated_at": None,
            "reason": "no_heartbeat",
        }
    try:
        then = datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%S%z")
        age_seconds = (datetime.now(then.tzinfo) - then).total_seconds()
    except ValueError:
        return {
            "fresh": False,
            "same_dispatch": same_dispatch,
            "age_seconds": None,
            "updated_at": updated_at,
            "reason": "unparsable_updated_at",
        }
    return {
        "fresh": age_seconds <= ttl_seconds,
        "same_dispatch": same_dispatch,
        "age_seconds": age_seconds,
        "updated_at": updated_at,
        "reason": None,
    }


def cmd_liveness(args: argparse.Namespace) -> int:
    """Read-only: is this run's owning process plausibly still active?

    See the `liveness` entry in this module's docstring for the full
    same_dispatch/fresh decision table this feeds into.
    """
    run_path = Path(args.run)
    record = _load(run_path)
    result = _run_liveness(record, args.ttl_seconds, args.dispatch_id)
    print(json.dumps({"run_id": record.get("run_id"), "path": str(run_path), **result}))
    return 0


def _resolve_worktree_owner(repo_dir: Path, worktree: str) -> dict[str, Any] | None:
    """Non-terminal run record (if any) under `repo_dir` whose `worktree`
    field exactly matches `worktree`. Shared scan/tolerance/tie-break logic
    behind both `find-by-worktree` and `worktree-conflict` -- see the
    `find-by-worktree` docstring entry for the full behavior (malformed
    records skipped with a stderr warning, most-recently-started wins on a
    multi-match). Returns `None` when nothing matches; otherwise the winning
    candidate's `path`, `run_id`, and full `record` dict (needed by
    `worktree-conflict` for its `liveness` check).
    """
    candidates: list[dict[str, Any]] = []
    if repo_dir.is_dir():
        for path in sorted(repo_dir.glob("*.yaml")):
            record, warning = _load_lenient(path)
            if warning is not None:
                print(
                    f"warning: run_record: skipping malformed record {path}: {warning}",
                    file=sys.stderr,
                )
                continue
            if record.get("final_status") is not None:
                continue
            if record.get("worktree") != worktree:
                continue
            candidates.append(
                {
                    "path": str(path),
                    "run_id": record.get("run_id"),
                    "started_ts": _record_started_ts(record, path),
                    "record": record,
                }
            )
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["started_ts"], reverse=True)
    return candidates[0]


def cmd_find_by_worktree(args: argparse.Namespace) -> int:
    """Read-only: which non-terminal run record (if any) owns this worktree path?

    See the `find-by-worktree` entry in this module's docstring for the full
    scan/tie-break behavior. Feeds `liveness` for the deletion liveness
    guard (`#worktree-deletion-liveness-guard`).
    """
    repo = Path(args.repo).resolve()
    repo_dir = Path(args.dir).expanduser() / repo.name
    owner = _resolve_worktree_owner(repo_dir, args.worktree)
    if owner is None:
        print(json.dumps({"found": False, "path": None, "run_id": None}))
        return 0
    print(json.dumps({"found": True, "path": owner["path"], "run_id": owner["run_id"]}))
    return 0


def cmd_worktree_conflict(args: argparse.Namespace) -> int:
    """Read-only, single-call combination of `find-by-worktree` + `liveness`.

    See the `worktree-conflict` entry in this module's docstring for the full
    field contract. `conflict` is true only when a record owns this worktree
    AND is fresh AND is not the caller's own dispatch.
    """
    repo = Path(args.repo).resolve()
    repo_dir = Path(args.dir).expanduser() / repo.name
    owner = _resolve_worktree_owner(repo_dir, args.worktree)
    if owner is None:
        print(
            json.dumps(
                {
                    "conflict": False,
                    "found": False,
                    "run_id": None,
                    "path": None,
                    "fresh": None,
                    "same_dispatch": None,
                    "age_seconds": None,
                    "reason": "not_tracked",
                }
            )
        )
        return 0
    liveness = _run_liveness(owner["record"], args.ttl_seconds, args.dispatch_id)
    conflict = liveness["fresh"] and not liveness["same_dispatch"]
    reason = None
    if not conflict:
        reason = (
            "same_dispatch"
            if liveness["same_dispatch"]
            else (liveness["reason"] or "stale")
        )
    print(
        json.dumps(
            {
                "conflict": conflict,
                "found": True,
                "run_id": owner["run_id"],
                "path": owner["path"],
                "fresh": liveness["fresh"],
                "same_dispatch": liveness["same_dispatch"],
                "age_seconds": liveness["age_seconds"],
                "reason": reason,
            }
        )
    )
    return 0


def _claim_slug(specification: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", specification or "")
    return slug[:200] or "unknown"


def _claim_ref(specification: str) -> str:
    return f"refs/worktrail-claims/{_claim_slug(specification)}"


# SHA of the empty tree (`git hash-object -t tree /dev/null`) — the same
# constant for every git repo, so `commit-tree` needs no working-tree state.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# Wall-clock budget for a single git subprocess that talks to `origin`.
_REMOTE_GIT_TIMEOUT = 30


class RemoteClaimError(Exception):
    """Typed failure from a remote (cross-machine) claim git/network operation.

    `reason` is one of "git_error", "push_rejected", "verify_mismatch" so a
    caller can branch on failure kind without parsing the message text.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _run_remote_git(
    repo_dir: Path, args: list[str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_REMOTE_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteClaimError("git_error", f"git {' '.join(args)}: {exc}") from exc


def _push_remote_claim(
    repo_dir: Path,
    ref: str,
    run_id: str,
    ttl_seconds: int,
    expect_sha: str | None = None,
) -> str:
    """Publish a cross-machine claim: push an empty commit to `ref` on `origin`.

    The commit message carries the claim payload (`run_id`, `claimed_at`,
    `hostname`, `ttl_seconds`) so a reader needs only `git log -1
    --format=%B` on the ref, no working-tree checkout. `--force-with-lease`
    makes the push git's own atomic compare-and-swap: an empty `expect_sha`
    requires `ref` not already exist remotely (fresh claim); a stale claim's
    SHA requires the remote is still exactly that value (reclaim) -- so two
    machines racing the same reclaim resolve to exactly one winner. Returns
    the pushed commit SHA on success; raises `RemoteClaimError` on push
    rejection, a post-push verify mismatch (guards against a remote that
    silently drops the custom ref namespace), or any git/network error.
    """
    payload = json.dumps(
        {
            "run_id": run_id,
            "claimed_at": _now(),
            "hostname": socket.gethostname(),
            "ttl_seconds": ttl_seconds,
        }
    )
    commit = _run_remote_git(repo_dir, ["commit-tree", _EMPTY_TREE_SHA, "-m", payload])
    if commit.returncode != 0:
        raise RemoteClaimError(
            "git_error", f"commit-tree failed: {commit.stderr.strip()}"
        )
    sha = commit.stdout.strip()

    lease = f"--force-with-lease={ref}:{expect_sha or ''}"
    push = _run_remote_git(repo_dir, ["push", "origin", f"{sha}:{ref}", lease])
    if push.returncode != 0:
        raise RemoteClaimError("push_rejected", (push.stderr or push.stdout).strip())

    verify = _run_remote_git(repo_dir, ["ls-remote", "origin", ref])
    if verify.returncode != 0:
        raise RemoteClaimError(
            "git_error", f"ls-remote failed: {verify.stderr.strip()}"
        )
    remote_sha = verify.stdout.split()[0] if verify.stdout.strip() else None
    if remote_sha != sha:
        raise RemoteClaimError(
            "verify_mismatch",
            f"pushed {sha} but ls-remote reports {remote_sha!r}",
        )
    return sha


def _read_remote_claim(repo_dir: Path, ref: str) -> dict[str, Any] | None:
    """Read the current cross-machine claim on `ref`, if any.

    `git ls-remote origin <ref>` alone tells us whether a claim exists and at
    what SHA, with no fetch needed for the common "no claim" case. Only when
    a SHA is present do we `git fetch origin <ref>` (to get the commit
    locally) and `git log -1 --format=%B <sha>` to read back the JSON
    payload `_push_remote_claim` wrote. Returns `None` when no claim ref
    exists remotely (a "no claim exists" that's safe to reclaim fresh) --
    distinct from a `RemoteClaimError` raised on any ls-remote/fetch/parse
    failure, which means the claim state could not be determined and must
    not be treated as "no claim". On success, returns the parsed payload
    dict with the ref's current SHA added under `"sha"` (needed by a caller
    doing a `--force-with-lease` reclaim).
    """
    ls = _run_remote_git(repo_dir, ["ls-remote", "origin", ref])
    if ls.returncode != 0:
        raise RemoteClaimError("git_error", f"ls-remote failed: {ls.stderr.strip()}")
    if not ls.stdout.strip():
        return None
    sha = ls.stdout.split()[0]

    fetch = _run_remote_git(repo_dir, ["fetch", "origin", ref])
    if fetch.returncode != 0:
        raise RemoteClaimError("git_error", f"fetch failed: {fetch.stderr.strip()}")

    log = _run_remote_git(repo_dir, ["log", "-1", "--format=%B", sha])
    if log.returncode != 0:
        raise RemoteClaimError("git_error", f"log failed: {log.stderr.strip()}")

    try:
        payload = json.loads(log.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RemoteClaimError(
            "git_error", f"claim payload parse failed: {exc}"
        ) from exc

    payload["sha"] = sha
    return payload


def _delete_remote_claim(repo_dir: Path, ref: str) -> None:
    """Best-effort release of a cross-machine claim: delete `ref` on `origin`.

    Called from `finish` after the local lock is already released, so a
    failure here (network down, ref already gone, origin unreachable) must
    never surface as a `finish` failure -- it only leaves a claim to expire
    via its own TTL. Failures are logged, not raised.
    """
    try:
        result = _run_remote_git(repo_dir, ["push", "origin", "--delete", ref])
    except RemoteClaimError as exc:
        logger.warning("remote claim delete failed for %s: %s", ref, exc)
        return
    if result.returncode != 0:
        logger.warning(
            "remote claim delete failed for %s: %s",
            ref,
            (result.stderr or result.stdout).strip(),
        )


def _remote_claim_is_fresh(claim: dict[str, Any]) -> bool:
    """Whether a remote claim payload is still within its own advertised TTL.

    Staleness is measured against the claim's own `ttl_seconds` field (the
    value its claimer published via `_push_remote_claim`), not the reader's
    `--remote-ttl-seconds` -- otherwise a claim published with a short TTL
    would still block a reader applying its own longer default, and any
    reader could steal a live claim by passing a shorter `--remote-ttl-seconds`.

    An unparsable/missing `claimed_at` or `ttl_seconds` is logged and treated
    as stale rather than fresh: "fresh" has no elapsed-time path back out, so
    a single malformed claim ref would otherwise pin the specification on
    every machine until someone manually deletes the ref.
    """
    claimed_at = claim.get("claimed_at")
    ttl_seconds = claim.get("ttl_seconds")
    if not isinstance(claimed_at, str) or not isinstance(ttl_seconds, (int, float)):
        logger.warning(
            "remote claim has missing/invalid claimed_at or ttl_seconds, treating as stale: %r",
            claim,
        )
        return False
    try:
        claimed = datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        logger.warning(
            "remote claim has unparsable claimed_at, treating as stale: %r", claimed_at
        )
        return False
    age = (datetime.now(claimed.tzinfo) - claimed).total_seconds()
    return age <= ttl_seconds


def _lock_path(run_path: Path, specification: str) -> Path:
    return run_path.parent / ".claims" / f"{_claim_slug(specification)}.lock"


def _load_lock(lock_path: Path) -> dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _lock_is_stale(owner: dict[str, Any]) -> bool:
    """A lock is stale if its owning run record is gone or already terminal."""
    owner_path = owner.get("path")
    if not owner_path:
        return True
    record_path = Path(owner_path)
    if not record_path.exists():
        return True
    return _load(record_path).get("final_status") is not None


def cmd_claim(args: argparse.Namespace) -> int:
    """Atomically claim repo+specification for the run at RUN before implementing it.

    Combines the active-conflicts exclusivity check and the run record's
    `specification` write into one OS-atomic step (O_CREAT|O_EXCL on a lock
    file under `<run's repo dir>/.claims/`), closing the TOCTOU gap where two
    sessions each pass the read-only scan before either's record is visible
    to the other.
    """
    run_path = Path(args.run)
    if not run_path.exists():
        raise SystemExit(f"run record not found: {run_path}")
    repo_dir = run_path.parent
    lock_path = _lock_path(run_path, args.specification)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _try_acquire() -> int | None:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None

    fd = _try_acquire()
    if fd is None:
        # Contended -- reclaim only if the existing lock's owning run is
        # provably done (finished) or gone (record deleted/pruned). A live
        # non-terminal owner is a real conflict, not staleness.
        if _lock_is_stale(_load_lock(lock_path)):
            lock_path.unlink(missing_ok=True)
            fd = _try_acquire()
        if fd is None:
            print(json.dumps({"status": "already-claimed", **_load_lock(lock_path)}))
            return 1

    record = _load(run_path)

    # Lock held locally: no new racer on this machine can reach this point
    # for the same repo+specification until we release or finish. When
    # --remote is set, also stake the claim on `origin` before trusting the
    # local lock, so a second machine (which has no visibility into this
    # machine's local lock file) is blocked too. Any failure here (existing
    # live claim, a stale claim's reclaim raced by a third machine, or a
    # push/read error) fails closed: release the local lock and report it
    # indistinguishably from a genuine conflict, extended with
    # `"scope": "remote"` so a caller can tell which layer contended.
    remote_project_repo_dir: Path | None = None
    remote_ref: str | None = None

    if args.remote:
        project_repo_dir = Path(record.get("repository") or repo_dir)
        ref = _claim_ref(args.specification)

        def _remote_already_claimed(
            claim: dict[str, Any] | None, exc: RemoteClaimError | None
        ) -> int:
            os.close(fd)
            lock_path.unlink(missing_ok=True)
            out: dict[str, Any] = {"status": "already-claimed", "scope": "remote"}
            if claim:
                out.update(claim)
            if exc is not None:
                out["reason"] = exc.reason
                out["error"] = str(exc)
            print(json.dumps(out))
            return 1

        try:
            remote_claim = _read_remote_claim(project_repo_dir, ref)
        except RemoteClaimError as exc:
            return _remote_already_claimed(None, exc)

        if remote_claim is not None and _remote_claim_is_fresh(remote_claim):
            return _remote_already_claimed(remote_claim, None)

        try:
            _push_remote_claim(
                project_repo_dir,
                ref,
                record.get("run_id"),
                args.remote_ttl_seconds,
                expect_sha=remote_claim.get("sha") if remote_claim else None,
            )
        except RemoteClaimError as exc:
            return _remote_already_claimed(remote_claim, exc)

        # Pushed successfully but not yet committed to the local lock/record --
        # release it below if the conflict re-check aborts this claim, so a
        # failed claim never orphans the remote ref for the full TTL.
        remote_project_repo_dir = project_repo_dir
        remote_ref = ref

    # Still honor any pre-existing non-terminal record tagged via plain
    # `set` (predates this primitive, or an out-of-band write) -- the lock
    # alone only guards against other `claim` callers, not stale
    # `specification` writes. Only a LIVE conflict blocks the claim -- a
    # STALE one (crashed run, worktree gone, files already merged) is not a
    # real contender, per _active_conflicts()'s own live/stale partition.
    repo_root = Path(record.get("repository") or repo_dir)
    conflicts = _active_conflicts(
        repo_dir, repo_root, args.specification, run_path.resolve()
    )
    live_conflicts = conflicts.get("live") or []
    scan_warnings = conflicts.get("warnings") or []
    if live_conflicts:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
        if remote_project_repo_dir is not None and remote_ref is not None:
            _delete_remote_claim(remote_project_repo_dir, remote_ref)
        print(
            json.dumps(
                {
                    "status": "conflict",
                    "conflicts": live_conflicts,
                    **({"warnings": scan_warnings} if scan_warnings else {}),
                }
            )
        )
        return 1

    lock_payload: dict[str, Any] = {
        "run_id": record.get("run_id"),
        "path": str(run_path),
        "claimed_at": _now(),
    }
    if remote_project_repo_dir is not None and remote_ref is not None:
        lock_payload["remote"] = True
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(lock_payload))
    record["specification"] = args.specification
    _save(run_path, record)
    print(
        json.dumps(
            {
                "status": "claimed",
                "specification": args.specification,
                **({"warnings": scan_warnings} if scan_warnings else {}),
            }
        )
    )
    return 0


def _record_started_ts(record: dict[str, Any], path: Path) -> float:
    """Best-effort start timestamp for retention sorting: parse `started_at`,
    falling back to the file's mtime for a record written before this field
    existed or by a tool that didn't set it.
    """
    started = record.get("started_at")
    if isinstance(started, str) and started:
        try:
            return time.mktime(time.strptime(started[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            pass
    return path.stat().st_mtime


def _prune_repo_dir(
    repo_dir: Path, keep_count: int, keep_days: int, dry_run: bool
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(repo_dir.glob("*.yaml")):
        record, warning = _load_lenient(path)
        if warning is not None:
            # Skip, never delete: retention math can't be computed without a
            # parseable record, and the hand-edit hint tells operators to keep
            # the file as an audit artifact -- pruning it would contradict that.
            warnings.append(warning)
            continue
        entries.append(
            {
                "path": path,
                "started_ts": _record_started_ts(record, path),
                # A run with no final_status yet is still in progress -- retention
                # must never delete the audit trail of active work.
                "active": record.get("final_status") is None,
            }
        )
    entries.sort(key=lambda e: e["started_ts"], reverse=True)

    keep: set = {e["path"] for e in entries[:keep_count]}
    cutoff = time.time() - (keep_days * 86400)
    for e in entries:
        if e["started_ts"] >= cutoff or e["active"]:
            keep.add(e["path"])

    pruned = [str(e["path"]) for e in entries if e["path"] not in keep]
    if not dry_run:
        for e in entries:
            if e["path"] not in keep:
                e["path"].unlink()
    return {
        "repo": repo_dir.name,
        "pruned": pruned,
        "kept": len(entries) - len(pruned),
        "warnings": warnings,
    }


def cmd_prune(args: argparse.Namespace) -> int:
    if args.keep_count < 0 or args.keep_days < 0:
        raise SystemExit("--keep-count and --keep-days must be >= 0")
    base = Path(args.dir).expanduser() if args.dir else worktrail_home() / "runs"
    if not base.is_dir():
        print(json.dumps({"repos": []}))
        return 0
    if args.repo:
        repo_dirs = [base / Path(args.repo).name]
    else:
        repo_dirs = [d for d in sorted(base.iterdir()) if d.is_dir()]
    results = [
        _prune_repo_dir(repo_dir, args.keep_count, args.keep_days, args.dry_run)
        for repo_dir in repo_dirs
        if repo_dir.is_dir()
    ]
    print(json.dumps({"repos": results}, sort_keys=True))
    return 0


def _sweep_orphans_repo_dir(
    repo_dir: Path, status: str, ttl_seconds: int, note: str | None, dry_run: bool
) -> dict[str, Any]:
    closed: list[str] = []
    skipped_live: list[str] = []
    warnings: list[str] = []
    for path in sorted(repo_dir.glob("*.yaml")):
        record, warning = _load_lenient(path)
        if warning is not None:
            warnings.append(warning)
            continue
        if record.get("final_status") is not None:
            continue  # already terminal -- nothing for this sweep to do
        liveness = _run_liveness(record, ttl_seconds, caller_dispatch_id=None)
        if liveness["fresh"]:
            skipped_live.append(str(path))
            continue
        closed.append(str(path))
        if dry_run:
            continue
        liveness_reason = liveness["reason"] or "stale_heartbeat"
        merge_result = note or (
            f"auto-reconciled: orphan sweep closed run {record.get('run_id')} "
            f"(liveness reason={liveness_reason}, age_seconds={liveness['age_seconds']})"
        )
        finish_args = argparse.Namespace(
            path=str(path),
            status=status,
            pr=None,
            merge_result=merge_result,
        )
        # cmd_finish() prints its own confirmation line; this sweep reports
        # one summary object per repo dir instead, so that print is captured
        # and discarded rather than interleaved with it.
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_finish(finish_args)
    return {
        "repo": repo_dir.name,
        "closed": closed,
        "skipped_live": skipped_live,
        "warnings": warnings,
    }


def cmd_sweep_orphans(args: argparse.Namespace) -> int:
    if args.status not in COMPLETION_STATES:
        raise SystemExit(
            f"'{args.status}' is not an allowed completion state.\nAllowed: "
            + ", ".join(COMPLETION_STATES)
        )
    base = Path(args.dir).expanduser() if args.dir else worktrail_home() / "runs"
    if not base.is_dir():
        print(json.dumps({"repos": []}))
        return 0
    if args.repo:
        repo_dirs = [base / Path(args.repo).name]
    else:
        repo_dirs = [d for d in sorted(base.iterdir()) if d.is_dir()]
    results = [
        _sweep_orphans_repo_dir(
            repo_dir, args.status, args.ttl_seconds, args.note, args.dry_run
        )
        for repo_dir in repo_dirs
        if repo_dir.is_dir()
    ]
    print(json.dumps({"repos": results}, sort_keys=True))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--repo", required=True)
    s.add_argument("--request", default="")
    s.add_argument("--route", required=True, choices=list("ABCDEFGHIJ"))
    s.add_argument(
        "--risk", required=True, choices=["low", "medium", "high", "critical"]
    )
    s.add_argument("--reason", default=None)
    s.add_argument("--agent", default=None)
    s.add_argument(
        "--native-skill-available",
        default=None,
        choices=["true", "false"],
        help="resolved native-Skill host capability (invocation_context.py); "
        "omit to record no field at all (predates capability persistence)",
    )
    s.add_argument(
        "--dispatch-mode",
        default=None,
        choices=list(DISPATCH_MODES),
        help="dispatch path the invocation context selected (invocation_context.py)",
    )
    s.add_argument(
        "--dispatch-id",
        default=None,
        help="stable identity for this /go invocation (invocation_context.py's "
        "dispatch_id); the Active-run-resume evidence test compares it against "
        "a later invocation's own dispatch_id to tell a genuine same-session "
        "continuation apart from a different, possibly concurrent, dispatch "
        "evaluating the same run record. Omit to record no field at all.",
    )
    s.add_argument(
        "--routing-decision",
        default=None,
        help="JSON object from resolve_routing() with the resolved primary agent, roles, and fallback chain",
    )
    s.add_argument(
        "--gates",
        default=None,
        help="comma-joined classify.py 'gates' array (e.g. never_automerge,require_human_approval); "
        'omit to record no gates field at all (predates gates persistence), pass "" to record an explicit empty list',
    )
    s.add_argument("--base-branch", default=None)
    s.add_argument("--base-commit", default=None)
    s.add_argument(
        "--dir",
        default=None,
        help="run records directory (default worktrail_home()/runs)",
    )
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("set")
    s.add_argument("path")
    s.add_argument("key")
    s.add_argument("value")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("append")
    s.add_argument("path")
    s.add_argument("key")
    s.add_argument("value")
    s.set_defaults(func=cmd_append)

    s = sub.add_parser("intervention")
    s.add_argument("path")
    s.add_argument("--category", required=True)
    s.add_argument("--minutes", default="?")
    s.add_argument("--tokens", default="?")
    s.add_argument("--note", required=True)
    s.set_defaults(func=cmd_intervention)

    s = sub.add_parser("capacity-gate")
    s.add_argument("path")
    s.add_argument("--provider", action="append", required=True)
    s.add_argument("--failure-class", required=True)
    s.add_argument("--retry-after", default=None)
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_capacity_gate)

    s = sub.add_parser("scope-review")
    s.add_argument("path")
    s.add_argument("--item", required=True)
    s.add_argument(
        "--status", required=True, choices=["complete", "out-of-scope", "blocked"]
    )
    detail = s.add_mutually_exclusive_group(required=True)
    detail.add_argument("--evidence")
    detail.add_argument("--reason")
    s.set_defaults(func=cmd_scope_review)

    s = sub.add_parser("decision")
    s.add_argument("path")
    s.add_argument(
        "--event",
        required=True,
        choices=list(DECISION_EVENTS),
        help="lifecycle hop to stamp: asked (guard filed the "
        "envelope), presented (attended host showed it), "
        "answered (human replied), consumed (resuming run "
        "applied it), superseded (replaced by a newer decision)",
    )
    s.add_argument(
        "--decision-id",
        required=True,
        dest="decision_id",
        help="the pending decision's id (workqueue/decisions.py)",
    )
    s.add_argument(
        "--note",
        default=None,
        help="optional detail, e.g. the answer digest or the superseding decision id",
    )
    s.set_defaults(func=cmd_decision)

    s = sub.add_parser("finish")
    s.add_argument("path")
    s.add_argument("--status", required=True)
    s.add_argument("--pr", default=None)
    s.add_argument("--merge-result", default=None)
    s.set_defaults(func=cmd_finish)

    s = sub.add_parser("active-conflicts")
    s.add_argument("--dir", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--specification", required=True)
    s.add_argument("--exclude", default=None)
    s.set_defaults(func=cmd_active_conflicts)

    s = sub.add_parser("claim")
    s.add_argument("run")
    s.add_argument("--specification", required=True)
    s.add_argument("--remote", action="store_true")
    s.add_argument("--remote-ttl-seconds", type=int, default=86400)
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser("reconcile")
    s.add_argument("run")
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_reconcile)

    s = sub.add_parser("prune")
    s.add_argument(
        "--dir",
        default=None,
        help="run records directory (default worktrail_home()/runs)",
    )
    s.add_argument(
        "--repo",
        default=None,
        help="only prune this repo's run records (matched by repo directory name); omit to prune every repo",
    )
    s.add_argument(
        "--keep-count",
        type=int,
        default=50,
        help="always keep the N most recent run records per repo",
    )
    s.add_argument(
        "--keep-days",
        type=int,
        default=30,
        help="always keep run records started within the last N days",
    )
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be pruned without deleting",
    )
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("sweep-orphans")
    s.add_argument(
        "--dir",
        default=None,
        help="run records directory (default worktrail_home()/runs)",
    )
    s.add_argument(
        "--repo",
        default=None,
        help="only sweep this repo's run records (matched by repo directory name); omit to sweep every repo",
    )
    s.add_argument(
        "--status",
        required=True,
        help="completion state to close each stale record with (one of the ten COMPLETION_STATES)",
    )
    s.add_argument(
        "--ttl-seconds",
        type=int,
        default=_DEFAULT_LIVENESS_TTL_SECONDS,
        help="liveness heartbeat freshness window in seconds (default 1200)",
    )
    s.add_argument(
        "--note",
        default=None,
        help="--merge-result text for each closed record (default: an auto-reconciled note naming the liveness reason)",
    )
    s.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be closed without writing",
    )
    s.set_defaults(func=cmd_sweep_orphans)

    s = sub.add_parser("liveness")
    s.add_argument("run")
    s.add_argument(
        "--ttl-seconds",
        type=int,
        default=_DEFAULT_LIVENESS_TTL_SECONDS,
        help="heartbeat freshness window in seconds (default 1200)",
    )
    s.add_argument(
        "--dispatch-id",
        default=None,
        help="this invocation's own dispatch_id, to check same_dispatch",
    )
    s.set_defaults(func=cmd_liveness)

    s = sub.add_parser("find-by-worktree")
    s.add_argument("--dir", required=True, help="run records directory")
    s.add_argument("--repo", required=True)
    s.add_argument(
        "--worktree", required=True, help="worktree path to look up (exact match)"
    )
    s.set_defaults(func=cmd_find_by_worktree)

    s = sub.add_parser("worktree-conflict")
    s.add_argument("--dir", required=True, help="run records directory")
    s.add_argument("--repo", required=True)
    s.add_argument(
        "--worktree", required=True, help="worktree path to look up (exact match)"
    )
    s.add_argument(
        "--ttl-seconds",
        type=int,
        default=_DEFAULT_LIVENESS_TTL_SECONDS,
        help="heartbeat freshness window in seconds (default 1200)",
    )
    s.add_argument(
        "--dispatch-id",
        default=None,
        help="caller's own dispatch id -- an owning record matching this is never a conflict",
    )
    s.set_defaults(func=cmd_worktree_conflict)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except RunRecordFormatError as exc:
        # Fail loud with the recovery hint, not a traceback — the audience is
        # a headless agent that needs the next command, not a stack.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

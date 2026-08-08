#!/usr/bin/env python3
"""GO v2 run record — the machine-readable audit log of every front-door run.

One YAML file per run under <dir>/<repo-name>/<run-id>.yaml (default dir
~/.go/runs — operational telemetry, deliberately outside the project repo;
override via policy `run_record_dir`). Fields follow the assignment's §20
structure. `finish` enforces the ten explicit completion states (§22) so a run
can never end in vague language. It also code-enforces the
`no_implementation_without_approval` gate (routes.md §A): a run whose
`selected_route` is A cannot `finish` on an implementation-completion state
(completed_and_merged/completed_pr_open/completed_awaiting_human_approval)
unless a `decisions` entry was recorded first.

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
  scope-review PATH --item "..." --status complete|out-of-scope|blocked
               (--evidence "..." | --reason "...")
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
"""
from __future__ import annotations

import argparse
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
from typing import Any, Dict, List

ALLOWED_AGENTS = ("claude", "codex", "opencode")

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
    "intake", "classified", "state_restored", "policy_loaded", "route_selected",
    "executing", "validating", "pr_open", "merge_gate", "done",
)

SECRET_PAT = re.compile(
    r"(api[-_ ]?key|secret|token|password|authorization:\s*bearer)\s*[:=]\s*\S+",
    re.IGNORECASE)

logger = logging.getLogger(__name__)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _quote(value: str) -> str:
    value = str(value)
    if SECRET_PAT.search(value):
        raise SystemExit("refusing to record what looks like a credential")
    if re.search(r"[:#\n\"']", value) or value != value.strip() or value == "":
        return json.dumps(value.replace("\n", " "))
    return value


def _render(record: Dict[str, Any]) -> str:
    lines: List[str] = []
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


def _load(path: Path) -> Dict[str, Any]:
    record: Dict[str, Any] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current:
            item = line[4:].strip()
            record[current].append(json.loads(item) if item.startswith('"') else item)
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
    return record


def _save(path: Path, record: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_render(record), encoding="utf-8")
    tmp.replace(path)  # atomic — a crashed write never corrupts the record


def _parse_json_object(raw: str, label: str) -> Dict[str, Any]:
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
    out_dir = Path(args.dir).expanduser() / repo.name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.yaml"
    agent = (args.agent or "").strip().lower()
    if agent and agent not in ALLOWED_AGENTS:
        raise SystemExit(
            f"invalid agent '{args.agent}'; allowed: {', '.join(ALLOWED_AGENTS)}")
    routing_decision = None
    if args.routing_decision is not None:
        routing_decision = _parse_json_object(args.routing_decision, "--routing-decision")
    gates = [g for g in (args.gates or "").split(",") if g] if args.gates is not None else None
    # Two starts in the same second must not overwrite the first audit record.
    serial = 1
    while path.exists():
        serial += 1
        run_id = f"go-{time.strftime('%Y%m%d-%H%M%S')}-{serial}"
        path = out_dir / f"{run_id}.yaml"
    record: Dict[str, Any] = {
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
        "status": "route_selected",
        **({"routing_decision": routing_decision} if routing_decision is not None else {}),
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
    "grounding",            # corrected a greenfield-on-brownfield assumption / found existing code
    "integration_repair",   # fixed a cross-task bug that per-task review missed
    "ci_repair",            # repaired red CI on a PR
    "conflict_resolution",   # hand-resolved a merge/integration conflict
    "env_setup",            # built/repaired a verification environment to run tests
    "quota_wait",           # blocked on model/account rate or session limit
    "capacity_gate",        # all configured headless providers are unavailable
    "orchestrator_defect",   # worked around a bug in the orchestrator itself
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
            + ", ".join(INTERVENTION_CATEGORIES))
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
        "note": (args.note or "all configured headless providers are unavailable")[:300],
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
    record.setdefault("scope_review", []).append(
        f"{args.status} | {item} | {detail}"
    )
    _save(path, record)
    print(json.dumps({"status": args.status, "item": item}))
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    if args.status not in COMPLETION_STATES:
        raise SystemExit(
            f"'{args.status}' is not an allowed completion state.\nAllowed: "
            + ", ".join(COMPLETION_STATES))
    path = Path(args.path)
    record = _load(path)
    if (record.get("selected_route") == "A"
            and args.status in IMPLEMENTATION_COMPLETION_STATES
            and not record.get("decisions")):
        raise SystemExit(
            "no_implementation_without_approval: Route A cannot finish with "
            f"'{args.status}' without a recorded decision. Route A's own "
            "completions are investigation_complete or "
            "planned_ready_for_implementation; proceeding to implementation "
            "requires an explicit decision entry first "
            f"(run_record.py append {path} decisions \"...\").")
    record["completed_at"] = _now()
    record["status"] = "done"
    record["final_status"] = args.status
    if args.pr:
        record["pull_request"] = args.pr
    if args.merge_result:
        record["merge_result"] = args.merge_result
    _save(path, record)
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


def _is_stale(record: Dict[str, Any], repo_dir: Path, base_branch: str) -> bool:
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
            ["git", "-C", str(repo_dir), "cat-file", "-e", f"{base_branch}:{candidate}"],
            capture_output=True,
        )
        if result.returncode != 0:
            return False
    return True


def _active_conflicts(
    repo_dir: Path, repo_root: Path, specification: str, exclude: Path | None
) -> Dict[str, List[Dict[str, Any]]]:
    """Other non-terminal run records under `repo_dir` targeting `specification`,
    partitioned into `{"live": [...], "stale": [...]}` via `_is_stale()`.

    `repo_root` is the actual git repository (for `_is_stale()`'s `git cat-file`
    check), distinct from `repo_dir` (the run-records directory for this repo).
    Each record's own `base_branch` is used — never a single value shared
    across records, since concurrent runs may target different base branches.
    A record with no `base_branch` is treated as live (can't check staleness).
    """
    live: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    if repo_dir.is_dir():
        for path in sorted(repo_dir.glob("*.yaml")):
            if exclude and path.resolve() == exclude:
                continue
            record = _load(path)
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
    return {"live": live, "stale": stale}


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
    repo_root = Path(record["repository"]) if record.get("repository") else run_path.parent
    base_branch = record.get("base_branch")
    if not base_branch or not _is_stale(record, repo_root, base_branch):
        print(json.dumps({
            "status": "not_stale",
            "run_id": record.get("run_id"),
            "path": str(run_path),
        }))
        return 0
    merge_result = args.note or (
        f"auto-reconciled: staleness reconciler closed run {record.get('run_id')}"
    )
    finish_args = argparse.Namespace(
        path=str(run_path), status="completed_and_merged", pr=None,
        merge_result=merge_result,
    )
    return cmd_finish(finish_args)


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


def _run_remote_git(repo_dir: Path, args: List[str]) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True, text=True, timeout=_REMOTE_GIT_TIMEOUT,
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
    payload = json.dumps({
        "run_id": run_id,
        "claimed_at": _now(),
        "hostname": socket.gethostname(),
        "ttl_seconds": ttl_seconds,
    })
    commit = _run_remote_git(repo_dir, ["commit-tree", _EMPTY_TREE_SHA, "-m", payload])
    if commit.returncode != 0:
        raise RemoteClaimError("git_error", f"commit-tree failed: {commit.stderr.strip()}")
    sha = commit.stdout.strip()

    lease = f"--force-with-lease={ref}:{expect_sha or ''}"
    push = _run_remote_git(repo_dir, ["push", "origin", f"{sha}:{ref}", lease])
    if push.returncode != 0:
        raise RemoteClaimError("push_rejected", (push.stderr or push.stdout).strip())

    verify = _run_remote_git(repo_dir, ["ls-remote", "origin", ref])
    if verify.returncode != 0:
        raise RemoteClaimError("git_error", f"ls-remote failed: {verify.stderr.strip()}")
    remote_sha = verify.stdout.split()[0] if verify.stdout.strip() else None
    if remote_sha != sha:
        raise RemoteClaimError(
            "verify_mismatch",
            f"pushed {sha} but ls-remote reports {remote_sha!r}",
        )
    return sha


def _read_remote_claim(repo_dir: Path, ref: str) -> Dict[str, Any] | None:
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
        raise RemoteClaimError("git_error", f"claim payload parse failed: {exc}") from exc

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
            "remote claim delete failed for %s: %s", ref, (result.stderr or result.stdout).strip()
        )


def _remote_claim_is_fresh(claim: Dict[str, Any]) -> bool:
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
        logger.warning("remote claim has missing/invalid claimed_at or ttl_seconds, treating as stale: %r", claim)
        return False
    try:
        claimed = datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        logger.warning("remote claim has unparsable claimed_at, treating as stale: %r", claimed_at)
        return False
    age = (datetime.now(claimed.tzinfo) - claimed).total_seconds()
    return age <= ttl_seconds


def _lock_path(run_path: Path, specification: str) -> Path:
    return run_path.parent / ".claims" / f"{_claim_slug(specification)}.lock"


def _load_lock(lock_path: Path) -> Dict[str, Any]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _lock_is_stale(owner: Dict[str, Any]) -> bool:
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

        def _remote_already_claimed(claim: Dict[str, Any] | None, exc: RemoteClaimError | None) -> int:
            os.close(fd)
            lock_path.unlink(missing_ok=True)
            out: Dict[str, Any] = {"status": "already-claimed", "scope": "remote"}
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
                project_repo_dir, ref, record.get("run_id"), args.remote_ttl_seconds,
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
    # `specification` writes.
    conflicts = _active_conflicts(repo_dir, args.specification, run_path.resolve())
    if conflicts:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
        if remote_project_repo_dir is not None and remote_ref is not None:
            _delete_remote_claim(remote_project_repo_dir, remote_ref)
        print(json.dumps({"status": "conflict", "conflicts": conflicts}))
        return 1

    lock_payload: Dict[str, Any] = {
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
    print(json.dumps({"status": "claimed", "specification": args.specification}))
    return 0


def _record_started_ts(record: Dict[str, Any], path: Path) -> float:
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
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for path in sorted(repo_dir.glob("*.yaml")):
        record = _load(path)
        entries.append({
            "path": path,
            "started_ts": _record_started_ts(record, path),
            # A run with no final_status yet is still in progress -- retention
            # must never delete the audit trail of active work.
            "active": record.get("final_status") is None,
        })
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
    return {"repo": repo_dir.name, "pruned": pruned, "kept": len(entries) - len(pruned)}


def cmd_prune(args: argparse.Namespace) -> int:
    if args.keep_count < 0 or args.keep_days < 0:
        raise SystemExit("--keep-count and --keep-days must be >= 0")
    base = Path(args.dir).expanduser()
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--repo", required=True)
    s.add_argument("--request", default="")
    s.add_argument("--route", required=True, choices=list("ABCDEFGHIJ"))
    s.add_argument("--risk", required=True,
                   choices=["low", "medium", "high", "critical"])
    s.add_argument("--reason", default=None)
    s.add_argument("--agent", default=None)
    s.add_argument(
        "--routing-decision",
        default=None,
        help="JSON object from resolve_routing() with the resolved primary agent, roles, and fallback chain",
    )
    s.add_argument(
        "--gates",
        default=None,
        help="comma-joined classify.py 'gates' array (e.g. never_automerge,require_human_approval); "
             "omit to record no gates field at all (predates gates persistence), pass \"\" to record an explicit empty list",
    )
    s.add_argument("--base-branch", default=None)
    s.add_argument("--base-commit", default=None)
    s.add_argument("--dir", default="~/.go/runs")
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
    s.add_argument("--status", required=True,
                   choices=["complete", "out-of-scope", "blocked"])
    detail = s.add_mutually_exclusive_group(required=True)
    detail.add_argument("--evidence")
    detail.add_argument("--reason")
    s.set_defaults(func=cmd_scope_review)

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
    s.add_argument("--dir", default="~/.go/runs")
    s.add_argument("--repo", default=None,
                    help="only prune this repo's run records (matched by repo directory name); omit to prune every repo")
    s.add_argument("--keep-count", type=int, default=50,
                    help="always keep the N most recent run records per repo")
    s.add_argument("--keep-days", type=int, default=30,
                    help="always keep run records started within the last N days")
    s.add_argument("--dry-run", action="store_true",
                    help="report what would be pruned without deleting")
    s.set_defaults(func=cmd_prune)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

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
         [--routing-decision JSON]
                                               -> prints {run_id, path}
  set    PATH KEY VALUE                       -> set/replace a top-level field
  append PATH KEY VALUE                       -> append VALUE to a list field
  intervention PATH --category C --minutes N --tokens N --note "..."
         -> log a manual rescue (process-friction telemetry for retros)
  capacity-gate PATH --provider AGENT:MODEL --failure-class CLASS
          [--retry-after ISO8601] [--note "..."]
         -> record a sanitized all-provider capacity gate
finish PATH --status completed_pr_open [--pr URL] [--merge-result ...]
  scope-review PATH --item "..." --status complete|out-of-scope|blocked
               (--evidence "..." | --reason "...")
  active-conflicts --dir DIR --repo REPO --specification SPEC [--exclude PATH]
         -> read-only scan for other non-terminal runs on the same
            repo+specification; prints a JSON array (see contracts/active-conflicts-cli.md)
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
import re
import sys
import time
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
    print(json.dumps({"final_status": args.status, "path": str(path)}))
    return 0


def cmd_active_conflicts(args: argparse.Namespace) -> int:
    """Read-only scan for other non-terminal runs on the same repo+specification."""
    repo = Path(args.repo).resolve()
    repo_dir = Path(args.dir).expanduser() / repo.name
    exclude = Path(args.exclude).resolve() if args.exclude else None
    results: List[Dict[str, Any]] = []
    if repo_dir.is_dir():
        for path in sorted(repo_dir.glob("*.yaml")):
            if exclude and path.resolve() == exclude:
                continue
            record = _load(path)
            if record.get("final_status") is not None:
                continue
            if record.get("specification") != args.specification:
                continue
            results.append({
                "run_id": record.get("run_id"),
                "path": str(path),
                "started_at": record.get("started_at"),
                "request_summary": record.get("request_summary"),
                "agent": record.get("agent"),
            })
    print(json.dumps(results))
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

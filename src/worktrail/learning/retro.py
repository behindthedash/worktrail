"""Post-run retro: curate a repo's run-outcome memory with a Claude agent (design D3-D9)."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from worktrail.learning.digest import build_outcome_digest
from worktrail.learning.paths import RETRO_AGENT_NAME, learning_dir, retro_memory_path
from worktrail.orchestrator import progress, spawnlib
from worktrail.orchestrator.dispatch import REVIEW_DEFAULT_TIER
from worktrail.router.policy import load_policy, resolve_routing
from worktrail.runtime.selection import NoExecutionTarget, select_cell
from worktrail.shared.homedir import worktrail_home

RETRO_TIMEOUT = 900
MAX_NOTES = 20
MAX_MEMORY_BYTES = 25 * 1024
NOTES_SECTION = "## Notes for workers"
_AGENT_PROMPT_FILE = Path(__file__).with_name("retro_agent.md")


def agent_definition() -> dict:
    return {
        RETRO_AGENT_NAME: {
            "description": "Curates this repository's run-outcome memory for future workers.",
            "prompt": _AGENT_PROMPT_FILE.read_text(encoding="utf-8"),
            "tools": ["Read", "Write", "Edit"],
            "memory": "project",
        }
    }


def build_prompt(repo: Path, digest: dict) -> str:
    return (
        f"Repository: {Path(repo).resolve().name}\n"
        f"Date: {datetime.now().astimezone().date().isoformat()}\n\n"
        "Fold this run's outcome digest into your MEMORY.md per your curation rules.\n\n"
        f"```json\n{json.dumps(digest, indent=2, sort_keys=True)}\n```\n"
    )


def check_memory_contract(path: Path) -> list[str]:
    path = Path(path)
    if not path.is_file():
        return ["missing_file"]
    violations = []
    if path.stat().st_size > MAX_MEMORY_BYTES:
        violations.append("oversize")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if NOTES_SECTION not in (line.strip() for line in lines):
        return violations + ["missing_notes_section"]
    notes = []
    in_notes = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_notes = stripped == NOTES_SECTION
        elif in_notes and stripped.startswith(("- ", "* ")):
            notes.append(stripped)
    if len(notes) > MAX_NOTES:
        violations.append("too_many_notes")
    if any("(evidence:" not in note for note in notes):
        violations.append("note_missing_evidence")
    return violations


def _gate(repo: Path, journal_path: Path, select: Callable) -> tuple[dict, dict | None]:
    """Policy, digest-signal and Claude-cell gates; returns (decision, digest)."""
    if not load_policy(repo).get("agent_learning"):
        return {"status": "skipped", "reason": "disabled"}, None
    try:
        journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {"status": "failed", "reason": "journal_unreadable"}, None
    digest = build_outcome_digest(journal)
    if not digest["has_signal"]:
        return {"status": "skipped", "reason": "no_signal"}, digest
    try:
        routing = resolve_routing(load_policy(worktrail_home()))
        cell = select(routing, REVIEW_DEFAULT_TIER, prefer="claude")
    except NoExecutionTarget:
        return {"status": "failed", "reason": "no_execution_target"}, digest
    if cell.harness != "claude":
        return {"status": "skipped", "reason": "claude_harness_unavailable"}, digest
    return {"status": "ready", "reason": "spawn"}, digest


def _curate(
    repo: Path, digest: dict, spawn: Callable, log: Callable, timeout: int
) -> dict:
    ldir = learning_dir(repo)
    ldir.mkdir(parents=True, exist_ok=True)
    with open(ldir / ".retro.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "locked"}
        try:
            spawnlib.raise_if_exhausted(
                spawn(
                    build_prompt(repo, digest),
                    ldir,
                    tier=REVIEW_DEFAULT_TIER,
                    prefer="claude",
                    timeout=timeout,
                    extra_args=[
                        "--agents",
                        json.dumps(agent_definition()),
                        "--agent",
                        RETRO_AGENT_NAME,
                    ],
                    log=log,
                ),
                context="retro",
            )
        except subprocess.TimeoutExpired:
            return {"status": "failed", "reason": "timeout"}
        except Exception as e:  # noqa: BLE001 -- SpawnExhausted/NoExecutionTarget included
            return {"status": "failed", "reason": type(e).__name__}
        memory = retro_memory_path(repo)
        result: dict[str, Any] = {"status": "completed", "reason": "curated"}
        if memory.is_file():
            result["memory_sha256"] = hashlib.sha256(memory.read_bytes()).hexdigest()
        violations = check_memory_contract(memory)
        if violations:
            result["contract_violations"] = violations
        return result


def run_retro(
    repo,
    journal_path,
    *,
    spawn=spawnlib.spawn_agent,
    select=select_cell,
    log=print,
    timeout=RETRO_TIMEOUT,
) -> dict:
    repo = Path(repo)
    try:
        result, digest = _gate(repo, Path(journal_path), select)
        if result["status"] == "ready":
            result = _curate(repo, digest, spawn, log, timeout)
    except Exception as e:  # noqa: BLE001 -- best-effort: always record a marker
        result = {"status": "failed", "reason": type(e).__name__}
    progress.append_safety_net_events(journal_path, [{"event": "retro", **result}])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="worktrail-retro", description="Curate a repo's run-outcome memory."
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    repo = Path(args.repo)
    if not repo.is_dir() or not Path(args.journal).is_file():
        print(
            "worktrail-retro: --repo must be a directory and --journal a file",
            file=sys.stderr,
        )
        return 2
    if args.dry_run:
        decision, digest = _gate(repo, Path(args.journal), select_cell)
        if decision["reason"] == "disabled":
            try:
                journal = json.loads(Path(args.journal).read_text(encoding="utf-8"))
                digest = build_outcome_digest(journal)
            except OSError, json.JSONDecodeError:
                pass
        out: dict[str, Any] = {"digest": digest, "decision": decision}
        print(json.dumps(out, indent=2, sort_keys=True) if args.json else out)
        return 1 if decision["status"] == "failed" else 0
    result = run_retro(repo, args.journal)
    print(
        json.dumps(result, sort_keys=True)
        if args.json
        else f"retro: {result['status']} ({result['reason']})"
    )
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())

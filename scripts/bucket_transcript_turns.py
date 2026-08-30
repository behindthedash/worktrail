#!/usr/bin/env python3
"""Bucket assistant turns in $WORKTRAIL_KEEP_TRANSCRIPTS-captured stream-json
transcripts by activity, per worker role -- the analysis method
docs/specs/research/worker-spawn-cache-read-amplification.md's "Validation steps
for the next proposal" calls for. Not a supported orchestrator entry point: a
one-off diagnostic over a transcript directory + the matching run journal.

Usage: python3 scripts/bucket_transcript_turns.py <transcripts-dir> <run-journal.json>
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TEST_RE = re.compile(r"\b(pytest|npm test|npm run test|go test|jest|vitest)\b")
GIT_HISTORY_RE = re.compile(r"\bgit (log|status|diff|show|blame)\b")
EXPLORE_TOOLS = {"Read", "Grep", "Glob"}


def classify_turn(tool_uses: list[tuple[str | None, str | None]]) -> str:
    """tool_uses: [(tool_name, bash_command_or_None), ...] for one assistant turn.
    Priority order below picks one dominant bucket per turn when several tools
    were used in the same turn."""
    buckets = set()
    for tool, cmd in tool_uses:
        if tool == "Bash" and cmd and TEST_RE.search(cmd):
            buckets.add("test_execution")
        elif tool == "Bash" and cmd and GIT_HISTORY_RE.search(cmd):
            buckets.add("git_history_exploration")
        elif tool in EXPLORE_TOOLS:
            buckets.add("repo_exploration")
        elif tool in ("Edit", "Write"):
            buckets.add("edit")
        elif tool == "Bash":
            buckets.add("other_bash")
    if not buckets:
        return "final_report"
    for b in (
        "test_execution",
        "edit",
        "git_history_exploration",
        "repo_exploration",
        "other_bash",
    ):
        if b in buckets:
            return b
    return "other"


def analyze_transcript(path: Path) -> dict:
    turns: dict[str, list[tuple[str | None, str | None]]] = {}
    order: list[str] = []
    num_turns_reported = None
    for line in path.read_text().strip().split("\n"):
        event = json.loads(line)
        if event.get("type") == "result":
            num_turns_reported = event.get("num_turns")
            continue
        if event.get("type") != "assistant":
            continue
        mid = event["message"].get("id")
        if mid not in turns:
            turns[mid] = []
            order.append(mid)
        for block in event["message"].get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                cmd = block.get("input", {}).get("command") if name == "Bash" else None
                turns[mid].append((name, cmd))
    buckets = defaultdict(int)
    for mid in order:
        buckets[classify_turn(turns[mid])] += 1
    return {
        "file": path.name,
        "observed_turns": len(order),
        "num_turns_reported": num_turns_reported,
        "buckets": dict(buckets),
    }


def match_journal(transcripts_dir: Path, journal_path: Path) -> list[dict]:
    """Match each transcript file to its (task, role) by nearest finish
    timestamp (started_at + duration_s) in the run journal -- the transcript
    filename only carries the spawn's cwd basename, agent, and finish epoch,
    not its role."""
    journal = json.loads(journal_path.read_text())
    spawns = [
        e
        for e in journal["entries"]
        if e.get("usage") and e["usage"].get("num_turns") is not None
    ]
    files = sorted(transcripts_dir.glob("*.jsonl"))
    results = []
    for entry in spawns:
        finish_ts = entry["started_at"] + entry["duration_s"]
        best = min(files, key=lambda f: abs(int(f.stem.rsplit("-", 2)[-2]) - finish_ts))
        r = analyze_transcript(best)
        r["task"] = entry["task"]
        r["role"] = entry["role"]
        r["reported_cost_usd"] = entry["usage"].get("total_cost_usd")
        results.append(r)
    return results


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    results = match_journal(Path(sys.argv[1]), Path(sys.argv[2]))
    by_role = defaultdict(lambda: defaultdict(int))
    for r in results:
        print(json.dumps(r))
        for b, c in r["buckets"].items():
            by_role[r["role"]][b] += c
    print("--- aggregate by role ---", file=sys.stderr)
    for role, buckets in by_role.items():
        total = sum(buckets.values())
        print(f"{role} ({total} turns):", file=sys.stderr)
        for b, c in sorted(buckets.items(), key=lambda kv: -kv[1]):
            print(f"  {b:26s} {c:3d}  ({100 * c / total:.0f}%)", file=sys.stderr)


if __name__ == "__main__":
    main()

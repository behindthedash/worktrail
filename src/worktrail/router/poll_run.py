#!/usr/bin/env python3
"""Poll a run record for completion.

Bounded-iteration poller that checks if a run record has finished (finish or final_status key set).
Reads YAML run record file, sleeps between iterations, exits with completion state or ceiling message.

On completion with a PR URL, also applies the same post-hoc `go:risk-*` PR
label correction drain.py applies after its own spawned one-shots
(`ensure_pr_risk_label`, extracted to `router/pr_labels.py`): a headless
worker's own `gh pr create` isn't guaranteed to be covered by the Claude Code
PreToolUse label-enforcement hook, so this is go's own Phase 7 poll-exit
equivalent of drain.py's queue-drain correction -- see
docs/specs/research/go-dispatch-one-shot-pr-label-gap.md.

On completion, unresolved entries in the record's `pending_decisions` audit
list are recognized as first-class pending-user-decision results (never a
generic failure): each still-unanswered decision id is printed with its
exact resume token so the attended host can present the decision, answer
it, and resume through that exact id (`worktrail-skill-dispatch
--resume-decision <id>`). Consumed or superseded decisions are not surfaced.

Usage:
  poll_run.py --run /path/to/run.yaml [--interval 30] [--max-iterations 20]

Exit codes:
  0 = run finished; prints completion state and PR URL (if present), plus any
      pending user decision awaiting a human answer
  1 = ceiling reached; prints "ceiling reached — subprocess still running"
"""

import argparse
import re
import sys
import time
from pathlib import Path

from .pr_labels import ensure_pr_risk_label

# One `pending_decisions` audit entry as run_record.record_decision_event
# writes it: `<ts> [<event>] <decision-id>` (plus an optional note). Token
# comparison only -- `[asked] dec-x` must never match id `dec-x-extra`.
_DECISION_ENTRY_RE = re.compile(r"\[(?P<event>[a-z_-]+)\]\s+(?P<id>\S+)")

# Events that close a decision's lifecycle on the run record; anything else
# (asked/presented/answered) means the human's input is still outstanding.
_TERMINAL_DECISION_EVENTS = ("consumed", "superseded")


def parse_yaml_value(value_str: str) -> str:
    """Parse a simple YAML scalar value (unquoting if necessary)."""
    value_str = value_str.strip()
    if value_str.startswith('"') and value_str.endswith('"'):
        return value_str[1:-1]
    return value_str


def read_run_record(path: Path) -> dict:
    """Read YAML run record file and extract finish/final_status and pull_request fields.

    Simple `- item` lines nested under a key are collected into a list for
    that key (e.g. `pending_decisions:`); scalar keys keep their existing
    string/None values.
    """
    if not path.exists():
        raise FileNotFoundError(f"Run record not found: {path}")

    record: dict = {}
    last_key = None
    content = path.read_text(encoding="utf-8")

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("- ") and last_key is not None:
            current = record.get(last_key)
            item = parse_yaml_value(stripped[2:])
            if isinstance(current, list):
                current.append(item)
            elif current in ("", None):
                record[last_key] = [item]
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            last_key = key

            if value == "null":
                record[key] = None
            elif value.startswith('"') and value.endswith('"'):
                record[key] = parse_yaml_value(value)
            else:
                record[key] = value

    return record


def decision_lifecycle(record: dict) -> dict:
    """Map each decision id in the record's `pending_decisions` audit list to
    its ordered event names. Malformed entries are ignored -- the audit list
    is advisory context, never a parser crash."""
    lifecycle: dict = {}
    entries = record.get("pending_decisions")
    if not isinstance(entries, list):
        return lifecycle
    for entry in entries:
        if not isinstance(entry, str):
            continue
        m = _DECISION_ENTRY_RE.search(entry)
        if m:
            lifecycle.setdefault(m.group("id"), []).append(m.group("event"))
    return lifecycle


def unresolved_decision_ids(record: dict) -> list:
    """Decision ids whose most recent lifecycle event is neither consumed nor
    superseded -- i.e. decisions still awaiting (or freshly carrying) a human
    answer, in first-seen order."""
    pending = []
    for decision_id, events in decision_lifecycle(record).items():
        if not events or events[-1] not in _TERMINAL_DECISION_EVENTS:
            pending.append(decision_id)
    return pending


def is_finished(record: dict) -> bool:
    """Check if run record has a completion status (`final_status` is set by
    `run_record.py finish`; a `finish` *field* never exists in the record)."""
    return record.get("final_status") is not None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Poll a run record for completion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 = finished, 1 = ceiling reached",
    )
    parser.add_argument("--run", required=True, help="Path to run record YAML file")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Sleep interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Maximum poll iterations (default: 20)",
    )

    args = parser.parse_args(argv)
    run_path = Path(args.run)

    for iteration in range(args.max_iterations):
        try:
            record = read_run_record(run_path)

            if is_finished(record):
                final_status = record.get("final_status") or "unknown"
                pr_url = record.get("pull_request")

                if pr_url:
                    applied = ensure_pr_risk_label(
                        record.get("repository"), pr_url, record.get("risk_level")
                    )
                    if applied:
                        print(f"poll_run: added missing {applied} label to {pr_url}")
                    print(f"Run completed: {final_status} — PR: {pr_url}")
                else:
                    print(f"Run completed: {final_status}")

                pending = unresolved_decision_ids(record)
                if pending:
                    print(
                        "pending_user_decision: a human must answer before "
                        "this run resumes (present, answer, then resume via "
                        "`worktrail-skill-dispatch --resume-decision <id>`)"
                    )
                    for decision_id in pending:
                        print(f"pending_user_decision: {decision_id}")
                return 0

        except FileNotFoundError:
            pass

        if iteration < args.max_iterations - 1:
            time.sleep(args.interval)

    print("ceiling reached — subprocess still running")
    return 1


if __name__ == "__main__":
    sys.exit(main())

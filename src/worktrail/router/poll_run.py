#!/usr/bin/env python3
"""Poll a run record for completion.

Bounded-iteration poller that checks if a run record has finished (finish or final_status key set).
Reads YAML run record file, sleeps between iterations, exits with completion state or ceiling message.

Usage:
  poll_run.py --run /path/to/run.yaml [--interval 30] [--max-iterations 20]

Exit codes:
  0 = run finished; prints completion state and PR URL (if present)
  1 = ceiling reached; prints "ceiling reached — subprocess still running"
"""
import argparse
import sys
import time
from pathlib import Path


def parse_yaml_value(value_str: str) -> str:
    """Parse a simple YAML scalar value (unquoting if necessary)."""
    value_str = value_str.strip()
    if value_str.startswith('"') and value_str.endswith('"'):
        return value_str[1:-1]
    return value_str


def read_run_record(path: Path) -> dict:
    """Read YAML run record file and extract finish/final_status and pull_request fields."""
    if not path.exists():
        raise FileNotFoundError(f"Run record not found: {path}")

    record = {}
    content = path.read_text(encoding="utf-8")

    for line in content.splitlines():
        if not line.strip() or line.startswith("  - "):
            continue

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value == "null":
                record[key] = None
            elif value.startswith('"') and value.endswith('"'):
                record[key] = parse_yaml_value(value)
            else:
                record[key] = value

    return record


def is_finished(record: dict) -> bool:
    """Check if run record has a completion status (`final_status` is set by
    `run_record.py finish`; a `finish` *field* never exists in the record)."""
    return record.get("final_status") is not None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Poll a run record for completion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 = finished, 1 = ceiling reached")
    parser.add_argument("--run", required=True, help="Path to run record YAML file")
    parser.add_argument("--interval", type=int, default=30,
                       help="Sleep interval in seconds (default: 30)")
    parser.add_argument("--max-iterations", type=int, default=20,
                       help="Maximum poll iterations (default: 20)")

    args = parser.parse_args(argv)
    run_path = Path(args.run)

    for iteration in range(args.max_iterations):
        try:
            record = read_run_record(run_path)

            if is_finished(record):
                final_status = record.get("final_status") or "unknown"
                pr_url = record.get("pull_request")

                if pr_url:
                    print(f"Run completed: {final_status} — PR: {pr_url}")
                else:
                    print(f"Run completed: {final_status}")
                return 0

        except FileNotFoundError:
            pass

        if iteration < args.max_iterations - 1:
            time.sleep(args.interval)

    print("ceiling reached — subprocess still running")
    return 1


if __name__ == "__main__":
    sys.exit(main())

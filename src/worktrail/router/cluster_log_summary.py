#!/usr/bin/env python3
"""
Human-eyeball report for the cluster-detection precision telemetry log
(spec 018, change 2026-07-14--cluster-precision-telemetry). Reads
`~/.go/cluster-log.jsonl` (or `--log-path`/`$GO_CLUSTER_LOG`) and prints
shown/consolidated/declined counts plus the derived precision.

No dashboard UI is needed for this -- this script is the whole reporting
surface until cluster detection has proven trustworthy in practice.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import cluster_telemetry


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Summarize cluster-detection precision telemetry")
    p.add_argument(
        "--log-path",
        default=None,
        help="override the telemetry log path (default: ~/.go/cluster-log.jsonl or $GO_CLUSTER_LOG)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of a human-readable report")
    args = p.parse_args(argv)

    log_path = Path(args.log_path).expanduser() if args.log_path else None
    summary = cluster_telemetry.summarize(log_path)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Clusters shown:  {summary['shown']}")
    print(f"Consolidated:    {summary['consolidated']}")
    print(f"Declined:        {summary['declined']}")
    if summary["precision"] is None:
        print("Precision:       n/a (no consolidate/decline decisions recorded yet)")
    else:
        print(f"Precision:       {summary['precision']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

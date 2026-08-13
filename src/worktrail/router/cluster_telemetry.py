#!/usr/bin/env python3
"""
Append-only JSONL telemetry for the dashboard's cluster detection (spec 018,
change 2026-07-14--cluster-precision-telemetry).

Two record kinds share one log:

- `"shown"` -- written once per surfaced cluster whenever `dashboard.py`
  computes clusters for a `--json` call.
  `{"kind": "shown", "at": <iso8601>, "members": [...], "signals": [...], "size": N}`
- `"outcome"` -- written once per `consolidate_cluster.py` execute decision.
  `{"kind": "outcome", "at": <iso8601>, "status": "consolidated"|"declined", "members": [...]}`

This module never computes or persists a running precision score -- it only
appends raw events. `cluster_log_summary.py` derives
`consolidated / (consolidated + declined)` from the log on demand, so a
future session can judge cluster-detection precision from real usage data
instead of a one-time manual spot-check.

Best-effort, never fatal: any failure to write (permission error, disk full,
missing/unwritable parent) is swallowed. Telemetry is observability, not a
control path, and must never break a dashboard render or a consolidation
action -- mirrors `cluster_detect.py`'s own never-crash-the-caller posture.

Stdlib-only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..shared.homedir import worktrail_home


def default_log_path() -> Path:
    """`worktrail_home()/cluster-log.jsonl`, overridable via `GO_CLUSTER_LOG` --
    mirrors `run_record.py`'s `worktrail_home()/runs` default/override pattern.
    Cross-repo by design: cluster detection scans one shared work queue, not a
    per-repo one."""
    override = os.environ.get("GO_CLUSTER_LOG")
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "cluster-log.jsonl"


def _append(record: Dict[str, Any], log_path: Optional[Path] = None) -> None:
    path = log_path or default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # best-effort -- telemetry must never break the caller


def log_shown(clusters: Iterable[Dict[str, Any]], log_path: Optional[Path] = None) -> None:
    """Append one `"shown"` record per surfaced cluster, all sharing one
    timestamp (they were computed together, in one render)."""
    at = datetime.now(timezone.utc).isoformat()
    for cluster in clusters:
        _append(
            {
                "kind": "shown",
                "at": at,
                "members": list(cluster.get("members", [])),
                "signals": list(cluster.get("signals", [])),
                "size": cluster.get("size", len(cluster.get("members", []))),
            },
            log_path,
        )


def log_outcome(status: str, member_ids: Iterable[str], log_path: Optional[Path] = None) -> None:
    """Append one `"outcome"` record for a `consolidate_cluster.py execute`
    decision. `status` is the caller's own vocabulary (`"consolidated"` or
    `"declined"`) -- this module does not validate or constrain it, so a
    future outcome kind needs no change here."""
    _append(
        {
            "kind": "outcome",
            "at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "members": list(member_ids),
        },
        log_path,
    )


def read_records(log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read every record from the log, skipping any unparseable line.
    Returns `[]` if the log doesn't exist."""
    path = log_path or default_log_path()
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize(log_path: Optional[Path] = None) -> Dict[str, Any]:
    """Compute `shown`/`consolidated`/`declined` counts and the derived
    precision (`consolidated / (consolidated + declined)`) from the log.

    `precision` is `None` (not `0.0`) when no outcome has been recorded yet
    -- "no data" and "0% precision" are different findings and must not be
    conflated."""
    records = read_records(log_path)
    shown = sum(1 for r in records if r.get("kind") == "shown")
    consolidated = sum(
        1 for r in records if r.get("kind") == "outcome" and r.get("status") == "consolidated"
    )
    declined = sum(
        1 for r in records if r.get("kind") == "outcome" and r.get("status") == "declined"
    )
    decided = consolidated + declined
    precision = (consolidated / decided) if decided else None
    return {
        "shown": shown,
        "consolidated": consolidated,
        "declined": declined,
        "precision": precision,
    }

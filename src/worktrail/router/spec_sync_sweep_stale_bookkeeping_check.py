#!/usr/bin/env python3
"""Per-repo stale-bookkeeping check with per-repo error isolation.

Mirrors `spec_sync_sweep_checkbox_check.py`'s shape: the whole body runs
inside one try/except, returning {"repo": str(repo), "findings": [...],
"error": None} on success or {"repo": str(repo), "findings": [], "error":
str(exc)} on failure -- the exception never propagates out of this
function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import dashboard


def check_repo_stale_bookkeeping(repo: Path) -> dict[str, Any]:
    """Run the stale-bookkeeping check against repo's docs/specs/ tree.

    Returns a Stale Bookkeeping Finding: {"repo": str(repo), "findings":
    [...], "error": None} on success (findings empty when nothing is
    stale), or {"repo": str(repo), "findings": [], "error": "<message>"}
    if the check raises -- the exception never propagates out of this
    function.
    """
    try:
        rows = dashboard.scan(repo / "docs" / "specs")
        stale_rows = [row for row in rows if row.get("stage") == "stale-bookkeeping"]
        findings: list[dict[str, Any]] = []
        for row in stale_rows:
            fmt = "openspec" if row.get("format") == "openspec" else "devkit"
            row_tasks = row.get("tasks") if fmt == "openspec" else None
            for task_id in row.get("stale_task_ids", []):
                files: list[Any] = []
                if fmt == "openspec" and isinstance(row_tasks, list):
                    for task in row_tasks:
                        if task.get("id") == task_id and task.get("files"):
                            files = task["files"]
                            break
                findings.append(
                    {
                        "format": fmt,
                        "spec_id": row["id"],
                        "task_id": task_id,
                        "next_action": row.get("next_action"),
                        "files": files,
                    }
                )
        return {"repo": str(repo), "findings": findings, "error": None}
    except Exception as exc:  # noqa: BLE001 - per-repo isolation is the point
        return {"repo": str(repo), "findings": [], "error": str(exc)}

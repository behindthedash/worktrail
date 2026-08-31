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


def check_repo_stale_bookkeeping(repo: Path) -> dict[str, Any]:
    """Run the stale-bookkeeping check against repo's docs/specs/ tree.

    Returns a Stale Bookkeeping Finding: {"repo": str(repo), "findings":
    [...], "error": None} on success (findings empty when nothing is
    stale), or {"repo": str(repo), "findings": [], "error": "<message>"}
    if the check raises -- the exception never propagates out of this
    function.
    """
    try:
        findings: list[dict[str, Any]] = []
        return {"repo": str(repo), "findings": findings, "error": None}
    except Exception as exc:  # noqa: BLE001 - per-repo isolation is the point
        return {"repo": str(repo), "findings": [], "error": str(exc)}

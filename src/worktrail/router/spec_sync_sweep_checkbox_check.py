#!/usr/bin/env python3
"""Per-repo checkbox-drift check with per-repo error isolation.

Wraps `worktrail.taskformats.devkit.checkbox_audit.audit_repo()` into a
structured Checkbox Drift Finding:

    {"repo": str, "findings": [{"path": <task file path relative to repo>,
                                 "unchecked_count": int,
                                 "total_count": int,
                                 "sections": [str, ...]},
                                ...],
     "error": <str> | None}

The entire per-repo check runs inside a try/except: a crash checking one
repo's checkbox drift (e.g. an unreadable docs/specs/ tree) is captured as
that repo's `error` field instead of propagating, so a caller sweeping many
repos can continue checking every other repo's checkbox-drift check, and
that same repo's independent spec-sync-drift check, unconditionally. This
function is read-only -- it only calls the existing audit_repo() and never
writes to the checked repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..taskformats.devkit.checkbox_audit import audit_repo


def check_repo_checkbox_drift(repo: Path) -> dict[str, Any]:
    """Run audit_repo() against repo's docs/specs/**/tasks/TASK-*.md tree.

    Returns a Checkbox Drift Finding: {"repo": str(repo), "findings": [...],
    "error": None} on success (findings empty when no drift, or when the
    repo has no matching task files), or {"repo": str(repo), "findings": [],
    "error": "<message>"} if the check raises -- the exception never
    propagates out of this function.
    """
    try:
        findings = [
            {
                "path": str(hit.path.relative_to(repo)),
                "unchecked_count": hit.unchecked_count,
                "total_count": hit.total_count,
                "sections": hit.sections,
            }
            for hit in audit_repo(repo)
        ]
        return {"repo": str(repo), "findings": findings, "error": None}
    except Exception as exc:  # noqa: BLE001 - per-repo isolation is the point
        return {"repo": str(repo), "findings": [], "error": str(exc)}

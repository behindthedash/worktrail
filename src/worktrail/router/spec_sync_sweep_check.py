#!/usr/bin/env python3
"""Per-repo spec-sync drift check with per-repo error isolation.

Wraps the existing, unmodified check_spec_sync.check_spec() into a
structured Repo Drift Finding:

    {"repo": str, "findings": [{"spec": <spec dir name>,
                                 "reason": <check_spec() failure message>},
                                ...],
     "error": <str> | None}

Every numbered spec directory under repo/docs/specs/ is checked -- the same
iteration convention as pre_pr_gate.py's spec_sync_drift()
(`re.match(r"^\\d+-", d.name)`) -- but each check_spec() failure message is
tagged with the spec directory name it concerns instead of being flattened
into a plain string list.

The entire walk runs inside a try/except: a crash checking one repo (e.g. an
unreadable spec directory) is captured as that repo's `error` field instead
of propagating, so a caller sweeping many repos can continue checking every
other repo unconditionally. This function is read-only -- it only calls the
existing check_spec() and never writes to the checked repo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .check_spec_sync import check_spec


def check_repo_drift(repo: Path) -> dict[str, Any]:
    """Run check_spec() across every numbered spec dir under repo/docs/specs/.

    Returns a Repo Drift Finding: {"repo": str(repo), "findings": [...],
    "error": None} on success (findings empty when no drift, or when
    docs/specs/ has no numbered spec subdirectories), or {"repo": str(repo),
    "findings": [], "error": "<message>"} if the check raises -- the
    exception never propagates out of this function.
    """
    try:
        findings: list[dict[str, str]] = []
        specs_root = repo / "docs" / "specs"
        if specs_root.is_dir():
            spec_dirs = sorted(
                d
                for d in specs_root.iterdir()
                if d.is_dir() and re.match(r"^\d+-", d.name)
            )
            for spec_dir in spec_dirs:
                for reason in check_spec(spec_dir, repo=repo):
                    findings.append({"spec": spec_dir.name, "reason": reason})
        return {"repo": str(repo), "findings": findings, "error": None}
    except Exception as exc:  # noqa: BLE001 - per-repo isolation is the point
        return {"repo": str(repo), "findings": [], "error": str(exc)}

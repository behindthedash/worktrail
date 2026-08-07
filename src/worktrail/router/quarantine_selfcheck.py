#!/usr/bin/env python3
"""quarantine_selfcheck.py — cross-repo QUARANTINED-group detector.

The orchestrator's `integrate.py` marks a group `QUARANTINED` in its run
journal (`<repo>-worktrees/run-<spec_id>.json`) when it cannot be safely
integrated on its own (e.g. it depends on another quarantined group, or its
merge attempt failed) and needs a human to look at it. That journal state was
previously only visible by opening the JSON file directly -- nothing swept
for it. This is a passive detector, not a gate: it flags signals for a
human/agent to judge, matching `policy_selfcheck.py`'s and
`automerge_selfcheck.py`'s own posture.

No network calls (local file inspection only, matching
`check_repo_freshness.py`'s default posture).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


def _iter_journal_files(worktrees_dir: Path):
    for path in sorted(worktrees_dir.glob("run-*.json")):
        if not path.is_file():
            continue
        if path.name.endswith(".status.json"):
            continue
        yield path


def _spec_id_from_journal_path(path: Path) -> str:
    return path.stem[len("run-"):] if path.stem.startswith("run-") else path.stem


def _age_days(path: Path) -> float:
    return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)


def check_repo(repo: Path) -> Dict[str, Any]:
    """Findings for every run journal in one repo. Empty `findings` = clean."""
    repo = Path(repo)
    result: Dict[str, Any] = {"repo": repo.name, "path": str(repo), "findings": []}
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    if not worktrees_dir.is_dir():
        return result
    for journal_path in _iter_journal_files(worktrees_dir):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(journal, dict):
            continue
        groups = journal.get("groups")
        if not isinstance(groups, dict):
            continue
        spec_id = _spec_id_from_journal_path(journal_path)
        age_days = _age_days(journal_path)
        for group_name, group in groups.items():
            if not isinstance(group, dict):
                continue
            if group.get("state") != "QUARANTINED":
                continue
            result["findings"].append(
                {
                    "spec_id": spec_id,
                    "group": group_name,
                    "pr_url": group.get("pr_url", ""),
                    "age_days": age_days,
                }
            )
    return result

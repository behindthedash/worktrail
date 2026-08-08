#!/usr/bin/env python3
"""audit_postmerge.py — fleet-wide post-merge reconciliation audit.

`verify.py`'s `classify_checks()` only ever runs while a PR's own orchestrator
run is still live, polling that one PR's `statusCheckRollup` until it goes
green or the run gives up. Nothing re-checks a PR after it merges — a check
that starts failing *after* merge (a flaky re-run, a required check added to
the branch ruleset post-merge, a rollup that was still pending when the
orchestrator's own poll budget ran out and it merged anyway) is never
flagged again. This module closes that gap with a periodic sweep across
every worktrail-managed repo's recently-merged PRs, re-classifying each PR's
current `statusCheckRollup` with the exact same `classify_checks()` every
in-flight verify run already uses, so a merged-but-red PR surfaces the same
way a not-yet-merged one would.

Repo discovery reuses `reconcile_pr_labels.py`'s `discover_managed_repos()`
rather than re-implementing the "which repos has this machine opted into
GO/worktrail" scan a second time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .reconcile_pr_labels import discover_managed_repos
from ..orchestrator.verify import classify_checks

__all__ = [
    "discover_managed_repos", "classify_checks",
    "DEFAULT_STATE_DIR", "DEFAULT_LOOKBACK_DAYS",
    "resolve_state_dir", "first_run_lookback", "load_state",
    "read_marker", "write_marker", "effective_since",
]

DEFAULT_STATE_DIR = "~/.go/postmerge-audit-state"

# Bounded first-run window: how far back a repo with no (or a corrupt)
# marker looks for merged PRs, so a brand-new/never-swept repo doesn't pull
# in a repo's entire merge history on its first sweep.
DEFAULT_LOOKBACK_DAYS = 7


def resolve_state_dir(cli_arg: Optional[str] = None) -> Path:
    """`--state-dir` > `$GO_POSTMERGE_AUDIT_STATE` > `~/.go/postmerge-audit-state`."""
    raw = cli_arg or os.environ.get("GO_POSTMERGE_AUDIT_STATE") or DEFAULT_STATE_DIR
    return Path(raw).expanduser()


def first_run_lookback(lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                        now: Optional[datetime] = None) -> str:
    """ISO8601 timestamp `lookback_days` before `now` (UTC) -- the window a
    repo with no persisted marker falls back to."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=lookback_days)).isoformat()


def _state_path(repo_name: str, state_dir: Path) -> Path:
    return state_dir / f"{repo_name}.json"


def load_state(repo_name: str, state_dir: Path) -> Dict[str, Any]:
    """The repo's full persisted state dict. `{}` when the file is missing,
    unreadable, or not valid JSON -- a corrupt marker must degrade to the
    first-run lookback window rather than raise."""
    path = _state_path(repo_name, state_dir)
    try:
        raw = path.read_text()
    except OSError:
        return {}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def read_marker(repo_name: str, state_dir: Path) -> Optional[str]:
    """The repo's persisted `last_swept_at` ISO8601 string, or `None` if
    absent/corrupt (missing file, invalid JSON, wrong type, or a value that
    doesn't parse as ISO8601)."""
    value = load_state(repo_name, state_dir).get("last_swept_at")
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def write_marker(repo_name: str, state_dir: Path, last_swept_at: str) -> None:
    """Persist `last_swept_at`, preserving any other keys already in the
    repo's state file (e.g. flagged-PR records written by a later sweep
    step) rather than clobbering the whole file."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(repo_name, state_dir)
    state["last_swept_at"] = last_swept_at
    _state_path(repo_name, state_dir).write_text(json.dumps(state, indent=2))


def effective_since(repo_name: str, state_dir: Path,
                     lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> str:
    """The ISO8601 timestamp to sweep from: the repo's marker if present and
    valid, else the bounded first-run lookback window."""
    return read_marker(repo_name, state_dir) or first_run_lookback(lookback_days)

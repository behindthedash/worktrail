"""Cross-sweep detection of remediation-table findings that keep recurring
despite their action reporting apparent success.

Persisted state is machine-local, advisory, and follows the same
atomic-write/lock pattern as `agent_capacity.py` (see design.md D4): a JSON
file under `worktrail_home()`, written via `tempfile.mkstemp` + `os.replace`,
guarded by `agent_capacity.write_lock` for the read-modify-write sequence.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..orchestrator.agent_capacity import write_lock
from ..shared.homedir import env_setting, worktrail_home

DEFAULT_RETENTION = timedelta(days=30)


def history_path() -> Path:
    override = env_setting("WORKTRAIL_STUCK_REMEDIATION_HISTORY")
    if override:
        return Path(override).expanduser()
    return worktrail_home() / "remediation-history.json"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"version": 1, "identities": {}}
    if not isinstance(value, dict) or not isinstance(value.get("identities"), dict):
        return {"version": 1, "identities": {}}
    return value


def save(value: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def record_and_detect(
    history: dict[str, Any],
    resumed: dict[str, list[dict[str, Any]]],
    now: datetime,
    threshold: int,
    retention: timedelta,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extend the streak for every identity re-affirmed this sweep and drop
    every other identity, returning the identities that reached `threshold`.

    `resumed` is the pre+post merged dict `drain()` already builds -- one
    entry per remediation-table key, each holding the findings whose action
    returned normally (apparent success) this sweep. An identity absent from
    `resumed` this sweep -- either genuinely fixed, or its action raised --
    does not carry its prior streak forward: only identities re-affirmed this
    sweep survive into the returned history, which already satisfies the
    retention requirement (a miss prunes immediately, which is a stronger
    guarantee than "pruned after `retention`").
    """
    prior = history.get("identities", {}) if isinstance(history, dict) else {}
    updated: dict[str, Any] = {}
    stuck: list[dict[str, Any]] = []
    for key, findings in resumed.items():
        for finding in findings:
            repo_name, spec_id = finding.get("repo"), finding.get("spec_id")
            if not repo_name or not spec_id:
                continue
            ident = f"{key}::{repo_name}::{spec_id}"
            streak = prior.get(ident, {}).get("streak", 0) + 1
            updated[ident] = {"streak": streak, "last_seen": now.isoformat()}
            if streak >= threshold:
                stuck.append(
                    {
                        "key": key,
                        "repo_name": repo_name,
                        "spec_id": spec_id,
                        "streak": streak,
                    }
                )
    return {"version": 1, "identities": updated}, stuck


def sweep_and_record(
    resumed: dict[str, list[dict[str, Any]]],
    path: Path,
    threshold: int = 3,
    retention: timedelta = DEFAULT_RETENTION,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    with write_lock(path):
        history = load(path)
        new_history, stuck = record_and_detect(
            history, resumed, now, threshold, retention
        )
        save(new_history, path)
    return stuck

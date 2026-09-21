"""
Durable external-event materialization records.

An external relay (PullHook) may redeliver the same event more than once. The
record written here is what makes materialization idempotent: before creating a
handoff, the caller looks the event up; after creating it, the caller records
the resulting handoff ID/path against the same key.

Storage is WorkTrail-owned queue metadata, one JSON file per event under

    $WORK_QUEUE_DIR/.worktrail/external-events/<sha256(schema + "\\0" + event_id)>.json

so it survives process restart, is safe to inspect before creation (a missing
file simply reads as "not materialized"), and gives distinct keys to distinct
schemas that happen to share an event id.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from worktrail.workqueue.work_queue import base_dir

METADATA_SUBDIR = (".worktrail", "external-events")


@dataclass(frozen=True)
class ExternalEventRecord:
    """A materialized external event: which event, and what it produced."""

    schema: str
    event_id: str
    handoff_id: str
    handoff_path: str
    recorded_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "handoff_id": self.handoff_id,
            "handoff_path": self.handoff_path,
            "recorded_at": self.recorded_at,
        }


def records_dir(queue_base: Path | str | None = None) -> Path:
    """Directory holding the materialization records."""
    root = Path(queue_base).expanduser() if queue_base is not None else base_dir()
    return root.joinpath(*METADATA_SUBDIR)


def event_key(schema: str, event_id: str) -> str:
    """Stable key for a (schema, event id) pair.

    The NUL separator keeps the two fields from colliding across a boundary
    (schema "a", id "bc" must not key the same as schema "ab", id "c").
    """
    if not schema or not event_id:
        raise ValueError("schema and event_id are both required")
    digest = hashlib.sha256(f"{schema}\0{event_id}".encode())
    return digest.hexdigest()


def record_path(
    schema: str, event_id: str, queue_base: Path | str | None = None
) -> Path:
    return records_dir(queue_base) / f"{event_key(schema, event_id)}.json"


def lookup(
    schema: str, event_id: str, queue_base: Path | str | None = None
) -> ExternalEventRecord | None:
    """Return the record for an already-materialized event, else None.

    Safe to call before creation: an absent (or unreadable) record reads as
    "not materialized yet".
    """
    path = record_path(schema, event_id, queue_base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ExternalEventRecord(
            schema=data["schema"],
            event_id=data["event_id"],
            handoff_id=data["handoff_id"],
            handoff_path=data["handoff_path"],
            recorded_at=data["recorded_at"],
        )
    except KeyError:
        return None


def record(
    schema: str,
    event_id: str,
    handoff_id: str,
    handoff_path: str | Path,
    queue_base: Path | str | None = None,
) -> ExternalEventRecord:
    """Durably record that `event_id` materialized as `handoff_id`.

    Idempotent: re-recording the same event returns the existing record
    unchanged, so a retry after a crash never rewrites history.
    """
    existing = lookup(schema, event_id, queue_base)
    if existing is not None:
        return existing

    entry = ExternalEventRecord(
        schema=schema,
        event_id=event_id,
        handoff_id=handoff_id,
        handoff_path=str(handoff_path),
        recorded_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    path = record_path(schema, event_id, queue_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(entry.to_dict(), indent=2) + "\n")
    return entry


def _atomic_write(path: Path, text: str) -> None:
    """Write via a same-directory temp file + fsync + rename, so a crash
    mid-write can never leave a half-written record behind."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

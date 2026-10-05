"""
Exactly-once materialization of external handoff events.

One event travels this sequence, and the order is the contract:

    claim -> validate -> dedupe -> create_handoff() -> record marker
          -> git persist -> ack

Each step is only allowed to be undone by a *safe* redelivery, so the pipeline
is built around what a crash between any two steps leaves behind:

* **before create** -- nothing was written and nothing was acked; the relay
  redelivers and the run starts over.
* **after create, before marker** -- the marker that normally makes redelivery
  cheap does not exist yet, so the dedupe step falls back to
  `find_materialized_brief()`, which scans the queue for the provenance line the
  envelope adapter writes into every external brief. The retry adopts that brief
  and records the missing marker instead of creating a second one.
* **after marker, before ack** -- the marker is found, the original handoff is
  returned, no file is created, and the ack is retried.
* **after ack, response lost** -- the relay redelivers; the marker short-circuits
  it exactly like the previous case.

Acknowledgement is *last* and conditional: when git persistence is required and
the push fails, the item stays unacked and the run reports failure, so the relay
redelivers and the retry completes persistence idempotently (the brief and marker
are already there; only the push is re-attempted).

`dry_run=True` peeks instead of claiming, and stops after validation: it never
creates a brief, never records a marker, and never acks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worktrail.workqueue import create_handoff as create_handoff_mod
from worktrail.workqueue import external_events, queue_git_persist
from worktrail.workqueue.pullhook_client import PullHookItem
from worktrail.workqueue.pullhook_envelope import (
    EnvelopeError,
    map_envelope,
    validate_envelope,
)
from worktrail.workqueue.work_queue import base_dir

#: Sub-directories of the queue root that may hold an already-created brief.
_BRIEF_DIRS = ("queue", "picked", "done", "archive")

__all__ = [
    "IngressResult",
    "exit_code",
    "find_materialized_brief",
    "ingest",
    "ingest_once",
    "provenance_line",
]


def provenance_line(schema: str, event_id: str) -> str:
    """The artifacts line `pullhook_envelope` writes for an external event.

    This is the on-disk evidence that an event was materialized even when the
    marker write never happened.
    """
    return f"- External event: {schema} {event_id}"


@dataclass(frozen=True)
class IngressResult:
    """Outcome for one relay item.

    ``status`` is one of ``created``, ``duplicate``, ``adopted`` (an unmarked
    brief from an interrupted earlier run), ``rejected`` (the envelope is not
    materializable), ``persist-failed``, or ``dry-run``.
    """

    status: str
    event_id: str | None = None
    delivery_id: str | None = None
    handoff_id: str | None = None
    handoff_path: str | None = None
    acked: bool = False
    created: bool = False
    persist_status: str | None = None
    persist_reason: str | None = None
    error: str | None = None
    envelope: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        """True when this item needs no operator attention."""
        return self.status in ("created", "duplicate", "adopted", "dry-run")

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "event_id": self.event_id,
            "delivery_id": self.delivery_id,
            "handoff_id": self.handoff_id,
            "handoff_path": self.handoff_path,
            "acked": self.acked,
            "created": self.created,
            "persist_status": self.persist_status,
            "persist_reason": self.persist_reason,
            "error": self.error,
        }
        return {key: value for key, value in data.items() if value is not None}


def find_materialized_brief(
    schema: str, event_id: str, queue_base: Path | str | None = None
) -> Path | None:
    """Locate a brief already created for this event, by its provenance line.

    Only used when the marker is missing: it closes the crash window between
    `create_handoff()` and the marker write, where the event is still unacked and
    the relay will redeliver it.
    """
    root = Path(queue_base).expanduser() if queue_base is not None else base_dir()
    needle = provenance_line(schema, event_id)
    for name in _BRIEF_DIRS:
        directory = root / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if needle in text:
                return path
    return None


CreateFn = Callable[..., dict[str, Any]]
RecordFn = Callable[..., external_events.ExternalEventRecord]
PersistFn = Callable[..., queue_git_persist.PersistResult]


def ingest_once(
    client: Any,
    *,
    queue_base: Path | str | None = None,
    dry_run: bool = False,
    require_git: bool | None = None,
    create_fn: CreateFn | None = None,
    record_fn: RecordFn | None = None,
    persist_fn: PersistFn | None = None,
) -> IngressResult | None:
    """Process at most one relay item. Returns None when the channel is empty."""
    items = client.peek(1) if dry_run else client.claim(1)
    if not items:
        return None
    return _process(
        items[0],
        client=client,
        queue_base=queue_base,
        dry_run=dry_run,
        require_git=require_git,
        create_fn=create_fn or create_handoff_mod.create_handoff,
        record_fn=record_fn or external_events.record,
        persist_fn=persist_fn or queue_git_persist.persist_external_brief,
    )


def ingest(
    client: Any,
    *,
    max_items: int = 1,
    queue_base: Path | str | None = None,
    dry_run: bool = False,
    require_git: bool | None = None,
    create_fn: CreateFn | None = None,
    record_fn: RecordFn | None = None,
    persist_fn: PersistFn | None = None,
) -> list[IngressResult]:
    """Process up to `max_items` items, one claim at a time.

    Claiming one at a time keeps the unacked window to a single item: a crash
    mid-run leaves at most one claimed-but-unprocessed delivery behind, and the
    loop stops at the first item that needs operator attention rather than
    burning the rest of the batch against the same broken precondition.
    """
    if max_items < 1:
        raise ValueError("max_items must be at least 1")
    results: list[IngressResult] = []
    for _ in range(max_items):
        result = ingest_once(
            client,
            queue_base=queue_base,
            dry_run=dry_run,
            require_git=require_git,
            create_fn=create_fn,
            record_fn=record_fn,
            persist_fn=persist_fn,
        )
        if result is None:
            break
        results.append(result)
        if not result.ok or dry_run:
            break
    return results


def _process(
    item: PullHookItem,
    *,
    client: Any,
    queue_base: Path | str | None,
    dry_run: bool,
    require_git: bool | None,
    create_fn: CreateFn,
    record_fn: RecordFn,
    persist_fn: PersistFn,
) -> IngressResult:
    # 1. validate -- before any queue mutation.
    try:
        valid = validate_envelope(item.payload)
        kwargs = map_envelope(item.payload)
    except EnvelopeError as exc:
        # Left unacked on purpose: a payload WorkTrail cannot materialize is a
        # producer-side defect, and dropping it silently would erase the only
        # copy of the evidence.
        return IngressResult(
            status="rejected",
            event_id=item.event_id,
            delivery_id=item.delivery_id,
            error=str(exc),
        )

    schema = valid["schema"]
    event_id = valid["event_id"]

    if dry_run:
        return IngressResult(
            status="dry-run",
            event_id=event_id,
            delivery_id=item.delivery_id,
            envelope=valid,
        )

    # 2. dedupe -- marker first, then the provenance scan that covers a crash
    #    between create and marker.
    existing = external_events.lookup(schema, event_id, queue_base)
    status = "created"
    if existing is not None:
        handoff_id = existing.handoff_id
        handoff_path = Path(existing.handoff_path)
        status = "duplicate"
    else:
        orphan = find_materialized_brief(schema, event_id, queue_base)
        if orphan is not None:
            handoff_id, handoff_path = orphan.stem, orphan
            status = "adopted"
        else:
            # 3. create through the canonical writer.
            created = create_fn(queue_base=_queue_base_arg(queue_base), **kwargs)
            handoff_id = created["id"]
            handoff_path = Path(created["path"])

        # 4. record the marker (idempotent).
        record_fn(schema, event_id, handoff_id, handoff_path, queue_base)

    # 5. git persist -- the acknowledgement precondition.
    persist = persist_fn(
        _persist_paths(handoff_path, schema, event_id, queue_base),
        event_id=event_id,
        queue_base=_queue_base_arg(queue_base),
        require_git=require_git,
    )
    if not persist.ok:
        return IngressResult(
            status="persist-failed",
            event_id=event_id,
            delivery_id=item.delivery_id,
            handoff_id=handoff_id,
            handoff_path=str(handoff_path),
            created=status == "created",
            persist_status=persist.status,
            persist_reason=persist.reason,
            error=persist.error,
        )

    # 6. ack -- last, and only now.
    acked = False
    if item.delivery_id:
        client.ack(item.delivery_id)
        acked = True

    return IngressResult(
        status=status,
        event_id=event_id,
        delivery_id=item.delivery_id,
        handoff_id=handoff_id,
        handoff_path=str(handoff_path),
        acked=acked,
        created=status == "created",
        persist_status=persist.status,
        persist_reason=persist.reason,
    )


def _queue_base_arg(queue_base: Path | str | None) -> Path | None:
    return Path(queue_base).expanduser() if queue_base is not None else None


def _persist_paths(
    handoff_path: Path,
    schema: str,
    event_id: str,
    queue_base: Path | str | None,
) -> Sequence[Path]:
    """The brief plus the marker that records it -- nothing else."""
    return [
        handoff_path,
        external_events.record_path(schema, event_id, queue_base),
    ]


def exit_code(results: list[IngressResult]) -> int:
    """0 when every item is settled, 1 when any needs a retry or attention."""
    return 0 if all(result.ok for result in results) else 1

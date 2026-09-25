"""Exactly-once materialization: crash boundaries, redelivery, persistence, dry run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from worktrail.workqueue import external_events
from worktrail.workqueue.pullhook_client import PullHookItem
from worktrail.workqueue.pullhook_ingress import (
    IngressResult,
    exit_code,
    find_materialized_brief,
    ingest,
    ingest_once,
    provenance_line,
)
from worktrail.workqueue.queue_git_persist import PersistResult

SCHEMA = "datalena.worktrail-handoff.v1"


def envelope(event_id: str = "evt-1") -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "event_id": event_id,
        "dedupe_key": event_id,
        "captured_by": "datalena:review",
        "target": {
            "remote": "behindthedash/datalena",
            "base_branch": "dev",
        },
        "handoff": {
            "focus": "Harden the relay ack ordering in the ingress path",
            "context": "Found while reviewing the merged relay change.",
            "suggested_approach": "Ack only after the push lands.",
            "implementation_intent": "requested",
        },
        "source": {
            "repository": "behindthedash/datalena",
            "pr_number": 42,
            "merge_sha": "merge-sha-42",
            "run_id": "run-42",
            "brief_path": "docs/specs/usability-briefs/example.md",
        },
        "finding": {
            "identity_key": "F-17 ack precedes durability",
            "persona": "operator",
            "route": "/workqueue",
            "journey": "review handoff",
            "step": "acknowledge event",
            "severity": "medium",
            "heuristic": "visibility",
            "evidence_refs": ["logs/relay-2026-09-20.txt"],
            "judge_model": "test-model",
        },
    }


class FakeClient:
    """A relay holding a fixed set of items, recording claims/peeks/acks."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.items = [
            PullHookItem(
                delivery_id=f"dlv-{index}",
                event_id=payload.get("event_id", f"evt-{index}"),
                payload=payload,
            )
            for index, payload in enumerate(payloads, start=1)
        ]
        self.acked: list[str] = []
        self.claims = 0
        self.peeks = 0

    def claim(self, max_items: int = 1) -> list[PullHookItem]:
        self.claims += 1
        return self.items[:max_items]

    def peek(self, limit: int = 1) -> list[PullHookItem]:
        self.peeks += 1
        return self.items[:limit]

    def ack(self, delivery_id: str) -> None:
        self.acked.append(delivery_id)
        self.items = [i for i in self.items if i.delivery_id != delivery_id]


@pytest.fixture
def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated queue root, with git persistence off unless a test enables it."""
    root = tmp_path / "work-queue"
    (root / "queue").mkdir(parents=True)
    monkeypatch.setenv("WORK_QUEUE_DIR", str(root))
    monkeypatch.delenv("WORK_QUEUE_GIT_SYNC", raising=False)
    monkeypatch.delenv("WORK_QUEUE_GIT_REQUIRED", raising=False)
    return root


def briefs(queue_base: Path) -> list[Path]:
    return sorted((queue_base / "queue").glob("*.md"))


# -- the happy path ----------------------------------------------------


def test_claim_to_ack_creates_one_brief_marker_and_acks(queue: Path):
    client = FakeClient([envelope()])

    result = ingest_once(client, queue_base=queue)

    assert isinstance(result, IngressResult)
    assert result.status == "created"
    assert result.created is True
    assert result.acked is True
    assert client.acked == ["dlv-1"]
    assert len(briefs(queue)) == 1

    marker = external_events.lookup(SCHEMA, "evt-1", queue)
    assert marker is not None
    assert marker.handoff_id == result.handoff_id
    assert exit_code([result]) == 0


def test_created_brief_retains_external_provenance(queue: Path):
    client = FakeClient([envelope()])
    result = ingest_once(client, queue_base=queue)

    text = Path(result.handoff_path).read_text(encoding="utf-8")
    assert provenance_line(SCHEMA, "evt-1") in text
    assert "behindthedash/datalena" in text
    assert "captured-by: datalena:review" in text


def test_empty_channel_returns_none_and_acks_nothing(queue: Path):
    client = FakeClient([])
    assert ingest_once(client, queue_base=queue) is None
    assert client.acked == []


# -- crash / retry boundaries ------------------------------------------


def test_crash_before_create_leaves_nothing_and_retry_creates_once(queue: Path):
    client = FakeClient([envelope()])
    boom = RuntimeError("process died before the writer ran")

    def exploding_create(**_kwargs):
        raise boom

    with pytest.raises(RuntimeError):
        ingest_once(client, queue_base=queue, create_fn=exploding_create)

    assert briefs(queue) == []
    assert external_events.lookup(SCHEMA, "evt-1", queue) is None
    assert client.acked == []

    # The relay redelivers; the retry materializes exactly one brief.
    retry = ingest_once(client, queue_base=queue)
    assert retry.status == "created"
    assert len(briefs(queue)) == 1
    assert client.acked == ["dlv-1"]


def test_crash_after_create_before_marker_is_adopted_not_duplicated(queue: Path):
    client = FakeClient([envelope()])

    def exploding_record(*_args, **_kwargs):
        raise RuntimeError("process died before the marker landed")

    with pytest.raises(RuntimeError):
        ingest_once(client, queue_base=queue, record_fn=exploding_record)

    first = briefs(queue)
    assert len(first) == 1
    assert external_events.lookup(SCHEMA, "evt-1", queue) is None
    assert client.acked == []

    # The provenance scan finds the orphaned brief instead of writing a second one.
    assert find_materialized_brief(SCHEMA, "evt-1", queue) == first[0]

    retry = ingest_once(client, queue_base=queue)
    assert retry.status == "adopted"
    assert retry.created is False
    assert retry.handoff_path == str(first[0])
    assert briefs(queue) == first
    assert external_events.lookup(SCHEMA, "evt-1", queue).handoff_id == first[0].stem
    assert client.acked == ["dlv-1"]


def test_crash_after_marker_before_ack_retries_the_ack_only(queue: Path):
    client = FakeClient([envelope()])

    def exploding_persist(*_args, **_kwargs):
        raise RuntimeError("process died after the marker, before the ack")

    with pytest.raises(RuntimeError):
        ingest_once(client, queue_base=queue, persist_fn=exploding_persist)

    first = briefs(queue)
    assert len(first) == 1
    assert external_events.lookup(SCHEMA, "evt-1", queue) is not None
    assert client.acked == []

    retry = ingest_once(client, queue_base=queue)
    assert retry.status == "duplicate"
    assert retry.created is False
    assert retry.handoff_path == str(first[0])
    assert briefs(queue) == first
    assert client.acked == ["dlv-1"]


def test_lost_ack_response_redelivery_creates_no_second_brief(queue: Path):
    """The ack landed but its response was lost, so the relay redelivers."""
    client = FakeClient([envelope()])
    first = ingest_once(client, queue_base=queue)
    assert first.acked is True

    # Simulate the relay re-offering the same delivery despite the ack.
    client.items = [
        PullHookItem(delivery_id="dlv-1", event_id="evt-1", payload=envelope())
    ]

    again = ingest_once(client, queue_base=queue)
    assert again.status == "duplicate"
    assert again.handoff_id == first.handoff_id
    assert len(briefs(queue)) == 1
    assert client.acked == ["dlv-1", "dlv-1"]


# -- redelivery of a materialized event --------------------------------


def test_redelivery_returns_the_original_handoff_and_creates_no_file(queue: Path):
    client = FakeClient([envelope()])
    first = ingest_once(client, queue_base=queue)
    before = briefs(queue)

    client.items = [
        PullHookItem(delivery_id="dlv-9", event_id="evt-1", payload=envelope())
    ]

    def must_not_create(**_kwargs):
        raise AssertionError("create_handoff() must not run for a marked event")

    second = ingest_once(client, queue_base=queue, create_fn=must_not_create)
    assert second.status == "duplicate"
    assert second.created is False
    assert second.handoff_id == first.handoff_id
    assert second.handoff_path == first.handoff_path
    assert briefs(queue) == before
    assert second.acked is True
    assert exit_code([second]) == 0


def test_distinct_events_each_materialize(queue: Path):
    client = FakeClient([envelope("evt-1"), envelope("evt-2")])

    results = ingest(client, max_items=2, queue_base=queue)

    assert [r.status for r in results] == ["created", "created"]
    assert len({r.handoff_id for r in results}) == 2
    assert len(briefs(queue)) == 2
    assert client.acked == ["dlv-1", "dlv-2"]


# -- persistence gating the ack ----------------------------------------


def test_push_failure_returns_nonzero_and_does_not_ack(queue: Path):
    client = FakeClient([envelope()])

    def failing_persist(paths, **_kwargs):
        return PersistResult(
            status="failed",
            reason="push-failed",
            committed=True,
            paths=tuple(str(p) for p in paths),
            error="remote rejected",
        )

    result = ingest_once(client, queue_base=queue, persist_fn=failing_persist)

    assert result.status == "persist-failed"
    assert result.persist_reason == "push-failed"
    assert result.acked is False
    assert client.acked == []
    assert exit_code([result]) == 1
    # The brief and its marker are durable locally, so the retry is idempotent.
    assert len(briefs(queue)) == 1
    assert external_events.lookup(SCHEMA, "evt-1", queue) is not None


def test_retry_after_push_failure_completes_persistence_idempotently(queue: Path):
    client = FakeClient([envelope()])
    attempts: list[tuple[str, ...]] = []

    def flaky_persist(paths, **_kwargs):
        rel = tuple(str(p) for p in paths)
        attempts.append(rel)
        if len(attempts) == 1:
            return PersistResult(
                status="failed", reason="push-failed", committed=True, paths=rel
            )
        return PersistResult(status="pushed", pushed=True, paths=rel)

    first = ingest_once(client, queue_base=queue, persist_fn=flaky_persist)
    assert first.status == "persist-failed"

    second = ingest_once(client, queue_base=queue, persist_fn=flaky_persist)
    assert second.status == "duplicate"
    assert second.acked is True
    assert second.handoff_id == first.handoff_id
    assert len(briefs(queue)) == 1
    assert client.acked == ["dlv-1"]
    # Both attempts persisted the same brief + marker pair.
    assert attempts[0] == attempts[1]


def test_persist_receives_only_the_brief_and_its_marker(queue: Path):
    client = FakeClient([envelope()])
    seen: list[list[Path]] = []

    def capturing_persist(paths, **_kwargs):
        seen.append([Path(p) for p in paths])
        return PersistResult(status="pushed", pushed=True)

    result = ingest_once(client, queue_base=queue, persist_fn=capturing_persist)

    assert seen == [
        [
            Path(result.handoff_path),
            external_events.record_path(SCHEMA, "evt-1", queue),
        ]
    ]


def test_skipped_persistence_still_acks(queue: Path):
    client = FakeClient([envelope()])

    def skipping_persist(_paths, **_kwargs):
        return PersistResult(status="skipped", reason="sync-disabled")

    result = ingest_once(client, queue_base=queue, persist_fn=skipping_persist)

    assert result.status == "created"
    assert result.acked is True
    assert exit_code([result]) == 0


def test_batch_stops_at_the_first_item_needing_attention(queue: Path):
    client = FakeClient([envelope("evt-1"), envelope("evt-2")])

    def failing_persist(_paths, **_kwargs):
        return PersistResult(status="failed", reason="push-failed")

    results = ingest(client, max_items=2, queue_base=queue, persist_fn=failing_persist)

    assert [r.status for r in results] == ["persist-failed"]
    assert client.acked == []
    assert exit_code(results) == 1


# -- dry run -----------------------------------------------------------


def test_dry_run_peeks_and_creates_and_acks_nothing(queue: Path):
    client = FakeClient([envelope()])

    result = ingest_once(client, queue_base=queue, dry_run=True)

    assert result.status == "dry-run"
    assert result.event_id == "evt-1"
    assert result.envelope["handoff"]["focus"].startswith("Harden the relay")
    assert client.peeks == 1
    assert client.claims == 0
    assert client.acked == []
    assert briefs(queue) == []
    assert external_events.lookup(SCHEMA, "evt-1", queue) is None


def test_dry_run_inspects_only_one_envelope(queue: Path):
    client = FakeClient([envelope("evt-1"), envelope("evt-2")])

    results = ingest(client, max_items=2, queue_base=queue, dry_run=True)

    assert [r.status for r in results] == ["dry-run"]
    assert [r.event_id for r in results] == ["evt-1"]
    assert briefs(queue) == []


# -- rejection ---------------------------------------------------------


def test_unsupported_schema_is_rejected_unacked_and_nonzero(queue: Path):
    bad = envelope()
    bad["schema"] = "datalena.worktrail-handoff.v2"
    client = FakeClient([bad])

    result = ingest_once(client, queue_base=queue)

    assert result.status == "rejected"
    assert "unsupported envelope schema" in result.error
    assert client.acked == []
    assert briefs(queue) == []
    assert exit_code([result]) == 1


def test_missing_required_field_is_rejected_before_any_queue_mutation(queue: Path):
    bad = envelope()
    del bad["source"]["merge_sha"]
    client = FakeClient([bad])

    result = ingest_once(client, queue_base=queue)

    assert result.status == "rejected"
    assert "source.merge_sha" in result.error
    assert briefs(queue) == []
    assert external_events.lookup(SCHEMA, "evt-1", queue) is None
    assert client.acked == []


def test_max_items_must_be_positive(queue: Path):
    with pytest.raises(ValueError):
        ingest(FakeClient([]), max_items=0, queue_base=queue)

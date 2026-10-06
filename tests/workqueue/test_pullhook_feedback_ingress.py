"""GGB feedback materialization through the normal ingress transaction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktrail.workqueue import external_events
from worktrail.workqueue.pullhook_client import PullHookItem
from worktrail.workqueue.pullhook_feedback_envelope import SUPPORTED_SCHEMA
from worktrail.workqueue.pullhook_ingress import ingest_once
from worktrail.workqueue.queue_git_persist import PersistResult

from .test_pullhook_feedback_envelope import EVENT_ID, envelope


class FakeClient:
    def __init__(self, payload: dict):
        self.item = PullHookItem("delivery-1", EVENT_ID, payload)
        self.acked: list[str] = []

    def claim(self, max_items: int = 1):
        return [self.item]

    def peek(self, limit: int = 1):
        return [self.item]

    def ack(self, delivery_id: str):
        self.acked.append(delivery_id)


@pytest.fixture
def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "queue"
    (root / "queue").mkdir(parents=True)
    monkeypatch.setenv("WORK_QUEUE_DIR", str(root))
    monkeypatch.delenv("WORK_QUEUE_GIT_SYNC", raising=False)
    monkeypatch.delenv("WORK_QUEUE_GIT_REQUIRED", raising=False)
    real_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = list(args[0])
        if command[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args[0], 0, stdout="[]", stderr="")
        return real_run(*args, **kwargs)

    monkeypatch.setattr("worktrail.workqueue.create_handoff.subprocess.run", fake_run)
    return root


def test_feedback_event_creates_canonical_brief_and_marker(queue: Path):
    client = FakeClient(envelope())
    result = ingest_once(client, queue_base=queue)

    assert result.status == "created"
    assert result.acked is True
    brief = Path(result.handoff_path).read_text(encoding="utf-8")
    repo_line = next(line for line in brief.splitlines() if line.startswith("repo:"))
    assert Path(repo_line.partition(":")[2].strip()).name == "gracefully-giving-back"
    assert f"{SUPPORTED_SCHEMA} {EVENT_ID}" in brief
    assert (
        external_events.lookup(SUPPORTED_SCHEMA, EVENT_ID, queue).handoff_id
        == result.handoff_id
    )
    assert client.acked == ["delivery-1"]


def test_duplicate_delivery_returns_original_handoff(queue: Path):
    client = FakeClient(envelope())
    first = ingest_once(client, queue_base=queue)
    before = sorted((queue / "queue").glob("*.md"))

    second = ingest_once(client, queue_base=queue)

    assert second.status == "duplicate"
    assert second.handoff_id == first.handoff_id
    assert sorted((queue / "queue").glob("*.md")) == before


def test_required_push_failure_retries_without_duplicate(queue: Path):
    client = FakeClient(envelope())
    attempts = 0

    def persist(paths, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return PersistResult(status="failed", reason="push-failed")
        return PersistResult(status="pushed", pushed=True)

    first = ingest_once(client, queue_base=queue, require_git=True, persist_fn=persist)
    assert first.status == "persist-failed"
    assert client.acked == []
    count = len(list((queue / "queue").glob("*.md")))

    retry = ingest_once(client, queue_base=queue, require_git=True, persist_fn=persist)
    assert retry.status == "duplicate"
    assert retry.acked is True
    assert retry.handoff_id == first.handoff_id
    assert len(list((queue / "queue").glob("*.md"))) == count == 1


def test_unknown_source_is_rejected_without_queue_mutation(queue: Path):
    event = envelope()
    event["source"]["id"] = "untrusted-repository"
    client = FakeClient(event)

    result = ingest_once(client, queue_base=queue)

    assert result.status == "rejected"
    assert client.acked == []
    assert list((queue / "queue").glob("*.md")) == []
    assert external_events.lookup(SUPPORTED_SCHEMA, EVENT_ID, queue) is None


def test_oversized_transport_body_is_rejected_before_queue_mutation(queue: Path):
    client = FakeClient(envelope())
    client.item = PullHookItem(
        "delivery-1",
        EVENT_ID,
        client.item.payload,
        payload_size_bytes=262_145,
    )

    result = ingest_once(client, queue_base=queue)

    assert result.status == "rejected"
    assert "exceeds 262144 bytes" in result.error
    assert client.acked == []
    assert list((queue / "queue").glob("*.md")) == []


def test_feedback_dry_run_creates_and_acks_nothing(queue: Path):
    client = FakeClient(envelope())

    result = ingest_once(client, queue_base=queue, dry_run=True)

    assert result.status == "dry-run"
    assert result.event_id == EVENT_ID
    assert client.acked == []
    assert list((queue / "queue").glob("*.md")) == []
    assert external_events.lookup(SUPPORTED_SCHEMA, EVENT_ID, queue) is None

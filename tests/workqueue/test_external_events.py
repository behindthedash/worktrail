from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from worktrail.workqueue.external_events import (
    ExternalEventRecordError,
    event_key,
    lookup,
    record,
    record_path,
    records_dir,
)

SCHEMA = "datalena.worktrail-handoff.v1"


def test_lookup_before_create_returns_none(tmp_path: Path):
    assert lookup(SCHEMA, "evt-1", queue_base=tmp_path) is None
    # Inspection must not have created anything.
    assert not records_dir(tmp_path).exists()


def test_record_then_lookup_returns_handoff_identity(tmp_path: Path):
    entry = record(
        SCHEMA,
        "evt-1",
        handoff_id="20260920-120000-fix-thing",
        handoff_path=tmp_path / "queue" / "20260920-120000-fix-thing.md",
        queue_base=tmp_path,
    )
    assert entry.handoff_id == "20260920-120000-fix-thing"

    found = lookup(SCHEMA, "evt-1", queue_base=tmp_path)
    assert found is not None
    assert found.handoff_id == "20260920-120000-fix-thing"
    assert found.handoff_path.endswith("20260920-120000-fix-thing.md")
    assert found.schema == SCHEMA
    assert found.event_id == "evt-1"
    assert found.recorded_at.endswith("Z")


def test_record_is_idempotent_and_does_not_overwrite(tmp_path: Path):
    first = record(SCHEMA, "evt-1", "handoff-a", "/q/a.md", queue_base=tmp_path)
    second = record(SCHEMA, "evt-1", "handoff-b", "/q/b.md", queue_base=tmp_path)
    assert second == first
    assert lookup(SCHEMA, "evt-1", queue_base=tmp_path).handoff_id == "handoff-a"


def test_record_survives_process_restart(tmp_path: Path):
    record(SCHEMA, "evt-1", "handoff-a", "/q/a.md", queue_base=tmp_path)

    # A genuinely fresh interpreter: no in-process state can carry the answer.
    script = (
        "from worktrail.workqueue.external_events import lookup;"
        f"r = lookup({SCHEMA!r}, 'evt-1');"
        "print(r.handoff_id if r else 'MISSING')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={
            "WORK_QUEUE_DIR": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "handoff-a"


def test_distinct_schemas_with_same_event_id_are_distinct_keys(tmp_path: Path):
    other = "datalena.worktrail-handoff.v2"
    record(SCHEMA, "evt-1", "handoff-v1", "/q/v1.md", queue_base=tmp_path)

    assert lookup(other, "evt-1", queue_base=tmp_path) is None
    record(other, "evt-1", "handoff-v2", "/q/v2.md", queue_base=tmp_path)

    assert lookup(SCHEMA, "evt-1", queue_base=tmp_path).handoff_id == "handoff-v1"
    assert lookup(other, "evt-1", queue_base=tmp_path).handoff_id == "handoff-v2"
    assert event_key(SCHEMA, "evt-1") != event_key(other, "evt-1")


def test_event_key_does_not_collide_across_the_field_boundary():
    assert event_key("a", "bc") != event_key("ab", "c")


@pytest.mark.parametrize(("schema", "event_id"), [("", "evt-1"), (SCHEMA, "")])
def test_event_key_requires_both_fields(schema: str, event_id: str):
    with pytest.raises(ValueError):
        event_key(schema, event_id)


def test_record_lives_under_worktrail_owned_queue_metadata(tmp_path: Path):
    record(SCHEMA, "evt-1", "handoff-a", "/q/a.md", queue_base=tmp_path)
    path = record_path(SCHEMA, "evt-1", queue_base=tmp_path)
    assert path.parent == tmp_path / ".worktrail" / "external-events"
    assert path.exists()
    assert json.loads(path.read_text())["handoff_id"] == "handoff-a"
    # No temp files left behind by the atomic write.
    assert [p.name for p in path.parent.iterdir()] == [path.name]


@pytest.mark.parametrize(
    "body",
    ["{not json", "[]", json.dumps({"schema": SCHEMA, "event_id": "evt-1"})],
    ids=["unparseable", "not-an-object", "missing-fields"],
)
def test_corrupt_record_raises_instead_of_reading_as_absent(tmp_path: Path, body: str):
    # A present-but-unreadable marker must never degrade to "not materialized":
    # that is how a redelivered event gets a duplicate handoff.
    path = record_path(SCHEMA, "evt-1", queue_base=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    with pytest.raises(ExternalEventRecordError):
        lookup(SCHEMA, "evt-1", queue_base=tmp_path)


def test_record_refuses_to_overwrite_a_corrupt_marker(tmp_path: Path):
    path = record_path(SCHEMA, "evt-1", queue_base=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    with pytest.raises(ExternalEventRecordError):
        record(SCHEMA, "evt-1", "handoff-b", "/q/b.md", queue_base=tmp_path)
    assert path.read_text() == "{not json"


def test_unreadable_queue_base_raises_rather_than_reading_as_absent(tmp_path: Path):
    not_a_dir = tmp_path / "queue-file"
    not_a_dir.write_text("")
    with pytest.raises(ExternalEventRecordError):
        lookup(SCHEMA, "evt-1", queue_base=not_a_dir)


def test_record_verifies_its_own_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import worktrail.workqueue.external_events as mod

    # A write that silently does not land must not report success.
    monkeypatch.setattr(mod, "_atomic_write", lambda path, text: None)
    with pytest.raises(ExternalEventRecordError):
        record(SCHEMA, "evt-1", "handoff-a", "/q/a.md", queue_base=tmp_path)


@pytest.mark.parametrize(
    ("handoff_id", "handoff_path"), [("", "/q/a.md"), ("handoff-a", "")]
)
def test_record_requires_a_usable_handoff_identity(
    tmp_path: Path, handoff_id: str, handoff_path: str
):
    with pytest.raises(ValueError):
        record(SCHEMA, "evt-1", handoff_id, handoff_path, queue_base=tmp_path)


def test_queue_base_defaults_to_work_queue_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path))
    record(SCHEMA, "evt-1", "handoff-a", "/q/a.md")
    assert (tmp_path / ".worktrail" / "external-events").is_dir()
    assert lookup(SCHEMA, "evt-1").handoff_id == "handoff-a"

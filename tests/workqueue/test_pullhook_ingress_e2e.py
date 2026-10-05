"""Local HTTP-to-git integration for the PullHook ingress contract."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from worktrail.workqueue import create_handoff, external_events
from worktrail.workqueue.pullhook_client import PullHookClient
from worktrail.workqueue.pullhook_ingress import ingest_once


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_datalena_event_is_pushed_before_pullhook_ack(tmp_path, monkeypatch):
    queue = tmp_path / "work-queue"
    queue.mkdir()
    bare = tmp_path / "work-queue.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    _git(queue, "init", "-b", "main")
    _git(queue, "config", "user.name", "WorkTrail test")
    _git(queue, "config", "user.email", "worktrail-test@example.invalid")
    (queue / ".keep").write_text("", encoding="utf-8")
    _git(queue, "add", ".keep")
    _git(queue, "commit", "-m", "initial queue")
    _git(queue, "remote", "add", "origin", str(bare))
    _git(queue, "push", "--set-upstream", "origin", "main")

    monkeypatch.setenv("WORK_QUEUE_DIR", str(queue))
    monkeypatch.setenv("WORK_QUEUE_GIT_SYNC", "1")
    monkeypatch.setenv("WORK_QUEUE_GIT_REQUIRED", "1")
    monkeypatch.setattr(
        create_handoff, "_scan_durable_artifact_overlaps", lambda *_: []
    )

    event = {
        "schema": "datalena.worktrail-handoff.v1",
        "event_id": "test-event-1",
        "dedupe_key": "test-event-1",
        "source": {
            "repository": "behindthedash/datalena",
            "pr_number": 2962,
            "merge_sha": "deadbeef",
            "run_id": "actions-123",
            "brief_path": "docs/specs/usability-briefs/example.md",
        },
        "finding": {
            "identity_key": "persona:journey:step",
            "persona": "operator",
            "route": "/workspace",
            "journey": "review data",
            "step": "save view",
            "severity": "medium",
            "heuristic": "feedback",
            "evidence_refs": ["docs/evidence/example.md"],
            "judge_model": "reviewer-v1",
        },
        "handoff": {
            "focus": "Datalena usability: improve save view",
            "context": "Accepted finding persona:journey:step.",
            "suggested_approach": "Add a clearer save action.",
            "artifacts": ["https://github.com/behindthedash/datalena/pull/2962"],
        },
        "target": {"remote": "behindthedash/datalena", "base_branch": "dev"},
        "captured_by": "datalena-persona-usability",
    }
    state = {"acked": False, "remote_files_at_ack": [], "requests": []}
    bare_files = ["git", f"--git-dir={bare}", "ls-tree", "-r", "--name-only", "main"]
    missing_remote = tmp_path / "missing-remote.git"

    class RelayOpener:
        def __call__(self, request, timeout=None):
            state["requests"].append((request.method, request.full_url, timeout))
            path = urlparse(request.full_url).path
            if request.method == "POST" and path == "/api/hooks/test-channel/claim":
                payload = {
                    "ok": True,
                    "webhook": {"id": "pullhook-item-1", "body": json.dumps(event)},
                }
            elif (
                request.method == "DELETE"
                and path == "/api/hooks/test-channel/items/pullhook-item-1"
            ):
                state["acked"] = True
                state["remote_files_at_ack"] = subprocess.run(
                    bare_files, check=True, capture_output=True, text=True
                ).stdout.splitlines()
                payload = {"ok": True}
            else:
                raise AssertionError(
                    f"unexpected PullHook request: {request.method} {path}"
                )
            data = json.dumps(payload).encode("utf-8")
            return io.BytesIO(data)

    client = PullHookClient(
        "https://pullhook.example",
        "test-channel",
        "test-credential",
        opener=RelayOpener(),
    )
    _git(queue, "remote", "set-url", "origin", str(missing_remote))
    failed = ingest_once(client, queue_base=queue)
    local_briefs = sorted((queue / "queue").glob("*.md"))
    assert failed.status == "persist-failed"
    assert failed.acked is False
    assert state["acked"] is False
    assert len(local_briefs) == 1

    _git(queue, "remote", "set-url", "origin", str(bare))
    result = ingest_once(client, queue_base=queue)

    assert result.status == "duplicate"
    assert result.acked is True
    assert state["acked"] is True
    assert [request[0] for request in state["requests"]] == ["POST", "POST", "DELETE"]
    assert all(request[2] == client.timeout for request in state["requests"])
    assert sorted((queue / "queue").glob("*.md")) == local_briefs
    assert sum(name.startswith("queue/") for name in state["remote_files_at_ack"]) == 1
    assert any(name.startswith("queue/") for name in state["remote_files_at_ack"])
    marker = external_events.record_path(event["schema"], event["event_id"], queue)
    assert marker.relative_to(queue).as_posix() in state["remote_files_at_ack"]

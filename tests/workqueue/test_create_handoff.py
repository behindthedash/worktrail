from __future__ import annotations

import json
import os
from pathlib import Path

from worktrail.shared.brief_frontmatter import read_frontmatter, validate_brief
from worktrail.workqueue.create_handoff import create_handoff, main


def test_create_handoff_writes_valid_brief_and_classifies(tmp_path: Path):
    result = create_handoff(
        "Fix the broken handoff dashboard",
        queue_base=tmp_path,
        repo="/tmp/example",
        base_branch="main",
        suggested_skills=["debugging.skill"],
        approach="Reproduce the dashboard failure and add a regression test.",
    )

    path = Path(result["path"])
    assert path.parent == tmp_path / "queue"
    assert path.is_file()
    assert result["status"] == "created"
    assert result["recommended_route"] == "F"
    assert read_frontmatter(path) == {
        "id": path.stem,
        "created": read_frontmatter(path)["created"],
        "focus": "Fix the broken handoff dashboard",
        "repo": "/tmp/example",
        "remote": None,
        "base-branch": "main",
        "status": "queued",
        "suggested-skills": ["debugging.skill"],
        "recommended-route": "F",
    }
    assert "## Suggested approach" in path.read_text(encoding="utf-8")
    assert validate_brief(path)[0]
    assert os.environ.get("WORK_QUEUE_DIR") != str(tmp_path)


def test_create_handoff_omits_recommended_route_on_zero_signal_focus(tmp_path: Path):
    # A content-free focus string classifies as a zero-signal default (E,
    # low confidence, route_source="no-signal-default"), not a real pick --
    # it must not be persisted as recommended-route frontmatter, the same as
    # the ambiguous-tie omission (20260731-151701).
    result = create_handoff(
        "Standardize the shared helper used by two call sites",
        queue_base=tmp_path,
        repo="/tmp/example",
        base_branch="main",
    )

    path = Path(result["path"])
    assert result["recommended_route"] is None
    assert "recommended-route" not in read_frontmatter(path)
    assert "recommended-route" not in path.read_text(encoding="utf-8")


def test_create_handoff_auto_links_high_confidence_candidate(tmp_path: Path):
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "20260101-000001-auth.md").write_text(
        "---\n"
        "id: 20260101-000001-auth\n"
        "focus: Fix broken auth dashboard access\n"
        "repo: /tmp/example\n"
        "status: queued\n"
        "---\n\n"
        "## Focus\n\nFix broken auth dashboard access\n",
        encoding="utf-8",
    )

    result = create_handoff(
        "Fix broken auth dashboard access",
        queue_base=tmp_path,
        repo="/tmp/example",
    )

    assert "20260101-000001-auth" in result["auto_linked"]
    new_path = Path(result["path"])
    assert "20260101-000001-auth" in read_frontmatter(new_path)["related"]


def test_cli_emits_json_and_accepts_structured_fields(tmp_path: Path, capsys):
    assert main([
        "--focus", "Create a new OpenSpec handoff",
        "--queue-dir", str(tmp_path),
        "--change-kind", "new",
        "--json",
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "created"
    assert read_frontmatter(Path(output["path"]))["change-kind"] == "new"

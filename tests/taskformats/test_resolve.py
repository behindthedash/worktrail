"""The format seam: which `TaskSource` owns a given spec path.

This is the format seam: a single repo can hold both the legacy devkit format
and OpenSpec, so detection and adapter routing have to be right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.orchestrator import dispatch
from worktrail.taskformats import resolve
from worktrail.taskformats.devkit.source import DevkitSpecTaskSource
from worktrail.taskformats.openspec.source import OpenSpecTaskSource
from worktrail.taskformats.speckit.source import SpecKitTaskSource


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/repo/openspec/changes/add-export", resolve.FORMAT_OPENSPEC),
        ("openspec/changes/add-export", resolve.FORMAT_OPENSPEC),
        ("/repo/docs/specs/025-feature", resolve.FORMAT_DEVKIT),
        (
            "/repo/docs/specs/053-parent/changes/2026-07-21-output-contract",
            resolve.FORMAT_DEVKIT,
        ),
        ("docs/specs/025-feature", resolve.FORMAT_DEVKIT),
        # a repo literally named "openspec" must not be mistaken for the format
        ("/home/me/openspec/docs/specs/001-x", resolve.FORMAT_DEVKIT),
        # nor a change dir that isn't under changes/
        ("/repo/openspec/specs/data-export", resolve.FORMAT_DEVKIT),
        ("/repo/.specify/specs/data-export", resolve.FORMAT_SPECKIT),
    ],
)
def test_detect_format(path, expected):
    assert resolve.detect_format(path) == expected


def test_detect_works_on_a_path_that_does_not_exist_yet():
    """A worktree can be branched before its spec folder lands; detection must
    not depend on the directory existing."""
    assert resolve.detect_format("/nope/openspec/changes/ghost") == resolve.FORMAT_OPENSPEC


@pytest.mark.parametrize(
    "path,cls",
    [
        ("/repo/openspec/changes/add-export", OpenSpecTaskSource),
        ("/repo/docs/specs/025-feature", DevkitSpecTaskSource),
        ("/repo/.specify/specs/data-export", SpecKitTaskSource),
    ],
)
def test_task_source_for_returns_the_right_adapter(path, cls):
    assert isinstance(resolve.task_source_for(path), cls)


@pytest.mark.parametrize(
    "path,repo_root",
    [
        ("/repo/openspec/changes/add-export", Path("/repo")),
        ("/repo/docs/specs/025-feature", Path("/repo")),
        (
            "/repo/docs/specs/053-parent/changes/2026-07-21-output-contract",
            Path("/repo"),
        ),
        ("/repo/.specify/specs/data-export", Path("/repo")),
    ],
)
def test_repo_root_is_recovered_from_the_joined_path(path, repo_root):
    """The orchestrator only ever holds the joined path; the adapters are
    constructed from a repo root plus a short ref. A wrong split silently points
    every subsequent read and write at the wrong tree."""
    assert resolve.task_source_for(path).repo_root == repo_root


@pytest.mark.parametrize(
    "path,prefix",
    [
        ("/repo/openspec/changes/add-export", "openspec/"),
        ("/repo/docs/specs/025-feature", "docs/specs/"),
        ("/repo/.specify/specs/data-export", ".specify/"),
    ],
)
def test_spec_root_prefix_tracks_the_format(path, prefix):
    assert resolve.spec_root_prefix_for(path) == prefix


def test_load_spec_dispatches_to_openspec(tmp_path):
    d = tmp_path / "openspec" / "changes" / "add-export"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## 1. Setup\n\n- [ ] 1.1 First\n- [ ] 1.2 Second\n")
    spec_id, tasks = resolve.load_spec(d)
    assert spec_id == "add-export"
    assert [t["id"] for t in tasks] == ["1.1", "1.2"]


def test_load_spec_dispatches_to_devkit(tmp_path):
    d = tmp_path / "docs" / "specs" / "025-feature" / "tasks"
    d.mkdir(parents=True)
    (d / "TASK-001.md").write_text(
        "---\nid: TASK-001\ntitle: First\nstatus: pending\nkind: impl\n---\n\nbody\n"
    )
    _, tasks = resolve.load_spec(d.parent)
    assert [t["id"] for t in tasks] == ["TASK-001"]


def test_nested_devkit_change_preserves_parent_spec_path(tmp_path):
    d = tmp_path / "docs" / "specs" / "053-parent" / "changes" / "2026-07-21-output-contract"
    (d / "tasks").mkdir(parents=True)
    (d / "tasks" / "TASK-CHG-003.md").write_text(
        "---\nid: TASK-CHG-003\ntitle: API\nstatus: pending\nkind: impl\n---\n\nbody\n"
    )

    spec_id, tasks = resolve.load_spec(d)

    assert spec_id == "2026-07-21-output-contract"
    assert [t["id"] for t in tasks] == ["TASK-CHG-003"]
    assert resolve.spec_ref_for(d) == "053-parent/changes/2026-07-21-output-contract"
    assert resolve.task_brief_ref_for(d, "TASK-CHG-003")[0] == (
        "docs/specs/053-parent/changes/2026-07-21-output-contract/tasks/TASK-CHG-003.md"
    )


def test_load_spec_dispatches_to_spec_kit(tmp_path):
    d = tmp_path / ".specify" / "specs" / "025-feature"
    d.mkdir(parents=True)
    (d / "tasks.md").write_text("## Phase 1: Setup\n\n- [ ] T001 First\n")
    _, tasks = resolve.load_spec(d)
    assert [t["id"] for t in tasks] == ["T001"]


# --------------------------------------------------------------------------- #
# the reason the seam exists
# --------------------------------------------------------------------------- #
def _ctx(spec_folder: str) -> dict:
    return {
        "spec_id": "x",
        "spec_folder": spec_folder,
        "worktree_path": "/w",
        "branch": "b",
        "spec_root_prefix": resolve.spec_root_prefix_for(spec_folder),
    }


def test_worker_prompt_names_the_spec_root_of_the_running_format():
    """A hard rule naming the wrong tree is decoration, not a guard: the worker
    is warned off a tree that isn't there while the one it can actually damage
    goes unmentioned."""
    task = {"id": "1.1", "files": ["src/a.py"]}
    os_prompt = dispatch.build_worker_prompt(
        "implement", task, _ctx("openspec/changes/add-export/")
    )
    assert "Do NOT modify openspec/** at all." in os_prompt
    assert "docs/specs" not in os_prompt

    dk_prompt = dispatch.build_worker_prompt(
        "implement", {"id": "TASK-001", "files": ["src/a.py"]}, _ctx("docs/specs/025-feature/")
    )
    assert "Do NOT modify docs/specs/** at all." in dk_prompt
    assert "openspec/" not in dk_prompt


def test_worker_prompt_defaults_to_devkit_for_callers_predating_the_seam():
    """`spec_root_prefix` absent from ctx must not silently drop the guard."""
    ctx = {"spec_id": "x", "spec_folder": "docs/specs/025/", "worktree_path": "/w", "branch": "b"}
    prompt = dispatch.build_worker_prompt("implement", {"id": "TASK-001", "files": []}, ctx)
    assert "Do NOT modify docs/specs/** at all." in prompt


def test_verify_deny_list_tracks_the_format():
    """`_forbidden_paths_touched` is the deterministic backstop behind the
    prompt's soft rule. Checked against the wrong root it reports clean while a
    worker rewrites the spec it is implementing against."""
    from worktrail.orchestrator import verify

    assert verify.forbidden_prefixes_for("openspec/changes/add-export") == (
        ".github/workflows/",
        "openspec/",
    )
    assert verify.forbidden_prefixes_for("docs/specs/025-feature") == (
        ".github/workflows/",
        "docs/specs/",
    )
    assert verify.forbidden_prefixes_for(".specify/specs/data-export") == (
        ".github/workflows/",
        ".specify/",
    )
    # unknown spec path -> today's behavior, not a dropped guard
    assert verify.forbidden_prefixes_for(None) == verify.FORBIDDEN_WORKER_PATH_PREFIXES

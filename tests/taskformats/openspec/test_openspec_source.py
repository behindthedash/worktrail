"""Tests for the OpenSpec `TaskSource`.

Includes a live round-trip against the real `openspec` CLI when it is installed
(skipped otherwise). Fixture-only coverage would prove we parse our own idea of
the format; the point of contention in the design was whether OpenSpec accepts
checkboxes written by something other than `/opsx:apply`, and only the real CLI
can answer that.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from worktrail.taskformats.openspec import schema as os_schema
from worktrail.taskformats.openspec.source import OpenSpecTaskSource

TASKS_MD = textwrap.dedent(
    """\
    ## 1. Setup

    - [ ] 1.1 Create export module structure
    - [ ] 1.2 Add CSV encoder dependency

    ## 2. Core Implementation

    - [x] 2.1 Implement export function
    - [ ] 2.2 Add endpoint wiring
    """
)


def _change(tmp_path: Path, change_id: str = "add-export", tasks: str = TASKS_MD) -> Path:
    d = tmp_path / "openspec" / "changes" / change_id
    d.mkdir(parents=True)
    (d / "tasks.md").write_text(tasks)
    return tmp_path


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def test_parses_ids_titles_groups_and_status():
    parsed = os_schema.parse_tasks_md(TASKS_MD)
    assert [t.id for t in parsed.tasks] == ["1.1", "1.2", "2.1", "2.2"]
    assert [t.group for t in parsed.tasks] == ["1", "1", "2", "2"]
    assert parsed.tasks[0].group_title == "Setup"
    assert parsed.tasks[2].group_title == "Core Implementation"
    assert parsed.tasks[0].title == "Create export module structure"
    assert parsed.tasks[2].status == os_schema.STATUS_COMPLETED
    assert parsed.tasks[3].status == os_schema.STATUS_PENDING
    assert parsed.warnings == []


def test_multi_digit_ids_parse():
    """`2.10` must not be truncated to `2.1` -- a group of ten or more tasks is
    ordinary, and a collision here would silently merge two tasks."""
    parsed = os_schema.parse_tasks_md("## 2. Big\n\n- [ ] 2.9 nine\n- [ ] 2.10 ten\n")
    assert [t.id for t in parsed.tasks] == ["2.9", "2.10"]


def test_untrackable_checkbox_is_warned_not_dropped_silently():
    """OpenSpec's own tracker ignores a checkbox with no `N.M` id. Silently
    matching that behavior would leave an author with a task that never runs and
    no way to see why."""
    parsed = os_schema.parse_tasks_md("## 1. G\n\n- [ ] no id here\n- [ ] 1.1 fine\n")
    assert [t.id for t in parsed.tasks] == ["1.1"]
    assert any("not trackable" in w for w in parsed.warnings)


def test_task_before_any_group_heading_is_warned():
    parsed = os_schema.parse_tasks_md("- [ ] 1.1 orphan\n")
    assert parsed.tasks[0].group == ""
    assert any("before any" in w for w in parsed.warnings)


def test_duplicate_id_is_warned_and_first_wins():
    parsed = os_schema.parse_tasks_md("## 1. G\n\n- [ ] 1.1 first\n- [ ] 1.1 second\n")
    assert [t.title for t in parsed.tasks] == ["first"]
    assert any("duplicate" in w for w in parsed.warnings)


# --------------------------------------------------------------------------- #
# load()
# --------------------------------------------------------------------------- #
def test_load_makes_tasks_sequential_within_a_group_and_groups_independent(tmp_path):
    _, tasks = OpenSpecTaskSource(_change(tmp_path)).load("add-export")
    by_id = {t["id"]: t for t in tasks}
    assert by_id["1.1"]["deps"] == []
    assert by_id["1.2"]["deps"] == ["1.1"]
    # group 2 does not depend on group 1
    assert by_id["2.1"]["deps"] == []
    assert by_id["2.2"]["deps"] == ["2.1"]


def test_load_declares_no_file_scope(tmp_path):
    """Inventing a file scope would be worse than declaring none: an empty set
    reads as 'collides with nothing' to runnable_frontier(), so a guess would
    licence concurrent edits to the same file."""
    _, tasks = OpenSpecTaskSource(_change(tmp_path)).load("add-export")
    assert all(t["files"] == [] for t in tasks)


def test_load_missing_change_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        OpenSpecTaskSource(tmp_path).load("nope")


# --------------------------------------------------------------------------- #
# paths + guards
# --------------------------------------------------------------------------- #
def test_every_task_resolves_to_the_single_tasks_md(tmp_path):
    src = OpenSpecTaskSource(_change(tmp_path))
    assert src.task_file_path("1.1", "add-export") == src.task_file_path("2.2", "add-export")
    assert src.task_file_path("1.1", "add-export").name == "tasks.md"


def test_spec_root_prefix_guards_the_whole_openspec_tree(tmp_path):
    """FORBIDDEN_WORKER_PATH_PREFIXES is built from this. Returning
    `openspec/changes/` would leave `openspec/specs/` (the merged, archived
    source of truth) writable by a worker."""
    assert OpenSpecTaskSource(tmp_path).spec_root_prefix() == "openspec/"


# --------------------------------------------------------------------------- #
# mark_status
# --------------------------------------------------------------------------- #
def test_mark_status_ticks_only_the_named_task(tmp_path):
    root = _change(tmp_path)
    src = OpenSpecTaskSource(root)
    assert src.mark_status("1.1", "completed", spec_ref="add-export") is True
    text = (root / "openspec/changes/add-export/tasks.md").read_text()
    assert "- [x] 1.1 Create export module structure" in text
    assert "- [ ] 1.2 Add CSV encoder dependency" in text  # untouched


def test_mark_status_is_idempotent_and_reports_no_change(tmp_path):
    src = OpenSpecTaskSource(_change(tmp_path))
    assert src.mark_status("2.1", "completed", spec_ref="add-export") is False  # already [x]


def test_mark_status_ignores_non_terminal_statuses(tmp_path):
    """Intermediate run states live in the journal; a checkbox cannot hold them."""
    root = _change(tmp_path)
    src = OpenSpecTaskSource(root)
    assert src.mark_status("1.1", "reviewing", spec_ref="add-export") is False
    assert "- [ ] 1.1" in (root / "openspec/changes/add-export/tasks.md").read_text()


def test_mark_status_requires_spec_ref(tmp_path):
    with pytest.raises(ValueError):
        OpenSpecTaskSource(tmp_path).mark_status("1.1", "completed")


def test_write_back_changes_exactly_one_character(tmp_path):
    """tasks.md lands in a PR diff. A full re-render would turn a one-character
    state change into review noise and risk dropping unmodelled content."""
    root = _change(tmp_path)
    p = root / "openspec/changes/add-export/tasks.md"
    before = p.read_text()
    OpenSpecTaskSource(root).mark_status("1.1", "completed", spec_ref="add-export")
    after = p.read_text()
    assert len(before) == len(after)
    assert sum(1 for a, b in zip(before, after) if a != b) == 1


# --------------------------------------------------------------------------- #
# validate_dependencies
# --------------------------------------------------------------------------- #
def test_validate_dependencies_no_diagnostic_for_a_resolving_dep(tmp_path):
    src = OpenSpecTaskSource(tmp_path)
    tasks = [{"id": "1.1", "deps": []}, {"id": "1.2", "deps": ["1.1"]}]
    assert src.validate_dependencies("add-export", tasks) == []


def test_validate_dependencies_flags_unresolved_same_spec_dep(tmp_path):
    src = OpenSpecTaskSource(tmp_path)
    tasks = [{"id": "1.1", "deps": []}, {"id": "1.2", "deps": ["1.1", "9.9"]}]
    diagnostics = src.validate_dependencies("add-export", tasks)
    assert len(diagnostics) == 1
    assert "1.2" in diagnostics[0]
    assert "9.9" in diagnostics[0]


def test_validate_dependencies_on_a_loaded_change_has_no_diagnostics(tmp_path):
    """`load()`'s own sequential-within-group deps must always resolve --
    round-tripping through the real loader is the case the same-spec check
    exists to keep clean."""
    src = OpenSpecTaskSource(_change(tmp_path))
    _, tasks = src.load("add-export")
    assert src.validate_dependencies("add-export", tasks) == []


def test_validate_dependencies_reports_decision_refs_unsupported(tmp_path):
    src = OpenSpecTaskSource(tmp_path)
    tasks = [{"id": "1.1", "deps": [], "decision-refs": ["D1"]}]
    diagnostics = src.validate_dependencies("add-export", tasks)
    assert len(diagnostics) == 1
    assert "1.1" in diagnostics[0]
    assert "decision-refs unsupported" in diagnostics[0]


def test_validate_dependencies_ignores_tasks_without_decision_refs(tmp_path):
    src = OpenSpecTaskSource(tmp_path)
    tasks = [{"id": "1.1", "deps": []}]
    assert src.validate_dependencies("add-export", tasks) == []


# --------------------------------------------------------------------------- #
# external dependencies
# --------------------------------------------------------------------------- #
def test_resolve_external_dependency_across_changes(tmp_path):
    root = _change(tmp_path)
    _change(root, "add-import", "## 1. G\n\n- [x] 1.1 done\n- [ ] 1.2 pending\n")
    src = OpenSpecTaskSource(root)
    assert src.resolve_external_dependency("add-import/1.1")["satisfied"] is True
    assert src.resolve_external_dependency("add-import/1.2")["satisfied"] is False
    assert src.resolve_external_dependency("add-import/9.9")["reason"] == "task not found"
    assert src.resolve_external_dependency("nope/1.1")["reason"] == "change folder not found"
    assert src.resolve_external_dependency("malformed")["reason"] == "malformed"


def test_resolve_external_dependency_rejects_traversal(tmp_path):
    src = OpenSpecTaskSource(_change(tmp_path))
    assert src.resolve_external_dependency("../../etc/1.1")["resolved"] is False


# --------------------------------------------------------------------------- #
# live CLI round-trip
# --------------------------------------------------------------------------- #
openspec_cli = pytest.mark.skipif(
    shutil.which("openspec") is None, reason="openspec CLI not installed"
)


@openspec_cli
def test_archive_accepts_checkboxes_this_adapter_wrote(tmp_path):
    """The load-bearing assumption of the whole design: worktrail replaces
    `/opsx:apply` and hands back to `openspec archive`. If archive only trusted
    ticks written by its own apply step, the orchestrator could not drive an
    OpenSpec change at all.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["openspec", "init", "--tools", "none"], cwd=tmp_path, check=True,
                   capture_output=True, text=True)

    d = tmp_path / "openspec" / "changes" / "add-export"
    (d / "specs" / "data-export").mkdir(parents=True)
    (d / "proposal.md").write_text(
        "## Why\n\nOperators need CSV export because manual extraction is slow, "
        "error-prone, and does not scale to recurring reporting needs.\n\n"
        "## What Changes\n\n- Add a CSV export endpoint\n\n"
        "## Capabilities\n\n**New Capabilities**\n- `data-export`\n\n"
        "## Impact\n\nNew module `src/export/`.\n"
    )
    (d / "design.md").write_text("## Approach\n\nStream rows through a CSV encoder.\n")
    (d / "specs" / "data-export" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: CSV export\n"
        "The system SHALL export a dataset as CSV.\n\n"
        "#### Scenario: Export succeeds\n"
        "- **WHEN** a user requests an export\n- **THEN** a CSV file is returned\n"
    )
    (d / "tasks.md").write_text(TASKS_MD)

    src = OpenSpecTaskSource(tmp_path)
    _, tasks = src.load("add-export")
    for t in tasks:
        src.mark_status(t["id"], "completed", spec_ref="add-export")

    r = subprocess.run(["openspec", "archive", "add-export", "--yes"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    # archive reports completion from the checkboxes THIS adapter wrote
    assert "Task status: ✓ Complete" in r.stdout, r.stdout
    assert not (tmp_path / "openspec/changes/add-export").exists()
    assert list((tmp_path / "openspec/changes/archive").glob("*-add-export"))
    # the delta was merged into the durable spec
    assert (tmp_path / "openspec/specs/data-export/spec.md").is_file()


@openspec_cli
def test_parser_agrees_with_the_shipped_schema_template(tmp_path):
    """Guards against OpenSpec changing its own tasks format under us: parse the
    template the installed CLI actually resolves, not a copy pinned in this repo."""
    r = subprocess.run(["openspec", "templates", "--json"], cwd=tmp_path,
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("openspec templates --json unavailable")
    try:
        paths = json.loads(r.stdout)
    except json.JSONDecodeError:
        pytest.skip("openspec templates --json not machine-readable in this version")
    entry = paths.get("tasks") if isinstance(paths, dict) else None
    # 1.6.0 reports {"tasks": {"path": ..., "source": ...}}; tolerate a bare string too.
    tasks_template = entry.get("path") if isinstance(entry, dict) else entry
    if not tasks_template or not Path(tasks_template).is_file():
        pytest.skip("tasks template path not reported")
    parsed = os_schema.parse_tasks_md(Path(tasks_template).read_text())
    assert [t.id for t in parsed.tasks] == ["1.1", "1.2", "2.1", "2.2"]
    assert [t.group for t in parsed.tasks] == ["1", "1", "2", "2"]


# --------------------------------------------------------------------------- #
# task kind via leading [tag]
# --------------------------------------------------------------------------- #
TAGGED = textwrap.dedent(
    """\
    ## 1. Implementation

    - [ ] 1.1 [TASK-001] Build the thing
    - [ ] 1.2 [TASK-002] Build the other thing

    ## 2. Independent

    - [ ] 2.1 [TASK-008] Unrelated work

    ## 3. Verification and cleanup

    - [ ] 3.1 [TASK-013] [e2e] Verify the journey
    - [ ] 3.2 [TASK-014] [cleanup] Tidy up
    """
)


def test_kind_is_recovered_from_a_leading_tag(tmp_path):
    _, tasks = OpenSpecTaskSource(_change(tmp_path, tasks=TAGGED)).load("add-export")
    kinds = {t["id"]: t["kind"] for t in tasks}
    assert kinds == {"1.1": "impl", "1.2": "impl", "2.1": "impl", "3.1": "e2e", "3.2": "cleanup"}


def test_tag_is_stripped_from_the_title(tmp_path):
    _, tasks = OpenSpecTaskSource(_change(tmp_path, tasks=TAGGED)).load("add-export")
    by_id = {t["id"]: t for t in tasks}
    assert by_id["3.1"]["title"] == "Verify the journey"
    assert by_id["3.1"]["tags"] == ["TASK-013", "e2e"]


def test_tail_tasks_are_held_out_of_the_fan_out(tmp_path):
    """The gap this closes. Without `kind`, e2e/cleanup land in an independent
    section and get dispatched in PARALLEL with the implementation they exist to
    verify. Found during the first real devkit->OpenSpec conversion (spec 080),
    where the adapter reported every task as `impl` and detected no tail at all.
    """
    from worktrail.orchestrator import coordinator

    _, tasks = OpenSpecTaskSource(_change(tmp_path, tasks=TAGGED)).load("add-export")
    held = set(coordinator.tail_held_out_task_ids(tasks))
    assert held == {"3.1", "3.2"}


def test_tail_depends_on_every_preceding_non_tail_task(tmp_path):
    """Sections read as independent, so a tail task needs explicit edges to
    every earlier implementation task — including ones in *other* sections."""
    _, tasks = OpenSpecTaskSource(_change(tmp_path, tasks=TAGGED)).load("add-export")
    by_id = {t["id"]: t for t in tasks}
    assert by_id["3.1"]["deps"] == ["1.1", "1.2", "2.1"]
    # ...and cleanup follows e2e via the normal within-section chain
    assert "3.1" in by_id["3.2"]["deps"]


def test_untagged_tasks_keep_section_independence(tmp_path):
    """Only tail kinds get cross-section edges; ordinary sections stay parallel."""
    _, tasks = OpenSpecTaskSource(_change(tmp_path, tasks=TAGGED)).load("add-export")
    by_id = {t["id"]: t for t in tasks}
    assert by_id["2.1"]["deps"] == []


def test_multiple_kind_tags_warn_and_take_the_first(tmp_path):
    md = "## 1. G\n\n- [ ] 1.1 [e2e] [cleanup] Ambiguous\n"
    parsed = os_schema.parse_tasks_md(md)
    assert parsed.tasks[0].kind == "e2e"
    assert any("multiple kind tags" in w for w in parsed.warnings)

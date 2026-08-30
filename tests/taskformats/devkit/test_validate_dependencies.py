#!/usr/bin/env python3
"""Tests for `DevkitSpecTaskSource.validate_dependencies`'s same-spec `deps`
check and its `decision-refs:` / `decision-log.md` check."""

from __future__ import annotations

from worktrail.taskformats.devkit.source import DevkitSpecTaskSource, load_spec


def _write_task(
    tasks_dir,
    task_id,
    *,
    deps=None,
    external_deps=None,
    decision_refs=None,
    title="Task",
):
    lines = ["---", f"id: {task_id}", f"title: {title}", "status: pending"]
    if deps:
        lines.append("dependencies:")
        lines.extend(f"  - {d}" for d in deps)
    if external_deps:
        lines.append("external-dependencies:")
        lines.extend(f"  - {d}" for d in external_deps)
    if decision_refs:
        lines.append("decision-refs:")
        lines.extend(f"  - {d}" for d in decision_refs)
    lines.append("---")
    lines.append("")
    lines.append("body")
    lines.append("")
    (tasks_dir / f"{task_id}.md").write_text("\n".join(lines))


def test_resolving_same_spec_dependency_emits_no_diagnostic(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", deps=["TASK-002"])
    _write_task(tasks_dir, "TASK-002")

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    assert source.validate_dependencies(spec_id, tasks) == []


def test_decided_decision_ref_emits_no_diagnostic(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", decision_refs=["D1"])
    (spec_dir / "decision-log.md").write_text("## D1: Use widgets\n\nStatus: decided\n")

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    assert source.validate_dependencies(spec_id, tasks) == []


def test_missing_decision_id_is_flagged(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", decision_refs=["D9"])
    (spec_dir / "decision-log.md").write_text("## D1: Use widgets\n\nStatus: decided\n")

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    diagnostics = source.validate_dependencies(spec_id, tasks)

    assert len(diagnostics) == 1
    assert "TASK-001" in diagnostics[0]
    assert "D9" in diagnostics[0]
    assert "missing decision" in diagnostics[0]


def test_open_decision_ref_is_flagged(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", decision_refs=["D2"])
    (spec_dir / "decision-log.md").write_text("## D2: Use widgets\n\nStatus: open\n")

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    diagnostics = source.validate_dependencies(spec_id, tasks)

    assert len(diagnostics) == 1
    assert "TASK-001" in diagnostics[0]
    assert "D2" in diagnostics[0]
    assert "open decision" in diagnostics[0]


def test_decision_refs_with_no_decision_log_file_is_flagged(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", decision_refs=["D1"])

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    diagnostics = source.validate_dependencies(spec_id, tasks)

    assert len(diagnostics) == 1
    assert "TASK-001" in diagnostics[0]
    assert "D1" in diagnostics[0]
    assert "missing decision" in diagnostics[0]


def test_unresolved_same_spec_dependency_is_flagged(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(tasks_dir, "TASK-001", deps=["TASK-999"])

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    diagnostics = source.validate_dependencies(spec_id, tasks)

    assert len(diagnostics) == 1
    assert "TASK-001" in diagnostics[0]
    assert "TASK-999" in diagnostics[0]
    assert "unresolved same-spec dependency" in diagnostics[0]


def test_external_dependency_is_not_flagged_by_same_spec_check(tmp_path):
    spec_dir = tmp_path / "docs" / "specs" / "025-feature"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True)
    _write_task(
        tasks_dir,
        "TASK-001",
        external_deps=["098-recursive-organization-model/TASK-036"],
    )

    spec_id, tasks = load_spec(str(spec_dir))
    source = DevkitSpecTaskSource(tmp_path)

    assert source.validate_dependencies(spec_id, tasks) == []

from __future__ import annotations

from pathlib import Path

from worktrail.taskformats.speckit import schema
from worktrail.taskformats.speckit.source import SpecKitTaskSource


TASKS = """# Tasks: Export

## Phase 1: Setup

- [ ] T001 Create the export module in `src/export.py`
- [x] T002 [P] Add the encoder dependency

## Phase 2: Implementation

- [ ] **T003** Implement the endpoint
"""


def _feature(tmp_path: Path) -> Path:
    feature = tmp_path / ".specify" / "specs" / "add-export"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(TASKS)
    return tmp_path


def test_parser_reads_spec_kit_ids_tags_and_status():
    parsed = schema.parse_tasks_md(TASKS)
    assert [task.id for task in parsed.tasks] == ["T001", "T002", "T003"]
    assert parsed.tasks[0].group_title == "Setup"
    assert parsed.tasks[1].tags == ["P"]
    assert parsed.tasks[1].status == schema.STATUS_COMPLETED


def test_source_loads_tasks_and_keeps_group_order(tmp_path):
    _, tasks = SpecKitTaskSource(_feature(tmp_path)).load("add-export")
    by_id = {task["id"]: task for task in tasks}
    assert by_id["T001"]["deps"] == []
    assert by_id["T002"]["deps"] == ["T001"]
    assert by_id["T003"]["deps"] == []
    assert all(task["files"] == [] for task in tasks)


def test_source_marks_only_named_task(tmp_path):
    root = _feature(tmp_path)
    source = SpecKitTaskSource(root)
    assert source.mark_status("T001", "completed", spec_ref="add-export") is True
    text = (root / ".specify/specs/add-export/tasks.md").read_text()
    assert "- [x] T001" in text
    assert "- [x] T002" in text
    assert "- [ ] **T003**" in text


def test_source_resolves_external_dependency(tmp_path):
    root = _feature(tmp_path)
    other = root / ".specify" / "specs" / "import-data"
    other.mkdir(parents=True)
    (other / "tasks.md").write_text("## Phase 1: Setup\n\n- [x] T001 Done\n")
    source = SpecKitTaskSource(root)
    assert source.resolve_external_dependency("import-data/T001")["satisfied"] is True


def test_source_guards_entire_specify_tree(tmp_path):
    assert SpecKitTaskSource(tmp_path).spec_root_prefix() == ".specify/"

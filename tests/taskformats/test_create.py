from pathlib import Path

import pytest

from worktrail.taskformats.create import create_spec


def test_create_openspec_scaffold(tmp_path: Path):
    result = create_spec(tmp_path, "add-export", "Export records", "openspec")
    root = tmp_path / "openspec" / "changes" / "add-export"
    assert result["path"] == str(root)
    assert (root / "proposal.md").is_file()
    assert (root / "design.md").is_file()
    assert (root / "specs" / "add-export" / "spec.md").is_file()
    assert "- [ ] 1.1" in (root / "tasks.md").read_text()


def test_create_devkit_scaffold(tmp_path: Path):
    create_spec(tmp_path, "001-export", "Export records", "devkit")
    root = tmp_path / "docs" / "specs" / "001-export"
    assert (root / "user-request.md").read_text() == "Export records\n"
    assert (root / "spec.md").is_file()
    assert (root / "tasks" / "TASK-001.md").is_file()


def test_create_refuses_overwrite(tmp_path: Path):
    create_spec(tmp_path, "add-export", "Export records")
    with pytest.raises(FileExistsError):
        create_spec(tmp_path, "add-export", "Try again")

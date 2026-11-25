from pathlib import Path

from worktrail.taskformats.openspec.normalize import (
    main,
    normalize_untrackable_task_ids,
)


def test_assigns_group_ids_above_existing_suffix_without_renumbering():
    text = (
        "## 1. First\n- [ ] missing\n- [ ] 1.4 existing\n"
        "## 2. Second\n- [x] 2.1 done\n- [ ] missing too\n"
    )
    assert normalize_untrackable_task_ids(text) == (
        "## 1. First\n- [ ] 1.5 missing\n- [ ] 1.4 existing\n"
        "## 2. Second\n- [x] 2.1 done\n- [ ] 2.2 missing too\n"
    )


def test_preserves_state_continuations_and_unrelated_bytes():
    text = "lead\r\n## 3. Group\r\n- [x] Keep me\r\n  files: src/a.py\r\n"
    assert normalize_untrackable_task_ids(text) == (
        "lead\r\n## 3. Group\r\n- [x] 3.1 Keep me\r\n  files: src/a.py\r\n"
    )


def test_leaves_ungrouped_checkboxes_unchanged_and_is_idempotent():
    text = "- [ ] orphan\n## 1. Group\n- [ ] item\n"
    normalized = normalize_untrackable_task_ids(text)
    assert normalized == "- [ ] orphan\n## 1. Group\n- [ ] 1.1 item\n"
    assert normalize_untrackable_task_ids(normalized) == normalized


def test_cli_default_rewrites_change_directory_and_check_is_read_only(
    tmp_path: Path, capsys
):
    change = tmp_path / "change"
    change.mkdir()
    tasks = change / "tasks.md"
    original = "## 1. Group\n- [ ] no id\n"
    tasks.write_text(original, encoding="utf-8")

    assert main([str(change), "--check"]) == 1
    assert tasks.read_text(encoding="utf-8") == original
    assert "line 2" in capsys.readouterr().out

    assert main([str(change)]) == 0
    normalized = "## 1. Group\n- [ ] 1.1 no id\n"
    assert tasks.read_text(encoding="utf-8") == normalized
    assert main([str(tasks), "--check"]) == 0


def test_cli_check_reports_ungrouped_checkbox_without_writing(tmp_path: Path, capsys):
    tasks = tmp_path / "tasks.md"
    original = "- [ ] orphan\n"
    tasks.write_text(original, encoding="utf-8")
    assert main([str(tasks), "--check"]) == 1
    assert tasks.read_text(encoding="utf-8") == original
    assert "line 1" in capsys.readouterr().out

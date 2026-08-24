## 1. Hard-refuse gate on incomplete tasks.md

- [x] 1.1 In `src/worktrail/drain/drain.py`'s `_run_openspec_archive`, before
      invoking `openspec archive -y <change-id>`, parse
      `wt / "openspec" / "changes" / spec_id / "tasks.md"` with
      `worktrail.taskformats.openspec.schema.parse_tasks_md` and raise
      `RuntimeError` (no `openspec archive` subprocess call, no commit, no
      push, no PR) if any parsed task's status is not `STATUS_COMPLETED`; add
      a regression test in `tests/drain/test_drain.py` proving
      `archive_openspec_change` raises without ever invoking
      `openspec archive` when a finding's `tasks.md` has an unchecked task
      (Requirement: OpenSpec change archive remediation)

## 1. Behavior-evidence gate on OpenSpec stale detection

- [x] 1.1 Implement requirement: A task's claimed identifiers must be present before it is
      classified stale. In `src/worktrail/router/dashboard.py`, add a module-level helper that
      extracts identifier evidence from a task's own text (backticked code-like tokens matching
      `[A-Za-z_][A-Za-z0-9_]{2,}` after stripping a trailing `()`, dropping tokens containing `/`
      or `.` and tokens naming one of the task's declared files) and a helper that decides whether
      every extracted identifier appears in the current on-disk text of those declared files (an
      unreadable file contributes nothing). Wire both into `_pending_openspec_stale` so a
      candidate task is added to the stale list only when `_task_files_are_shipped` passes AND the
      behavior-evidence check passes; a task with no extractable identifiers passes the check
      (design.md Decisions 1 and 2). Neither helper may make a model or network call.
      Cover it in `tests/router/test_dashboard.py` against a fixture repo with a cached RunPlan:
      (a) the brief's shape — a pending task naming a backticked identifier whose single declared
      file is tracked and committed after the change's creation baseline but does not contain that
      identifier is NOT stale, and the change reports `stage: "ready-to-implement"`; (b) the same
      task is stale once the identifier is present in the file; (c) a task naming several
      identifiers with one absent is NOT stale; (d) a prose-only task with no extractable
      identifiers keeps the file-level verdict.

- [x] 1.2 Implement requirement: OpenSpec stale-bookkeeping reporting matches the devkit path's
      shape. Have `_pending_openspec_stale` also report, per stale task, whether its verdict
      carried identifier evidence, and in `_safe_detect_openspec` set `stale_evidence` to
      `"behavior"` when every stale task was symbol-verified and `"files-only"` otherwise, using
      the file-level-only `next_action` wording in the `files-only` case and today's wording
      otherwise. Keep the `stage` string `"stale-bookkeeping"` unchanged in both cases and emit no
      `stale_evidence` on the `ready-to-implement` path (design.md Decision 3).
      Cover it in `tests/router/test_dashboard.py`: `stale_evidence == "behavior"` when every
      stale task was symbol-verified, `stale_evidence == "files-only"` with the
      confirm-the-behavior `next_action` when at least one stale task had no identifiers,
      `stage == "stale-bookkeeping"` in both cases, and no `stale_evidence` key on a
      `ready-to-implement` result.

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and confirm it is green, including the new tests
      from section 1. Verification-only — no file changes expected.
- [x] 2.2 [e2e] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (golden
      record/replay regression) and confirm it is green. Verification-only — no file changes
      expected.

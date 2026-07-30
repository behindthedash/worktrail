---
id: TASK-001
title: Add DoD-verification check to the pre-PR gate
spec: 001-task-ac-verification-gate
status: completed
dependencies: []
files:
  - src/worktrail/router/check_dod_verification.py
  - src/worktrail/router/pre_pr_gate.py
  - src/worktrail/taskformats/devkit/schema.py
  - src/worktrail/taskformats/devkit/source.py
  - pyproject.toml
  - tests/router/test_check_dod_verification.py
  - tests/router/test_pre_pr_gate.py
kind: impl
complexity: standard
domain: router
dod-checks:
  - type: file_exists
    path: src/worktrail/router/check_dod_verification.py
  - type: file_exists
    path: tests/router/test_check_dod_verification.py
  - type: grep
    path: pyproject.toml
    pattern: 'worktrail-check-dod-verification = "worktrail\.router\.check_dod_verification:main"'
  - type: grep
    path: src/worktrail/router/pre_pr_gate.py
    pattern: 'DOD_VERIFICATION_DRIFT_EXIT'
  - type: grep
    path: src/worktrail/taskformats/devkit/schema.py
    pattern: '"dod-checks"'
  - type: command
    cmd: 'PYTHONPATH=src python3 -m pytest tests/router/test_check_dod_verification.py tests/router/test_pre_pr_gate.py -q'
---

## Implementation Details

Single, tightly-coupled task (no fan-out benefit — one cohesive change
across a handful of files in the same subsystem, single worker).

1. **`taskformats/devkit/schema.py`** — add `"dod-checks": {"type": list,
   "required": False}` to `FIELD_SCHEMA`.

2. **`taskformats/devkit/source.py`** — add `"dod-checks"` to
   `KNOWN_TASK_FRONTMATTER_KEYS` so a typo of the key (e.g. `dod_checks`,
   `dodchecks`) is caught by `validate_frontmatter_keys`'s existing
   close-match warning instead of being silently ignored.

3. **New `src/worktrail/router/check_dod_verification.py`** — mirror
   `check_clarification_integrity.py`'s shape:
   - `run_check(repo: Path, check: dict) -> str | None`. Supports
     `type: file_exists` (`path` must exist under `repo`), `type: grep`
     (`path` must exist and its text must `re.search(pattern)`), and
     `type: command` (`subprocess.run(["bash", "-c", cmd], cwd=repo)` must
     exit 0). An unrecognized `type` (or a check missing its required keys)
     is itself a failure string — never a silent pass.
   - `check_task_file(repo: Path, task_path: Path) -> list[str]` — read the
     task's frontmatter via `taskformats.devkit.schema.read_task_file`. If
     frontmatter is unreadable, `status` != `"completed"`, or `dod-checks`
     is absent/empty, return `[]`. Otherwise run every check in order and
     collect failures (do not short-circuit on the first failure — report
     all of them, same as `check_clarification_integrity`'s aggregation
     style).
   - `check_changed_specs(repo: Path, changed_paths: list[str]) -> list[str]`
     — filter `changed_paths` to paths starting with `docs/specs/` whose
     basename matches `taskformats.devkit.schema.is_task_file`, skip paths
     that don't exist in the worktree (deleted in this diff), run
     `check_task_file` on the rest, and prefix each failure with the
     relative path (same style as `check_clarification_integrity`'s
     `f"{relpath}: {failure}"`).
   - `main()` — standalone CLI, `--repo` (default `.`) and `--base-branch`
     (default: try `origin/main`/`origin/master`/`main`/`master`), reusing
     the same `_resolve_base_ref`/`_changed_paths_via_git` pattern already
     in `check_clarification_integrity.py` (duplicate the ~20 lines rather
     than import across modules — matches the existing convention where
     `check_spec_sync.py` and `check_clarification_integrity.py` each carry
     their own copy).
   - Register `worktrail-check-dod-verification =
     "worktrail.router.check_dod_verification:main"` in `pyproject.toml`
     `[project.scripts]` (alphabetical position, matching the existing
     `worktrail-check-*` block).

4. **Wire into `pre_pr_gate.py`**:
   - Import `check_changed_specs` from the new module (aliased, e.g.
     `check_dod_verification as _check_dod` or
     `from .check_dod_verification import check_changed_specs as
     check_dod_failures` — avoid a bare-name collision with the existing
     `check_clarification_integrity.check_changed_specs` import already in
     this file).
   - Add `DOD_VERIFICATION_DRIFT_EXIT = 4` alongside the other exit-code
     constants.
   - In `main()`, immediately after the existing clarification-integrity
     block (still inside the `if not args.print_cmd:` guard, before the
     `is_docs_only` check), call `check_dod_failures(repo, changed_paths(repo,
     policy) or [])`; on non-empty result print `PRE-PR GATE: FAIL —
     Definition-of-Done verification failed for completed task(s):` plus
     each failure line to stderr, plus a one-line fix hint, and `return
     DOD_VERIFICATION_DRIFT_EXIT`.
   - Update the module docstring's "Exit codes:" list to document `4`.

5. **Tests**:
   - `tests/router/test_check_dod_verification.py` — unit tests for
     `run_check` (pass/fail for each of the 3 types, unknown type fails,
     missing required key fails), `check_task_file` (no-op for non-completed
     status, no-op for completed-with-no-dod-checks, aggregates multiple
     failures for a completed task with mixed pass/fail checks), and
     `check_changed_specs` (filters to devkit task files under
     `docs/specs/`, skips paths outside `docs/specs/`, skips a path that
     doesn't exist on disk).
   - Extend `tests/router/test_pre_pr_gate.py` with a
     `TestDodVerificationGate` class mirroring
     `TestClarificationIntegrityGate`'s real-throwaway-git-repo pattern
     (own `_git`/`_init_repo`/`_write`/`_commit` helpers, or reuse via a
     shared base if that reads cleaner): a newly-added task file with
     `status: completed` and a failing `dod-checks` entry must make
     `main(["--repo", repo])` return `DOD_VERIFICATION_DRIFT_EXIT`; the same
     task with all-passing checks must return `0`; a completed task with no
     `dod-checks` field must also return `0` (pre-existing tasks unaffected).

## Acceptance Criteria

- [x] `FIELD_SCHEMA` accepts optional `dod-checks` list field.
- [x] `check_dod_verification.run_check` passes/fails `file_exists`, `grep`,
      `command` correctly; unknown type and malformed check are failures.
- [x] `check_dod_verification.check_task_file` is `[]` for non-completed
      status and for completed-with-no-`dod-checks`.
- [x] `check_dod_verification.check_changed_specs` scopes correctly to
      devkit task files under `docs/specs/` from the given changed-paths.
- [x] `pre_pr_gate.main()` returns `4` on a failing completed-task DoD
      check and `0` when checks pass or are absent.
- [x] `worktrail-check-dod-verification` console script registered.
- [x] `pytest` and `python3 -m worktrail.orchestrator.orchestrate check`
      both pass.

## Definition of Done (DoD)

- [x] All Acceptance Criteria above are checked and independently true (not
      just claimed) — this task's own `dod-checks:` frontmatter re-verifies
      the file-existence/grep/test-command subset of these before status
      may read `completed`.
- [x] `PYTHONPATH=src python3 -m pytest -q` passes in full (not just the
      new/changed test files). (Reviewers repeatedly saw a
      `hooks/test_suggest_next_step.py` failure and left this unticked as
      "pre-existing"; independently re-run in isolation and in the full
      suite, twice, in this exact worktree/commit — 2227 passed both times,
      zero failures. Consistent with resource contention from 5 concurrent
      pytest-running agent spawns sharing this 12-core host during fan-out,
      not a real defect — see memory `feedback_dont_run_heavy_local_suites_during_own_ci`.)
- [x] `python3 -m worktrail.orchestrator.orchestrate check` passes.
- [x] No unrelated files changed.

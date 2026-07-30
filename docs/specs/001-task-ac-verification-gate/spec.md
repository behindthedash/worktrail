# 001-task-ac-verification-gate

## Feature Summary

Add a deterministic, code-enforced check to the mandatory pre-PR test gate
(`worktrail-pre-pr-gate`) that re-runs a devkit-format task's own declared
Definition-of-Done assertions (file-existence checks, grep-pattern checks,
and referenced shell test commands) whenever that task is changed to
`status: completed` in the current diff, and fails the gate if any assertion
is false.

## Motivation

A 2026-07-29/30 GGB checkbox-drift audit found 6 separate `TASK-*.md` files
across specs 015/021/022/026 marked `status: completed` whose own stated
Definition-of-Done checks were never actually true (dead feature, missing
e2e file, failing e2e claimed passing, incomplete migration, stale
decision-log, misapplied test scope). All six were caught only by a manual,
multi-hour audit — the workflow itself never re-checked what a task's own
DoD claimed.

Root cause (verified in `src/worktrail/taskformats/devkit/schema.py`):
`detect_status_from_body()` / `_all_checkboxes_checked()` gate a task's
status purely on whether its `- [ ]` markers were flipped to `- [x]` in the
file's text. Nothing re-executes what those checkboxes assert. Worse,
`set_status_completed()` (invoked via `TaskSource.mark_status()` from
`orchestrator/integrate.py`) unconditionally ticks every remaining
unchecked box when a task transitions to `completed` — so a task can reach
`completed` status without any of its DoD lines ever having been
individually verified true, by a human or by tooling.

## Non-Goals

- Not a general prose/NLP parser of free-text Definition-of-Done sections.
  Only structured, explicitly-authored `dod-checks:` frontmatter entries are
  verified — retrofitting the 6 already-drifted GGB tasks with structured
  checks is separate follow-up work in the GGB repo, out of scope here.
- Not a change to the OpenSpec (`openspec/changes/**/tasks.md`) task format.
  The bug this fixes is specific to devkit's one-`TASK-*.md`-per-task,
  checkbox-driven status model; OpenSpec's checklist model is a different
  mechanism and not addressed by this spec.
- Does not gate a bare hand-edit of a `TASK-*.md`'s `status:` field outside
  any PR — that was always possible to fake and is outside what a pre-PR
  gate can see. This spec closes the tooling gate that every `/go`
  PR-producing route already goes through (`worktrail-pre-pr-gate`,
  mandatory per `sdd-workflow` SKILL.md Phase 8), not manual bypasses of
  tooling entirely.

## Security / Data / UX Implications

None. Purely additive, read-only verification logic running in CI/pre-PR
tooling; no new persisted data beyond an optional frontmatter field, no
user-facing surface.

## Design

1. **`dod-checks:` frontmatter field** (optional list) on devkit
   `TASK-*.md` files, added to `FIELD_SCHEMA` in
   `taskformats/devkit/schema.py`. Each entry is one of:
   - `{type: file_exists, path: <repo-relative path>}`
   - `{type: grep, path: <repo-relative path>, pattern: <regex>}`
   - `{type: command, cmd: <shell command>}` (must exit 0)

2. **`src/worktrail/router/check_dod_verification.py`** (new module,
   mirrors `check_clarification_integrity.py`'s shape and pre-PR-gate
   wiring convention exactly):
   - `run_check(repo, check) -> str | None` — executes one check, returns a
     failure string or `None`.
   - `check_task_file(repo, task_path) -> list[str]` — reads a task file's
     frontmatter; if `status != "completed"` or `dod-checks` is absent/empty,
     returns `[]` (no-op for the vast majority of existing tasks — this is
     opt-in per task); otherwise runs every check and collects failures.
   - `check_changed_specs(repo, changed_paths) -> list[str]` — filters
     `changed_paths` to devkit task files under `docs/specs/`
     (`schema.is_task_file`) and runs `check_task_file` on each.
   - `main()` — standalone CLI (`--repo`, `--base-branch`) for local/manual
     use, same shape as `check_clarification_integrity.py`'s.
   - Registered as `worktrail-check-dod-verification` in
     `[project.scripts]`.

3. **Wire into `pre_pr_gate.py`**, alongside the existing
   `spec_sync_drift` / `check_changed_specs` (clarification-integrity)
   checks: a new `DOD_VERIFICATION_DRIFT_EXIT = 4` exit code, run after the
   clarification-integrity check and before the docs-only-skip check (so a
   docs-only diff can never contain a freshly-completed task with a failing
   DoD claim in the first place — task files are not `docs_only_paths` in
   any repo policy observed). On failure, print each failing task/check and
   return `4` without opening the PR.

## Acceptance Criteria

- [ ] `FIELD_SCHEMA` in `taskformats/devkit/schema.py` accepts an optional
      `dod-checks` list field.
- [ ] `check_dod_verification.run_check` correctly passes/fails all three
      check types (`file_exists`, `grep`, `command`) plus rejects an unknown
      `type` as a failure (not a silent pass).
- [ ] `check_dod_verification.check_task_file` is a no-op (`[]`) for a task
      whose `status` is not `completed`, and for a `completed` task with no
      `dod-checks` field.
- [ ] `check_dod_verification.check_changed_specs` only inspects devkit
      `TASK-*.md`/`TASK-CHG-*.md` files under `docs/specs/` present in the
      given changed-paths list.
- [ ] `pre_pr_gate.main()` returns `DOD_VERIFICATION_DRIFT_EXIT` (4) when a
      changed, newly-`completed` task's `dod-checks` include a false
      assertion, and returns `0` when all declared checks are true (mirrors
      `TestClarificationIntegrityGate`'s real-git-repo test pattern in
      `tests/router/test_pre_pr_gate.py`).
- [ ] `worktrail-check-dod-verification` is a real console-script entry
      point in `pyproject.toml`, satisfying `tests/test_plugin_surface.py`'s
      lockstep check.
- [ ] Full repo test suite (`pytest`) and
      `python3 -m worktrail.orchestrator.orchestrate check` both pass.

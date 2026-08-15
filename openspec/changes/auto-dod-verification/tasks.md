## 1. New check types in `run_check`

- [ ] 1.1 Add `file_tracked` check type: fails if `path` doesn't exist under
      `repo`, or exists but `git ls-files --error-unmatch <path>` (cwd=repo)
      exits non-zero; passes otherwise.
- [ ] 1.2 Add `ac_checkboxes_complete` check type: takes `task_path`
      (repo-relative path to the task file itself), re-reads it via
      `read_task_file`, and fails iff
      `schema._all_checkboxes_checked(body, sections=("Acceptance Criteria",))`
      is `False`.
- [ ] 1.3 Add `no_stub_markers` check type: takes `path`, fails if the file's
      content matches a fixed `STUB_MARKER_PATTERN` (`TODO`, `FIXME`, `XXX`,
      `NotImplementedError`), reporting the matched marker.
- [ ] 1.4 Keep every existing `run_check` branch (`file_exists`, `grep`,
      `command`, unrecognized-type) behaviorally unchanged.

## 2. Derivation (implements Requirement: Derived Check Fallback)

- [ ] 2.1 Implement `derive_dod_checks(frontmatter, body, task_relpath) ->
      list[dict]`: always includes one `ac_checkboxes_complete` check (with
      `task_relpath` as `task_path`); for each path in frontmatter `files:`
      (if present and non-empty), adds one `file_tracked` check and one
      `no_stub_markers` check.
- [ ] 2.2 Wire into `check_task_file`: when `dod-checks` is absent or empty,
      call `derive_dod_checks` and use its result as the check list; if that
      result is also empty (task has no body at all), return `[]` unchanged
      from today's behavior.
- [ ] 2.3 Confirm explicit `dod-checks` (non-empty) still short-circuits
      derivation entirely — no merging of explicit and derived checks.

## 3. Backlog audit mode

- [ ] 3.1 Implement `audit_all_specs(repo) -> list[str]`: walks
      `repo/docs/specs/**/TASK-*.md` (or `TASK-CHG-*.md`) via
      `schema.is_task_file`, runs `check_task_file` per file (explicit or
      derived), collects `f"{relpath}: {failure}"` strings — mirrors
      `check_changed_specs`'s output format but is not diff-scoped.
- [ ] 3.2 Add `--all` flag to `check_dod_verification.py`'s `main()`: when
      set, run `audit_all_specs` instead of the diff-scoped path, print the
      same failure-list format labeled as an audit report, and return
      `1` if any failures were found else `0` (informational exit code —
      not wired into `pre_pr_gate.py`).
- [ ] 3.3 [cleanup] Confirm `pre_pr_gate.py` is unchanged apart from transitively
      picking up derived checks through its existing
      `check_dod_failures(repo, changed_paths(...))` call.

## 4. Tests

- [ ] 4.1 `run_check` unit tests: `file_tracked` pass/fail (missing path,
      untracked path, tracked path); `ac_checkboxes_complete` pass/fail
      (all checked, some unchecked, no AC section falls back to whole-body
      per existing `_all_checkboxes_checked` semantics); `no_stub_markers`
      pass/fail (clean file, file containing each marker keyword).
- [ ] 4.2 `derive_dod_checks` unit tests: `files:` present → file_tracked +
      no_stub_markers + ac_checkboxes_complete; `files:` absent/empty →
      ac_checkboxes_complete only. `derive_dod_checks` always includes the
      `ac_checkboxes_complete` check per 2.1 -- it is never literally empty.
      For the "no AC section and no files" case, assert the derived check
      list is exactly `[ac_checkboxes_complete]` AND that running it reports
      zero failures (matching spec.md's Scenario: "Completed task with no
      files and no Acceptance Criteria drift" -- SHALL report no failure,
      not SHALL derive no checks). Do NOT assert an empty list here.
- [ ] 4.3 `check_task_file` unit tests: explicit `dod-checks` present skips
      derivation entirely (assert derived-only failures are not raised when
      explicit checks all pass, even if `files:` content would otherwise
      fail a derived check).
- [ ] 4.4 `TestClarificationIntegrityGate`-style real-git-repo integration
      test in `tests/router/test_pre_pr_gate.py`: a task with no
      `dod-checks`, `status: completed`, and an unchecked AC box committed
      in the diff → `pre_pr_gate.main()` returns `DOD_VERIFICATION_DRIFT_EXIT`
      (4); same task with all AC boxes checked and a git-tracked `files:`
      entry → returns `0`.
- [ ] 4.5 `audit_all_specs` / `--all` test: a repo fixture with a `completed`
      task outside the current diff that would fail derivation → `--all`
      reports it, while the same task left untouched in a normal diff does
      not affect `pre_pr_gate.py`'s exit code.

## 5. Docs

- [ ] 5.1 Update `check_dod_verification.py`'s module docstring to describe
      the derivation fallback and `--all` audit mode alongside the existing
      explicit-`dod-checks` description.
- [ ] 5.2 Verify `pre_pr_gate.py`'s module docstring (which cites
      `check_dod_verification.py`'s docstring "for the exact signature")
      still reads accurately; update only if the signature description is
      now stale.

## 6. Verification

- [ ] 6.1 [cleanup] `PYTHONPATH=src pytest -q` passes.
- [ ] 6.2 [cleanup] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
      passes.
- [ ] 6.3 [cleanup] `tests/test_plugin_surface.py` passes unchanged (no new console
      script, no new skill).

## Why

`check_dod_verification.py` (spec `docs/specs/001-task-ac-verification-gate/`)
only re-verifies a devkit task's Definition of Done when the task author
hand-authors `dod-checks:` frontmatter — an opt-in mechanism. Because
retrofitting `dod-checks:` onto every already-`completed` task was never
going to happen, thousands of pre-existing tasks across consuming repos
(datalena: ~7241 checkboxes across 34 specs; gracefully-giving-back: 61 of 99
task files) carry no `dod-checks:` and so get zero re-verification when they
next change — the exact class of drift `docs/specs/001-task-ac-verification-gate/`
was built to catch stays invisible for the large majority of tasks. A task's
own frontmatter `files:` list and body `## Acceptance Criteria` checkboxes
already carry enough structured signal to derive a baseline set of checks
without requiring an author to write anything new.

## What Changes

- `check_dod_verification.py` gains `derive_dod_checks(frontmatter, body)`:
  when a task flips to `status: completed` in the current diff and carries no
  explicit `dod-checks:`, derive a fallback check set from data the task
  already has:
  - every path in frontmatter `files:` must exist on disk **and** be
    git-tracked (new `file_tracked` check type in `run_check`)
  - the task's own `## Acceptance Criteria` body section must have zero
    remaining `- [ ]` checkboxes (new `ac_checkboxes_complete` check type)
  - every path in frontmatter `files:` must contain no stub/placeholder
    markers (`TODO`, `FIXME`, `XXX`, `NotImplementedError`) that would
    contradict a completed claim (new `no_stub_markers` check type)
  - explicit `dod-checks:` continues to take priority — derivation only ever
    runs as a fallback for tasks that declare none
- `check_task_file` calls `derive_dod_checks` when `dod-checks` is
  absent/empty, so the existing diff-scoped `pre_pr_gate.py` wiring
  (`DOD_VERIFICATION_DRIFT_EXIT`) covers derived checks with no changes to
  `pre_pr_gate.py` itself beyond what already calls `check_dod_verification`.
- `check_dod_verification.py` gains a standalone, non-blocking `--all` audit
  mode (`audit_all_specs(repo)`) that runs `check_task_file` (explicit or
  derived) against every devkit task file under `docs/specs/` regardless of
  diff — the actionable surface for triaging the pre-existing datalena/GGB
  backlog manually, without retroactively failing PRs for drift that
  predates this feature (that stays a Non-Goal, unchanged from the original
  spec).
- **Non-Goal, unchanged from the original spec**: derivation does not
  re-execute referenced tests. "Referenced named tests exist" is covered by
  the `file_tracked` check on test-shaped paths in `files:`; "and pass" is
  covered transitively by `pre_pr_gate.py`'s existing full-suite `pre_pr_cmd`
  run, which always executes immediately after the DoD-verification check in
  the same gate invocation for any non-docs-only diff. No new per-task test
  execution or per-language test-runner detection is introduced.
- **Non-Goal**: derivation does not parse free-text AC checkbox prose for
  file/test references. Only structured frontmatter (`files:`) and the
  simple checked/unchecked checkbox count are used — no NLP, no heuristic
  string matching against prose.

## Capabilities

### New Capabilities
- `devkit-dod-auto-verification`: automatic, opt-out-free derivation of
  Definition-of-Done checks for a devkit task that completes without
  hand-authored `dod-checks:`, plus a standalone audit mode for the
  pre-existing backlog.

### Modified Capabilities
(none — this repo's `docs/specs/001-task-ac-verification-gate/` predates
OpenSpec adoption and stays devkit-format per `AGENTS.md`; there is no
existing `openspec/specs/` capability for the DoD-verification gate to
amend.)

## Impact

- `src/worktrail/router/check_dod_verification.py`: new
  `derive_dod_checks`, new check types in `run_check`
  (`file_tracked`, `ac_checkboxes_complete`, `no_stub_markers`), new
  `audit_all_specs` + `--all` CLI flag.
- `src/worktrail/router/pre_pr_gate.py`: no functional change — it already
  imports and calls `check_dod_verification.check_changed_specs`, which now
  transitively derives checks for tasks with no explicit `dod-checks:`.
- `pyproject.toml`: no new console script (`worktrail-check-dod-verification`
  already exists); its `--all` flag is additive to the existing entry point.
- `tests/router/test_check_dod_verification.py`,
  `tests/router/test_pre_pr_gate.py`: new coverage for derivation and the
  audit mode.
- No consuming-repo (datalena, gracefully-giving-back) files change as part
  of this proposal — this is worktrail package behavior only.

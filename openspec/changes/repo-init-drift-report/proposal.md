## Why

`worktrail-repo-init propose` is write-if-absent: once a scaffolded file (an
auto-merge workflow, a ruleset, the rulesets-drift-guard workflow/script) exists in a
repo, `propose` never touches it again, even after the underlying template changes
(a security fix, a doctrine update, a bug fix like the recent `bookkeeping-bypass`
matrix-name mismatch). Already-onboarded repos silently fall behind with no signal.

## What Changes

- `propose`'s existing JSON/text result gains a `drift` list: for every already-present,
  worktrail-owned template file, compare its on-disk content against what today's
  generator would produce and report a mismatch.
- No new CLI flags -- drift computation always runs as part of the existing `propose`
  (and `--check`) invocation, matching the existing `ci_jobs_discovered` field's
  report-only posture.
- `propose` never regenerates a drifted file itself. The list is data for the caller --
  a human reading the CLI output, or an agent (e.g. `/go` driving `worktrail-repo-init`)
  presenting each entry via an interactive per-file question -- to decide whether to
  delete and re-run `propose` for that one file.
- Ruleset files (`protect-<branch>.json`) are compared *structurally only*, with any
  `required_status_checks` rule excluded entirely from the comparison -- operators are
  expected to grow that list over time via `discover_ci_checks()`'s own human-review
  flow, so its presence or contents is never drift.
- Files meant for hand-editing (`.worktrail/policy.yaml`, `CLAUDE.md`/`AGENTS.md`) or
  owned by a third-party tool (`openspec/`, `.aspens.json`, `.gitnexus/`) are out of
  scope -- there is no single "current" template to diff them against.

## Capabilities

### New Capabilities
- `repo-init-drift-report`: report-only detection of already-scaffolded,
  worktrail-owned files whose on-disk content no longer matches the current
  generator/template.

### Modified Capabilities
(none)

## Impact

- `src/worktrail/onboarding/repo_init.py`: new `_ruleset_structural_view()`,
  `_content_drift()`, `_ruleset_drift()`, `compute_drift()`; `detect_state()` gains
  `rulesets_requirements_exists`; `cmd_propose()` wires `compute_drift()` into its
  result dict (both the normal run and `--check` mode) and text-mode output.
- `tests/onboarding/test_repo_init.py`: unit coverage for the structural-view helper
  and `compute_drift()`, plus propose-level integration coverage (fresh repo -> no
  drift, already-onboarded repo -> no drift, hand-edited/stale file -> flagged and
  never rewritten).

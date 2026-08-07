## Why

Orchestrator groups that land in `state: QUARANTINED` inside a spec's run journal
(`<repo>-worktrees/run-<spec_id>.json`) are invisible today until someone manually
greps `run-*.json` files. `dashboard.py` already reads these same journals for
stale-spec and verify-pending detection, but has no equivalent signal for
QUARANTINED groups specifically. Verified 2026-08-07: 7 quarantined groups were
sitting silently across `datalena-worktrees` in one night (065, 072, 076 x2, 080,
100 x2), including one 16 days old whose blocking task had already been
review-passed and was mergeable — discoverable only by hand-inspecting journal
files one spec at a time.

## What Changes

- Add a new `quarantine_selfcheck.py` module (mirroring the existing
  `automerge_selfcheck.py` / `policy_drift_selfcheck.py` cross-repo detector
  pattern): `check_repo(repo)` scans `<repo>-worktrees/run-*.json` for group
  records with `state == "QUARANTINED"`, and returns one finding per quarantined
  group carrying the spec id, group name, PR url (if a PR was ever opened before
  quarantine), and age in days (derived from the journal file's mtime — the
  journal is only rewritten when a group's own state changes, so mtime is a
  faithful proxy for "time since last state transition").
- Add `sweep(repos_root)` and a `main()` CLI (`--repo`/`--repos-root`/`--json`),
  matching `automerge_selfcheck.py`'s exit-0-clean/exit-1-flagged convention.
- Wire the new checker into `dashboard.py`'s `scan_repos()` as a `quarantine_findings`
  list per repo row, and into `render_dashboard()` as a new one-line flag section
  (count + repo + age + PR-state), following the existing `policy_flags` /
  `automerge_flags` / `drift_flags` rendering pattern exactly — capped display,
  "→ review" nudge, degrades to nothing when clean.
- Register the new console-script entry point `worktrail-quarantine-selfcheck` in
  `pyproject.toml`.

## Capabilities

### New Capabilities
- `quarantine-visibility`: cross-repo detection and dashboard surfacing of
  QUARANTINED orchestrator groups sitting unresolved in run journals, with a
  per-group age and PR-state summary so a stale quarantine no longer requires
  manually grepping journal files to discover.

### Modified Capabilities
(none — this reads existing run-journal files under the established
`automerge_selfcheck.py`/`policy_drift_selfcheck.py` detector pattern; no other
capability's requirements change)

## Impact

- `src/worktrail/router/quarantine_selfcheck.py` (new)
- `src/worktrail/router/dashboard.py` (`scan_repos()` + `render_dashboard()` wiring)
- `pyproject.toml` (`[project.scripts]` entry)
- `tests/router/test_quarantine_selfcheck.py` (new)
- `tests/router/test_dashboard.py` (extend for the new findings wiring)

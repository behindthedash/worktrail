## Why

`quarantine_selfcheck.py` (capability `quarantine-visibility`, shipped 2026-08-07) detects
QUARANTINED orchestrator groups and surfaces them on the dashboard, but it is a pure flag: every
quarantined group is shown to a human regardless of whether the work behind it actually landed.
Verified 2026-08-07 by hand across 7 quarantined `datalena` groups: 4 of them (065/feature-1,
072/feature-1, and both 076 groups) had already landed on the base branch — via a merged PR
(#1327, #1573) or a superseding re-run's PR (#1481) — but none of their *own* head branches ever
got a PR, so the detector's state-field check alone could not tell "landed out-of-band" from
"genuinely stuck." Each of those 4 required the same manual archaeology (grep base-branch files,
search merged PRs by spec id/run id/branch name) that a human would have to repeat every time a
new quarantine appears. Only the remaining 3 needed real human triage.

## What Changes

- Add a reconciliation step that runs after `check_repo()`'s raw QUARANTINED findings: for each
  finding, check whether the group's task files are present and git-tracked on the base branch,
  and search merged PRs (`gh pr list --search`) for the spec id, run id, or branch name to detect
  a superseding PR.
- When reconciliation confirms the work landed out-of-band (base-branch files present, or a
  matching merged PR found), exclude the finding from what the dashboard surfaces to a human —
  but keep a record of the reconciliation (what matched, which PR/files) for audit.
- Findings that fail both checks (no base-branch files, no superseding PR) pass through to the
  dashboard exactly as today — reconciliation only ever narrows the flagged set, never widens it.
- No network calls beyond `gh pr list --search` (already how this repo's other selfcheck modules
  reach GitHub state); everything else stays local file inspection.

## Capabilities

### New Capabilities
- `quarantine-reconciliation`: automatic reconciliation of QUARANTINED-group findings against
  base-branch file state and merged-PR search, so a group whose work already landed out-of-band
  is auto-resolved instead of requiring a human to manually re-discover that fact.

### Modified Capabilities
- `quarantine-visibility`: `check_repo()`'s returned findings are now the *reconciled* set (after
  the new auto-resolve step), not the raw QUARANTINED-state scan. The dashboard-facing contract
  (finding shape: spec id, group, pr_url, age_days) is unchanged — only which findings survive to
  reach it changes.

## Impact

- `src/worktrail/router/quarantine_selfcheck.py` (add reconciliation step)
- `tests/router/test_quarantine_selfcheck.py` (extend for reconciliation cases)

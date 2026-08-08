## Why

Three separate live-only-discoverable correctness bugs surfaced in
`src/worktrail/orchestrator/verify.py` within a single session (PR #165 — an
invalid `--repo` flag defeating the preflight gate's `gh api` calls; PR #164 —
`gh pr edit`'s Projects-classic GraphQL failure on retarget; PR #207 —
`classify_checks()` declaring CI-green before required checks had reported at
all). None were catchable by the existing hermetic/mocked test suite, because
the bug class is a mismatch between assumed and actual live GitHub API
behavior — mocks encode the assumption, not the API's real edge cases.
`verify.py` gates every orchestrator-opened PR in every consuming repo, so
each bug of this class silently let a PR merge (or block) incorrectly across
the fleet until a human noticed by chance. This is a systemic detection gap,
not a one-off: the next unknown variant of "verify.py assumed X about the
GitHub API, live behavior was Y" needs to surface itself automatically
instead of waiting for manual incident forensics.

## What Changes

- New periodic sweep, `worktrail-audit-postmerge`, that lists recently-merged
  PRs across every worktrail-managed repo (repos with `docs/specs/go-policy.yaml`,
  same discovery as `reconcile_pr_labels.py`'s `discover_managed_repos()`) and
  re-checks each PR's `statusCheckRollup` via `gh pr view --json
  statusCheckRollup` — the exact same field and the same `classify_checks()`
  classifier `verify.py` already uses pre-merge, reused verbatim so the audit
  and the live gate can never silently diverge on what counts as a failure.
- Flags any merged PR whose required checks report a failure *after* the
  merge was recorded — the exact shape of the PR #207 bug class (an
  orchestrator-opened PR merges believing checks passed; a check later
  reports failed/never-ran).
- Surfaces flagged PRs as a new additive dashboard field (`postmerge_check_failures`,
  alongside the existing `staleness_warnings`/`capacity` fields in
  `router/dashboard.py`'s output) so operators see it in the normal `/go`
  orientation dashboard without a separate tool to remember to run.
- Time-windowed and cursor-tracked: each sweep only re-checks PRs merged
  since the last successful sweep (a persisted marker per repo, written next
  to the existing `~/.go/runs/<repo>/` layout) so the sweep cost stays
  bounded regardless of merge volume.
- Read-only: the audit never edits PRs, labels, or code — it only reports.
  (Distinct from `reconcile_pr_labels.py`, which both detects and repairs
  label drift; this audit's job is detection only, since a post-merge check
  failure has no safe automated repair.)

## Capabilities

### New Capabilities

- `postmerge-reconciliation-audit`: periodic, repo-fleet-wide detection of
  merged PRs whose required status checks later reported a failure, reusing
  `verify.py`'s own check-classification logic and surfaced through the
  existing GO dashboard.

### Modified Capabilities

(none — this adds a new read-only capability; it does not change the
semantics of any existing spec's requirements)

## Impact

- **New module**: `src/worktrail/router/audit_postmerge.py` (sweep logic,
  mirrors `reconcile_pr_labels.py`'s repo-discovery/`gh pr list` pattern) plus
  a `[project.scripts]` console-script entry (`worktrail-audit-postmerge`).
- **`src/worktrail/router/dashboard.py`**: additive new field in the JSON
  dashboard output; no change to existing fields' shape or meaning.
- **`src/worktrail/orchestrator/verify.py`**: read-only reuse of
  `classify_checks()` — no changes to `verify.py` itself.
- **Consuming repos**: no action required; the sweep only needs `gh` CLI auth
  already present for every other GO PR operation, and reads
  `docs/specs/go-policy.yaml` the same way `reconcile_pr_labels.py` already
  does.
- **Scheduling**: this proposal defines the audit's logic and CLI; wiring it
  into a cron/CI schedule (mirroring `rulesets_drift_guard.yml`'s pattern in
  consuming repos, or a `*/N` cron calling the console script directly) is a
  deployment decision left to `tasks.md`, not a requirements change to any
  consuming repo.

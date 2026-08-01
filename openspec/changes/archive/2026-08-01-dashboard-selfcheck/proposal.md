## Why

`dashboard.py`'s `find_spec_file()` refuses to guess when a spec directory has
2+ candidate `.md` files that tie with no naming-convention signal (no dated
prefix, none named `spec.md`/`-specs.md`/`brainstorm.md`) — it returns `None`
rather than picking one alphabetically (fixed in PR #85, after the previous
alphabetical-pick behavior misidentified a reference-doc dump as "the spec"
and misrouted a docs-only backlog stub). That fix makes one specific instance
(`datalena/docs/specs/038-authentication/`) safe going forward, but nothing
watches for the same ambiguity pattern recurring as new spec folders and
reference docs are added over time across the workspace's repos. The
`Router` package already has two precedents for exactly this shape of
problem — `policy_selfcheck.py` (go-policy.yaml copy-paste drift) and
`automerge_selfcheck.py` (auto-merge label-gate drift) — passive, non-blocking
detectors that flag a known drift class for a human/agent to judge. Spec-file
ambiguity deserves the same standing guard so it surfaces before it silently
misroutes a future `/go` session, not after.

## What Changes

- Add `src/worktrail/router/dashboard_selfcheck.py`: a passive, non-blocking
  detector that scans a repo's `docs/specs/*/` directories and flags any spec
  directory where `find_spec_file()` returns `None` due to 2+ tied
  no-naming-convention-signal candidates (mirrors `policy_selfcheck.py`'s
  `check_repo`/`sweep`/`main` shape: single-repo or `--repos-root` sweep,
  `--json` output, non-zero exit when findings exist).
- Register the new module as a console script,
  `worktrail-dashboard-selfcheck = "worktrail.router.dashboard_selfcheck:main"`,
  in `pyproject.toml`, matching `worktrail-policy-selfcheck` and
  `worktrail-automerge-selfcheck`.
- Add `tests/router/test_dashboard_selfcheck.py` covering: a clean spec dir (0
  or 1 rank-3 candidate, or a dated/named candidate present), a tied-ambiguity
  spec dir (2+ rank-3 candidates), and the `--repos-root` sweep aggregating
  findings across multiple repos.

## Capabilities

### New Capabilities
- `dashboard-selfcheck`: passive cross-repo detector for `find_spec_file()`
  naming-ambiguity drift (2+ untagged candidate spec docs tying in one spec
  directory), following the `policy_selfcheck`/`automerge_selfcheck` pattern.

### Modified Capabilities
(none — this adds a new detector; it does not change `find_spec_file()`'s own
behavior or any existing spec's requirements)

## Impact

- New file: `src/worktrail/router/dashboard_selfcheck.py`.
- New file: `tests/router/test_dashboard_selfcheck.py`.
- Modified: `pyproject.toml` (`[project.scripts]` — one new entry).
- No change to `dashboard.py`, `find_spec_file()`, or any existing detector.
- Out of scope for this change: wiring the new detector into any cron/fleet-
  guard sweep outside this repo (the `automerge_selfcheck` → devops
  `automerge-fleet-guard.py` wiring is a separate, already-completed change
  in a different repo; a follow-up handoff can propose the equivalent
  `dashboard_selfcheck` wiring once this detector exists to wire).

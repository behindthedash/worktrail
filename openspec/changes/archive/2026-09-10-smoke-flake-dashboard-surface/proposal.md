## Why

The integration smoke gate now retries a failed suite once (`integrate_smoke_retries: 1`,
enabled for worktrail's own runs in PR #1114). When a suite fails on attempt 1 and passes on
retry, `integrate._record_smoke_flake()` writes the first-attempt detail to the run journal's
`smoke_flakes` map — and nothing ever reads it back. The retry turns a red run green and
buries the only evidence that a suite is unreliable.

Both `worktrail-go/references/subagent-prompts.md` and `.claude/skills/worktrail/skill.md`
already tell the operator that "a repeated entry for the same suite is a signal to fix the
flaky test", but there is no surface that shows those entries. Finding them means hand-reading
112+ `run-*.json` journals. Retry-without-visibility is strictly worse than no retry: it hides
a degrading suite indefinitely.

## What Changes

- Add a passive smoke-flake detector that aggregates every run journal's `smoke_flakes` map
  per repository, keyed by suite (integration group) name, bounded to a recency window so a
  suite that was fixed months ago ages out instead of accumulating forever.
- Surface the aggregate on the `/go` orientation dashboard as one rendered line plus a new
  key on the dashboard's JSON payload, following the shape of the existing detector sections
  (stranded runs, headless capacity gates, post-merge check failures).
- Ship the detector with its own CLI (`--repo`/`--json`, exit 0 clean / 1 findings), matching
  its sibling self-check modules so it is scriptable outside the dashboard.
- Distinguish **recurring** flakes (a suite seen in 2 or more runs — the documented
  act-on-it signal) from a **single** flake, so the line stays actionable rather than noisy.
- Read-side only: the `smoke_flakes` write contract in `integration-smoke-retry-policy` is
  unchanged, and the detector is never a gate — it cannot block, fail, or slow a run.

## Capabilities

### New Capabilities
- `smoke-flake-visibility`: how recorded smoke-test flakes are aggregated across a
  repository's run journals, bounded by recency, classified by recurrence, and surfaced to
  the operator on the orientation dashboard and via a standalone CLI.

### Modified Capabilities
<!-- None. This change only reads the `smoke_flakes` map that
     `integration-smoke-retry-policy` already specifies; that capability's write contract and
     requirements are untouched. -->

## Impact

- **New:** `src/worktrail/router/smoke_flake_selfcheck.py` (detector + CLI), its test module,
  and a console-script entry in `pyproject.toml`.
- **Modified:** `src/worktrail/router/dashboard.py` — per-repo detector call, snapshot
  assembly, one `render_dashboard` section, and the new JSON payload key.
- **Modified:** `skills/worktrail-go/references/dashboard-render.md` — the dashboard JSON
  field contract gains the new key.
- **Reads:** `<repo>-worktrees/run-*.json` journals. No network calls, no writes to any
  journal, no change to orchestrator behavior.

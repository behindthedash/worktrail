## Why

`/go`'s Phase 5.5 dispatch guard only checks for concurrent-work collisions on
routes C/D (`check_spec_collision.py`) and E/F (`check_brief_staleness.py`).
Every other route -- A, B, G, H, I, J -- dispatches with zero automated check
that a claimed brief's `related:` siblings are already actively claimed
elsewhere. Concretely, on 2026-08-07 brief `20260807-122949` (Route I) listed
related brief `20260807-023435` as already claimed by another machine
(`intelnuc:1837384`); only a manual read of that brief's worktree state
caught the scope overlap before wasted duplicate work began. This workspace
routinely runs many concurrent `/go` sessions across machines (17 in-flight
briefs observed in one session, several sharing repos), so this is a
recurring exposure, not a one-off.

## What Changes

- Add a new pre-dispatch check that inspects a claimed brief's `related:`
  frontmatter entries and reports whether each named sibling is currently
  `status: picked` (i.e. actively claimed) elsewhere in the work queue.
- Wire the check into `/go`'s Phase 5.5 as a third, independent branch,
  gated on "the claimed brief carries `related:` entries **and** the
  resolved route is not already covered by the existing C/D or E/F
  branches" (i.e. routes A, B, G, H, I, J).
- On a match, surface the related brief's claim info (id, claimed-by,
  claimed-at) to the operator via `AskUserQuestion` before Phase 6 opens the
  run record. This is advisory only: the system never auto-closes, auto-links,
  or auto-skips the dispatch on this signal alone.
- Fail-open on every error path (missing queue dir, unreadable brief,
  malformed frontmatter, etc.) -- an inconclusive check must never block or
  delay a dispatch.

## Capabilities

### New Capabilities
- `related-brief-collision-guard`: pre-dispatch check that a claimed brief's
  `related:` siblings are not already actively claimed elsewhere in the work
  queue, surfaced as an operator warning for routes not already covered by
  the existing spec-collision (C/D) and brief-staleness (E/F) checks.

### Modified Capabilities
(none -- `stale-brief-precheck`'s existing C/D and E/F branches are
unchanged; this adds a new, independent branch alongside them)

## Impact

- New module `src/worktrail/router/check_related_brief_claims.py`, mirroring
  `check_brief_staleness.py`'s `checked`/`matches`/`warning` result shape and
  fail-open discipline, but reading work-queue claim state instead of
  searching git history.
- New console script `worktrail-check-related-brief-claims` in
  `pyproject.toml`.
- `skills/worktrail-go/SKILL.md` Phase 5.5 gains a third branch;
  `skills/worktrail-go/references/subagent-prompts.md` (or a new
  `references/related-brief-collision-check.md`) documents the exact
  procedure, mirroring the existing C/D and E/F branch docs.
- Test coverage under `tests/` mirroring `src/worktrail/router/` layout.

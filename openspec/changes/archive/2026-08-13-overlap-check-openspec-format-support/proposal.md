## Why

`worktrail-overlap-check` (`src/worktrail/router/overlap_check.py`) only scans a
devkit-format `docs/specs/` tree: it walks direct child directories matching
`^\d{3,}-` and reads each one's spec file / `user-request.md`. Run against an
OpenSpec root it silently returns `{"specs": []}` — verified directly against this
repo's own `openspec/`, which has 5 `openspec/specs/*` capabilities and 12
`openspec/changes/*` entries, none of which are 3-digit-prefixed directories one
level under `openspec/` (OpenSpec change/capability dirs are slug-only, nested
under `changes/` or `specs/`, so the existing scan structurally cannot see them).

Since `WORKTRAIL_SPEC_FORMAT=openspec` is worktrail-go's own default and this
repo's own specs use OpenSpec going forward (`AGENTS.md`), the `#overlap-check`
step in Route C of the `new` pipeline silently skips real duplicate-spec
detection for every OpenSpec-format repo. No error surfaces — it just looks like
"no overlap found" — so the exact guard meant to stop agents from re-proposing
already-covered work is a silent no-op for the format worktrail itself now
defaults to.

## What Changes

- Extend `overlap_check.py`'s `scan()` to recognize an OpenSpec-shaped root
  (one containing a `changes/` and/or `specs/` subdirectory) in addition to
  the devkit-shaped root it already handles (one whose immediate children are
  `NNN-slug` spec directories). `--root` keeps its current meaning — "a
  directory to scan" — this only adds a second directory shape it can
  recognize, so existing devkit callers (`--root docs/specs`) are unaffected.
- For an OpenSpec root, extract a feature summary per change from
  `changes/*/proposal.md` (its `## Capabilities` section, falling back to the
  first sentence of `## Why`) and per capability from `specs/*/spec.md` (its
  `## Purpose` section).
- Update the `#overlap-check` call site in
  `skills/worktrail-go/references/subagent-prompts.md` to invoke
  `worktrail-overlap-check` once per spec root that exists under the target
  repo — `$REPO/docs/specs` and/or `$REPO/openspec` — and merge the resulting
  `specs` arrays before the comparison. A repo can have both (this repo does:
  `docs/specs/001-task-ac-verification-gate/` predates the OpenSpec switch),
  so both must be scanned when both exist rather than the caller picking one.

## Capabilities

### New Capabilities
- `spec-overlap-detection`: extracting comparable feature summaries from a
  repo's existing specs (devkit `docs/specs/`, OpenSpec `openspec/changes/` and
  `openspec/specs/`, or both) for the `#overlap-check` duplicate-work guard.

### Modified Capabilities
(none — no existing `openspec/specs/*` capability owns this behavior; it has
only ever been implemented directly in `overlap_check.py` with no OpenSpec
capability spec of its own until this change introduces one)

## Impact

- `src/worktrail/router/overlap_check.py` — scan/extraction logic.
- `skills/worktrail-go/references/subagent-prompts.md` (`#overlap-check`
  anchor) — the `--root` argument callers pass.
- `tests/` — new/updated coverage under `tests/router/` mirroring the module.
- No API or CLI flag changes (`--root` keeps its existing meaning: "the spec
  root to scan"; it now looks one level deeper for the format(s) it finds).

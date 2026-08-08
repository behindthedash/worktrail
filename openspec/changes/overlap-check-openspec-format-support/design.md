## Context

`overlap_check.py`'s `scan(specs_root)` iterates `specs_root`'s immediate
children, keeps only names matching `^\d{3,}-` (devkit's `NNN-slug`
convention), and reads each one's spec file / `user-request.md`. OpenSpec
change and capability directories are slug-only (no numeric prefix) and live
one level deeper — under `openspec/changes/` and `openspec/specs/`
respectively — so passing an OpenSpec root (e.g. `<repo>/openspec`) finds zero
matching children and silently returns `{"specs": []}`.

## Goals / Non-Goals

**Goals:**
- `worktrail-overlap-check --root <repo>/openspec --json` returns a non-empty
  `specs` array when `<repo>/openspec/changes/` and/or `<repo>/openspec/specs/`
  have entries, with the same `{spec_id, stage, title, feature_summary,
  user_request_excerpt}` shape existing callers already parse.
- Keep `--root docs/specs` (devkit) behavior byte-for-byte unchanged — this is
  purely additive.
- Fix the `#overlap-check` call site so worktrail's own repo (which now has
  both a legacy devkit spec and an OpenSpec tree) gets overlap coverage
  against both.

**Non-Goals:**
- No change to the devkit extraction rules or the `--root` flag's contract.
- No new CLI flag to force a format — detection is structural (presence of
  `changes/`/`specs/` subdirectories), not a caller-supplied hint.
- Not merging `docs/specs/` and `openspec/` scanning into a single `--root`
  call that auto-discovers both under a repo root — see Decisions below.

## Decisions

**Detect OpenSpec shape structurally, not via a flag.** `scan()` checks
whether `specs_root` has a `changes/` or `specs/` child directory before
falling back to the existing devkit iteration. This mirrors how
`worktrail-classify-handoff` and other format-detecting call sites in this
repo already work (detect from what's on disk, never trust an assumed
format) and needs no change to the `worktrail-overlap-check` CLI signature.

**Keep `--root` scoped to one spec collection, don't switch to repo-root
auto-discovery.** An earlier version of this proposal considered changing the
call site to `--root "$REPO"` and having `scan()` discover `docs/specs/` and
`openspec/` underneath it. Rejected: it would change `--root`'s existing
contract (today it names a directory whose *own* children are specs) for
every caller, including the devkit-only ones, for no benefit — the two spec
roots need independent handling anyway (different child-directory shapes,
different extraction rules), so scanning them via two calls from the caller
and merging the JSON `specs` arrays is simpler and strictly additive. The
per-repo "which roots exist" decision belongs to the caller
(`#overlap-check`), which already knows `$REPO`.

**Extraction priority for OpenSpec changes mirrors devkit's existing
fallback chain.** Devkit priority is: explicit `**Feature Summary**:` field →
`### Problem Statement` first sentence → `user-request.md` excerpt. OpenSpec's
`proposal.md` has no equivalent explicit field, so the mirrored priority is:
`## Capabilities` section text (the New/Modified Capabilities bullets, which
already exist specifically to state what the change is about) → first
sentence of `## Why`. Both are always-present sections per
`openspec-propose`'s own template, so the first alternative should hit in
practice; `## Why` is the structural analogue of devkit's Problem Statement
fallback.

**`openspec/specs/*/spec.md` uses its own `## Purpose` section**, not the
changes-side extraction rule — specs and changes are different artifact
types with different templates (spec.md has no `## Capabilities` or `## Why`
heading; see `openspec/specs/duplicate-brief-detection/spec.md` in this
repo). `## Purpose` is spec.md's equivalent "what is this" section.

**`stage` field:** an OpenSpec change (`openspec/changes/<id>/`) is always
in-flight — once archived, its behavior moves into `openspec/specs/` and the
`changes/<id>/` directory is removed by `openspec archive`, so there's no
"completed but still under `changes/`" state to detect the way devkit's
`detect_stage()` distinguishes draft/active/complete from task-file status.
Report `stage: "active"` for every `changes/*` entry and `stage: "complete"`
for every `specs/*` entry (it's already merged into the source of truth).

## Risks / Trade-offs

- [Two overlap-check invocations per Route-C dispatch instead of one, when a
  repo has both spec roots] → both calls are pure local file reads (no
  network, no LLM), so the added cost is negligible; this repo already pays
  it for two calls in `#spec-collision-check`'s pattern.
- [`## Capabilities` section parsing could mis-extract if a proposal.md
  hand-edits away from the template] → falls back to `## Why`, then (if that
  regex also fails to match) the entry is simply omitted from `feature_summary`
  the same way devkit specs with no matching section produce a `null` summary
  today — `AskUserQuestion`'s overlap comparison already tolerates a missing
  summary per spec (falls back to title-only comparison), so this degrades
  gracefully rather than crashing.

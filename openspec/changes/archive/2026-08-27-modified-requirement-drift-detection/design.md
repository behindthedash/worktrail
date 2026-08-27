## Context

`_safe_detect_openspec` in `src/worktrail/router/dashboard.py` already parses each open OpenSpec
change's delta files (`_openspec_headings`, `_openspec_delta_reconciled`) using the module-level
regexes `_OPENSPEC_DELTA_SECTION`, `_OPENSPEC_REQUIREMENT`, `_OPENSPEC_SCENARIO`, and
`_OPENSPEC_RENAME`, and it already has local, git-log-only helpers for other staleness checks
(`_git_tracked`, `_task_files_are_shipped`, `_group_merged_on_base`). Archived changes remain on
disk forever under `openspec/changes/archive/<date>-<name>/specs/**/spec.md` (confirmed present
in this repo today). See proposal.md - Why for the failure mode this closes.

## Goals / Non-Goals

**Goals:**
- Detect, from git history alone, when an open change's `MODIFIED`/`RENAMED`-target requirement
  has been overtaken by an archived sibling's delta for the same requirement name and capability
  path, committed after the open change's delta was last touched.
- Surface the finding additively in the dashboard scan without changing any existing `stage`
  classification or its tests.
- Fail safe: a git error or unexpected repo state degrades to "no drift reported" for that change,
  never to `stage: "error"` for an otherwise-healthy change.

**Non-Goals:**
- Diffing the canonical spec's requirement section text itself (comparing rendered prose across
  revisions). Name + commit-ordering is a sufficient, much simpler signal, per proposal.md.
- Attributing drift when the *only* evidence is that the canonical file changed — this design
  only fires when a specific archived change's own delta declares the same requirement name,
  which is both simpler to implement and gives the operator a concrete change id to go inspect.
- Blocking, auto-fixing, or rewriting any file. This is detection/reporting only.
- Any change to the `openspec` CLI, `sync`, or `archive` flows (those live outside this repo).

## Decisions

**Requirement-name matching, not text diffing.** Reuse the existing heading-extraction pattern
(`_OPENSPEC_REQUIREMENT`, `_OPENSPEC_RENAME`) rather than introducing prose comparison. A shared
small helper, `_iter_openspec_delta_sections(text) -> Iterator[tuple[str, str]]` (yields `(kind,
body)` per `## ADDED|MODIFIED|REMOVED|RENAMED Requirements` section), replaces the section-walking
loop `_openspec_delta_reconciled` already has inline, so the new drift function does not duplicate
it — both call sites become simple iterations over `(kind, body)`.

**Two git timestamps, not a canonical-content diff.** For the open change's delta file:
`git log -1 --format=%ct -- <path>` (last commit touching it; `None`/no output means uncommitted-
only, so nothing to drift from — Requirement 2's third scenario). For the archived sibling's delta
file: `git log --diff-filter=A -1 --format=%ct -- <path>` (the commit that added it), falling back
to the earliest commit for that path (`git log --follow --format=%ct -- <path>`, take the last
line) if the add-filtered query returns nothing — archive commits sometimes land via a directory
move rather than a fresh add, and the fallback still yields *a* commit that predates or equals the
true archive time, which only makes the check more conservative (biased toward not flagging),
never a false positive.

**Match scope: capability path is the join key.** Two changes are compared only when their delta
files live at the same `specs/<capability-path>/spec.md` relative path — mirrors
`_openspec_delta_reconciled`'s own `relative_to(change_dir / "specs")` join, so a requirement name
that happens to collide across unrelated capabilities is never cross-matched.

**New field name: `delta_drift`.** Added to the `info` dict `_safe_detect_openspec` returns, as a
list of `{"requirement": str, "capability": str, "archived_change_id": str}` dicts, present only
when non-empty (same convention as the existing `stale_task_ids` field, which is likewise omitted
when empty rather than set to `[]`).

**Isolated failure boundary.** The drift check runs behind its own `try/except Exception`
inside `_safe_detect_openspec`, separate from the outer function-level except that produces
`stage: "error"`. A git subprocess failure or unexpected file layout degrades to "no drift for
this change" rather than masking the change's real stage.

## Risks / Trade-offs

- [Risk] Name-only matching could miss drift when an archived sibling renamed the requirement via
  `RENAMED` with a `TO:` that then gets `MODIFIED` again by a second, later archived change under a
  further-renamed name — a rename chain. → Mitigation: out of scope for this change (single-hop
  match only); real-world OpenSpec renames are uncommon enough that this is an acceptable initial
  gap, and the additive `delta_drift` field can grow multi-hop matching later without a spec
  change to the other requirements.
- [Risk] The `--diff-filter=A` / `--follow` fallback could, in a squash-merge history, attribute an
  earlier timestamp to the archived change than when it actually became visible on the base
  branch. → Mitigation: an earlier timestamp only makes the check more conservative (fewer
  flags), never produces a false positive, which is the safer failure direction for a
  warning-only check.
- [Risk] A large number of archived changes could make the per-open-change archive glob
  (`archive/*/specs/<capability-path>/spec.md`) run once per open change per scan. → Mitigation:
  the glob is scoped to one capability path (not a full directory walk), matching the existing
  `_openspec_delta_reconciled` cost profile already accepted for every dashboard scan today.

## Migration Plan

Additive only — no existing field, stage, or test changes. Ships as ordinary `worktrail-go`
dashboard behavior once merged; no CI wiring, flag, or rollout step required (see proposal.md -
Impact).

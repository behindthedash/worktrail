## Context

See `proposal.md` - Why for the motivating datalena `097` observation. Two facts from
`docs/design/conductor-lanes.md` §4.2 (P1, shipped in worktrail PR #25) shape the approach:

- Lanes are unioned on **dependency edges ∪ shared-file edges**: any two tasks that touch the
  same file end up in one lane, serialized within it, regardless of how many phases separate
  them.
- worktrail PR #383 (`fold same-file serial dependent chains into base group`) already hardened
  what happens *after* a same-file chain is grouped — a pure single-writer continuation absorbs
  into its predecessor's group instead of stranding as an orphaned singleton. It does not change
  *how many* tasks end up sharing a file in the first place.

Task content itself — which files each task touches — is authored by `tasks.md` generation, which
today is the `openspec-propose` skill's tasks-artifact step (`skills/openspec-propose/SKILL.md`).
That step already carries worktrail-specific guidance beyond stock OpenSpec (the requirement-name-
coverage rule and the file-less-task `[e2e]`/`[cleanup]` tagging rule), so it is the established
place worktrail adds tasks-authoring guidance without inventing a new mechanism.

## Goals / Non-Goals

**Goals:**
- Reduce how often a file recurs as a shared-file edge across more than one phase, by giving the
  tasks-authoring step explicit guidance to prefer per-phase ownership.
- Cover the additive case specifically (registry/data-table entries), since that is the shape
  that is both common in practice (per-phase feature registration) and mechanically splittable
  without changing behavior (independent appends compose safely).

**Non-Goals:**
- Do not change `plan_groups()`, `coordinator.py`, or any grouping-time code — the shared-file
  union and PR #383's fold-into-base behavior stay exactly as they are, as the deterministic
  backstop for whatever the guidance does not prevent (spec requirement "Collision-Serialization
  Preserved For Unavoidable Same-File Edits").
- Do not add a mechanical check that verifies the LLM authoring `tasks.md` actually followed this
  guidance. The existing sibling rules in the same skill step (file-less-task tagging,
  requirement-name coverage) are also prose-only, backed by a downstream mechanical catch
  (`worktrail-compile`'s scope-check) rather than a proactive verifier; this change follows the
  same pattern rather than introducing a new enforcement mechanism for one guidance rule only.
- Do not attempt to detect "hot files" programmatically ahead of authoring (e.g. by statically
  analyzing a codebase for files likely to recur). The guidance is applied by the authoring LLM's
  own judgment while decomposing tasks, the same way the existing sibling rules are.

## Decisions

**Where the guidance lives: `skills/openspec-propose/SKILL.md`'s tasks-artifact step, not a new
skill or a code-level check.** Alternative considered: enforce this in `worktrail-compile`
(`src/worktrail/conductor/compile.py`) as a scope-check gate, matching how requirement-coverage is
enforced. Rejected because "does this file recur across phases and could the recurring tasks have
used separate files instead" is not a fact compile can determine after the fact — it would need to
re-litigate the authoring LLM's decomposition choice with no view into what the alternative
decomposition costs (e.g., whether the writes really are independent/additive). Prose guidance at
authoring time, where the LLM already holds that context, is the same tradeoff this skill file
already made for the file-less-task and requirement-coverage rules.

**Scope the guidance to "more than one phase", not "more than one task".** Two tasks in the *same*
phase sharing a file is already handled correctly by the existing grouping-time fold (they were
never going to run in parallel anyway, since same-phase tasks are typically sequenced by other
dependencies). The problem this guidance targets is specifically fan-out *across* phases collapsing
into a chain — matching the datalena `097` evidence (hot files serialized 5-task chains spanning
multiple phases).

**Distinguish additive/composable hot files (split into per-phase files + one composer task) from
non-additive hot files (single owner per phase, collision-serialization unchanged).** Alternative
considered: always recommend a compose-later pattern regardless of whether the edits are additive.
Rejected — forcing a compose step onto genuinely coupled edits (e.g. two phases both needing to
change the same function body) would produce an artificial merge task with no real independent
work to parallelize, adding authoring complexity for no DAG-width gain.

## Risks / Trade-offs

- [Guidance is prose, so an authoring LLM may not apply it consistently] → Accepted, matching the
  existing enforcement posture of this skill step's other two guidance rules; `worktrail-compile`
  and the shipped grouping fold remain the deterministic backstops regardless of whether the
  guidance was followed.
- [A poorly-chosen per-phase split could produce more files than the added parallelism is worth
  for a small change] → The guidance only fires for a file recurring across more than one phase;
  a small change with few phases and no shared files is unaffected.
- [Composer task becomes a new single point of serialization] → Accepted and unavoidable: the
  composer task depends on every per-phase file it composes, but it replaces N-1 unnecessary
  cross-phase collisions on the *original* file with a single, expected, terminal dependency —
  strictly less serialization than before, not equal to it.

## Migration Plan

No migration: this is additive guidance text in a skill file, applied only to `tasks.md` authored
after this change lands. No existing `tasks.md` is edited or re-validated. Rollback is a plain
revert of the `skills/openspec-propose/SKILL.md` and `docs/design/conductor-lanes.md` edits.

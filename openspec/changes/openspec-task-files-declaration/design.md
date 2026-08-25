## Context

See `proposal.md` — Why. Current mechanics that shape the approach:

- `parse_tasks_md` (`src/worktrail/taskformats/openspec/schema.py`) matches only `## N.`
  group headings and `- [ ] N.M` task lines; every other line is skipped. Leading bracket
  tags (`[e2e]`) are the one per-task channel that exists, peeled by `split_tags`.
- `OpenSpecTaskSource.load()` hard-codes `"files": []` in each task dict.
- `compile_run_plan` selects its source purely from data: `needs_compile(tasks)` returns
  non-tail task ids with empty `files`; an empty result short-circuits to `SOURCE_SEED`
  before any spawn seam is touched. The RunPlan fingerprint already folds
  `_norm_str_list(t.get("files"))` per task into the hash.
- `runplan.apply_to_tasks` treats plan-supplied files as fill-only and enforces the
  edge-drop-pays-with-scope invariant; nothing downstream distinguishes devkit-frontmatter
  scope from any other declared scope.

So the entire mechanism can be "make the parser produce what devkit frontmatter produces"
— no compile-side change is required for the seed path to fire.

## Goals / Non-Goals

**Goals:**
- Give OpenSpec authors a durable, reviewed way to pin file scope in the artifact itself,
  so compile takes the free no-model seed path (mirroring devkit D1 behavior) instead of
  degrading to baseline when the inference pass is unavailable.
- Make `worktrail-compile`'s existing remediation advice ("add explicit \`files:\` scope to
  the artifact") true for OpenSpec changes, retiring the hand-authored-seed-RunPlan-in-cache
  workaround.
- Keep the tolerant-parse posture: malformed declarations warn, never raise — OpenSpec
  considers such files valid and must stay runnable.

**Non-Goals:**
- No inline dependency syntax (`deps:`). Dependency edges stay baseline-sequential plus
  compile-inferred; authoring edges by hand reintroduces the cycle/drift risks
  `apply_to_tasks` exists to police, and the motivating failure was about scope, not order.
- No replacement of the compile pass. An undeclared or partially declared change behaves
  exactly as today; this is an opt-in escape hatch, not a mandate.
- No new validation gate requiring declaration, no schema change to OpenSpec's own
  `spec-driven` schema, no coupling to `openspec archive`.
- No devkit changes: `FIELD_SCHEMA`, `files-sync-exempt`, and the devkit adapter are
  untouched.

## Decisions

**Syntax: an indented `files:` continuation line under the task line, not a bracket tag.**

```markdown
- [ ] 2.1 Add parser module
  files: src/worktrail/taskformats/x.py tests/taskformats/test_x.py
```

Alternatives considered:
- *Leading `[files: a.py b.py]` tag on the task line* — rejected: overloads the tag
  channel that currently expresses kind (`[e2e]`/`[cleanup]`), gets cramped past two or
  three paths, and forces whitespace-delimited paths into a regex that would need
  quoting rules to survive spaces. It also sits before the title, hurting readability of
  the checklist OpenSpec's tracker renders.
- *Group-level declaration under `## N.`* — rejected: scope is a per-task fact (it feeds
  per-task collision checks and worker briefs); a group-level list hides which task owns
  which path and would force a split rule that duplicates per-task syntax anyway.
- *A fenced metadata block per group / YAML-ish frontmatter section* — rejected:
  reintroduces the per-task frontmatter structure §4.5 deliberately shrank away, and a
  second machine-readable region inside a human-reviewed checklist invites drift between
  prose and metadata.

The indented-line form keeps the declaration adjacent to the task it scopes, reads as
plain prose in rendered markdown, is invisible to OpenSpec's tracker (everything after the
`N.M` id on the checkbox line is already ignored; non-checkbox lines are untracked), and
reuses the exact key name devkit uses and the remediation message already cites.

**Token grammar: comma- and/or whitespace-separated repo-relative paths; backtick-wrapped
tokens unwrapped.** Mirrors devkit's inline-list tolerance rather than inventing a third
list dialect. Paths are normalized (deduped, sorted, stripped) by the same
`_norm_str_list` the seed path already applies, so devkit and OpenSpec seeds converge on
identical shapes. No path safety filtering at parse time — seeded scope is trusted as
authored-and-reviewed exactly like devkit frontmatter is; the LLM-output path keeps its
own stricter `_validate` checks, which is where untrusted input enters today.

**Parse placement: inside `parse_tasks_md`, tracked via a pending-declaration buffer.**
After matching a task line the scanner peeks at immediately-following lines that are
indented and start with `files:`; a blank line, a group heading, or the next task ends the
window. `ParsedTask` gains a `files: list[str]` field; `load()` copies it into the task
dict in place of the hard-coded `[]`. Warnings ride the existing `parsed.warnings` →
`frontmatter_warnings` plumbing, so no new diagnostic channel is needed.

**Seed-path activation: none needed beyond the parse.** `needs_compile` excludes tail
kinds and reads `t.get("files")` — once every impl task declares scope, gaps are empty and
`compile_run_plan` returns `SOURCE_SEED` with zero code changes there. Fingerprinting
already includes declared files, so editing a declaration invalidates the cached plan and
the next compile re-seeds for free. This decision is "reuse the existing selection logic";
its alternative — teaching compile about a new declaration flag — would add state for no
behavioral difference.

**Docs live in the two places authors and operators already look:** the
`openspec-propose` skill's tasks-artifact step (alongside `[e2e]`/`[cleanup]` tagging and
hot-file bias, amending its "schema carries no field for that" sentence) and a short
amendment note in `docs/design/conductor-lanes.md` §4.5/D2. The D2 note matters most:
D2 rejected hand-written scopes as *the* mechanism replacing compile; recording that an
opt-in declaration which satisfies compile — and otherwise defers to it — preserves D2's
token thesis keeps the design record honest instead of silently diverging from it.

## Risks / Trade-offs

- [Authors declare wrong or stale paths, and a wrong-but-present scope beats no scope]
  → Mitigated by process, same as devkit: declarations land in PR review like any other
  artifact content, and the fingerprint invalidation means a corrected declaration
  re-seeds immediately. Compile's collision check still runs over seeded plans, so an
  under-declared shared file surfaces as an ordering gap rather than passing silently.
- [Two dialects of file-scope authoring (devkit frontmatter vs tasks.md lines)] →
  Accepted: they are different formats with different carriers by construction; what this
  change unifies is the parsed shape both produce (`task["files"]`), not the surface
  syntax. Documenting the OpenSpec form in the propose skill keeps author-facing knowledge
  in one place.
- [Indentation-sensitive parsing in a format humans hand-edit] → The window rule
  (immediately-following indented `files:` lines, ended by heading/next task/blank-line)
  is the same tolerance class as the existing checkbox regexes; anything ambiguous warns
  and degrades to undeclared rather than mis-scoping a neighboring task.

## Migration Plan

No migration: existing `tasks.md` files contain no `files:` lines and parse identically.
Adoption is per-change, opt-in, at authoring time (or by hand-editing an existing change,
which just moves its fingerprint and re-seeds). Rollback is a plain revert; cached plans
keyed on pre-declaration fingerprints remain valid.

## Open Questions

(none)

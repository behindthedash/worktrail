## Why

An OpenSpec change has no syntax for declaring per-task file scope, so `worktrail-compile`
must run its LLM pass to infer it (conductor path 3). That makes OpenSpec changes the one
format that cannot take compile's free no-model seed path (path 2), and it breaks down in
two concrete ways (verified live 2026-08-25): when the compile model call is provider-gated
(e.g. billing windows), compile degrades to the baseline plan and a live `full-real` run
refuses to fan out; and `_print_scope_gap_error`'s remediation advice — "add explicit
\`files:\` scope to the artifact" — is a dead end for OpenSpec authors because `tasks.md`
carries no such field. The observed workaround was hand-authoring a seed RunPlan JSON into
the repo's `<repo>-worktrees/runplans/` cache: writing derived, unreviewed state behind the
orchestrator's back, which is exactly what the cache is supposed to never contain.

## What Changes

- Add an **optional, documented inline file-scope declaration** to the OpenSpec checklist
  format: an indented `files:` continuation line under a `- [ ] N.M` task line, listing
  repo-relative paths the task creates or modifies.
- `parse_tasks_md` (`src/worktrail/taskformats/openspec/schema.py`) parses the declaration
  into `ParsedTask.files`; `OpenSpecTaskSource.load()` emits it as `task["files"]`,
  mirroring how devkit frontmatter `files:` reaches the same dict.
- With every non-tail task declaring scope, `worktrail-compile` takes its existing free
  `SOURCE_SEED` path — no model call, same as every devkit spec. No compile-logic change:
  `needs_compile()` and the RunPlan fingerprint already read `t["files"]`.
- Tasks without a declaration behave exactly as today (LLM compile pass, or the
  conservative sequential baseline when compiling is unavailable). Partially declared
  changes get both: declared scopes respected, remaining gaps compiled as before.
- Malformed declarations are tolerant-parse warnings (matching `tasks.md`'s existing
  posture), never hard errors.
- Document the syntax in the `openspec-propose` skill's tasks-artifact guidance, and amend
  `docs/design/conductor-lanes.md` (§4.5 / D2) so the opt-in declaration is recorded as a
  scoped exception to "file scope comes from the compiled RunPlan", not a reversal of the
  compile-pass decision.

Not changing: dependency edges stay out of the artifact (no inline `deps:` syntax);
OpenSpec's own tracker/archive behavior is untouched (it ignores everything after the
`N.M` id); `set_task_checked` write-back still touches only the checkbox marker.

## Capabilities

### New Capabilities
- `openspec-task-file-declaration`: the parsing contract for the inline per-task
  `files:` declaration in an OpenSpec change's `tasks.md` — what is parsed into
  `task["files"]`, how declared scope satisfies `worktrail-compile` without a model call,
  how malformed declarations degrade, and where authors are told the syntax exists.

### Modified Capabilities

(none — no existing capability in `openspec/specs/` governs the OpenSpec `tasks.md`
parsing contract or compile's seed-vs-compile selection; the closest siblings,
`task-source-dependency-validation` and `openspec-requirement-coverage-gate`, govern
dependency diagnostics and requirement-name coverage respectively)

## Impact

- `src/worktrail/taskformats/openspec/schema.py` — parser gains the continuation-line
  rule; `ParsedTask` gains `files`; module docstring's "no file scope" claim updated.
- `src/worktrail/taskformats/openspec/source.py` — `load()` carries parsed files into the
  task dict; class docstring updated (declaration is now possible; synthesising/guessing
  scope remains forbidden).
- `src/worktrail/conductor/compile.py` — no behavioral change expected; module docstring
  and the scope-gap remediation message re-checked for accuracy (its advice becomes
  actionable for OpenSpec).
- `skills/openspec-propose/SKILL.md` — tasks-artifact step gains the declaration syntax
  alongside the existing `[e2e]`/`[cleanup]` and hot-file-bias rules; its "schema carries
  no field for that" sentence amended.
- `docs/design/conductor-lanes.md` — §4.5/D2 amendment note.
- Tests: `tests/taskformats/openspec/` (parsing, load-through, tolerance) and
  `tests/conductor/test_compile.py` (seed path taken from declared scope, no spawn).
- Downstream surfaces (`runplan.apply_to_tasks`, `needs_compile`, fingerprinting,
  coordinator collision checks) already consume `task["files"]` format-agnostically and
  pick the declaration up unchanged.

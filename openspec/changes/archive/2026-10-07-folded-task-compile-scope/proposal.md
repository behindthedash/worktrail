## Why

A change that declares file scope for some tasks cannot compile from source when the model pass
declines to scope a task whose scope the artifact already declared. Whenever any implementation
task lacks a declaration, `compile_run_plan` runs one model pass whose prompt asks for a `files`
answer for **every** task and sanctions `[]` as the answer when the model cannot determine one
(`src/worktrail/conductor/compile.py:385-387`). `_validate()` then builds the compiled plan from
those answers — the artifact's own declarations are never consulted for the row
(`compile.py:517-538`) — and `runplan.apply_to_tasks()` replaces each task's declared `files`
with the plan's per-task list (`src/worktrail/conductor/runplan.py:321`). So a declaring task
whose answer comes back empty loses its authored scope in the merge, `needs_compile()`
(`compile.py:305-317`) re-derives the gap **after** compilation, and `worktrail-compile` exits 1
on the "still have no file scope after compiling" error whose remedy text is the authored
declaration the compile just discarded (`compile.py:1026-1043`). Because `queue_triage`'s fold
apply runs that compile on the target change and fails closed on a non-zero exit
(`src/worktrail/workqueue/queue_triage.py:3229,3240-3249`), then releases the claimed brief when
no PR was created (`queue_triage.py:3319-3320`), every fold into such a change bounces back to
the queue.

The live instance is `openspec/changes/quarantine-recovery-command`. Its rows 5.1/6.1/7.1/8.1
(`tasks.md:93-115`) carry authored `files:` declarations — 5.1/6.1/8.1 a run-journal citation
beside the worktree (`../run-*.json`), 7.1 the change's own `proposal.md`, 8.1 `pyproject.toml`
as well — yet every one of those four came back in the compile's scope-gap error
(`4 implementation task(s) still have no file scope after compiling: 5.1, 6.1, 7.1, 8.1`,
brief `20261005-210406-recompile-cannot-infer-file-scope`). Reproduced in this worktree without
a model call: parsing the unmodified change yields declared scope for all four (5.1 →
`['../run-built-artifact-packaging-parity-gate.json']`, 8.1 →
`['../run-routing-target-selector.json', 'pyproject.toml']`, …), so the pre-compile gap set is
only `['9.1']`; applying the stored degraded compiled plan
(`runplans/quarantine-recovery-command-51d3e14beb33.json`, whose rows answered `[]` for those
four) through `runplan.apply_to_tasks()` and then re-running `needs_compile()` reports
`5.1, 6.1, 7.1, 8.1, 9.1, 10.1` — the declared scopes were erased by an answer for tasks the
inference pass did not need to scope. The healthy plan
(`runplans/quarantine-recovery-command-9ef06c647f00.json`) exists only as a cache hit, so the
cache masks a change that does not compile from source, and `--force` rewrites that same
fingerprint with the degraded plan.

The second half is the fold's side of the same coin. A folded task whose brief cites only
machine-local state beside the worktree — the quarantine/selfcheck class, whose evidence is a
run-journal read — derives no in-repo scope (`queue_triage.py:3397-3445`) and arrives at compile
with nothing declared, so the model pass decides its scope and varies run to run; the brief that
must land (`20261004-112543`, the same class as row 6.1) was the fold whose compile reported the
unscoped task 10.1. `run-journal-fold-scope-chain-cap` (in flight) stops the fold from
*declaring* those journal citations but leaves this consequence open: "the fold emits no `files:`
line ... leaving compile's own scope inference as before" — and compile's own inference is what
this change makes non-authoritative for declared tasks and dispensable for folded ops tasks.

## What Changes

- **An authored declaration is honored where the model pass meets the artifact.** When a task's
  `files:` declaration survives parsing, the compiled plan carries exactly that list — the
  model's per-task `files` answer is never consulted for that task, so an empty ("unknown") or
  differing answer cannot displace the declaration. This is the same rule the seed path already
  applies to a fully declared change and the same carry-through `kind` already gets
  (`compile.py:855-868`, "the artifact declares it ... so a seed and a compile agree"), applied
  to the one field a partially declared change could still lose. A task whose declaration
  survives parsing can no longer be reported by the post-compile scope gap, whatever the model
  answered.
- **The discard is complete**: the model's `files` value for a declaring task is not consulted
  at all — including its repo-escape check, which stays reserved for paths the model actually
  supplies for undeclared tasks.
- **A folded task whose evidence lives outside the worktree declares its tail kind.** When the
  fold derives an empty `files:` scope while the brief's focus or the verdict evidence cites
  at least one path that exists outside the worktree (the run-journal read a
  quarantine/selfcheck brief is made of), the appended task is rendered with the `e2e` tail kind
  — `- [ ] N.1 [e2e] <instruction>` — instead of arriving for compile's inference to guess at.
  Such a task's work is not in the shared tree the change's workers commit into; `[e2e]` is the
  tail kind whose dispatch spawns a worker that runs commands, and tail-kind tasks are held out
  of the fan-out and exempt from the scope gate by kind alone, so the fold's compile stops
  depending on the model for it. A fold whose citations resolve inside the worktree is
  unchanged; a fold that cites no existing path anywhere keeps today's inferred-scope behavior;
  `cleanup` is deliberately not used (its dispatch is a journal-only status transition that
  executes nothing — `parallelism.py`'s mismatch rule retags exactly this shape to `e2e`).
  Until `run-journal-fold-scope-chain-cap` lands, the derivation still admits the journal
  citation, so the derived scope is non-empty and this rule stays dormant — the declaration is
  honored by the compile half instead — and it is what keeps the class stable once the
  citation stops being declared.
- The workqueue skill's fold `files:`-scope bullet carries the new boundary: declared scope is
  what a task owns in the shared tree, and a brief whose evidence is all machine-local state
  gives the folded task a kind, not a scope.

Non-goals: the parser's token admission (including whether a hand-authored `..` token is
accepted at all — `openspec-files-path-validation`'s requirement space); the
`../run-*.json` rows already written into `quarantine-recovery-command/tasks.md` (another
change's artifact; this change makes them compile, it does not rewrite them); `apply_to_tasks()`
merge semantics themselves; and the `compile_max_same_file_chain` budget.

## Capabilities

### New Capabilities

<!-- None. Both halves tighten rules that already exist. -->

### Modified Capabilities

- `openspec-task-file-declaration`: a declared file scope is the task's scope on every plan
  source — the compiled plan carries an authored declaration verbatim and a model answer never
  displaces it, so only undeclared tasks are subject to inference (the requirement's own words,
  now true on the compiled path too).
- `intake-triage` (added requirement): a folded task whose evidence cites only state outside the
  worktree is appended with the `e2e` tail kind, so a quarantine/selfcheck fold compiles from
  source instead of depending on the model's per-run scoping.

## Impact

- `src/worktrail/conductor/compile.py` — `_validate()`'s payload-to-`TaskPlan` row loop: a
  declaring task's files come from the artifact; the model's value for it is ignored.
- `tests/conductor/test_compile.py` — coverage that a model answer of `[]` (and a differing
  list) for a declaring task leaves the declared scope in place, that a journal-style
  out-of-repo declaration survives compilation, and that the existing repo-escape rejection is
  unchanged for undeclared tasks.
- `src/worktrail/workqueue/queue_triage.py` — the fold's appended-task rendering consults a
  pure kind helper beside `_fold_task_file_scope()` (empty derived scope + citations resolving
  only outside the worktree) and prefixes the `[e2e]` tag when it holds.
- `tests/workqueue/test_queue_triage.py` — coverage for the tag on a journal-citation-only
  fold, its absence when an in-worktree path is admitted, and its absence when nothing cited
  exists.
- `.claude/skills/workqueue/skill.md` — the fold `files:`-scope bullet states the kind
  boundary.
- No CLI, task-syntax, RunPlan-format, policy, or fingerprint change; the four rows already in
  `quarantine-recovery-command` compile unchanged (their declarations are now honored), and a
  task that arrives with no declaration and no citation outside the worktree behaves exactly as
  before.

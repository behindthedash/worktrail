## Context

`worktrail-compile` produces a RunPlan on one of three paths (`conductor/compile.py:1-28`): a
cache hit, a seed from an artifact that declares file scope for every task, or a model pass for
the tasks that do not (`needs_compile()`, `compile.py:305-317`). The model's prompt asks for a
`files` answer for every task and sanctions `[]` as the correct answer for "genuinely cannot
determine" (`compile.py:385-387`); the compiled plan is built from those answers alone, never
consulting the artifact's own declarations (`compile.py:517-538`); and
`runplan.apply_to_tasks()` replaces each task's declared `files` with the plan's
(`conductor/runplan.py:321`). A declaring task therefore loses its authored scope whenever the
model declines it, `needs_compile()` is re-run on the merged result, and the compile exits 1
with a remedy that names the very declaration it discarded.

This is the reported state of `openspec/changes/quarantine-recovery-command` (brief
`20261005-210406-recompile-cannot-infer-file-scope`): rows 5.1/6.1/7.1/8.1 declare scope, the
pre-compile gap set on the unmodified change is only `['9.1']`, yet the compile reported all
four among the unscoped. Reproduced here in-process — the stored degraded plan
(`runplans/quarantine-recovery-command-51d3e14beb33.json`) applied through
`apply_to_tasks()` puts all four back into the post-compile gap set — so the mechanism is
pinned, not inferred. `queue_triage`'s fold apply runs this compile on the target change and
fails closed (`queue_triage.py:3229,3240-3249`), releasing the brief when no PR exists
(`:3319-3320`) — so the defect converts directly into queue churn.

The fold's half: a folded task's `files:` scope is derived from the paths its brief cites that
exist in the fold's worktree (`queue_triage.py:3397-3445`). A quarantine/selfcheck brief's
evidence is a run-journal read beside the worktree — machine-local state, no in-tree artifact —
so the derivation returns nothing and the task arrives with no declaration for the model to
guess at. `run-journal-fold-scope-chain-cap` (in flight) stops the fold from declaring the
citation itself, and its delta states the consequence plainly: the task emits no `files:` line,
"leaving compile's own scope inference as before". This change makes that inference unnecessary
for this class.

## Goals / Non-Goals

**Goals:**

- A declaration that survives parsing is the task's scope on every plan source. No compile of a
  partially declared change can report a declaring task as unscoped, whatever the model
  answered — the reported failure mode and its whole class.
- A folded task whose evidence is entirely machine-local state beside the worktree declares
  *what it is* (a worker-running `e2e` tail task) instead of arriving for inference to scope,
  so the next quarantine/selfcheck fold into any change compiles from source.
- Both changes are small, local, and do not alter fingerprinting, the RunPlan format, the
  shape gate, or any already-written `tasks.md`.

**Non-Goals:**

- The parser's token admission, including whether a hand-authored `..` token is accepted as a
  declaration at all — that is `openspec-files-path-validation`'s requirement space, and this
  change's rule is deliberately written as "a declaration that survives parsing" so the two
  compose whichever order they land in.
- Rewriting the `../run-*.json` rows already written into
  `quarantine-recovery-command/tasks.md`. Each change authors its own `tasks.md`; with this
  change those declarations are honored, and the fold rule covers folds from here on.
- `apply_to_tasks()`'s merge semantics, the `compile_max_same_file_chain` budget, and the
  model prompt's wording.
- Repairing a fold whose evidence cites only non-existent paths (a code-fix brief whose
  evidence names bare filenames, like the `20261005-103628` stale-ancestry fold): its task
  arrives undeclared and inference still supplies its scope, as today.

## Decisions

### Declared scope is honored where the model answer meets the artifact, not at the merge

The rule lands in `_validate()`'s row loop (`compile.py:517-568`), which is the one place a
model answer becomes a `TaskPlan` for a compiled plan: when the artifact task for a row
declares a non-empty file list, that list is the plan's files for the task, and the model's
`files` value for the row is not consulted — not even its repo-escape check, which keeps
rejecting `..` paths the model supplies for *undeclared* tasks
(`test_paths_escaping_the_repo_reject_the_plan` keeps its premise: its fixture declares
nothing).

Why here and not in `apply_to_tasks()`: the plan is stored and read by consumers that never
merge it — `work_queue.py:402` resolves a spec's tasks from the raw cached plan,
`plan_audit.py:146` reads it directly — and, decisively, the degraded plan this change fixes is
*already stored under the live fingerprint* and would be served to every cache hit. A
merge-site fallback would leave that entry dishonest and would make correctness depend on which
caller remembered to merge; fixing the compiled plan means every plan source (seed, baseline,
compiled) satisfies the same contract, which is exactly what the requirement's own sentence
already says: "the declaring tasks' scopes are used as declared, and only the undeclared tasks'
scopes are subject to inference".

This is the `kind` precedent applied to the other field the model is allowed to answer:
`compile.py:858-861` already carries the parsed `kind` through with the comment that a seed and
a compile must agree. Declared `files` now gets the same treatment, so a seed, a baseline, and
a compile of the same content agree on every declaring task. The cost is explicit: a
cross-task overlap the model's final re-scan finds for a declaring task is discarded with the
rest of its answer for that row. That is the same exposure a fully declared change already
has — its plan is the declaration verbatim, with no model input at all — and the alternative
is the run-to-run variance that made a declared task's scope depend on the model's mood.

Rejected alternatives:

- **Merge-site fallback** (`apply_to_tasks`: plan files, else declared): fixes only merging
  callers; leaves the stored plan and the raw readers wrong; keeps the degrade-then-mask cycle
  the cached healthy plan demonstrates.
- **Union of declared and model files for a declaring task**: re-admits model guesswork for
  tasks the artifact already governs — the guess that produced 5.1's inferred scope in one
  stored plan and `[]` in the other — and inflates a worker's declared write surface.
- **Editing the change's rows** (stripping the journal citations from
  `quarantine-recovery-command/tasks.md`): not this change's artifact, and the brief's own
  acceptance asks for the compile to work "on the unmodified change".
- **Silently dropping the out-of-repo declared entries and letting inference replace them**
  (the reading `run-journal-fold-scope-chain-cap`'s simulation ruled out): leaves the four
  tasks model-dependent, which is the variance this change exists to remove.

### The fold declares a kind only when the evidence leaves the worktree

The predicate for tagging the appended task `[e2e]`: the derived in-worktree scope is empty
**and** at least one cited path resolves to an existing file outside the worktree. That is the
observable shape of "this brief's evidence is machine-local state beside the worktree, and the
task has no shared-tree artifact" — the quarantine/selfcheck fleet-sweep class, whose folded
work is running commands (repair and resume, or discard, the quarantined groups) against that
state. `[e2e]` is chosen deliberately:

- **`[cleanup]` would silently drop the work.** Its dispatch is a journal-only status
  transition that executes nothing (`parallelism._cleanup_verification_mismatches`, and the
  scope-gap remedy text), so a triage action tagged `[cleanup]` would be journaled as success
  without ever having run. `[e2e]` is the tail kind that spawns a worker and runs commands —
  what these tasks are.
- **`[e2e]`'s zero-file dispatch instruction is right for the class.** A tail task with no
  files renders "expect zero file changes" (`dispatch.py:657-661`), which is exactly true of a
  quarantine triage: its mutations are branch repairs and journal writes beside the worktree,
  already serialized by the orchestrator's RunLock — never a commit into the change's tree.
  It also gives the class the ordering it needs for free: the tail phase runs after every
  implementation task, so the fold's triage cannot run before the recovery command it drives
  exists.

Rejected alternatives:

- **Tag whenever the derived scope is empty**: misfiles the code-fix fold. The stale-ancestry
  fold (`20261005-103628`) derived no scope either — its evidence cited bare filenames and a
  pytest command line — but its work is a `live.py` fix the model scopes correctly; tagging it
  `[e2e]` would hand its worker the "verification-only, expect zero file changes" instruction
  for a code change.
- **Matching the journal filename (`../run-*.json`)**: already rejected in
  `run-journal-fold-scope-chain-cap` as encoding a filename convention as if it were the
  invariant; the outside-the-worktree boundary is the general form of the same rule.
- **Matching the selfcheck emitter's prose** (`worktrail-quarantine-selfcheck`,
  `worktrail-selfcheck-fleet-sweep`): couples the fold to an out-of-repo emitter's wording, and
  covers only today's emitter.
- **Making compile infer a kind for scopeless tasks**: `kind` is never taken from the model
  (`compile.py:858-861`); the artifact declares it, and for folds the fold is the artifact
  author.

Considered risk, accepted: a code-fix brief whose evidence cites a real path only outside the
worktree would be tagged `[e2e]`. The premise-check doctrine makes this rare — evidence argues
its premise against the code, so a fix's evidence cites its fix target — and the class this
rule exists for is precisely the one whose evidence cannot cite an in-tree artifact.

## Interactions with in-flight changes

- `run-journal-fold-scope-chain-cap`: composes in both directions. Pre-landing, a quarantine
  fold still derives the journal citation, so its scope is non-empty and the tag does not fire —
  the declaration is honored by this change's compile half instead. Post-landing, the citation
  is dropped, the derived scope is empty, and the tag fires. Either way the folded task ends up
  stable, which is the answer to that change's open consequence ("leaving compile's own scope
  inference as before"). Both changes touch `_fold_task_file_scope()`'s neighborhood and the
  same skill bullet; whichever lands second rebases its delta text onto the other's.
- `openspec-files-path-validation`: that change decides whether a `..` token is a valid
  declaration at parse time. This change's compile rule is written as "a declaration that
  survives parsing", so if such tokens are later rejected, the affected task becomes undeclared
  and the ordinary rules apply — and for the folded ops class, the kind tag means it never
  depended on the declaration in the first place. It does not resolve that change's interaction
  with hand-authored journal rows; that decision belongs to the change that owns the parser.

## Verification

- Unit: a partially declared change whose model answer returns `[]` for the declaring task
  compiles with the declaration intact and zero post-compile gaps; a differing model list does
  not displace it; an out-of-repo declared path (a journal citation) survives as seeded and
  baseline plans already carry it; undeclared tasks are still inferred, and the repo-escape
  rejection still fires for them.
- Unit: the fold tags a journal-citation-only brief `[e2e]` with no `files:` line; adds no tag
  when an in-worktree path is admitted; adds no tag when nothing cited exists.
- End to end (the change's own `[e2e]` task): `worktrail-compile --force
  openspec/changes/quarantine-recovery-command --cache-dir <scratch>` exits 0 on the unmodified
  change with no scope-gap report — run against a scratch cache dir so the shared plan entry is
  never rewritten (the brief's own artifact: a `--force` against the shared cache replaces the
  healthy plan under its fingerprint). The fold's own landing for brief `20261004-112543` is
  the queue's next apply once this change lands; its rendering is unit-covered here.

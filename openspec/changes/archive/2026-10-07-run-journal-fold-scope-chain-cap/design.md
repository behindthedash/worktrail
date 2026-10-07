## Context

`_fold_task_file_scope()` (`src/worktrail/workqueue/queue_triage.py:3397`) turns the
`fold-into-change` apply into a task with an explicit `files:` line, so the folded work enters
the target change's plan with a declared scope instead of a model inference. It probes the
brief's focus and the verdict evidence with `router.brief_probes.extract_probes()`, keeps
every path token that exists in the fold's worktree, adds each `src/` path's existing sibling
test, and skips the paths the caller passes through `exclude=` -- which #1459 uses to keep the
target change's own `proposal.md`/`tasks.md` out of scope.

The probes are relative-path tokens: `brief_probes` already rejects absolute and `~`-prefixed
tokens (they name another machine's filesystem), so the only way a citation escapes the
worktree is a relative `..` path -- and run journals are exactly that. A spec's journal lives
beside the worktree (`~/projects/worktrail-worktrees/run-<spec>.json`, cited from the worktree
root as `../run-<spec>.json`), and quarantine-triage evidence cites the journal read on every
re-filing. Admitted into scope, the journal string is seeded verbatim into the plan by
`worktrail-compile` (`taskformats/openspec/schema.py`'s `split_files` does not normalize),
same-file writers get repair edges (`conductor/runplan.py:336`), and
`conductor/parallelism.py`'s chain rule (`shape_problems()`, threshold
`compile_max_same_file_chain` = 2) refuses the third declarer:
`same-file chain: 1.1 -> 2.1 -> 3.1 all declare ../run-a.json (3 > 2)` (reproduced in a
scratch repo). Because the fold compiles the target change itself and fails closed
(`queue_triage.py:3229`), that refusal is a hard cap of two folds per journal -- and the
change classes whose folds keep re-filing are the ones whose evidence always cites the same
journals.

The brief that originated this change marks the decision explicitly: excluding journals "is
the same mechanical shape as #1459's fix but is NOT obviously correct: a quarantine-triage
task does write the journal, so the serialization the chain rule imposes may be intended. If
journals stay in scope, repeated folds over the same spec need a deliberate answer."

See `proposal.md` for the full motivation. This document records the decisions behind the
boundary rule, the journal-scope question the brief raises, and the rejected alternatives.

## Goals / Non-Goals

**Goals:**

- The derived scope contains only paths the folded task can own: canonical repo-relative paths
  that resolve inside the worktree. A citation that resolves outside it -- a run journal, or
  any other machine-local path -- is never declared.
- The #1459 exclusion of the fold's own `proposal.md`/`tasks.md` holds by path identity, not
  by how the evidence spells the citation.
- The brief's journal question is answered with its alternatives recorded: a journal path does
  not belong in a folded task's `files:` scope, and repeated folds over one spec have a
  deliberate answer that is not "the third one is refused".
- The derivation stays a pure function of `(worktree_dir, texts, exclude)`: no new I/O beyond
  the `is_file()` existence check it already performs, no new parameter at the call site.

**Non-Goals:**

- Changing `compile_max_same_file_chain`, `shape_problems()`, or the `.worktrail/policy.yaml`
  value. The chain budget is a real serialization signal for repo files; the defect is the
  false declaration, not the gate.
- Rewriting the `../run-*.json` rows already written into
  `openspec/changes/quarantine-recovery-command/tasks.md`: two declarers each, under the cap,
  and an in-flight change whose own folds would collide with a foreign edit.
- Making compile's seed path reject hand-authored `..` scope. `compile.py`'s `_validate()`
  rejects `..` for *inferred* scope, but tasks.md-authored scope may name journals deliberately
  (the recovery command's rows do today); tightening the seed path would fail active changes
  and is a separate decision.
- The `propose-change` path, which derives no scope.

## Decisions

### The admission boundary is the worktree root, not a run-journal filename glob

A probe is admitted only if it resolves inside `worktree_dir`. A `run-*.json` glob would cover
today's citation shape and miss every other machine-local path a brief can cite -- a sibling
change's file inside another worktree, another repo's checkout, a scratch path. The worktree
root is exactly the set of paths the task's work can name: only the worktree is committed and
merged, so a declared file outside it can never be the task's work. It is also the boundary
compile already enforces for the scope it infers (`compile.py:538`: a `..` path is
`file path outside the repo`), so the generator and compile agree on what a legal scope entry
is -- before this change the fold's derivation was the one scope *producer* emitting paths
that compile's own inference refuses (hand-authored `files:` rows are a separate, deliberate
case; see Non-Goals).

Rejected alternative: matching the journal naming (`../run-*.json`) is narrow and encodes a
filename convention as if it were the invariant.

### Admission and exclusion are decided by canonical path identity

Each probe is line-suffix-stripped and normalized with `os.path.normpath`; it is admitted only
when it resolves inside `worktree_dir` and exists there; `exclude` entries are compared in the
same normalized repo-relative form; and the emitted entry is the canonical spelling. `./`- and
interior-`..` spellings of a path therefore cannot dodge either check.

Rejected: a lexical `".." in Path(rel).parts` check. It would drop in-repo paths spelled with
an interior `..` (`openspec/../src/x.py`) that are perfectly declarable, and it leaves the
`exclude` guarantee spelling-dependent -- the brief's own target change sits at exactly two
declarers on its `proposal.md`, so a single `./`-spelled citation would re-trip the `(3 > 2)`
cap #1459 just closed. Canonicalizing also means a respelled citation of a file the task does
own lands in the same same-file chain as every other spelling, instead of hiding from the
collision rule.

The normalization is lexical (`normpath` plus `Path.is_relative_to`), not a symlink-resolving
`realpath`, so the decision never depends on filesystem layout beyond the `is_file()` check
the function already performed.

### A run-journal citation is evidence of a read, not a declaration the task writes it

This is the brief's central question, and the answer is that the journal stays out of scope:

- **The citation does not mean the task writes the file.** Compile's own contract for the
  inferred path says scope is "repo-relative paths the task will create or modify. Files it
  only reads do not belong here". The exemplar is live: task 4.1 of
  `quarantine-recovery-command` cites both journals to prove the selfcheck re-files stale
  briefs -- its work is in `quarantine_selfcheck.py`, it writes neither journal -- and it
  already consumes one slot on each. A derivation that cannot tell a read citation from a work
  declaration must not spend a write budget on it.
- **An out-of-tree path has no plan semantics to preserve.** The plan's file machinery orders
  and serializes writers of the shared tree the workers commit into; the journal is in no
  worktree diff and is never merged. The writes the brief correctly points at are serialized
  by the orchestrator's RunLock (`live.py:379`), flocked per (repo, spec) beside the journal --
  a stronger guarantee than ordering, and one the plan was never providing.
- **The cap has no remedy for a machine-authored task.** `policy.py`'s rationale for
  `compile_max_same_file_chain` is that the *author* is asked to consolidate or split file
  scope when a chain exceeds it. A fold cannot be asked; it can only refuse, failing the
  verdict closed and re-queueing the brief. Counting out-of-tree citations converts a
  consolidation signal into a wall with no exit.

Rejected: keeping the journal in scope and exempting out-of-tree paths from the cap only. The
entry would stay a path the plan cannot meaningfully order or merge, the read/write conflation
would remain, and `profile()`'s hot-file signal would count a non-repo path. Rejected: raising
`compile_max_same_file_chain` in this repo's policy -- a local silencer that weakens a real
signal for every change, for one repo.

### Repeated folds over one spec have an answer that is not the cap

The brief asks what happens when the same spec is folded repeatedly once journals leave scope:
nothing contends through the plan any more. Whether a second triage task for the same groups
should exist is the queue's duplicate/re-filing judgment -- the folded siblings already record
their re-filing history in their own evidence ("the next re-filing of the class already folded
as ...") -- and it is the triage evaluator's verdict to make, not a file-scope budget's. If
two folded tasks do mutate the same journal, the RunLock serializes those writes exactly as it
does for any other two runs against one spec.

### Already-written declarations stay; the fix is prospective

The two-declarer journal rows and the two-declarer `proposal.md` row in
`quarantine-recovery-command` compile cleanly (`> 2` is the failure), and that change is in
flight under its own folds. This change only stops new declarations: after it, a further fold
of the quarantine class adds an in-repo-only scope and compiles.

## Testing

Unit coverage in `tests/workqueue/test_queue_triage.py` beside the existing fold-scope tests:
the derived scope for a journal-citing evidence against a fixture worktree with the journal
beside it; canonical admission of `./`- and interior-`..`-spelled in-repo paths;
identity-based exclusion of the fold's own docs; the no-`files:`-line outcome when every
citation leaves the worktree; and a three-fold regression driving the derived scopes through
`conductor.parallelism.shape_problems()` to assert no same-file chain problem (the pre-fix
derivation emits the journal three times and is refused with `(3 > 2)`). The scratch-repo
reproduction remains the verification task's end-to-end check.

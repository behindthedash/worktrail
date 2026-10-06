## Why

A `fold-into-change` verdict appends a task to the target change with an explicit `files:`
scope, derived by `_fold_task_file_scope()` (`src/worktrail/workqueue/queue_triage.py:3397`)
from every path probe in the brief's focus and the verdict evidence that exists in the fold's
worktree. `exclude=` (added by #1459, commit `b73ad822`) keeps the target change's own
`proposal.md`/`tasks.md` out of that scope; everything else that exists inside the worktree
*or beside it* is admitted.

Run journals are everything else. A spec's journal lives beside the worktree --
`~/projects/worktrail-worktrees/run-<spec>.json`, cited from the worktree root as
`../run-<spec>.json` -- and the quarantine brief class's evidence carries exactly that
citation (both folded quarantine briefs already in `quarantine-recovery-command` cite the
journal read, e.g. "Journal still quarantined (read via python3.14 json.load of
$HOME/projects/worktrail-worktrees/run-built-artifact-packaging-parity-gate.json: tail-3.5 +
tail-4.2 both state=QUARANTINED reason=merge_conflict)"). Verified against a fixture that
replicates the layout: `_fold_task_file_scope()` returns
`['../run-built-artifact-packaging-parity-gate.json']` for that evidence shape.

`worktrail-compile` then seeds the declared scope into the plan verbatim,
`runplan.apply_to_tasks()` orders same-file writers with repair edges
(`src/worktrail/conductor/runplan.py:336`), and `parallelism.shape_problems()` refuses the plan
when the longest dependent same-file chain exceeds `compile_max_same_file_chain` -- default 2
(`src/worktrail/router/policy.py:262`, `DEFAULT_MAX_SAME_FILE_CHAIN` at
`parallelism.py:47`; no override in `.worktrail/policy.yaml`). Reproduced in a scratch repo:
three tasks declaring the same `../run-a.json` fail with

```
same-file chain: 1.1 -> 2.1 -> 3.1 all declare ../run-a.json (3 > 2)
```

and exit 1. The fold runs `worktrail-compile` on the target change itself
(`queue_triage.py:3229`) and fails closed on a non-zero exit, releasing the brief back to
`queue/` -- so the third fold into a change whose evidence cites one journal never lands.
Fold depth is capped at two per journal, and the change nearest the cap is the one whose fold
class keeps re-filing: `quarantine-recovery-command/tasks.md` L87+L94 put
`../run-built-artifact-packaging-parity-gate.json` at exactly two declarers and L87+L101 put
`../run-openspec-validate-ci-gate.json` at exactly two, because the
`worktrail-quarantine-selfcheck` briefs folded into it cite those journals by construction --
and the class is still arriving (queued sibling `20261004-112543`, the same spec as task 6.1).
Brief `20261005-113038` (this change's origin) is one of that class; #1459's commit message
recorded the same `(3 > 2)` refusal from the fold's own docs before that fix excluded them.

**Does a run-journal path belong in a folded task's `files:` scope at all?** The brief that
originated this change flags the counter-consideration explicitly: a quarantine-triage task
does write the journal it cites, so the serialization the chain rule imposes may be intended.
The answer is no, and the live change shows why. First, the citation is evidence of a *read*:
task 4.1 of `quarantine-recovery-command` cites both journals as proof that the selfcheck
re-files stale briefs -- its work is in `quarantine_selfcheck.py`, it writes neither journal --
yet it already consumes one slot on each, so a budget meant for tasks that will fight over a
file is spent by a task that never touches it. Second, a `files:` entry the plan can act on
must be a path in the shared tree the workers commit into: the journal lives outside every
worktree, is never merged, and its writes are already serialized by the orchestrator's RunLock
(`live.py:379`, flocked per (repo, spec) beside the journal) -- plan-level ordering of an
out-of-tree path buys nothing the runtime lock does not already guarantee. `design.md` records
this decision, the rejected alternatives (a `run-*.json` filename rule, exempting out-of-tree
paths from the cap only, raising the cap), and the deliberate answer for repeated folds over
one spec.

**Premise re-verification** (brief `20261005-113038-run-journals-hit-chain-cap`): repo live
(`gh repo view --json isArchived,name` -> `{"isArchived":false,"name":"worktrail"}`); the
declarer counts above and the effective cap of 2 (`router/policy.py:262`, no
`.worktrail/policy.yaml` override) read directly in this worktree; `queue_triage.py:3536`
passes only `exclude=(proposal_path, tasks_path)`, so the derivation drops only excluded and
non-existent paths (`queue_triage.py:3428-3436`); and both the fixture result and the
scratch-repo refusal (`worktrail-compile --no-llm`, exit 1, `(3 > 2)`) reproduced end-to-end.
Pre-#1459 fold residue leaves the target's own `proposal.md` at exactly two declarers as well
(L87 4.1, L108 7.1) -- one respelled citation away from re-tripping the cap #1459 closed,
which is why this change matches `exclude` by path identity, not string spelling. The brief's
unverified sibling hypothesis (queued `20261004-112543` hits this wall when triaged) was not
run here; its structural half is verified -- that class's evidence cites
`../run-openspec-validate-ci-gate.json`, which is what put task 6.1's row there.

## What Changes

- **`_fold_task_file_scope()` decides admission by path identity, not by string spelling.**
  Each probe is canonicalized (line-number suffix stripped, then `os.path.normpath`), admitted
  only if it resolves inside the worktree and exists there, matched against the normalized
  forms of `exclude`, and emitted as the canonical repo-relative spelling. A probe that
  resolves outside the worktree -- a triage evidence's `../run-<spec>.json` journal citation,
  or any other machine-local path beside the worktree -- and a path the fold writes itself
  (the target change's `proposal.md`/`tasks.md`, however the evidence spells it) can no longer
  enter the derived `files:` scope. A fold whose citations all leave the worktree emits no
  `files:` line, exactly as when it cites no existing path at all.
- **Repeated folds over one spec stop contending through the plan.** With the journal no
  longer declared, a second and third fold of the same quarantine class into one change
  compile; whether they should both exist is the queue's duplicate/re-filing judgment, not a
  file-scope budget, and if two folded tasks do mutate the same journal the RunLock serializes
  those writes as it always did.
- The rest of the derivation is unchanged: focus and evidence are both probed, a `src/` path
  still pulls in its existing sibling test file, and the worktrail-compile seed path for
  hand-authored scope is untouched.
- The workqueue skill doc (`.claude/skills/workqueue/skill.md`) carries the boundary rule in
  its `files:`-scope bullet and in the critical rule that keeps the fold's own docs out of
  scope.
- Non-goals: the compile shape gate's threshold and its counting of hand-authored `files:`
  rows; the `../run-*.json` rows already written into `quarantine-recovery-command/tasks.md`
  (two declarers each, under the cap, and another change in flight); and `compile.py`'s
  `_validate()` payload-path rule, which already rejects `..` for inferred scope.

## Capabilities

### New Capabilities

<!-- None. This change tightens one existing requirement's file-scope derivation. -->

### Modified Capabilities

- `intake-triage`: the fold's derived `files:` scope names only canonical repo-relative paths
  that resolve inside the worktree -- a cited run journal beside the worktree and a path the
  fold writes itself are never declared, by path identity rather than spelling -- so the
  compile shape gate's same-file chain budget is spent only on files the task owns and fold
  depth is not capped by the journals an evidence cites.

## Impact

- `src/worktrail/workqueue/queue_triage.py` -- `_fold_task_file_scope()`'s admission rule and
  docstring.
- `tests/workqueue/test_queue_triage.py` -- regression coverage for the worktree boundary, the
  identity-based `exclude`, and the three-fold chain that the pre-fix derivation refused with
  `(3 > 2)`.
- `.claude/skills/workqueue/skill.md` -- the fold `files:`-scope bullet and the critical rule
  on the fold's own docs.
- No new console script, policy key, dependency, or journal schema; `worktrail-compile`,
  `parallelism.py`, and the RunPlan cache are untouched, and no already-written `tasks.md`
  changes behavior.

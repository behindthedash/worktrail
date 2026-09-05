## Why

`_apply_fold_into_change()` (`src/worktrail/workqueue/queue_triage.py:2664-2739`) appends a
`fold-into-change` verdict's `evidence` verbatim as the new `## N. Folded from <brief-id>` task
group's `- [ ] N.1 <evidence>` checklist item, then hands off to `_worktree_pr_close()`
(`queue_triage.py:2382-2530`), which runs `worktrail-compile` against the target change before
committing anything. When `evidence` names no file, `worktrail-compile` refuses with
`ERROR: 1 implementation task(s) still have no file scope after compiling` -- it has no file
path to infer scope from -- and `_worktree_pr_close()` treats that as a hard failure: no commit,
no push, no pull request. The brief is left completely untouched (per the existing "Fold and
propose are applied as a pull request, fail-closed" contract), so nothing is lost, but the whole
`fold-into-change` verdict is wasted and the brief sits queued again until a human notices and
re-triages it by hand.

This was observed live on 2026-09-04: a `fold-into-change` verdict for brief
`20260904-180534-land-pr-push-branch-tracking` targeted `shared-pr-landing-pipeline` with
evidence that never named a source file, `apply --confirm` rolled back cleanly per the
fail-closed contract, and the fold only succeeded after the evidence was hand-rewritten to name
`src/worktrail/router/land_pr.py` and its test file. Nothing before this compile gate --
`_has_valid_target()` (`queue_triage.py:1392-1433`), the sole mechanical check `parse_verdicts()`
runs on a `fold-into-change` verdict today -- checks anything about `evidence`'s content, only
that `target_change` is one of the presented candidates. A verdict that clears that check but
carries no file reference is accepted exactly like one that does, and the gap is only discovered
at apply time, on a live worktree, after a wasted PR-open attempt.

This repo already has a solved version of this exact shape of problem for `needs-update`:
`refuted_span` forces the evaluator to commit to something mechanically checkable rather than
free-form judgment, and `_has_valid_target()`-adjacent parse-time validation rejects a malformed
one before it is ever acted on. `fold-into-change` has no equivalent commitment device for the
one piece of `evidence` content the compile gate actually needs: a file reference.

## What Changes

- **Parse-time validation**: `_has_valid_target()` additionally requires a `fold-into-change`
  verdict's `evidence` to cite at least one file-path-shaped token, reusing
  `premise_check.extract_needles()`'s existing `"path"` needle extraction (the same path-probe
  logic `run_premise_check()` already runs against a brief's `focus:` text, itself backed by
  `router/brief_probes.py`'s `extract_probes()`) rather than inventing a second path-detection
  regex. A `fold-into-change` verdict whose `evidence` names no file is downgraded to `keep`
  with the raw verdict retained as evidence -- the same fail-closed fallback an out-of-candidate
  `target_change` already gets -- so it never reaches `_apply_fold_into_change()`, the worktree,
  or `worktrail-compile` at all.
- **Evaluator prompt**: `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a states the new requirement
  explicitly -- a `fold-into-change` verdict's `evidence` must cite at least one specific file
  path -- so the evaluator has a chance to satisfy it up front instead of being downgraded after
  the fact.

This closes the gap deterministically, before an unattended `apply --confirm` run ever opens a
worktree for a fold that cannot compile: a `fold-into-change` verdict either demonstrably names
a file the resulting task can be scoped against, or it is never accepted as one in the first
place. It does not change `_apply_fold_into_change()`, `_worktree_pr_close()`, or the
`worktrail-compile` gate itself -- those already behave correctly (fail-closed, brief left
untouched) for a fold that reaches them; this only stops a fold that would fail with no file
scope from reaching them.

## Capabilities

### Modified Capabilities
- `queue-triage`: `Evidence-required verdict per brief` gains the file-path requirement for
  `fold-into-change`'s `evidence`.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`_has_valid_target()`, `EVALUATOR_PROMPT_TEMPLATE`)
- `tests/workqueue/test_queue_triage.py`

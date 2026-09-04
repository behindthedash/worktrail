## Why

`_apply_propose_change()`'s `prepare()` (`queue_triage.py:2439`) spawns one
agent per `PROPOSE_CHANGE_PROMPT_TEMPLATE` (`queue_triage.py:214-233`) to
author `proposal.md`/`design.md`/`specs/`/`tasks.md`, then falls through to
the shared `_worktree_pr_close()` sequence. That sequence runs `openspec
validate --strict` (`queue_triage.py:2104`) *and* `worktrail-compile`
(`queue_triage.py:2124-2142`), failing the whole apply with
`"worktrail-compile failed: ..."` on a non-zero exit — but the prompt the
agent is actually given only tells it to satisfy `openspec validate
--strict`. It never mentions `worktrail-compile`, so the agent has no reason
to write `tasks.md` file scope that survives the compile gate's own checks
(`src/worktrail/conductor/parallelism.py:258` same-file-chain,
`src/worktrail/conductor/parallelism.py:279` missing-test-scope — the compile
gate was added in `156ba6a`). The result is a `propose-change` apply that
passes its own prepare step, then fails at the very next gate for a reason
the authoring agent was never told to avoid, landing as a `status: error`
outcome and leaving the brief stuck.

None of this repo's active changes touch `PROPOSE_CHANGE_PROMPT_TEMPLATE` or
`_worktree_pr_close()`'s compile step, so this needs its own change.

## What Changes

- `PROPOSE_CHANGE_PROMPT_TEMPLATE` gains an explicit instruction to also run
  `worktrail-compile openspec/changes/<proposed_change_name>` before
  finishing, and to fix any reported problem (including a same-file chain
  or a missing test-scope task) in `tasks.md` before finishing — mirroring
  the `openspec validate --strict` instruction already there, so the agent
  is told about both gates its own output will be checked against, not just
  one of them.

## Capabilities

### Modified Capabilities
- `intake-triage`: `Fold and propose are applied as a pull request,
  fail-closed` gains a requirement that a `propose-change` verdict's
  authoring prompt names both gates (`openspec validate --strict` and
  `worktrail-compile`) the generated change must pass.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`PROPOSE_CHANGE_PROMPT_TEMPLATE`)
- `tests/workqueue/test_queue_triage.py`

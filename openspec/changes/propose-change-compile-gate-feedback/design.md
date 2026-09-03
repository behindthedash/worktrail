## Context

`PROPOSE_CHANGE_PROMPT_TEMPLATE` (`src/worktrail/workqueue/queue_triage.py:214-233`)
is the only instruction the propose-change authoring agent gets. Today it
reads (paraphrased): scaffold the change's artifacts, then "the change must
pass `openspec validate <name> --strict` — run it yourself and fix any
errors before finishing." `_worktree_pr_close()` (called right after
`prepare()` returns) runs that same `openspec validate` command
(`queue_triage.py:2104-2122`), and then, only on success, runs
`worktrail-compile` against the change directory (`queue_triage.py:2124-2145`)
and fails the whole apply on a non-zero exit. The agent is never told the
second gate exists, so it has no way to know its `tasks.md` needs file scope
that clears `worktrail-compile`'s own checks (same-file chain, missing test
scope — `src/worktrail/conductor/parallelism.py:237-284`).

## Goals / Non-Goals

**Goals:**
- Tell the propose-change authoring agent about both gates its output must
  clear, so a compile-gate failure becomes rare instead of a surprise
  discovered only after the agent has already finished and handed off.

**Non-Goals:**
- Changing `_worktree_pr_close()`'s gate order, retry behavior, or error
  reporting — this is a prompt-content fix, not a control-flow change. A
  compile failure after this change still fails the apply the same way; it
  should just happen less often because the agent now knows to avoid it.
- Adding a second agent turn or a compile-and-retry loop. One prompt, one
  authoring pass, same as today — only its content changes.

## Decisions

### Extend the prompt's existing "must pass" instruction to name both gates

Change the trailing instruction from:

```
When you are done, the change must pass `openspec validate
{proposed_change_name} --strict` -- run it yourself and fix any errors
before finishing.
```

to additionally name `worktrail-compile openspec/changes/{proposed_change_name}`
as a second command the agent must run and fix before finishing, calling out
that a same-file chain or a missing test-scope task in `tasks.md` will fail
it. This is a single-template text change in
`src/worktrail/workqueue/queue_triage.py`; no other call site references
`PROPOSE_CHANGE_PROMPT_TEMPLATE`.

## Migration

None — prompt-text change only, no data or interface migration.

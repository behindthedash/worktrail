# Batch Consumption (`claim` action)

One brief per run is the floor, not the ceiling — when the queue is deep, fold related
queued briefs into the same run instead of leaving them behind. A batch is an execution
convenience, never a scope expansion: batch only briefs that share the same repo AND
would ride the same route/worktree/PR; when in doubt, leave a candidate in the queue.

## Procedure

1. Resolve the chosen brief's path (`worktrail-work-queue resolve <id> --json`), then detect
   batchable neighbours — same repo, similar spec/module surface:

   ```bash
   worktrail-score-candidates "<queue-path>" --queue-dir "$BASE" --mode batch
   ```

   Returns `{"batch": [{id, focus, reason}, ...]}` — queue-only, same-repo only;
   `related`-linked and same-`target-spec` briefs rank first; ≤3 companions. `reason` is
   one of `related-link` | `same-target-spec` | `score`.

2. If `batch` is empty, claim just the primary (`worktrail-work-queue claim <id> --json`) and
   continue. With two or more candidates, ask via `AskUserQuestion` (multiSelect, header
   `Batch briefs`): "These queued briefs touch the same surface — fold them into this
   run?", one option per candidate showing its id, focus, and reason. With exactly one
   candidate, the single-option guard applies (`AskUserQuestion` rejects one-element
   option arrays): skip the picker, fold the candidate in, and print a one-line note —
   `Batching <id> (<reason>); say so to drop it.` Claiming is reversible (`release`), so
   folding in beats losing the batch.

3. Claim the primary and the selected companions in one call:

   ```bash
   worktrail-work-queue claim-batch <primary-id> <companion-id> ... --json
   ```

   A companion reported `already-claimed`/`none` lost a race — proceed without it, never
   retry-loop. Claimed companions are stamped `batch-primary: <primary>`; the primary
   records `batch: [...]`.

4. Read every claimed brief, then treat their union as ONE request for the rest of the
   flow: one classification (Phase 5), one run record (list every brief id in
   `handoffs_consumed`), one worktree/PR — unless a companion genuinely classifies to a
   different route or repo, in which case release it (`worktrail-work-queue release <id>`)
   rather than forcing it in.

5. On completion mark EACH brief done individually. Use
   `worktrail-work-queue done <id> --implementation-complete` after implementation, or
   `--planning-only` only when the run explicitly stopped at planning.
   — batch execution never blurs per-brief completion state. A companion whose scope did
   NOT actually land must be released back to the queue, not marked done.

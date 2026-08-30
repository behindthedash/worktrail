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

2. If `batch` is empty, claim just the primary and continue:

   ```bash
   worktrail-work-queue claim <id> --by "$INVOCATION_CONTEXT_DISPATCH_ID" --json
   ```

   Always pass `--by "$INVOCATION_CONTEXT_DISPATCH_ID"` (resolved once in the Invocation
   Context step) — it is the identity `claim()` compares against a prior claimant's
   `claimed-by` to compute `same_owner`, distinguishing "this exact /go dispatch already
   claimed this brief" from "a different, possibly concurrent, dispatch already owns it."
   `status: "claimed"` → proceed. `status: "already-claimed"` with `same_owner: true` →
   also proceed (a retried claim within this same dispatch). `status: "already-claimed"`
   with `same_owner: false` (or `null`, which means `--by` was somehow not honored — treat
   it the same as `false`, never as `true`) → a different dispatch owns this brief; do NOT
   seed a pipeline against it. Report which brief was contended and stop this selection —
   re-list the queue (Step 1 above) and pick a different candidate rather than silently
   continuing on someone else's claim (`docs/specs/research/
   concurrent-go-dispatch-brief-claim-race.md` is the incident this guards against).

   With two or more batch candidates, ask via `AskUserQuestion` (multiSelect, header
   `Batch briefs`): "These queued briefs touch the same surface — fold them into this
   run?", one option per candidate showing its id, focus, and reason. With exactly one
   candidate, the single-option guard applies (`AskUserQuestion` rejects one-element
   option arrays): skip the picker, fold the candidate in, and print a one-line note —
   `Batching <id> (<reason>); say so to drop it.` Claiming is reversible (`release`), so
   folding in beats losing the batch.

3. Claim the primary and the selected companions in one call:

   ```bash
   worktrail-work-queue claim-batch <primary-id> <companion-id> ... --by "$INVOCATION_CONTEXT_DISPATCH_ID" --json
   ```

   The primary's result carries `same_owner` exactly as in Step 2 above — apply the same
   rule (`already-claimed` + `same_owner: false`/`null` aborts the batch, do not seed).
   A companion reported `already-claimed`/`none` lost a race — proceed without it, never
   retry-loop (companions don't need the `same_owner` check: losing a companion race just
   drops it from the batch, it never causes a duplicate pipeline the way a primary
   collision would). Claimed companions are stamped `batch-primary: <primary>`; the primary
   records `batch: [...]`.

4. Read every claimed brief, then treat their union as ONE request for the rest of the
   flow: one classification (Phase 5), one run record (list every brief id in
   `handoffs_consumed`), one worktree/PR — unless a companion genuinely classifies to a
   different route or repo, in which case release it
   (`worktrail-work-queue release <id> --by "$INVOCATION_CONTEXT_DISPATCH_ID"`) rather than
   forcing it in.

5. On completion mark EACH brief done individually. Use
   `worktrail-work-queue done <id> --implementation-complete --run "$RUN" --by "$INVOCATION_CONTEXT_DISPATCH_ID"`
   after implementation (the shared batch run record from step 4, verified against a
   PR-owning `finish()` state — see `work_queue.py`'s "Implementation closure evidence
   gate"), or `--planning-only` only when the run explicitly stopped at planning.
   — batch execution never blurs per-brief completion state. A companion whose scope did
   NOT actually land must be released back to the queue
   (`worktrail-work-queue release <id> --by "$INVOCATION_CONTEXT_DISPATCH_ID"`), not marked done.

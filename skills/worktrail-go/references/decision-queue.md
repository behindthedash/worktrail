# Decision queue — handing a product decision to a human without stalling

The decision queue (`worktrail-decision`, storage under `$WORK_QUEUE_DIR/decisions/`) is how an
unattended run converts a genuine product decision into an asynchronous question a human answers
on their own time — instead of stranding the brief in `picked/` until someone opens an
interactive session. Filed correctly, the loop closes itself: the brief goes back to `queue/`
blocked on the open decision, the human answers (`worktrail-decision answer <id> --answer "..."`
or by editing the record's `## Answer` section and moving the file to `decisions/answered/`),
the brief unblocks automatically, and the next drain pass claims it and continues from the
blocked point.

## Filing guardrails — what qualifies {#decision-filing-guardrails}

A decision record is for questions **only a human can answer**, that can be answered
**asynchronously**, about **what the product should do**:

- ambiguity between two defensible user-facing behaviors
- scope conflicts (brief vs spec vs epic disagree about what is in scope)
- whether apparent duplication is a true duplicate or a distinct deliverable
- ownership calls (which existing spec owns a capability)
- accepting a risk, cost, or irreversible action the policy does not pre-authorize

The litmus test: **if you had unlimited time and full repo access, could you determine the
answer yourself?** If yes, it is not a decision record — it is your job. Never file one for:

- engineering choices (naming, structure, library, test strategy) — decide and record the
  decision on the run record
- information recoverable from the repo, specs, git history, PRs, or run records — go read it
- failing tests, CI breakage, or environment problems — fix or finish `failed_recoverable`
- provider capacity or auth — that is the capacity-gate path, not a decision
- "the task is large/tedious" — that is the work

The drain enforces the incentive: a one-shot that finishes `blocked_product_decision` **with** a
freshly filed decision is a clean handled outcome; one that blocks **without** filing still
counts toward the drain's circuit breaker. A lazy or padded record wastes the human's time once
and gets rejected — `worktrail-decision ask` refuses records missing the question, the
why-this-is-a-product-call, the what-was-attempted context, or at least two concrete options,
and refuses a second open decision for the same brief.

## Filing procedure (auto mode) {#file-a-decision}

At any `$AUTO_MODE=true` site whose documented fallback is `blocked_product_decision`, file the
decision **before** finishing the run record, then release the brief and terminate:

```bash
DECISION=$(worktrail-decision ask \
  --question "<the single decision, phrased so it can be answered in one sentence>" \
  --background "<plain English for a reader with no context: what the problem is, why it exists, how this run got here>" \
  --why "<why this is a product call, not an engineering one>" \
  --context "<what was attempted, what evidence was gathered>" \
  --option "<option 1 — your preferred/priority option, with its tradeoff>" \
  --option-cost "low -- <e.g. config-only, ships today>" \
  --option "<option 2 — the alternative, with its tradeoff>" \
  --option-cost "high -- <e.g. better long-term architecture, ~3 days>" \
  --recommendation "<which option and why; condition it on product priority when it genuinely depends, e.g. 'quick to production: option 1; long-term architecture: option 2'>" \
  --repo "$REPO" --brief "$BRIEF_ID" --release --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
worktrail-run-record finish "$RUN" --status blocked_product_decision --merge-result \
  "<site's own one-line block summary> -- decision $DECISION filed; brief released awaiting the answer."
```

Write the record so a product owner can answer it from their phone without opening the repo:

- **Background is the story, not a log line** — what the situation is, why the tension exists,
  and how the run arrived at it, in plain English. Assume zero prior context.
- **Options in priority order** (your preference first), each a complete sentence with its
  tradeoff. One `--option-cost` per option labels the cost/effort axis so the human can weigh
  quick-to-production against the better long-term solution at a glance.
- **Condition the recommendation when it genuinely depends** on a product priority you cannot
  know ("if speed to production matters most: option 1; if the long-term architecture matters
  most: option 2 at higher cost"). When one option is simply right, say so plainly instead of
  manufacturing a condition.

`--brief --release` stamps `awaiting-decision: $DECISION` on the brief and returns it to
`queue/`, where `work_queue.py list` reports it `blocked` until the decision is answered — it
never surfaces to auto-pick in the meantime, and unblocks the moment the human answers. Then
**stop the session** — the whole point is that nothing lingers waiting on a human.

Dispatches with no claimed brief (brainstorm-sourced) file the same record without
`--brief`/`--release`; the open decision is still the human's inbox entry for the question.

If `worktrail-decision ask` itself fails (validation refusal you cannot satisfy, unwritable
queue), fall back to the legacy behavior for the site: finish `blocked_product_decision` and
leave the brief claimed in `picked/` for the stalled-in-flight resume path.

## Resuming from an answered decision {#resume-from-decision}

When a claimed brief's frontmatter carries `awaiting-decision: <id>`:

1. `worktrail-decision show <id>` — the `## Answer` section is the human's binding answer.
   Treat it exactly as an interactive `AskUserQuestion` response at the original block site:
   continue the route from that point, never re-litigate the question.
2. Record the consumed answer on the run record
   (`worktrail-run-record append "$RUN" decisions "decision <id>: <answer summary>"`).
3. `worktrail-decision resolve <id>` — archives the record to `decisions/resolved/` and strips
   the brief's `awaiting-decision` field.

If the decision resolves to no record (deleted) or is already `resolved`, proceed normally —
a stale link never blocks work. If it is somehow still `open` (the brief should have been
`blocked`), release the brief back (`worktrail-work-queue release "$BRIEF_ID"`) and stop.

## Human surface

- `worktrail-decision list` — everything open/answered/resolved at a glance.
- `worktrail-decision answer <id> --answer "..."` — the one command a human needs.
- The drain's end-of-run summary prints the count of decisions awaiting a human.

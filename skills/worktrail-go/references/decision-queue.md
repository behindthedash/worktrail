# Decision queue — handing a product decision to a human without stalling

The decision queue (`worktrail-decision`, storage under `$WORK_QUEUE_DIR/decisions/`) is how an
unattended run converts a genuine product decision into an asynchronous question a human answers
on their own time — instead of stranding the brief in `picked/` until someone opens an
interactive session. Filed correctly, the loop closes itself: the brief goes back to `queue/`
blocked on the open decision, the human answers (`worktrail-decision answer <id> --answer "..."`
or by editing the record's `## Answer` section and moving the file to `decisions/answered/`),
the brief unblocks automatically, and the next drain pass claims it and continues from the
blocked point.

## The versioned pending-decision envelope {#decision-envelope}

A decision record is markdown for the human; it also has a machine contract on
top, so the lifecycle stays auditable no matter which dispatch mode touched it.
Guards (`spec-collision-check.md`, `related-brief-collision-check.md`) emit every
judgment call they hand to a human as a **provider-neutral, versioned JSON envelope** (schema
`worktrail.pending-decision`, version `1`) under their result's
`pending_decision` key — built by `workqueue/decisions.py`'s
`pending_decision_envelope()`:

```json
{"schema": "worktrail.pending-decision", "version": 1,
 "decision_id": "dec-spec-collision-a1b2c3d4e5f6", "status": "pending",
 "question": "...", "options": ["...", "..."], "supersedes": null,
 "created_at": "<ISO-8601>",
 "provenance": {"source": "check_spec_collision", "repo": "/path/to/repo",
                "subject": "<spec id>", "run_id": "...", "dispatch_mode": "..."}}
```

Three properties carry the cross-mode auditability requirement:

- **Deterministic identity.** `decision_id` comes from
  `decisions.decision_identity()`, keyed on `(source, repo, subject,
  question)` — a guard re-firing on unchanged facts converges on ONE record
  instead of filing duplicates.
- **Provenance travels inside.** `source`/`repo`/`subject` (plus `brief`,
  `run_id`, `dispatch_mode` when known) are recorded on the record's
  frontmatter, so any later surface can verify that an answer it found
  actually belongs to the question that was asked.
- **Versioned.** A reader that does not understand the schema or version must
  refuse to act on the envelope, never misread its fields.

Filing still goes through `worktrail-decision ask` (the procedure below); once
a record exists, any host can reload its envelope with
`worktrail-skill-dispatch --present-decision <id>` and resume through the
exact id (`#decision-audit`, below).

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
worktrail-run-record decision "$RUN" --event asked --decision-id "$DECISION" \
  --note "pending-decision envelope filed; brief released awaiting the answer"
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

## Audit trail across dispatch modes {#decision-audit}

Every lifecycle hop of a filed decision is stamped onto the originating run
record's `pending_decisions` audit list — one `<ts> [<event>] <decision-id>`
entry per hop, idempotent per (event, decision-id) pair — so a later session
(attended, adapter, or unattended) can reconstruct what happened to a decision
from the run record alone:

```bash
worktrail-run-record decision "$RUN" --event asked --decision-id "$DECISION"
```

The event vocabulary is fixed by `run_record.py`'s `DECISION_EVENTS`; each
dispatch mode contributes its own hops:

| Event | Who stamps it | When |
|---|---|---|
| `asked` | the blocking run | a guard or procedure files the decision (the filing blocks above stamp this before `finish`) |
| `presented` | the attended host | `worktrail-skill-dispatch --present-decision` showed the envelope (`--run "$RUN"` stamps it automatically) |
| `answered` | whoever relays the human's reply | `worktrail-decision answer <id> --answer "..."` landed |
| `consumed` | the resuming run | the validated answer was applied to exactly one continuation (`#resume-from-decision`) |
| `superseded` | the replacing procedure | facts changed and the question was retired for a newer decision |

**Attended presentation — same bytes on every host.** An attended session never
re-renders the record by hand before asking; it surfaces the provider-neutral
envelope through the adapter boundary, which prints the identical versioned
JSON whether the child provider would be claude, codex, or opencode, stamps
the `[presented]` hop itself, and spawns nothing:

```bash
worktrail-skill-dispatch --present-decision "$DECISION" --run "$RUN"
```

This works for any status — presenting an open question IS the attended use
case. Exit 2 means the id did not resolve exactly; never guess at a near-match.

**Unattended surfacing.** A drain one-shot cannot wait: when its run ends with
unresolved decisions, drain classifies the iteration as its first-class
`pending_user_decision` stop (fail-closed, recoverable, never counted as a
generic failure), prints each blocking id with the recovery pair — answer,
then resume through the exact id (`worktrail-skill-dispatch --resume-decision
<id>`, threading the verbatim `decision:<decision-id>` token into the resumed
invocation) — and stops instead of re-spawning into the same unanswered guard.
The orchestrator's dispatch gate mirrors that posture from the other side: it
accepts only a provenance-validated resolved envelope and refuses every
unresolved one. Neither boundary guesses around a refused decision.

## Resuming from an answered decision {#resume-from-decision}

When a claimed brief's frontmatter carries `awaiting-decision: <id>`:

1. `worktrail-decision show <id>` — the `## Answer` section is the human's binding answer.
   Treat it exactly as an interactive `AskUserQuestion` response at the original block site:
   continue the route from that point, never re-litigate the question.
2. Validate before acting — the same gate the machine boundaries enforce
   (`validate_decision_answer()`): the record sits in `answered/` (not still
   open), it was not superseded after being answered, and its provenance
   matches this resume (same repo/subject/brief). A mismatch is a
   stop-and-report, never a guess.
3. Consume it exactly once — prefer the consume primitive over manual
   archiving: it verifies there IS an answer, archives the record stamped with
   who consumed it and when, unblocks the linked brief, and refuses a second
   consume of the same id (an answer applies to one continuation; it is never
   replayed into another):

   ```bash
   worktrail-decision consume "$DECISION_ID" --consumed-by "$INVOCATION_CONTEXT_DISPATCH_ID"
   ```

   `worktrail-decision resolve <id>` remains the manual-archive fallback — it
   neither stamps ownership nor refuses replay.
4. Stamp the audit hop and record the applied answer on the run record, so the
   next session sees the decision was consumed here and not elsewhere:

   ```bash
   worktrail-run-record decision "$RUN" --event consumed --decision-id "$DECISION_ID"
   worktrail-run-record append "$RUN" decisions "decision <id>: <answer summary>"
   ```

If the decision resolves to no record (deleted) or is already resolved/consumed,
proceed normally — a stale link never blocks work. If it is somehow still `open`
(the brief should have been `blocked`), release the brief back
(`worktrail-work-queue release "$BRIEF_ID" --by "$INVOCATION_CONTEXT_DISPATCH_ID"`) and stop.
If the record was superseded, do **not** act on its answer: re-run the guard at
the original block site so today's facts file (or converge on) the replacement
decision.

## Human surface

- `worktrail-decision list` — everything open/answered/resolved at a glance.
- `worktrail-decision answer <id> --answer "..."` — the one command a human needs.
- `worktrail-decision consume <id> --consumed-by <agent|dispatch-id>` /
  `worktrail-decision supersede <id> --new-decision-id <new-id>` — the resuming
  agent's once-only apply / retirement primitives.
- The drain's end-of-run summary prints the count of decisions awaiting a human,
  lists each blocking id, and names the exact recovery pair (`worktrail-decision
  answer`, then `worktrail-skill-dispatch --resume-decision <id>`).

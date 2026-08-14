# Pre-Dispatch Related-Brief Collision Guard {#related-brief-collision-check}

`/go` Phase 5.5's third branch, alongside `spec-collision-check.md` (Route C/D: does a shipped
spec already cover this request?) and `brief-staleness-check.md` (every brief-sourced dispatch:
did this brief's own work already land?). This branch asks a different question again: a
brief's `related:` frontmatter names other briefs describing adjacent or overlapping work — is
one of *those* **actively claimed and in flight right now**, by this agent or another one?
Nothing before dispatch checks this today, so a second agent can start work that collides with a
session already underway, discovered only when the two land conflicting changes.

**Gate: brief-sourced dispatch, claimed brief has a `related:` field, and the resolved route is
not C, D, E, or F.** This scope is unchanged by the brief-staleness widening below: routes C/D
keep their own dedicated spec-collision branch and routes E/F keep the narrowest, cheapest
signal (staleness alone) without adding a second prompt surface on top of it. Route A/B/G–J is
where this branch adds a check nothing else runs. It runs alongside the brief-staleness branch
on every route it fires on, since that branch is not route-gated. A brainstorm/free-text
dispatch has no claimed brief to read `related:` off, so it skips this branch regardless of
route. A claimed brief with no `related:` entries skips it too — `check()` itself short-circuits
on that, but the caller should not invoke it needlessly.

## Running it

```bash
RELATED_JSON=$(worktrail-check-related-brief-claims \
  --brief "$CLAIMED_BRIEF_PATH" --json 2>/dev/null)
```

`--brief` is the only required flag; it reads the claimed brief's `related:` frontmatter directly
(no separate extraction step). `--picked-dir` and `--queue-dir` default to the work queue's own
`picked/` and `queue` directories and rarely need overriding. Like its two siblings, the command
**always exits 0** — a signal source for a human decision, never a gate. Never test its exit
code; read `checked`.

## Reading the result

```json
{"checked": true,
 "active": [{"id": "", "path": "", "claimed-by": "", "claimed-at": "", "repo": "",
             "focus": "", "run_record": ""}],
 "warning": null}
```

| Result | Meaning | Action |
|---|---|---|
| `checked: false` | The question could not be asked — the claimed brief itself couldn't be read or its `related:` field wasn't a list. | **Proceed.** Treat as no signal, never as "nothing collides". Do not prompt. |
| `checked: true`, `active` empty | Every related id was checked; none currently resolves to a `picked`-status brief. A definite negative — a related id that is merely still queued, or already `done`, is not an active match. | **Proceed.** Do not prompt. |
| `checked: true`, `active` non-empty | One or more related briefs are claimed and in flight right now. | **Prompt the operator** (below), batched across every entry in `active`. |

`warning` may be non-null on any row (an individual related id that failed to resolve was
skipped, not fatal to the rest of the check) and never changes the action on its own — surface it
alongside the evidence when prompting; ignore it otherwise.

Each `active` entry's `run_record` field is present only when that match's `claimed-by` is this
same machine's own agent label *and* a local `~/.worktrail/runs/<repo-name>/*.yaml` run record could be
found referencing the related brief's id — purely informational, never required for the prompt.

## The operator prompt

One brief can have several `related:` entries actively claimed at once; ask about all of them in
a single batched question rather than one prompt per match:

```
AskUserQuestion(
  questions=[{
    question: "This brief lists related work that is actively claimed right now:\n"
              "{for each entry in active}\n"
              "  {id}  claimed-by={claimed-by}  claimed-at={claimed-at}  repo={repo}\n"
              "    focus: {focus}\n"
              "{end for}\n\nHow should we proceed?",
    header: "Related-brief collision",
    options: [
      {label: "Proceed with the dispatch",
       description: "The overlap is unrelated or acceptable. Continue to Phase 6/7 unchanged."},
      {label: "Pause and coordinate",
       description: "Hold off dispatching this brief until the related, in-flight work lands or is checked with its owner."}
    ]
  }]
)
```

Never default-select, never auto-proceed, and never infer the answer from the match count. Unlike
the spec-collision and brief-staleness branches, a match here is never grounds to auto-close the
brief — the related work being in flight says nothing about whether *this* brief's own work is
already done, so `work_queue.py done` is never called from this branch.

**`$AUTO_MODE=true`: no ask.** There is no human present to answer, and `AskUserQuestion` is not
even a callable tool inside the headless one-shot `worktrail-go drain` spawns — do not attempt
the call above. Default to the safe side, same as an interactive "Pause and coordinate": do not
open a worktree and do not start Phase 6/7. Phase 6 has not run yet for this dispatch, so open a
minimal run record now (the same fields Phase 6 would use) purely to record the block. **Before**
finishing it, file the judgment call as a decision record and release the brief per
`decision-queue.md#file-a-decision` — question: is the overlap with the actively claimed
brief(s) acceptable, or should this dispatch wait?; options: "proceed — overlap acceptable" vs
"wait for <ids> to land first"; context: the claimed ids and what they touch:

```bash
RUN=$(worktrail-run-record start --repo "$REPO" \
  --request "${BRIEF_FOCUS:-$ARG_INTENT}" --route "$ROUTE" --risk "${RISK_LEVEL:-medium}" \
  --agent "$INVOCATION_CONTEXT_AGENT" | python3 -c "import sys, json; print(json.load(sys.stdin)['path'])")
DECISION=$(worktrail-decision ask \
  --question "Is the overlap with <id list> (actively claimed right now) acceptable, or should this dispatch wait?" \
  --background "The related-brief collision check found <id list> actively claimed and in flight right now, named as related: by this brief. Auto mode has no one to ask, so the brief is being released back to the queue pending this answer instead of dispatched or stranded." \
  --why "Whether the in-flight overlap is acceptable to proceed alongside, or should block this dispatch until it lands, is a judgment call only a human can make from what each brief touches." \
  --context "Actively claimed related brief(s): <id list>; what they touch: <summary>." \
  --option "Proceed -- overlap acceptable" \
  --option-cost "low -- dispatch continues on next pass" \
  --option "Wait for <ids> to land first" \
  --option-cost "medium -- re-release with --next-check-after on next pass" \
  --recommendation "Read what each in-flight brief touches: if the file/module surface is disjoint from this dispatch, proceed; if it overlaps, wait." \
  --repo "$REPO" --brief "$BRIEF_ID" --release --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
worktrail-run-record finish "$RUN" --status blocked_product_decision --merge-result \
  "Auto-mode related-brief collision: <id list> actively claimed right now. Decision $DECISION filed; brief released awaiting the answer."
```

The human answers asynchronously; on "wait", the resuming session can re-release with
`--next-check-after` — see `decision-queue.md#resume-from-decision`. Do not call
`work_queue.py done` yourself, and do not release by hand — `worktrail-decision ask --brief ...
--release` above is what stamps `awaiting-decision`. Only if `worktrail-decision ask` itself
fails (validation refusal you cannot satisfy, unwritable queue), fall back to `run-record finish`
without a decision and leave the brief claimed in `picked/` for the stalled-in-flight resume
path (dashboard `resume` action). Stop; do not continue to Phase 6/7 for this dispatch.

**On "proceed"** — continue to Phase 6/7 unchanged. Once Phase 6 has opened the run record,
record the evidence and the decision on it, so a later session (including whoever lands the
related work) does not re-discover the same overlap cold:

```bash
worktrail-run-record append "$RUN" decisions \
  "Related-brief collision guard surfaced <N> actively-claimed related id(s) (<id list>); operator judged the overlap <unrelated|acceptable> and chose to proceed."
```

**On "pause and coordinate"** — do not open a worktree and do not start Phase 6/7 for this
dispatch. Report the pause in the run's status output (e.g. `Dispatch paused: brief $BRIEF_ID
lists related id(s) <id list> claimed by <claimed-by>, still in flight.`) and stop; the brief
stays claimed by the current session (this branch never touches queue state), so it can simply be
re-attempted later once the related work lands.

## Relationship to the sibling branches

All three Phase 5.5 branches ask "has this already been done?" from a different angle, and their
gates are no longer mutually exclusive: brief-staleness runs on every brief-sourced dispatch, so
it co-runs with this branch on Route A/B/G–J and with spec-collision on Route C/D. This branch
and spec-collision remain mutually exclusive with each other (Route C/D vs. everything else),
each gated to the one route pair it covers. This branch is the only one of the three that checks
a *different* brief's status rather than the one being dispatched.

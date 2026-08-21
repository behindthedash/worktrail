# Pre-Dispatch Brief-Staleness Guard

`/go` Phase 5.5's branch that runs for every brief-sourced dispatch. It asks: **did the work
this brief describes already land while it sat in the queue?** Its siblings ask related but
different questions on a narrower route gate — `spec-collision-check.md` asks "does a shipped
spec already cover this request?" for a Route C/D dispatch, and `related-brief-collision-check.md`
asks "is a brief this one names as related actively claimed right now?" for routes A/B/G-J. This
branch, unlike those two, is not route-gated: it runs alongside whichever sibling also applies.

Incident (2026-08-05): brief `20260731-204048` (`prevent-destructive-commands.py` squash-merge +
`cd`-prefix verification) was fully delivered by `behindthedash/devops` PR #89, merged
2026-08-02 — one day after the brief was captured. It stayed claimable for five more days until
a session claimed it, classified it, opened a run record, and only then discovered there was
nothing to do. The verification itself cost about four tool calls; the waste was that it happened
*after* the dispatch rather than before it.

A second incident (2026-08-10, discovered while working brief `20260722-152347`) surfaced the
same waste on a Route J dispatch — a route this branch did not yet cover: the requested work had
already been delivered by prior PRs #68 and #70, caught only by manual `git log -S`/`gh pr view`
diligence after Phase 6 had already opened a run record.

**Gate: brief-sourced.** A brainstorm/free-text dispatch has no `created:` timestamp to bound
the search and no captured prose to extract probes from, so it skips this branch regardless of
route. A brief-sourced dispatch runs this branch on **every** resolved route (`A`-`J`) — there is
no route exclusion. On routes C/D it runs in addition to the spec-collision branch; on routes
A/B/G-J (when the brief carries `related:` entries) it runs in addition to the related-brief-
collision branch. Running more than one branch for the same dispatch is expected, not a bug:
each branch answers a different question and none suppresses another.

## Predicate re-check

Before any of the probe-based checking below, re-derive whether the brief's own captured
*predicate* is still true. Some briefs (e.g. those filed by a checkbox-drift sweep) do not just
describe stale work to search for — they carry a specific claim about repo state in their
frontmatter (`drift-source`, `drift-findings`) that may itself have already been resolved by the
time the brief is dispatched. Read the claimed brief's frontmatter, then run:

```bash
RECHECK_JSON=$(worktrail-recheck-brief-predicate \
  --repo "$REPO" --brief "$CLAIMED_BRIEF_PATH" --json 2>/dev/null)
```

Branch on `attempted` and `outcome` before ever reaching today's "Running it" step below:

| `attempted` | `outcome` | Meaning | Action |
|---|---|---|---|
| `false` | `no-predicate` | Brief carries no `drift-source`; nothing to re-check. | Fall through to "Running it" unchanged. |
| `false` | `unrecognized` | `drift-source` is set but no re-check is registered for it. | Fall through to "Running it" unchanged. |
| `true` | `error` | A predicate was registered but re-checking it failed (missing `drift-findings`, an unreadable task file, or the recheck function itself raised). | Fall through to "Running it" unchanged. |
| `true` | `still-true` | Every `drift-findings` entry still holds against current on-disk state. | Documented separately, below the probe-based flow. |
| `true` | `resolved` | No `drift-findings` entry still holds. | Documented separately, below the probe-based flow. |

Any `attempted: false` outcome (`no-predicate`, `unrecognized`) or `outcome: "error"` is not a
signal of anything — it means the same as never having run this check at all, and today's
"Running it" section proceeds exactly as it does for a non-drift brief, with no new behavior
inserted between the gate above and the `worktrail-check-brief-staleness` invocation below.

**On `outcome: "still-true"`** — the predicate still holds, so there is nothing to prompt about
and nothing to close: continue straight to Phase 6/7 unchanged, exactly as the probe-based
"proceed" branch does when its own evidence is empty (no operator prompt, no early run-record
open). Once Phase 6 has opened the run record, append the evidence line
`check_brief_predicate.format_still_true_evidence` builds from the recheck result, using the
same post-Phase-6 `run-record append` pattern the probe-based "proceed" branch uses below:

```bash
worktrail-run-record append "$RUN" decisions \
  "Predicate re-check (checkbox-drift-sweep) found the staleness predicate still true for \
2 finding(s): docs/specs/x/tasks/TASK-001.md, docs/specs/x/tasks/TASK-004.md. Proceeded \
automatically without an operator prompt."
```

Unlike the probe-based evidence line, there is no commit SHA or PR number to cite — the probe
search never runs on this path — so the still-true task-file paths themselves are the cited
evidence.

**On `outcome: "resolved"`** — every `drift-findings` entry has resolved, so the brief is already
delivered and there is nothing left to dispatch: close it now, before Phase 6, exactly as the
probe-based "close as already-delivered" branch does below (no operator prompt — the predicate
re-check itself is the evidence, not a human judgment call). The queue mutation goes through
`work_queue.py`, the single owner of brief lifecycle, using the closure note
`check_brief_predicate.format_resolved_closure_note` builds from the recheck result:

```bash
worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note \
  "Closed as already-delivered: predicate re-check (checkbox-drift-sweep) found the staleness \
predicate resolved for 2 finding(s): docs/specs/x/tasks/TASK-001.md, \
docs/specs/x/tasks/TASK-004.md. Surfaced by the Phase 5.5 predicate re-check; closed \
automatically without an operator prompt."
```

Then report the closure in the run's status output (e.g. `Brief $BRIEF_ID closed: predicate
re-check found N finding(s) resolved.`) and **stop** — do not continue to Phase 6's run-record
start or Phase 7 dispatch. As with the probe-based closure branch, this runs *before* Phase 6, so
there is no run record to open or finish. Do not open a worktree, and do not create a follow-up
handoff — nothing was deferred. Unlike that sibling note, there is no commit SHA or PR number to
cite here — the probe search never runs on this path — so the predicate re-check, named
explicitly, is the cited evidence.

## Running it

```bash
STALENESS_JSON=$(worktrail-check-brief-staleness \
  --repo "$REPO" --brief "$CLAIMED_BRIEF_PATH" --json 2>/dev/null)
```

`--brief` reads both the focus prose and the `created:` timestamp off the brief, using the same
reader the rest of the dispatch path uses (`handoff_seed.build_seed`), so there is no second,
subtly different parse of a brief in play. `--text` plus `--since` is the manual equivalent when
you already have the prose in hand. `--base` overrides the searched branch; omitted, it resolves
the remote's default branch, so a stale local checkout does not blind the check to upstream work.

The command **always exits 0**. It is a signal source for a human decision, not a gate — a
non-zero exit would turn "could not determine" into a dispatch failure, which is exactly the
fail-open contract it exists to honor. Never test its exit code; read `checked`.

## File-state verification

Runs only when there is both something to verify and no earlier step that already decided the
outcome: `STALENESS_JSON`'s `matches`, `pull_requests`, or `research_notes` is non-empty
(`checked: true` with all three empty is a definite negative with nothing to verify — see
"Reading the result" below), and the
predicate re-check above did not already determine the outcome (any `attempted`/`outcome`
combination other than `still-true`/`resolved` reaches this step unchanged). When both hold,
before showing the operator anything, read or grep the brief's named paths and symbols — the
same ones the probes matched — for the *specific capability* the brief's focus prose describes.
This is a targeted read of the matched files/symbols, not a fresh investigation of the whole
repo: the question is not merely whether a file exists or a symbol appears, but whether the
capability the brief asks for is actually implemented, as described, in what is there now.

Classify what you find as exactly one of:

| Classification | Meaning |
|---|---|
| `verifiably-absent` | The named paths/symbols were read, and the specific capability the brief describes is confirmed absent from their current content. |
| `verifiably-present` | The named paths/symbols were read, and the specific capability the brief describes is confirmed present in their current content. |
| `inconclusive` | The read was partial (e.g. a named path no longer exists), ambiguous, or the capability is implemented differently than the brief describes — anything short of a confident absent/present call. |

Default to `inconclusive` whenever in doubt. A guess that turns out wrong either strands the
brief with silently unfinished work (a false `verifiably-absent`) or auto-closes a brief whose
work is not actually done (a false `verifiably-present`) — both worse than falling through to
the human judgment call `inconclusive` reaches. The outcome of this classification decides the
next step, documented below.

## Reading the result

```json
{"checked": true, "probes": {"paths": [], "symbols": [], "pull_requests": [], "dropped": 0},
 "matches": [{"sha": "", "date": "", "subject": "", "probe": "", "kind": "path|symbol"}],
 "pull_requests": [{"number": 0, "title": "", "url": "", "merged_at": ""}],
 "research_notes": [{"sha": "", "date": "", "path": "", "probe": "", "kind": "path|symbol"}],
 "warning": null}
```

| Result | Meaning | Action |
|---|---|---|
| `checked: false` | The question could not be asked — not a git checkout, missing/malformed `created:`, no probes extracted, or a git failure. | **Proceed.** Treat as no signal, never as "nothing landed". Do not prompt. |
| `checked: true`, `matches`, `pull_requests`, and `research_notes` all empty | Probes were searched; nothing landed since capture. A definite negative. | **Proceed.** Do not prompt. |
| `checked: true`, `matches`, `pull_requests`, or `research_notes` non-empty | Evidence exists that something touched the brief's named files/symbols since capture, or that an existing research note already documents them. | Run **File-state verification** (above) before showing anything to the operator. |

`warning` may be non-null on any of these rows and never changes the action on its own — it
carries partial-degradation detail (a timed-out probe, `gh` unavailable, results capped). Surface
it alongside the evidence when prompting; ignore it otherwise.

Evidence is **not** proof. A probe like a widely-touched file, or a symbol name that appears in an
adjacent refactor, will match commits that have nothing to do with the brief. That is the expected
common case, and it is why the operator decides.

## The operator prompt

Show the matching commits, PRs, and research notes, then ask exactly one question. Two outcomes,
both first-class:

```
AskUserQuestion(
  questions=[{
    question: "This brief may already be delivered. Since it was captured ({created}), "
              "{N} commit(s), {M} merged PR(s), and {K} research note(s) touched or documented "
              "what it names:\n"
              "{evidence lines}\n\nIs this brief already done?",
    header: "Brief staleness",
    options: [
      {label: "Close as already-delivered",
       description: "The surfaced work satisfies this brief. Close it, citing the evidence. No dispatch."},
      {label: "Proceed with the dispatch",
       description: "The evidence is unrelated or only a partial delivery. Continue to Phase 6/7 unchanged."}
    ]
  }]
)
```

Never default-select, never auto-close, and never infer the answer from the match count.

**`$AUTO_MODE=true`: no ask.** There is no human present to answer, and `AskUserQuestion` is not
even a callable tool inside the headless one-shot `worktrail-go drain` spawns — do not attempt
the call above. Phase 6 has not run yet for this dispatch, so open a minimal run record now
(the same fields Phase 6 would use) purely to record the block. **Before** finishing it, file
the judgment call as a decision record and release the brief per
`decision-queue.md#file-a-decision` — question: does the cited delivery actually close this
brief?; options: "close as already-delivered" vs "still open — the commits are unrelated";
context: the exact commits/PRs found:

```bash
RUN=$(worktrail-run-record start --repo "$REPO" \
  --request "${BRIEF_FOCUS:-$ARG_INTENT}" --route "$ROUTE" --risk "${RISK_LEVEL:-medium}" \
  --agent "$INVOCATION_CONTEXT_AGENT" | python3 -c "import sys, json; print(json.load(sys.stdin)['path'])")
DECISION=$(worktrail-decision ask \
  --question "Does the cited delivery actually close brief $BRIEF_ID?" \
  --background "The staleness guard found evidence -- $N commit(s)/$M merged PR(s) -- touching what brief $BRIEF_ID names, since it was captured ($created). Auto mode has no one to ask, so the brief is being released back to the queue pending this answer instead of dispatched or stranded." \
  --why "Whether surfaced evidence is unrelated context or an actual delivery of the brief's own scope is a judgment call only a human can make from the evidence." \
  --context "Evidence: ${EVIDENCE_SUMMARY}" \
  --option "Close as already-delivered -- the evidence satisfies this brief" \
  --option-cost "low -- work_queue.py done on next pass, no dispatch" \
  --option "Still open -- the evidence is unrelated or only a partial delivery" \
  --option-cost "medium -- dispatch proceeds on next pass" \
  --recommendation "Read the evidence: if it plainly matches the brief's own requested scope, close; if it is context/prior-work the brief cites rather than delivers, proceed." \
  --repo "$REPO" --brief "$BRIEF_ID" --release --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
worktrail-run-record finish "$RUN" --status blocked_product_decision --merge-result \
  "Auto-mode staleness guard: brief may already be delivered -- $N commit(s)/$M merged PR(s) touched what it names since it was captured ($created). Decision $DECISION filed; brief released awaiting the answer."
```

The next drain pass either closes the brief (`work_queue.py done`, citing the answer) or
proceeds with the dispatch once the decision is answered — see
`decision-queue.md#resume-from-decision`. Do not call `work_queue.py done` yourself at this
point, and do not release by hand — `worktrail-decision ask --brief ... --release` above is what
stamps `awaiting-decision`. Only if `worktrail-decision ask` itself fails (validation refusal
you cannot satisfy, unwritable queue), fall back to `run-record finish` without a decision and
leave the brief claimed in `picked/` for the stalled-in-flight resume path (dashboard `resume`
action). Stop; do not continue to Phase 6/7 for this dispatch.

**On "close as already-delivered"** — the queue mutation goes through `work_queue.py`, the single
owner of brief lifecycle, exactly as every other close does. Cite the evidence in the note:

```bash
worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note \
  "Closed as already-delivered: <sha> \"<subject>\" / PR #<n> \"<title>\" merged <date>, after the brief's created: <created>. Surfaced by the Phase 5.5 staleness guard; confirmed by operator."
```

Then report the closure in the run's status output (e.g. `Brief $BRIEF_ID closed: already
delivered by <sha>/PR #<n>, confirmed by operator.`) and **stop** — do not continue to Phase 6's
run-record start or Phase 7 dispatch. As with the sibling collision branch, this runs *before*
Phase 6, so there is no run record to finish. Do not open a worktree, and do not create a
follow-up handoff — nothing was deferred.

**On "proceed"** — continue to Phase 6/7 unchanged. Once Phase 6 has opened the run record,
record both the evidence and the decision on it, so the next session does not re-litigate the
same matches:

```bash
worktrail-run-record append "$RUN" decisions \
  "Staleness guard surfaced <N> commit(s)/<M> PR(s) against this brief's probes; operator judged them <unrelated|partial> and chose to proceed. Evidence: <sha list>."
```

## Cost and bounds

Probe counts are capped per kind and every subprocess is timeout-bounded, with a separate
aggregate budget for the network-dependent `gh` phase — all module-level constants in
`check_brief_staleness.py`, deliberately not policy knobs, so the check stays cheap enough that
nobody weighs whether to run it. Anything dropped by a cap is *counted* (`probes.dropped`, and
warnings for capped PR results and skipped probes), never silently discarded.

The predicate re-check above (`check_brief_predicate.py`) adds no comparable cost: it spawns no
subprocess and hits no network, only `Path.read_text` on the task files named in the brief's own
`drift-findings` — bounded by however many findings the drift sweep that filed the brief captured,
which is small in practice since it lists specific task files, not a repo-wide scan. It stays
cheap enough that nobody weighs whether to run it, same as the probe-based check above.

## Relationship to the batch queue triager

This is the inline, per-dispatch, near-zero-cost check on the one brief you are about to start.
The batch triager is a scheduled sweep over the whole queue at a far larger token cost. They
compose: the triager catches backlog rot; this catches the brief in your hand. Neither replaces
the other, and this check never inspects a brief other than the one being dispatched.

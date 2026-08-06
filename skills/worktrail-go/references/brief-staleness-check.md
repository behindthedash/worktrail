# Pre-Dispatch Brief-Staleness Guard

`/go` Phase 5.5's sibling branch to `spec-collision-check.md`. That one asks "does a shipped spec
already cover this request?" for a Route C/D dispatch. This one asks a different question for a
brief-sourced Route E/F dispatch: **did the work this brief describes already land while it sat
in the queue?**

Incident (2026-08-05): brief `20260731-204048` (`prevent-destructive-commands.py` squash-merge +
`cd`-prefix verification) was fully delivered by `behindthedash/devops` PR #89, merged
2026-08-02 — one day after the brief was captured. It stayed claimable for five more days until
a session claimed it, classified it, opened a run record, and only then discovered there was
nothing to do. The verification itself cost about four tool calls; the waste was that it happened
*after* the dispatch rather than before it.

**Gate: brief-sourced AND route E or F.** Both conditions. A brainstorm/free-text dispatch has no
`created:` timestamp to bound the search and no captured prose to extract probes from, so it
skips this branch even on route E/F. A brief-sourced dispatch on any other route skips it too —
routes C/D run the spec-collision branch instead, and the two never both run.

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

## Reading the result

```json
{"checked": true, "probes": {"paths": [], "symbols": [], "pull_requests": [], "dropped": 0},
 "matches": [{"sha": "", "date": "", "subject": "", "probe": "", "kind": "path|symbol"}],
 "pull_requests": [{"number": 0, "title": "", "url": "", "merged_at": ""}], "warning": null}
```

| Result | Meaning | Action |
|---|---|---|
| `checked: false` | The question could not be asked — not a git checkout, missing/malformed `created:`, no probes extracted, or a git failure. | **Proceed.** Treat as no signal, never as "nothing landed". Do not prompt. |
| `checked: true`, `matches` and `pull_requests` both empty | Probes were searched; nothing landed since capture. A definite negative. | **Proceed.** Do not prompt. |
| `checked: true`, `matches` or `pull_requests` non-empty | Evidence exists that something touched the brief's named files/symbols since capture. | **Prompt the operator** (below). |

`warning` may be non-null on any of these rows and never changes the action on its own — it
carries partial-degradation detail (a timed-out probe, `gh` unavailable, results capped). Surface
it alongside the evidence when prompting; ignore it otherwise.

Evidence is **not** proof. A probe like a widely-touched file, or a symbol name that appears in an
adjacent refactor, will match commits that have nothing to do with the brief. That is the expected
common case, and it is why the operator decides.

## The operator prompt

Show the matching commits and PRs, then ask exactly one question. Two outcomes, both first-class:

```
AskUserQuestion(
  questions=[{
    question: "This brief may already be delivered. Since it was captured ({created}), "
              "{N} commit(s) and {M} merged PR(s) touched what it names:\n"
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

## Relationship to the batch queue triager

This is the inline, per-dispatch, near-zero-cost check on the one brief you are about to start.
The batch triager is a scheduled sweep over the whole queue at a far larger token cost. They
compose: the triager catches backlog rot; this catches the brief in your hand. Neither replaces
the other, and this check never inspects a brief other than the one being dispatched.

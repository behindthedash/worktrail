# Pre-Dispatch Spec-Collision Guard

`/go` classifies a request into a route and, for Route C (spec) or Route D (implementation),
dispatches straight into fresh spec/implementation work. Neither `classify.py` nor
`dashboard.py` ever compares the request against `docs/specs/`'s own shipped history — a Route
C/D dispatch has no signal that an existing, already-`Implemented` spec covers the same actor +
capability + primary domain. Incident (2026-07-24): a queued brief was claimed and dispatched
straight to Route D before anyone checked whether the feature it described had already shipped
under an earlier spec whose task `files:` were already git-tracked on the base branch — the
redundant implementation work was only caught in review, after a worktree and PR had already
been opened. Phase 5.5 closes that gap by running the same collision check right before Phase 6
opens a run record, using `check_spec_collision.py` (pure extraction + artifact verification)
plus the calling agent's own semantic judgment — the same actor + capability + primary domain
rule `overlap_check.py`'s `#overlap-check` step already applies to Route A/new-feature
brainstorming (`references/subagent-prompts.md`), just run once more, later, against briefs and
free-text that skip brainstorming's overlap gate entirely (claimed queue briefs,
`route:C`/`route:D` overrides, handoff-recommended routes).

**Gate: Route C or D only.** This check runs only when Phase 5's resolved route (`$ROUTE` from
classify.py's `route`, an explicit `route:C`/`route:D` override, or a handoff's
`recommended-route`) is `C` or `D`. Any other route (`A`, `B`, `E`–`J`, or a low-confidence
classification still awaiting clarification) skips it — a Route F bugfix dispatch, for example,
never runs *this* check, because a bugfix is a change to an existing codebase, not a new spec
that could collide with one already shipped.

A brief-sourced Route C/D dispatch is not covered by this check alone, though: Phase 5.5's
sibling **brief-staleness** branch also runs for it (it runs for every brief-sourced dispatch,
regardless of route), asking a different question — did the work this brief describes already
land while it sat in the queue? See `brief-staleness-check.md`. The two branches share no state
and run independently; neither suppresses, gates, or alters the other, and both MAY run for the
same Route C/D dispatch.

```bash
if [ "$ROUTE" = "C" ] || [ "$ROUTE" = "D" ]; then
  COMPARISON_TEXT="${BRIEF_FOCUS:-$ARG_INTENT}"
  COLLISION_JSON=$(worktrail-check-spec-collision --repo "$REPO" --json 2>/dev/null)
  CHECKED=$(echo "$COLLISION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('checked', False))" 2>/dev/null)
  if [ "$CHECKED" = "True" ]; then
    # Judge each candidate (spec_id/title/feature_summary) against $COMPARISON_TEXT using the
    # same actor + capability + primary domain rule as references/subagent-prompts.md#overlap-check.
    # Only when exactly one candidate is judged a strong match, verify it:
    VERIFY_JSON=$(worktrail-check-spec-collision --repo "$REPO" --verify "$MATCHED_SPEC_ID" --json 2>/dev/null)
    CONFIRMED=$(echo "$VERIFY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confirmed', False))" 2>/dev/null)
  fi
fi
```

`$BRIEF_FOCUS` is the claimed brief's `focus` frontmatter field, extracted the same way Phase 5
already extracts `recommended-route` (`handoff_seed.py seed "<claimed-path>" --json | python3 -c
"import sys, json; print(json.load(sys.stdin).get('focus') or '')"`). A brainstorm-sourced
dispatch (no claimed brief, free-text typed straight at `/go`, no `handoff:ID` in play) has no
brief to read `focus` from, so `$COMPARISON_TEXT` falls back to `$ARG_INTENT` (or classify.py's
own derived summary of it, when `$ARG_INTENT` alone is too terse to compare against a
`feature_summary`).

## Dispatch source 1: brief-sourced (claimed queue brief present)

On `CONFIRMED = True`, do **not** ask the user — close the brief immediately, citing the
matching spec and the verification evidence, then stop (no fresh dispatch):

```bash
worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note \
  "Closed as a pre-existing collision with $MATCHED_SPEC_ID ($MATCHED_TITLE) -- Status: Implemented, files: ${VERIFY_FILES} all git-tracked on $BASE."
```

A Route-C brief closes with `--implementation-complete` here, never `--planning-only` — Phase
5.5 has just established the requested implementation already exists, and `--planning-only`
would leave the brief re-eligible for a future planning pass against work that's already done.

Report the closure to the user in the run's status output (e.g. `Brief $BRIEF_ID closed:
already covered by $MATCHED_SPEC_ID ($MATCHED_TITLE), confirmed shipped.`) and stop — do not
continue to Phase 6's run-record start or Phase 7 dispatch for the original request.

## Dispatch source 2: brainstorm-sourced (no claimed brief)

On `CONFIRMED = True` with no brief to close, do **not** call `work_queue.py done` — there is no
brief in play, and closing one would be wrong. Instead, stop and ask the user:

```
AskUserQuestion(
  questions=[{
    question: "This request looks like it collides with an existing, already-shipped spec
      `{spec_id}` -- \"{title}\" (no brief to close -- this request came from free text).
      Verification: Status: Implemented, files: {files} all git-tracked on {base}.
      \n\nHow should we proceed?",
    header: "Spec collision found",
    options: [
      {label: "Stop", description: "Do not dispatch -- the feature already exists"},
      {label: "Extend existing spec", description: "Route this into the existing spec instead of a new one"},
      {label: "Continue anyway", description: "Dispatch as originally requested (false positive or deliberate re-implementation)"},
    ],
  }]
)
```

Dispatch proceeds to Phase 6/7 only per the user's explicit choice: "Continue anyway" resumes
the original Route C/D dispatch unmodified; "Extend existing spec" re-routes to the matched
spec instead of a new one; "Stop" ends the run with no dispatch.

## Non-file artifact spot-check

`verify()`'s `note` field surfaces a non-file artifact claim found in the matched spec's prose
(e.g. `**Artifacts**: migrated the donor table`, a cron entry, a deployed service, a remote log)
that its `files:` git-tracking check cannot confirm on its own. `note` never affects
`confirmed` — it is purely something to hand the user (brainstorm-sourced path, fold it into the
`AskUserQuestion` prompt) or record in the closure note (brief-sourced path) as a prompt to
spot-check that artifact by hand before fully trusting the collision.

**What the check does.** `check_spec_collision.py --json` enumerates every spec under
`docs/specs/` as a candidate (`spec_id`, `stage`, `title`, `feature_summary`) — pure extraction,
no semantic judgment. The calling agent (this SKILL.md's own reasoning turn, not the script)
judges whether any candidate is a strong match against `$COMPARISON_TEXT`, applying the exact
actor + capability + primary domain rule `references/subagent-prompts.md#overlap-check` already
documents. Only when the agent judges a match does `--verify <spec_id> --json` run, checking the
matched spec's `**Status**:` header (must read `Implemented`) and whether every task `files:`
entry is git-tracked at `$REPO`'s base branch — `confirmed: true` only when both hold.

**Best-effort, never a hard dependency.** `checked: false` (no `docs/specs/` directory, an
unreadable spec file, a sibling-module import failure) means "no signal" — proceed to Phase 6 as
if no candidates exist. Likewise, `confirmed: false` from `verify()` — a `Status` other than
`Implemented`, task `files:` not fully git-tracked, or a `--verify` call that itself raises or
times out — means "not a confirmed collision," never "assume a collision." Every non-confirmed
outcome (`checked:false`, no candidate judged a match, `Status` not `Implemented`, `files:` not
fully git-tracked) leaves Phase 6/7 dispatch unmodified and un-delayed, mirroring
`references/repo-freshness.md`'s own best-effort,
never-a-hard-dependency contract. Never block or delay dispatch on this check; it either closes
a genuinely redundant brief or gets out of the way.

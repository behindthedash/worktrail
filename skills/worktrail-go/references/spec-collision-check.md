# Pre-Dispatch Spec-Collision Guard

`/go` classifies a request into a route and, for Route C (spec), Route D (implementation),
Route F (defect repair), or Route G (spec change), dispatches straight into work against an
existing or new spec. Neither `classify.py` nor `dashboard.py` ever compares the request
against `docs/specs/`'s own shipped history — a Route C/D/F/G dispatch has no signal that an
existing, already-`Implemented` spec covers the same actor + capability + primary domain.
Incident (2026-07-24): a queued brief was claimed and dispatched straight to Route D before
anyone checked whether the feature it described had already shipped under an earlier spec
whose task `files:` were already git-tracked on the base branch — the redundant implementation
work was only caught in review, after a worktree and PR had already been opened. A second
incident (session go-20260810-102015, devops repo) hit the same gap on Route F: a queued brief
recommended Route F to build a new `cron-liveness-guard.py`, but `cron-environment-audit`
(merged the day before) already owned the same problem space — caught only by manually reading
existing specs before dispatch, since this check was gated to Route C/D only at the time. Phase
5.5 closes that gap by running the same collision check right before Phase 6 opens a run
record, using `check_spec_collision.py` (pure extraction + artifact verification) plus the
calling agent's own semantic judgment — the same actor + capability + primary domain rule
`overlap_check.py`'s `#overlap-check` step already applies to Route A/new-feature brainstorming
(`references/subagent-prompts.md`), just run once more, later, against briefs and free-text
that skip brainstorming's overlap gate entirely (claimed queue briefs, `route:C`/`route:D`/
`route:F`/`route:G` overrides, handoff-recommended routes).

When a claimed brief (or an explicit route override) names a known `target-spec:` OpenSpec
change, this check also surfaces **task-level matches** — open, unchecked tasks in that change
that overlap the request — kept structurally separate from the whole-spec match above so a
task-level match (open work) is never confused with a whole-spec match (shipped work). See
"Task-level matches: redirect, never auto-close" below.

**Gate: Route C, D, F, or G.** This check runs only when Phase 5's resolved route (`$ROUTE`
from classify.py's `route`, an explicit `route:C`/`route:D`/`route:F`/`route:G` override, or a
handoff's `recommended-route`) is `C`, `D`, `F`, or `G`. Any other route (`A`, `B`, `E`, `H`–`J`,
or a low-confidence classification still awaiting clarification) skips it.

A brief-sourced Route C/D/F/G dispatch is not covered by this check alone, though: Phase 5.5's
sibling **brief-staleness** branch also runs for it (it runs for every brief-sourced dispatch,
regardless of route), asking a different question — did the work this brief describes already
land while it sat in the queue? See `brief-staleness-check.md`. The two branches share no state
and run independently; neither suppresses, gates, or alters the other, and both MAY run for the
same Route C/D/F/G dispatch.

```bash
if [ "$ROUTE" = "C" ] || [ "$ROUTE" = "D" ] || [ "$ROUTE" = "F" ] || [ "$ROUTE" = "G" ]; then
  COMPARISON_TEXT="${BRIEF_FOCUS:-$ARG_INTENT}"
  # $TARGET_SPEC, when known (e.g. the claimed brief's `target-spec:` field), is passed through
  # --target so task_candidates is populated below; omitted, task_candidates is always empty.
  COLLISION_JSON=$(worktrail-check-spec-collision --repo "$REPO" ${TARGET_SPEC:+--target "$TARGET_SPEC"} --json 2>/dev/null)
  CHECKED=$(echo "$COLLISION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('checked', False))" 2>/dev/null)
  if [ "$CHECKED" = "True" ]; then
    # Judge each whole-spec candidate (spec_id/title/feature_summary) against $COMPARISON_TEXT
    # using the same actor + capability + primary domain rule as
    # references/subagent-prompts.md#overlap-check. Only when exactly one candidate is judged a
    # strong match, verify it:
    VERIFY_JSON=$(worktrail-check-spec-collision --repo "$REPO" --verify "$MATCHED_SPEC_ID" --json 2>/dev/null)
    CONFIRMED=$(echo "$VERIFY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('confirmed', False))" 2>/dev/null)
  fi
  # Separately -- never merged into the whole-spec candidates or $CONFIRMED above -- inspect any
  # task-level candidates $TARGET_SPEC produced. See "Task-level matches" below.
  TASK_CANDIDATES=$(echo "$COLLISION_JSON" | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('task_candidates', [])))" 2>/dev/null)
fi
```

**Confirmed-collision handling differs by route** — C/D auto-closes on a brief-sourced match
(the target work is new or not yet `Implemented`, so a match is always a genuine duplicate);
F/G never auto-closes, brief-sourced or not (routes.md §F/§G both open by locating the
controlling spec the fix/change is against, so the matched candidate is frequently that very
spec, not a separate collision — auto-closing on a self-match would wrongly kill a legitimate
brief). See "Dispatch source 1/2" below for the exact per-route flow.

`$BRIEF_FOCUS` is the claimed brief's `focus` frontmatter field, extracted the same way Phase 5
already extracts `recommended-route` (`handoff_seed.py seed "<claimed-path>" --json | python3 -c
"import sys, json; print(json.load(sys.stdin).get('focus') or '')"`). A brainstorm-sourced
dispatch (no claimed brief, free-text typed straight at `/go`, no `handoff:ID` in play) has no
brief to read `focus` from, so `$COMPARISON_TEXT` falls back to `$ARG_INTENT` (or classify.py's
own derived summary of it, when `$ARG_INTENT` alone is too terse to compare against a
`feature_summary`).

## Task-level matches: redirect, never auto-close

When `$TARGET_SPEC` is known, `task_candidates` holds one `{spec_id, task_id, task_text,
checked}` entry per open, unchecked task in that change's `tasks.md` (`checked` is always
`false` here — only unchecked tasks are candidates at all). Judge each `task_text` against
`$COMPARISON_TEXT` using the same actor + capability + primary domain rule used for whole-spec
candidates above.

A task-level match is never routed through `verify()` and never sets `$CONFIRMED` — the matched
task is by definition open, unshipped work, not a `Status: Implemented` spec with git-tracked
artifacts, so it can never satisfy the auto-close precondition the Route C/D path below relies
on. Auto-closing a brief because it overlaps *unfinished* work elsewhere would silently orphan
work that hasn't shipped and may not even land as currently scoped — so a task-level match is
**never** grounds for `work_queue.py done`, on any route, brief-sourced or brainstorm-sourced.

Instead, a task-level match **redirects**: stop the fresh dispatch and steer the request at the
already-open task/change rather than starting a second track of work against the same ground.

- **Brief-sourced, any route**: do not call `work_queue.py done`. Ask the user whether to
  redirect the brief — update its `target-spec:`/`target-task:` fields to point at the matched
  open task and leave it queued for a future claim against that change, or continue the original
  dispatch as a deliberate separate track. `$AUTO_MODE=true`: no ask — file a decision per
  `decision-queue.md#file-a-decision` the same way the Route F/G auto-mode path above does
  (question: redirect onto the matched open task, or proceed as a separate track?), and release
  the brief pending the answer rather than closing or dispatching it.
- **Brainstorm-sourced (no claimed brief)**: stop and ask the user with the same three-option
  shape as the brainstorm-sourced Route C/D ask below ("Stop" / "Extend existing spec" /
  "Continue anyway"), substituting the matched open task/change for the shipped spec. This path
  has no `$AUTO_MODE=true` variant, same as the brainstorm-sourced asks below.

Either way, a task-level match only ever redirects toward the existing open work or, on the
user's/decision's explicit say-so, proceeds as a deliberate duplicate — it never closes a brief
or blocks dispatch on its own judgment.

## The pending-decision envelope and its audit trail {#spec-collision-envelope}

On a confirmed collision, `verify()`'s result carries one more key alongside
`confirmed`: **`pending_decision`** — the provider-neutral, versioned
`worktrail.pending-decision` envelope built by `build_pending_decision()` from
`workqueue/decisions.py`'s `pending_decision_envelope()`, under a deterministic
`decision_identity()` keyed on (provenance source `check_spec_collision`,
repo, subject = the matched spec's id, question). Its static question/options
are exactly the proceed / extend / redirect call the dispatch-source sections
below resolve; the match evidence itself stays in `verify()`'s own output,
never inside the question text. Re-running the check against the same shipped
spec converges on the same decision id, so a retry files one record — never a
duplicate pile. The envelope degrades to `null` when the decision primitives
are unavailable; filing it via `worktrail-decision ask` stays the caller's
job.

Whatever the dispatch mode, the decision's hops land on the run record's
`pending_decisions` audit list (`decision-queue.md#decision-envelope` and
`decision-queue.md#decision-audit`), keeping the lifecycle auditable end to
end:

- **Auto mode** (the `$AUTO_MODE=true` branch below): file via
  `worktrail-decision ask`, then stamp the `[asked]` hop with
  `worktrail-run-record decision "$RUN" --event asked --decision-id "$DECISION"`
  before finishing `blocked_product_decision`.
- **Attended**: present the exact record through the provider-neutral boundary
  — `worktrail-skill-dispatch --present-decision "$DECISION" --run "$RUN"`
  prints the same versioned JSON for every host and stamps `[presented]`
  itself — then resume through the exact id with `--resume-decision "$DECISION"`
  once it is answered.
- **Unattended**: drain surfaces the unresolved id as a first-class
  `pending_user_decision` stop and the next pass resumes through it.

## Dispatch source 1: brief-sourced (claimed queue brief present)

### Route C/D — auto-close

On `CONFIRMED = True`, do **not** ask the user — close the brief immediately, citing the
matching spec and the verification evidence, then stop (no fresh dispatch):

```bash
worktrail-work-queue done "$BRIEF_ID" --implementation-complete --by "$INVOCATION_CONTEXT_DISPATCH_ID" --note \
  "Closed as a pre-existing collision with $MATCHED_SPEC_ID ($MATCHED_TITLE) -- Status: Implemented, files: ${VERIFY_FILES} all git-tracked on $BASE."
```

A Route-C brief closes with `--implementation-complete` here, never `--planning-only` — Phase
5.5 has just established the requested implementation already exists, and `--planning-only`
would leave the brief re-eligible for a future planning pass against work that's already done.

Report the closure to the user in the run's status output (e.g. `Brief $BRIEF_ID closed:
already covered by $MATCHED_SPEC_ID ($MATCHED_TITLE), confirmed shipped.`) and stop — do not
continue to Phase 6's run-record start or Phase 7 dispatch for the original request.

### Route F/G — ask, never auto-close

On `CONFIRMED = True`, do **not** call `work_queue.py done` and do **not** stop the run on your
own judgment — the match is ambiguous (it may simply be the spec this brief's fix or change
targets). Ask the user:

```
AskUserQuestion(
  questions=[{
    question: "This Route {F|G} request looks like it matches an existing, already-shipped spec
      `{spec_id}` -- \"{title}\" (Status: Implemented, files: {files} all git-tracked on {base}).
      Is {spec_id} the spec this fix/change is against, or a separate, already-covered
      duplicate?",
    header: "Spec collision found",
    options: [
      {label: "This is the target spec", description: "Continue -- {spec_id} is the controlling spec for this fix/change"},
      {label: "Separate duplicate -- stop", description: "The requested work is already fully covered elsewhere; do not dispatch"},
    ],
  }]
)
```

On "This is the target spec", dispatch proceeds to Phase 6/7 unmodified and the brief stays
open — this check never closes an F/G brief. On "Separate duplicate — stop", the run stops
without dispatching, but the brief itself is **not** auto-closed here either; report the
duplicate to the user and let a human close the brief through the normal completion path if
warranted.

**`$AUTO_MODE=true`: no ask.** There is no human present to answer, and `AskUserQuestion` is not
even a callable tool inside the headless one-shot `worktrail-go drain` spawns — do not attempt
the call above. Phase 6 has not run yet for this dispatch, so open a minimal run record now
(the same fields Phase 6 would use) purely to record the block. **Before** finishing it, file
the judgment call as a decision record and release the brief per
`decision-queue.md#file-a-decision` — question: is `$MATCHED_SPEC_ID` this work's own target
spec, or a separate duplicate?; options: "own target spec — proceed against it" vs "separate
duplicate — close/redirect the brief"; context: the match evidence already gathered:

```bash
RUN=$(worktrail-run-record start --repo "$REPO" \
  --request "${BRIEF_FOCUS:-$ARG_INTENT}" --route "$ROUTE" --risk "${RISK_LEVEL:-medium}" \
  --agent "$INVOCATION_CONTEXT_AGENT" | python3 -c "import sys, json; print(json.load(sys.stdin)['path'])")
DECISION=$(worktrail-decision ask \
  --question "Is $MATCHED_SPEC_ID this work's own target spec, or a separate duplicate?" \
  --background "Spec collision check found $MATCHED_SPEC_ID ($MATCHED_TITLE, Status: Implemented, files: $VERIFY_FILES all git-tracked on $BASE) matching this dispatch's request. Auto mode has no one to ask, so the brief is being released back to the queue pending this answer instead of dispatched or stranded." \
  --why "Whether the matched spec is this work's own target or an unrelated pre-existing duplicate is a judgment call only a human can make from the evidence." \
  --context "Match evidence: $MATCHED_SPEC_ID -- $MATCHED_TITLE, files: $VERIFY_FILES." \
  --option "Own target spec -- proceed against it" \
  --option-cost "low -- dispatch continues against $MATCHED_SPEC_ID on next pass" \
  --option "Separate duplicate -- close/redirect the brief" \
  --option-cost "low -- work_queue.py done on next pass, no dispatch" \
  --recommendation "Read the match: if it plainly is the request's own scope, proceed against it; if it is a distinct pre-existing feature, close/redirect." \
  --repo "$REPO" --brief "$BRIEF_ID" --release --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
worktrail-run-record decision "$RUN" --event asked --decision-id "$DECISION" \
  --note "spec-collision envelope filed; brief released awaiting the answer"
worktrail-run-record finish "$RUN" --status blocked_product_decision --merge-result \
  "Auto-mode spec collision: matches $MATCHED_SPEC_ID ($MATCHED_TITLE, Status: Implemented, files: $VERIFY_FILES all git-tracked on $BASE). Decision $DECISION filed; brief released awaiting the answer."
```

The human answers asynchronously and the next drain pass continues accordingly — see
`decision-queue.md#resume-from-decision`. Do not call `work_queue.py done` yourself, and do not
release by hand — `worktrail-decision ask --brief ... --release` above is what stamps
`awaiting-decision`. Only if `worktrail-decision ask` itself fails (validation refusal you
cannot satisfy, unwritable queue), fall back to `run-record finish` without a decision and leave
the brief claimed in `picked/` for the stalled-in-flight resume path (dashboard `resume`
action). Stop; do not continue to Phase 6/7 for this dispatch.

## Dispatch source 2: brainstorm-sourced (no claimed brief)

### Route C/D

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
spec instead of a new one; "Stop" ends the run with no dispatch. Like the brainstorm-sourced
Route F/G ask below, this has no `$AUTO_MODE=true` variant — auto mode only ever claims
work-queue briefs, so a brainstorm-sourced dispatch cannot occur while `$AUTO_MODE=true`.

### Route F/G

No brief exists to leave open or close, so use the identical two-option ask from the
brief-sourced Route F/G case above ("This is the target spec" / "Separate duplicate — stop").
"This is the target spec" resumes the original F/G dispatch unmodified; "Separate duplicate —
stop" ends the run with no dispatch. This path has no `$AUTO_MODE=true` variant: auto mode only
ever claims work-queue briefs (`references/auto-mode.md`), so a brainstorm-sourced dispatch —
free text with no claimed brief — cannot occur while `$AUTO_MODE=true`; a human is always present
to answer this ask.

## Non-file artifact spot-check

`verify()`'s `note` field surfaces a non-file artifact claim found in the matched spec's prose
(e.g. `**Artifacts**: migrated the donor table`, a cron entry, a deployed service, a remote log)
that its `files:` git-tracking check cannot confirm on its own. `note` never affects
`confirmed` — it is purely something to hand the user (brainstorm-sourced path, fold it into the
`AskUserQuestion` prompt) or record in the closure note (brief-sourced path) as a prompt to
spot-check that artifact by hand before fully trusting the collision.

**What the check does.** `check_spec_collision.py --json` enumerates every spec under
`docs/specs/` as a candidate (`spec_id`, `stage`, `title`, `feature_summary`) — pure extraction,
no semantic judgment. Passing `--target <change-id>` additionally populates `task_candidates`
with that OpenSpec change's open, unchecked tasks — kept in its own key, never merged into
`candidates`, so a task-level match can never be mistaken for a whole-spec one. The calling agent
(this SKILL.md's own reasoning turn, not the script) judges whether any whole-spec candidate or
task-level candidate is a strong match against `$COMPARISON_TEXT`, applying the exact actor +
capability + primary domain rule `references/subagent-prompts.md#overlap-check` already
documents. Only when the agent judges a whole-spec candidate a match does `--verify <spec_id>
--json` run, checking the matched spec's `**Status**:` header (must read `Implemented`) and
whether every task `files:` entry is git-tracked at `$REPO`'s base branch — `confirmed: true`
only when both hold. A task-level match is never passed to `--verify`; see "Task-level matches:
redirect, never auto-close" above.

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

# Auto Mode (`auto` argument — spec 017)

`worktrail-go auto` (and repo-scoped `worktrail-go REPO auto`) starts the next ranked queue brief with no
selection prompt. Auto mode removes the selection prompt ONLY — policy approval gates,
risk tiers, the run record, and the CI watch loop apply exactly as for interactive runs.

## Phase 1 — dashboard

Print the `rendered` dashboard as usual and skip BOTH picker levels. Add `--auto` (plus
`--auto-repo "$ARG_REPO"` when a repo was named) to the dashboard.py call so
`$DASHBOARD_JSON.auto_pick` is populated.

The pick is computed by dashboard.py, never improvised: FIFO oldest-first among
unblocked queue briefs whose repo exists on this machine and has no live orchestrator
run (a non-blocking flock probe of `<repo>-worktrees/*.lock`; the orchestrator's RunLock
holds that flock only while a run is live, so a held lock always means another agent is
actively working that repo — stale lock files probe as free).

## Phase 2 — selection and claim

1. Read `$DASHBOARD_JSON.auto_pick`. If `auto_pick.pick` is null, report the queue state
   and every `skipped` entry with its reason (`blocked`, `no-repo`, `repo-missing`,
   `repo-filter`, `orchestrator-run-active:<lock>`, `release-gate:<name>`), then STOP.
   Never invent work and never fall back to resuming an in-flight brief — stalled resumes
   require judging what a dead session already landed, which stays human-selected.
   Ranking is blocker-first (`triage: blocker` < untriaged < `triage: deferred`), FIFO
   within each tier; a repo whose policy sets `release_gate` is in a release freeze and
   only its `triage: blocker` briefs are eligible (`release-gate:<name>` skips the rest).

2. Announce the pick in one line (`Auto-picking <id> — <focus>`), then run the same
   batch detection as an interactive claim (`references/batch-consumption.md` step 1).
   Auto-fold ONLY companions whose `reason` is `related-link` or `same-target-spec` —
   with no human to confirm, score-only candidates stay in the queue.

3. Claim via `worktrail-work-queue claim-batch <pick-id> <companion-id>... --json`. The atomic
   claim is the final arbiter: if the PRIMARY claim exits 4 (`already-claimed` — another
   agent won the race between dashboard render and claim), re-run dashboard.py with
   `--auto` and take the fresh pick. After 3 lost primary races, stop and report rather
   than spinning.

4. Continue Phases 3–8 exactly as for an interactive claim: one classification fed the
   PRIMARY brief's `recommended-route` via `--handoff-route` (Phase 5) — at low/medium
   classifier confidence this wins outright over a low-signal organic guess, since auto
   mode has no human present to catch a bad one — one run record listing every claimed
   brief id in `handoffs_consumed`, dispatch, CI watch, and per-brief `done`/`release`.

## Phase 5.5 — collision/staleness checks have no ask

`AskUserQuestion` is not a callable tool inside the headless one-shot processes
`worktrail-go drain` spawns (verified 2026-08-10: a direct `claude -p` probe found the tool
entirely absent, not merely unanswered). Auto mode's three Phase 5.5 branches
(`references/spec-collision-check.md` Route F/G, `references/brief-staleness-check.md`,
`references/related-brief-collision-check.md`) each check `$AUTO_MODE` before their ask and,
when true, skip it: they open a minimal run record, `finish` it `blocked_product_decision` with
a summary of what needed a human call, and leave the brief claimed in `picked/` for the existing
stalled-in-flight resume path — never guessing an answer and never hanging on an unavailable
tool. (Route C/D's spec-collision ask has no `$AUTO_MODE=true` variant at all: auto mode always
has a claimed brief in play, per Phase 2 above, and a confirmed C/D match on a brief-sourced
dispatch auto-closes the brief outright rather than asking — see
`references/spec-collision-check.md`'s "Dispatch source 1" section — so it never reaches an ask
in the first place.) `drain.py` already classifies `blocked_product_decision` as a `blocked`
outcome — see `references/drain.md` and `classify_outcome()` in `drain.py` — so this fits the
existing stop conditions without any driver-loop change.

## Draining many items

Auto mode picks and runs ONE brief per invocation. To work through the whole queue
unattended, do not loop `worktrail-go auto` inside one session (context accumulates and
permission prompts stall the loop) — use `worktrail-go drain`, which launches a
fresh-context headless one-shot of `worktrail-go auto` per item and stops with an explicit
reason (queue empty, capacity gate, circuit breaker, budget).

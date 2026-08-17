# CI Watch Loop (Phase 8, after a PR is opened)

> **⚠️ MANDATORY — DO NOT SKIP.** Every PR-owning route MUST complete this loop
> and classify the outcome before calling `run_record.py finish`. The five cases
> below (all-pass, transient infra, code defect, product decision, ceiling) are
> the only valid terminal transitions. "Tests passed locally" is not a terminal
> state — run the loop.

**Intermediate-checkpoint variant.** Some routes open a PR mid-run without
closing the run record at that point — e.g. Route C's docs-only spec PR when
the run continues inline into Route D (`routes.md` §C: no new `finish()`
happens between the spec PR and the implementation phase). Invoked as an
intermediate checkpoint, run every step below unchanged with one
substitution confined to case 1 ("All pass"): replace each
`run_record.py finish ...` action with
`run_record.py append "$RUN" decisions "<the same outcome text>"`, then
return control to the calling route instead of stopping. Cases 2–5 are
unchanged in both modes — a transient-infra or code-defect retry already
loops locally without finishing, and a product-decision or iteration-ceiling
failure still ends the run via `finish(...)`, because the calling route
cannot safely continue past an unresolved intermediate PR either way.

Enter this loop on every PR-owning route before closing the run record. Track
patch iterations with `PATCH_ITER=0`; ceiling is **5**. Track `$PUSH_SHA` (unset
until a fixup is pushed) for the stale-head merge guard in case 1.

## Waiting for checks

**Use `--watch`, never a hand-rolled sleep loop** (the harness blocks `sleep`,
and a foreground poll loop strands the run — GO v1 defect L7). Run with the
Bash tool `timeout` parameter set to 600000; if the timeout fires with checks
still pending, re-issue the same command up to 3 times, then treat as stuck:

```bash
gh pr checks "$PR_NUM" --repo "$OWNER/$REPO_NAME" --watch --fail-fast
gh pr checks "$PR_NUM" --repo "$OWNER/$REPO_NAME" --json name,bucket,workflowRunId  # final state
```

**GraphQL outage, not a pending-checks timeout.** The command above fails outright — non-zero
exit with an HTTP 5xx or a GraphQL-error-body in stderr, not a clean timeout with checks still
pending — when GitHub's GraphQL API itself is degraded (`gh pr checks` is GraphQL-backed). This
is a different failure mode from the ordinary `--watch` timeout handled by the 3x-retry-then-
stuck-check-run path above: retrying the same GraphQL call just re-hits the outage. Route to the
"GraphQL outage fallback" subsection below instead.

(Optional event-driven variant when pullhook is deployed at
`https://pullhook.io`: `curl -sf "https://pullhook.io/api/hooks/<repo-channel>/pull"`
blocks up to 30 s and returns on a check_run event.)

**Stuck check-run fallback (after the 3 exhausted `--watch` retries above).** A required
check-run can report `status:in_progress`/`conclusion:null` indefinitely even after its
underlying job has actually finished — observed during a GitHub status-page "major"
incident (worktrail PR #498, 2026-08-17): the Jobs API's own per-step list showed every
step already `conclusion:success`, and the PR had already auto-merged (`mergedAt`
recorded before the stuck check-run's own `started_at`). Do not keep re-issuing
`gh pr checks --watch` past its own retry budget — cross-check before treating the
check-run as still-pending:

1. **Short-circuit on an already-merged PR first** — cheapest and most decisive check:
   ```bash
   gh pr view "$PR_NUM" --repo "$OWNER/$REPO_NAME" --json state,mergedAt
   ```
   `state == "MERGED"` — the PR is already done regardless of what any individual
   check-run still reports. Treat exactly like case 1's ("All pass") `state == "MERGED"`
   branch below (no `$PUSH_SHA` is set at this point in the loop, so the stale-head guard
   there does not apply) and stop. Skip step 2 entirely.

2. **Cross-check the actual job step conclusions** — only if the PR is not yet merged:
   ```bash
   gh api "repos/$OWNER/$REPO_NAME/actions/runs/<workflowRunId>/jobs" \
     --jq '.jobs[] | {name, status, conclusion, steps: [.steps[] | {name, conclusion}]}'
   ```
   using the `workflowRunId` the final-state `gh pr checks --json` query above returned
   for the stuck check-run. Every step already showing a real `conclusion`
   (`success`/`failure`/etc., none still `null`) despite the check-run API reporting
   `in_progress`/`null` means the job itself finished and only the check-run's own status
   report is stale — proceed to classify using the *job's* step conclusions instead of
   waiting on the check-run API to catch up (all steps succeeded → case 1's non-merged
   branches; any step failed → case 2 or 3 below, using that job's `--log-failed`). Any
   step still genuinely `status:in_progress`/`conclusion:null` means the job really is
   still running — this is not the stale-status case; re-enter the watch loop above
   rather than escalating further.

**GraphQL outage fallback (when the note above routes here).** `gh pr checks --watch` is
GraphQL-backed; the check-runs data itself is also available over REST, which can stay healthy
during a GraphQL-side outage. Poll that instead:

```bash
gh api "repos/$OWNER/$REPO_NAME/commits/$HEAD_SHA/check-runs"
```

Bounded to 3 discrete retries, matching the `--watch` retry cap above. No hand-rolled sleep loop
between attempts — the same rule the "Waiting for checks" note above already states (GO v1 defect
L7): the harness blocks `sleep`, and a foreground poll loop strands the run. Re-issue the same `gh
api` call up to 3 times; each invocation's own network round-trip is the only spacing between
attempts.

**Recovery.** The moment a `gh api` retry above returns cleanly (GraphQL has recovered),
resume ordinary `--watch` operation on the very next loop entry — go back to the
`gh pr checks --watch` command at the top of this section. There is no persistent
"degraded mode" flag to reset: the REST poll above is used only for the duration of the
outage itself, one retry attempt at a time, never latched across loop iterations.

**Retries exhausted.** All 3 REST retries above still fail — the outage has not lifted.
Do not keep retrying past this budget or escalate to a new terminal status; fall through
to case 5's stop below (`finish("failed_recoverable")`) exactly as if `PATCH_ITER` had
reached the ceiling, substituting the outage for the usual "patch iterations" summary:
note that the loop stopped because the GraphQL outage did not clear within the retry
budget, not because of a code defect, so a human reading the run record knows the outage,
not the change under test, is why the loop stopped.

## When the checks settle, classify the results and act

1. **All pass** — no `bucket: fail` entries. Before finishing, re-query the PR's live
   merge state — a repo with its own auto-merge CI (e.g. a `gh pr merge --auto` workflow)
   can merge the PR within seconds of checks going green, before this loop reacts, which
   makes `completed_pr_open` stale the instant it's written:
   ```bash
   gh pr view "$PR_NUM" --repo "$OWNER/$REPO_NAME" \
     --json state,mergedAt,autoMergeRequest,headRefOid,mergeStateStatus,statusCheckRollup
   ```
   - **Stale-head guard (only if this loop pushed a fixup — `$PUSH_SHA` is set, case 3
     below):** `state == "MERGED"` with `headRefOid` != `$PUSH_SHA` means the PR merged a
     head OLDER than the fix just pushed (GGB #556: a pyright fix landed 2s after native
     auto-merge had already merged the still-broken pre-fix head — `gh pr checks --watch`
     reported all-green with no signal that the merged head predated the push). Treat as a
     stale-merge incident, not a clean completion:
     `worktrail-run-record finish "$RUN" --status "completed_and_merged" --merge-result "STALE-HEAD MERGE: merged headRefOid <headRefOid> predates fixup $PUSH_SHA -- original defect may have shipped"`,
     then open a same-fix follow-up PR carrying `$PUSH_SHA`'s commit (mirrors GGB #558) and
     stop.
   - `state == "MERGED"` (no `$PUSH_SHA`, or `headRefOid` == `$PUSH_SHA`) — the PR is gone;
     a merged thread can no longer be replied to or resolved through this loop, so skip the
     review-thread gate below (no live PR left to act on) —
     `worktrail-run-record finish "$RUN" --status "completed_and_merged" --merge-result "merged externally"`
     and stop.
   - `state != "MERGED"` — **merge-state guard (mandatory before the review-thread gate
     below):** `gh pr checks --watch` reporting all-green, or `autoMergeRequest` being
     armed, says nothing about whether GitHub will actually let the merge through — a
     required-status-check *context* can carry a stray `CANCELLED` run alongside a later
     `SUCCESS` run of the same context (concurrency-group races across
     `opened`/`labeled`/`synchronize` events routinely produce this — e.g.
     `gh pr create --label a --label b` fires one `labeled` webhook per label, and two
     labels sharing one action-keyed concurrency group cancel each other), which GitHub's
     branch-protection evaluation can still treat as blocking even though the newer run
     passed (worktrail PR #393, 2026-08-14: `mergeStateStatus: BLOCKED` with every check
     green in `gh pr checks` and `autoMergeRequest` armed — this loop finished
     `completed_pr_open` on that basis and the merge stalled indefinitely until a human
     noticed and manually re-ran the stray cancelled run). Check `mergeStateStatus` from
     the query above before finishing on either branch that follows the review-thread gate:
     - `BLOCKED` — scan `statusCheckRollup` for a `CANCELLED` entry whose `name` also has a
       `SUCCESS` entry (same `name`, different run). Found: `gh run rerun <the CANCELLED
       run's databaseId> --repo "$OWNER/$REPO_NAME"`, wait for it
       (`gh run watch <databaseId> --repo "$OWNER/$REPO_NAME"`), then re-query
       `mergeStateStatus`. Bounded to 2 rerun attempts total (each targets a fresh
       `CANCELLED` entry if one remains) — does **not** increment `PATCH_ITER` (no code
       changed, nothing was actually diagnosed as broken). Still `BLOCKED` after 2 rounds,
       or no matching cancelled/success pair found: this is not self-healable by this
       loop — `worktrail-run-record finish "$RUN" --status "blocked_product_decision"
       --merge-result "mergeStateStatus stuck BLOCKED after merge-state guard; needs
       manual branch-protection/ruleset inspection: <raw statusCheckRollup summary>"` and
       stop.
     - Any other value (`CLEAN`, `HAS_HOOKS`, `UNSTABLE`, `UNKNOWN`) — proceed to the
       review-thread gate unchanged.
     - `DIRTY` or `DRAFT` — a real blocker (merge conflict, draft PR), not this guard's
       target; treat as case 4 below.
   - **Review-thread gate (mandatory before either branch below, after the merge-state
     guard above clears):** a
     required check going green only proves check pass/fail, never that reviewer findings
     (e.g. `security-review-llm`'s line comments) were actually resolved — datalena PR #2133
     accumulated 9 unresolved review threads across 4 rounds of findings that were all
     either fixed or explicitly decided-not-to-fix, and nothing in this loop noticed until a
     human manually replied+resolved each one via GraphQL. Run:
     ```bash
     worktrail-check-review-threads --repo "$PWD" --pr "$PR_NUM" --owner "$OWNER" --name "$REPO_NAME" --run "$RUN" --json
     ```
     Read the result the same way as the merge-state query above, not by exit code (always
     0 — this is a signal source, like `gh pr checks`, not a hard gate the tool enforces
     itself):
     - `checked: false` — the question could not be answered (gh unavailable/unauthenticated,
       unresolvable owner/repo, malformed GraphQL response). Treat as no signal — proceed to
       the two branches below unchanged, and note the warning in the eventual `finish`
       `--merge-result`.
     - `checked: true`, `blocking: false` — every unresolved thread was either already
       `isResolved`, or was auto-correlated (a commit in this run touched the thread's file
       after the thread's first comment, or the run record's `decisions` log named the
       thread/path) and the tool already posted a reply and resolved it. Proceed.
     - `checked: true`, `blocking: true` — at least one unresolved thread has no
       corresponding commit or recorded decision. Treat this exactly like case 3 below: for
       each entry in `unaddressed`, either fix the finding in code (commit, push, resolve
       naturally on the next run of this gate) or record an explicit decision
       (`worktrail-run-record append "$RUN" decisions "thread <id> (<path>): <reason not
       fixing>"`) and re-run the gate — never proceed to either branch below while
       `blocking: true`. The tool itself also stamps `go:no-automerge` on the PR the moment
       `blocking` goes true (additive-only, skipped under `--dry-run`) — the same label a
       repo's own auto-merge automation already reads before arming, so native `gh pr merge
       --auto` (which has no concept of `reviewThreads`) stops racing ahead of this gate on
       any subsequent evaluation once the label lands, not just at this loop's own `finish()`.
       **The block itself is also code-enforced inside `finish`** (mirroring the `go:risk-*`
       label correction): `run_record.py finish` re-runs this same check whenever the record
       carries a `pull_request` and the completion state is one of the three implementation
       states, and refuses to finish (`SystemExit`) on a `blocking: true` result — so skipping
       this manual step no longer lets an unresolved thread slip through. This loop-level check
       stays the primary path (faster feedback, no wasted retry on `finish`); `finish`'s own
       call is the backstop.

     Once the gate clears (`checked: false`, or `checked: true` with `blocking: false`):
     - `autoMergeRequest` is non-null — auto-merge is armed (native GitHub toggle, bot, or
       workflow) and will complete without further action. Name the mechanism using
       whichever of `mergeMethod` / `enabledBy.login` is present:
       `worktrail-run-record finish "$RUN" --status "completed_pr_open" --merge-result "auto-merge armed (<mergeMethod>, enabled by <enabledBy.login>); will complete without further action"`
       and stop.
     - `autoMergeRequest` is null —
       `worktrail-run-record finish "$RUN" --status "completed_pr_open"` and stop.

2. **Transient infrastructure failure** — matches any of:
   check name contains `Initialize containers` or `Set up job`; log contains
   `Error response from daemon` (Docker pull rate-limit); runner OS-level timeout at
   container init (not at a test or lint step).
   Action: `gh run rerun "$RUN_ID" --failed --repo "$OWNER/$REPO_NAME"`.
   Does **not** increment `PATCH_ITER`. Re-enter the watch loop.

3. **Code defect** — ImportError, test assertion, lint error, type error, build error:
   ```bash
   gh run view "$RUN_ID" --log-failed --repo "$OWNER/$REPO_NAME"
   ```
   Diagnose root cause (no-guessing rule: verified observation → hypothesis → confirmed).
   Apply the minimal patch in the worktree, then **before pushing**, disarm any native
   auto-merge that could race the push (stale-head fixup-push race — see the case-1
   guard above for the incident this closes): a PR with checks currently green or
   `autoMergeRequest` already armed can merge the OLD head at any moment, including in
   the gap between this diagnosis and the push landing.
   ```bash
   gh pr merge "$PR_NUM" --repo "$OWNER/$REPO_NAME" --disable-auto 2>/dev/null || true  # no-op if never armed
   git add -p   # stage only the targeted fix
   git commit -m "fix: <one-line root cause>"
   PUSH_SHA=$(git rev-parse HEAD)
   git push     # triggers a new CI run automatically
   ```
   Increment `PATCH_ITER`. Emit: `CI watch iteration $PATCH_ITER/5 — $FAIL_COUNT failing: $FAIL_NAMES`.
   Re-enter the watch loop with `$PUSH_SHA` held for the case-1 stale-head guard. If the
   repo's own auto-merge workflow re-arms on `pull_request: synchronize` (most do), it
   re-establishes itself on the new head automatically; no manual re-enable needed.

4. **Product / design / test-logic decision** — failure requires a behaviour change, new
   acceptance criteria, or a test that needs product input to write.
   Surface a clear summary of what decision is needed. In auto mode, first file it as a
   decision record and release the brief per `decision-queue.md#file-a-decision` so a human
   can answer asynchronously and the next drain pass resumes the CI loop from here.
   `finish("blocked_product_decision")` and stop.

5. **Iteration ceiling** — `PATCH_ITER` reaches 5 without a green run.
   Output a summary of all patch iterations (what was tried, what failed each time).
   `finish("failed_recoverable")` and stop.

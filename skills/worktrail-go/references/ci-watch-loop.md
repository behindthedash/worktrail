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

(Optional event-driven variant when pullhook is deployed at
`https://pullhook.io`: `curl -sf "https://pullhook.io/api/hooks/<repo-channel>/pull"`
blocks up to 30 s and returns on a check_run event.)

## When the checks settle, classify the results and act

1. **All pass** — no `bucket: fail` entries. Before finishing, re-query the PR's live
   merge state — a repo with its own auto-merge CI (e.g. a `gh pr merge --auto` workflow)
   can merge the PR within seconds of checks going green, before this loop reacts, which
   makes `completed_pr_open` stale the instant it's written:
   ```bash
   gh pr view "$PR_NUM" --repo "$OWNER/$REPO_NAME" --json state,mergedAt,autoMergeRequest,headRefOid
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
   - `state != "MERGED"` — **review-thread gate (mandatory before either branch below):** a
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

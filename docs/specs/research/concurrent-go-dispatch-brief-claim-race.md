# Investigation: concurrent /go dispatches on the same handoff brief

Source: work-queue brief `20260816-125901-concurrent-go-dispatches-on-the`
(repo of record: `devops`; the mechanism investigated here lives in `worktrail`).

## Incident summary (as reported)

Two independent, concurrent interactive Claude Code sessions on the same
machine (this session, and peer session `briank-5d`, active 12:00-12:57 PM)
both dispatched against the **same** devops handoff brief
(`20260816-090946`), both ended up writing to the **same shared run record**
(`go-20260816-124522.yaml`), and each independently implemented an equivalent
fix to `scripts/check-cron-path-safety.py` in its own git worktree. The
session that finished first opened and merged PR #208; its `run_record.py
finish` call overwrote the shared run record. During/after that, the losing
session's worktree directory **and** branch were both removed while a shell
was still `cd`'d into that worktree mid pre-PR-gate command (`getcwd()`
failure observed), with no warning surfaced to that session.

Two separate questions were raised:

1. Should worktrail-go's Brief-ID/handoff-seed dispatch path detect that a
   brief's associated run record is already `status=executing` before a
   second session re-enters Phase 7 execution and creates a second worktree?
2. What removed the losing session's worktree and branch?

## Verified Observations

- `work_queue.claim()` (`src/worktrail/workqueue/work_queue.py:581-660`)
  atomically renames a brief from `queue/` to `picked/` — this part is a
  correct, race-free OS-level arbiter for the *first* claim.
- **`claim()` cannot distinguish "I already own this brief" from "someone
  else already owns this brief."** Every code path that returns
  `already-claimed` for a brief already sitting in `picked/` — the
  `resolve()`-in-`picked_dir()` branch (lines 594-604) and the
  `dst.exists()` branch (lines 613-620) — returns the **same shape**,
  `{"status": "already-claimed", "path": <picked-path>, ...}`, regardless of
  who claimed it or when. Neither branch reads or compares the `claimed-by`
  frontmatter field that was stamped on the original claim
  (`_set_fm_fields(..., "claimed-by": by or _agent_label())`, line ~657).
  Only the narrow `FileNotFoundError` TOCTOU branch (lines 620-627, the
  brief vanishes from `queue/` between `resolve()` and `os.rename()` in the
  same instant) returns `path: None`.
- `sdd-workflow`'s own documented contract for this return value
  (`worktrail-go/references/subagent-prompts.md` §Handoff seed, Step 3 table)
  treats `already-claimed` **with** `path` as "expected... when the generic
  `go` front door claimed the brief before delegating to `sdd-workflow
  handoff:<id>`" — i.e. it assumes `path`-present always means "this is my
  own two-step claim," and instructs the caller to reuse the path and
  proceed. It treats `already-claimed` **without** `path` as "another
  session won the race... do NOT seed." But per the code above, a
  completely unrelated second session claiming a brief someone else already
  owns *also* gets `path` populated — the two cases are indistinguishable at
  the API boundary, and the documented behavior for the "expected,
  same-session" case is exactly the behavior that reproduces the incident
  for a genuinely different session.
- `_agent_label()` (work_queue.py:121-123) defaults `claimed-by` to
  `f"{socket.gethostname()}:{os.getpid()}"` — a fresh value on **every**
  `worktrail-work-queue` CLI invocation (each is a new process), including
  the two calls (`go`'s own claim, then `sdd-workflow handoff:<id>`'s claim)
  that make up one *legitimate* single-session dispatch. So even if
  `claimed-by` were compared, comparing raw PIDs would misclassify the
  legitimate intra-session case as "someone else" too — there is no stable
  per-`/go`-invocation identity threaded through today.
- `claim()`/`claim_batch()` both already accept a `--by <label>` CLI flag
  (work_queue.py:1102, 1110) for an explicit claiming-agent label, but no
  skill call site passes it — grepped every `worktrail-work-queue claim`
  invocation across `worktrail-go`, `worktrail-handoff`, and
  `worktrail-sdd-workflow`'s SKILL.md/references; all omit `--by`.
- A separate, **already-solved** version of this exact class of problem
  exists in the codebase: `run_record.py claim`/`active-conflicts`
  (`src/worktrail/router/run_record.py`, `#active-conflicts-scan` in
  `subagent-prompts.md`) is an atomic, `O_CREAT|O_EXCL`-backed claim on
  `(repo, specification)` that was built specifically to close a documented
  prior incident (2026-08-07, "duplicate-orchestrator incident" — cited
  in-line at `subagent-prompts.md` line ~862) where two concurrent `/go`
  sessions raced a read-then-write TOCTOU window.
- That guard is wired into exactly **one** call site:
  `implement`-pipeline step 1a (`pipeline-details.md#implement-pipeline`,
  Route D against an existing spec), keyed on `$SPEC_ID`. It is reached
  through `#sibling-worktree-check`, shared by `#spec-worktree-setup` (Route
  C / Route D-no-spec) and `#change-spec-worktree-setup` (Route F/G,
  spec-owned behavior).
- It is **not** wired into `#fix-branch-worktree-setup`
  (`subagent-prompts.md` line 1067) — the path Route F takes for **unspecced
  code**, which is what `scripts/check-cron-path-safety.py` is (a devops
  script with no owning spec). That setup does nothing but
  `git worktree add -b "fix/$SLUG" "$WT" "$BASE"` with no prior conflict
  check of any kind.
- It is also not consulted anywhere in the "Active-run resume (Route E)
  stays in-session" dispatch-policy rule in `worktrail-go/SKILL.md`. That
  rule checks only local filesystem/run-record state — "its run record
  exists with no `final_status` AND its `worktree` path already exists on
  disk" — to decide "the active parent already owns this run, hand
  execution back to it by continuing... in this session." That check has no
  concept of *which* session is "the active parent" — it is filesystem
  state, not a process-liveness or ownership check — so a second, unrelated
  session evaluating the same brief's run record sees the identical
  evidence ("non-terminal, worktree exists") and, by the letter of that same
  rule, also concludes it should "continue... in this session," which is a
  duplicate independent continuation, not a hand-back. The skill's own text
  cites a prior, structurally identical incident this rule was written to
  prevent (`Datalena run go-20260811-132806`) — that incident was the
  single-session nested-worker case; this incident is the same failure
  shape at the cross-session level, which the rule's own evidence test does
  not distinguish.
- `worktree-cleanup.md`'s `cleanup-worktrees` flow is dashboard-picker-only
  (never auto-invoked), requires an explicit user confirmation before
  removing anything, and refuses (`git worktree remove` "refuses if dirty")
  to touch a worktree with uncommitted changes or an unmerged/unpushed
  branch. Followed as documented, it could not have silently removed a
  worktree still holding in-progress uncommitted work mid pre-PR-gate.
- `run_record.py claim`/`active-conflicts`'s stale-reconciliation sweep
  (the loop in `#active-conflicts-scan` that closes out stale records before
  the hard-stop claim) is scoped to `--specification "$SPEC_ID"`. Since this
  incident's fix target was unspecced code, no `$SPEC_ID` exists for this
  brief's work, so this sweep would not have run against it at all.

## Unknowns / Missing Evidence

- **Which process actually ran `git worktree remove`/`git branch -D`
  against the losing session's worktree and branch.** Neither of the two
  documented cleanup pathways in this codebase (`cleanup-worktrees`,
  spec-scoped `active-conflicts` stale reconciliation) explains it under
  their own stated preconditions (human-confirmation-gated; spec-scoped and
  N/A here, respectively). No session transcript, shell history, or process
  log for either session was available to this investigation to attribute
  the actual command. A third, undocumented mechanism, or a manual/ad-hoc
  command issued by the winning session's own teardown step
  (`#fix-branch-worktree-teardown`) somehow resolving to the *wrong*
  `$WT`/branch pair, both remain unconfirmed candidates.
- Whether the winning session's own `#fix-branch-worktree-teardown` step
  (which only ever names its own `$WT`/`$SLUG` — confirmed by reading the
  procedure) could have been given the wrong worktree path by some upstream
  state confusion is not verified; the two sessions used different slugs
  (`fix-cron-guardrail-transitive-detection` vs
  `fix-cron-transitive-dispatch-detection`), which on its face should have
  kept their `$WT` paths and branch names distinct throughout.

## Hypotheses

None beyond the "Unknowns" above — the primary mechanism (duplicate
dispatch via a non-distinguishing `already-claimed` response) is a
**Confirmed Root Cause**, not a hypothesis; see below.

## Validation Steps (for the unconfirmed worktree-removal question)

- If available, pull both sessions' tool-call histories for the incident
  window (12:00-12:57 PM) and grep for `worktree remove`/`branch -D`
  invocations naming the losing session's `$WT`/branch.
- Check whether any cron/background sweep script on this machine
  (`~/bin/*`, devops-owned cleanup cron jobs) touches `*-worktrees/`
  directories unconditionally; none were found in this investigation's scope
  (limited to the `worktrail` package itself), but devops-side automation
  was out of scope here and was not checked.

## Confirmed Root Cause

`worktrail-work-queue claim` (`work_queue.py`'s `claim()`) returns an
identical `already-claimed`-with-`path` response whether the caller is
legitimately continuing its own already-claimed brief (the documented
two-step `go` → `sdd-workflow handoff:<id>` intra-session pattern) or is a
completely separate session encountering a brief someone else already owns.
`sdd-workflow`'s documented handling of that response ("Use the returned
path... this is the expected path when the generic go front door claimed
the brief before delegating") instructs the caller to proceed and seed a
pipeline in **both** cases, because the API gives it no way to tell them
apart. This is what let two independent sessions both seed and dispatch
against the same brief and converge on the same run record. A structurally
identical race (two concurrent `/go` sessions, TOCTOU on shared state) was
already identified and fixed for the spec-authoring path via
`run_record.py claim`/`active-conflicts` (2026-08-07 incident) and for the
single-session nested-worker case via the "Active-run resume stays
in-session" rule (`Datalena run go-20260811-132806`) — but neither fix's
coverage extends to brief-claim-level, cross-session collision on unspecced
Route F work, which is the exact shape of this incident.

The secondary question (what removed the losing worktree/branch) is **not
confirmed** from available evidence — see Unknowns above.

## Recommended Next Route

**Route J (workflow-evolution), against the `worktrail` repository** — the
controlling code (`work_queue.claim()`, `worktrail-go`'s Active-run-resume
rule, `#fix-branch-worktree-setup`) all live there, not in `devops`. Concrete
candidates for that follow-up, in order of leverage:

1. Give `claim()` a way to distinguish "same owner" from "different owner"
   on an `already-claimed` hit — e.g. thread a stable per-`/go`-invocation
   identity through `--by` (already an unused CLI flag) from the top-level
   `/go` dispatch down through both the `go`-level claim and the
   `sdd-workflow handoff:<id>` claim, and have `already-claimed` return an
   explicit `same_owner: true|false` (comparing the stored `claimed-by`
   against the caller's own passed identity) instead of forcing the caller
   to infer it from `path` presence alone.
2. Extend `#fix-branch-worktree-setup` (unspecced Route F) with an
   equivalent atomic ownership guard — it currently has none, unlike its
   spec-owned sibling `#change-spec-worktree-setup`. A brief-id-keyed claim
   (parallel to the spec-id-keyed one in `run_record.py`) would cover this
   without requiring a spec to exist.
3. Tighten the "Active-run resume stays in-session" rule in
   `worktrail-go/SKILL.md` so its evidence test can distinguish "I am the
   process that started this run" from "some other process started this
   run and is still live" — today's test (non-terminal status + worktree
   exists) is satisfied identically by both.

This investigation made no code changes (Route I constraint) beyond this
note; the JSON/CLI evidence above is directly reproducible by reading the
cited files and grepping the cited call sites.

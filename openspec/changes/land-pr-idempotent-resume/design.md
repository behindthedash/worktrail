## Context

`land_pr()` (`src/worktrail/router/land_pr.py`) is the single PR-landing
pipeline every caller composes with. Its module docstring names seven ordered
steps; steps 1-4 (`_commit_pending`, `_ensure_compile_markers`,
`_run_preflight_and_labels`, `_push`) exist to produce a gated commit on the
remote, and steps 5-7 exist to open/refresh the PR and drive it to a terminal
outcome. Today the function runs all seven unconditionally on every
invocation.

Facts established against the current worktree:

- `land_pr()` at line 1019 goes straight into `_commit_pending` after the
  route validation; no state is read first.
- `_ensure_compile_markers` (line 262) runs `git fetch origin <base>` and
  then `conductor_compile.main()` for every changed change dir — the
  expensive part of a re-run.
- `_run_preflight_and_labels` (line 323) shells the full gate through
  `preflight.main(["run", ...])`.
- `open_or_update_pull_request` (line 455) already implements idempotence for
  the `OPEN` case only: `gh pr view <branch> --json url,number,state,labels`,
  and `if data.get("state") == "OPEN"` refresh-in-place; anything else (no
  PR, `MERGED`, `CLOSED`) falls through to `gh pr create` at line ~623.
- `preflight.write_marker` (`preflight.py:470`) keys its pass marker to
  `tree_state()` (HEAD sha + working-tree state) and stores the exact
  `resolve_pr_labels()` output, which is why the resume path can obtain a
  byte-identical label set without re-running the gate.
- `_push_target()` (line 383) already yields the correct remote (honoring
  `remote.pushDefault`) and its `owner/repo` slug for `gh -R`.

## Goals / Non-Goals

**Goals:**
- Make a re-invocation against an already-pushed, already-PR'd commit cost
  one cheap probe instead of a full gate, while still resuming the PR
  refresh and CI watch.
- Stop an already-merged branch from reaching `gh pr create`.
- Keep the change entirely inside `land_pr()`; no caller edits, no
  `LandRequest`/`LandOutcome` field additions.

**Non-Goals:**
- Changing `open_or_update_pull_request`'s own `OPEN`-only short-circuit for
  the *normal* (non-fast-path) route. When `HEAD` differs from the remote
  tip, a merged PR plus new local commits genuinely does warrant a new PR;
  only the fast path (where the commit is provably already merged) suppresses
  creation.
- Caching or persisting resume state. The fast path is derived from git and
  `gh` on each call, so it is correct across machines, worktrees and fresh
  contexts without a new artifact to go stale.
- Skipping the CI watch. Resuming exists precisely to re-enter the watch.

## Decisions

### D1 — Step 0, not a change to steps 1-4

The check lands as a single guarded branch at the top of `land_pr()`, after
route validation and before `_commit_pending`. Pushing the logic down into
each of steps 1-4 would spread the same precondition across four functions
and four sets of tests; one branch keeps the "skip 1-4, enter at 5" story
readable and keeps every existing step function untouched.

### D2 — Preconditions (all must hold, else the fast path is declined)

1. `git status --porcelain` succeeds and reports a clean tree. A dirty tree
   means there is work to commit and gate — exactly what step 1 exists for.
2. `_current_branch()` resolves a branch (detached HEAD declines).
3. `git ls-remote <push_remote> refs/heads/<branch>` succeeds and its sha
   equals `git rev-parse HEAD`. This is the load-bearing condition: it is
   what proves the commit that would be gated is the commit already on the
   remote.
4. `gh pr view <branch> --json url,number,state` (with `-R <base_slug>` when
   `_push_target()` supplied one) succeeds and yields a PR.

`ls-remote` rather than `fetch`: it is one read-only network call that writes
no refs and mutates no local state, so a declined fast path leaves the repo
exactly as `_ensure_compile_markers`'s own `fetch` would find it.

### D3 — Fail-safe, not fail-closed

Any nonzero exit, timeout or unparseable output from the probes declines the
fast path and falls through to the existing pipeline. The worst case of a
wrong decline is today's behavior (pay the gate); the worst case of a wrong
*accept* is treating an ungated commit as gated, so the asymmetry is
deliberate and the probe never guesses.

### D4 — Terminal PR states

- `OPEN` — resume at step 5 with the existing `open_or_update_pull_request`
  call, then steps 6-7 unchanged.
- `MERGED` — the pipeline's whole purpose is already achieved. Record the PR
  on the run record (starting one via `_ensure_run_record` if the caller
  supplied none) and finish it through `_finish_or_checkpoint` with status
  `completed_and_merged`; return `outcome="landed"`. `gh pr create` is never
  reached.
- `CLOSED` and not merged — a human closed this PR deliberately. Silently
  re-opening a replacement would override that decision, so this returns
  `outcome="ceiling"` with `refused_step="pr_closed"` and a detail naming the
  closed PR URL. (`ceiling`, not `refused`: the remote already carries the
  pushed branch, and `refused` promises an untouched remote — see the module
  docstring.)

### D5 — Labels on the resume path

Step 3 is skipped, so its pass-marker read-back is unavailable. The fast path
calls `pre_pr_gate.resolve_pr_labels(repo, load_policy(repo), risk, gates,
base_branch, route)` directly — the same function `preflight.write_marker`
records its labels from and the same one `open_or_update_pull_request` already
falls back to for a marker-less caller (the orchestrator's group-PR step). The
computed labels feed both `render_pr_body` and
`open_or_update_pull_request(labels=...)`, and are reported in
`LandOutcome.labels`, so the resume path's outcome is shaped identically to a
normal landing.

`render_pr_body`'s `gate_evidence` on this path states that the gate was not
re-run because the commit was already pushed, rather than asserting a PASS
this invocation did not observe — the body must not claim gate evidence that
was produced by an earlier invocation.

## Risks / Trade-offs

- **A gate regression introduced between two invocations of the same commit
  is not re-detected.** Accepted: the gate's contract is over the commit, and
  the commit is unchanged. Anything that revalidates a merged/open PR against
  newer rules is CI's job, and CI is still watched on the `OPEN` arm.
- **`ls-remote` adds a network call to the non-resume path too.** It replaces
  nothing, but it is a single ref read against a remote the pipeline is about
  to `fetch` from anyway, and it is ordered first so a resume pays only it.

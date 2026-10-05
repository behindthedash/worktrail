## Context

`_worktree_pr_close()` (shared by the fold and propose apply paths) already treats the base
as `<push remote>/<base branch>`: it resolves the push remote once (`remote.pushDefault`,
falling back to `origin`), fetches `<remote> <base branch>`, refuses to proceed when the
local base branch carries commits absent from that ref, and creates the worktree off
`<remote>/<base branch>`. What it never does is resolve the branch *name*. The callers
compute it before the claim via `_repo_base_branch()`, whose last resort is a hardcoded
`main`. On a repo whose default branch is not `main` and whose checkout has no
`refs/remotes/origin/HEAD` — the wake-up-sooner incident, 2026-09-20 — that guess is wrong,
and the apply dies at the fetch with raw git stderr naming neither the resolved branch's
origin nor the `base_branch` policy key that would fix it. The push-remote shape for the
other three refs came from `triage-apply-honour-push-remote` (archived 2026-09-16); this
change completes it for the name.

## Goals / Non-Goals

**Goals:**

- Resolve the base branch from sources that actually verify it, so a non-main default works
  with no local remote-HEAD ref and no operator intervention.
- Fail closed with an actionable error — naming the repo's `base_branch` policy key — when
  no source resolves, before any fetch or worktree exists.
- Keep the already-claimed early return free of any probing, and keep the failure shape
  identical to every other pre-PR failure in this path.
- Preserve the existing test dispatchers' answers so today's suite passes unchanged.

**Non-Goals:**

- The triage evaluator's checkout freshness (brief premise 3) — evaluator-prompt behavior
  on a separate surface.
- Auto-skipping or pre-installing for the pre-PR gate (brief premise 2) — the
  `worktree_bootstrap_cmd` bootstrap step and the `pre-pr-cmd-without-bootstrap`
  drift advisory already cover the mechanical part; the rest is policy design.
- `router.sweep_stale_worktrees.default_base_branch()`'s identical `main` fallback: it
  classifies worktrees for reclamation, where a wrong guess degrades to "not reclaimable"
  rather than to a failed write, so it keeps its cheap local-only probe.
- `land_pr`'s own `--base` handling and the drain remediation paths.

## Decisions

- **Resolution order: policy → local `<remote>/HEAD` → remote `ls-remote --symref <remote>
  HEAD` → fail closed.** The probes run least-invasive first. Policy stays first: it is the
  fleet's explicit override, existing behavior, and must keep working with no probe at all.
  The local ref is a no-network fast path that already answers for any fresh `git clone`
  and is what git itself would use. The wire probe is the new authority for exactly the
  case that broke: a checkout that carries no local remote-HEAD ref. The hardcoded `main`
  is deleted rather than demoted to a last resort — an unverified guess is the defect, and
  the failure now names the one-line remedy instead of dying later inside a fetch.
- **Probe the push remote, not the `origin` literal.** The branch name must exist on the
  remote the fetch and worktree base ref use; on a fork layout (`origin` = upstream) the
  upstream's default can be a branch this fleet never merges to. The probe therefore takes
  the `remote` that `_worktree_pr_close()` already resolved, and the local probe ref
  becomes `refs/remotes/<remote>/HEAD` — extending the no-`origin`-literal invariant
  `triage-apply-honour-push-remote` established for the other three refs.
- **Parse the symref line, not the sha line.** `git ls-remote --symref <remote> HEAD`
  prints `ref: refs/heads/<name>` and the resolved sha on the following line; only the
  `ref:` line names a branch. A run that fails, times out, or answers with no `ref:` line
  is a non-answer — fail closed, never "assume main".
- **Resolve after the claim, inside `_worktree_pr_close()`.** The callers drop their
  `base_branch` computation and the parameter leaves the signature; the name is resolved as
  the first statement of the `try:` block that already wraps fetch → land. This (a) makes
  the already-claimed early return perform no git work at all — today the caller's
  `symbolic-ref` still runs before `_worktree_pr_close()` is entered — (b) resolves the
  name against the same single push-remote resolution as the refs it feeds, and (c) keeps
  every failure on the standard shape: each early return inside the `try:` still falls
  through the outer `finally`'s release-back-to-`queue/`.
- **Teach the fetch failure the remedy rather than adding a pre-flight check.** The
  existing fetch error keeps its raw git text and gains the resolved `<remote>/<base>` and
  the `base_branch` policy key. That covers the remaining mis-resolution — a policy naming
  a branch the remote does not have — without a new check that would itself need a network
  round-trip to be sound.

## Risks / Trade-offs

- **The wire probe fails while the local ref is absent** → the apply fails closed with the
  policy-key error even though a fetch might have succeeded moments later. Accepted: the
  fetch needs the same remote, so the common causes (offline, unauthenticated) fail either
  way; only which error the operator reads changes.
- **The local ref is stale** (the remote's default moved and `<remote>/HEAD` was never
  refreshed) → the stale name wins when that branch still exists, so the PR targets the old
  default; when the branch is gone, the fetch fails loudly with the remedy named. Accepted:
  refreshing the local ref is a checkout mutation this apply should not make, and policy
  `base_branch` is the documented override.
- **One extra network round-trip per fold/propose apply**, only in the no-local-ref case →
  the same apply already fetches, pushes, opens a PR, and watches CI; the probe uses the
  same bounded-timeout class as the surrounding git calls.
- **A repo with no remote at all** now fails at resolution instead of at the fetch — same
  outcome (an apply cannot land a PR without a remote), earlier and clearer.
- **Test-shape risk**: the two already-claimed tests' fake runners currently permit the
  caller-side `symbolic-ref`; their docstrings must stop describing that probe, so a future
  reader does not reinstate pre-claim work.

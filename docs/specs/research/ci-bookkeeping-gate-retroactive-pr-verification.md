# CI bookkeeping-gate integrity hole — retroactive per-commit verification of the 7 affected PRs

Follow-up to `ci-bookkeeping-gate-integrity-hole-audit.md` (which identified the 7 PRs and
confirmed the gate itself was fixed in PR #442 / brief 20260815-150306). That audit proved
current `main` HEAD is green but did not prove each of the 7 bypassed PRs was independently
correct at its own merge point. This note closes that gap.

## Verified Observations

- For each of the 7 PRs identified in the prior audit, the exact merge commit was checked out
  in an isolated `git worktree --detach`, oldest merge-time first, and
  `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
  was run against that exact tree (matching `docs/specs/go-policy.yaml`'s `pre_pr_cmd`). Results:

  | PR | Merge commit | Merged (UTC) | pytest | orchestrate check |
  |---|---|---|---|---|
  | #380 | `c01b4cf` | 2026-08-14T03:23:41Z | 3466 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #399 | `234d403` | 2026-08-15T00:05:43Z | 3516 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #406 | `f7107a2` | 2026-08-15T01:52:25Z | 3543 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #411 | `70c67bc` | 2026-08-15T05:10:04Z | 3546 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #422 | `4096c9c` | 2026-08-15T20:07:28Z | 3609 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #426 | `4e7c832` | 2026-08-15T21:49:29Z | 3618 passed, 2 skipped, 230 subtests | GOLDEN OK |
  | #438 | `96e124b` | 2026-08-15T22:00:06Z | 3623 passed, 2 skipped, 230 subtests | GOLDEN OK |

- All 7 merge commits pass both checks with zero failures, zero errors, and no unexpected
  skips (the same 2 skips present on current `main` HEAD at every point in the sequence). Test
  count grows monotonically PR-over-PR (3466 → 3623), consistent with each PR adding new
  coverage rather than any PR silently dropping tests.
- No worktree required any deviation from the exact `pre_pr_cmd` policy string to produce a
  clean run (no missing fixtures, no flaky reruns, no environment-specific failures).

## Unknowns / Missing Evidence

- None remaining for the scope of this brief. The prior audit's open question — "was each of
  the 7 PRs independently correct at its own merge point, not just in the accumulated
  current-HEAD state" — is answered by the per-commit results above.

## Hypotheses

- None outstanding; this investigation moved the prior audit's hypothesis ("each PR was likely
  individually sound") to a verified fact via direct per-commit test execution rather than
  inference from current-HEAD state.

## Validation Steps

Reproduce any row above:
```bash
git worktree add --detach /tmp/verify-<pr> <merge-commit-sha>
cd /tmp/verify-<pr>
PYTHONPATH=src pytest -q
PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check
git worktree remove --force /tmp/verify-<pr>   # from the canonical checkout
```

## Confirmed Root Cause

Not applicable — this is a verification pass, not a defect investigation. No regression was
found; the CI bookkeeping-gate integrity hole itself (root cause: `bookkeeping_gate.sh`'s
version-bump bypass branch never checked for other changed paths) was already root-caused and
fixed in the prior audit / PR #442.

## Recommended Next Route

None — no code change is needed. All 7 PRs that merged through the bypass are confirmed clean
at their individual merge commits. This brief is closed as `investigation_complete` with no
Route F follow-on.

## Why

The resolve/ci-fix forbidden-path guard decides what a worker "touched" from the wrong diff.
`_forbidden_paths_touched` (`src/worktrail/orchestrator/verify.py:1006-1044`) computes

```
git diff --name-only <pre_sha>..<group-branch>
```

where `pre_sha` is the worker's pre-run HEAD on the group branch. A resolve worker's whole job
is to merge `origin/<base>` into that branch, so **every file any sibling PR landed on base
since the group branched appears in that set** — untouched by this worker, byte-identical to
base. The guard then strikes the worker for "touching" them and the group is quarantined.

Observed live in ggb run `go-20260920-124658` (spec `gitleaks-test-fixture-allowlist`): group
`feature-1` was `CONFLICTING` with `dev`, the resolve worker merged `origin/dev` cleanly and
reported `status=success`, and the guard rejected it —

```
resolve worker touched forbidden path(s) despite status=success:
[.github/workflows/gitleaks.yml, openspec/changes/gitleaks-test-fixture-allowlist/tasks.md]
```

— sending `feature-1` to QUARANTINE as `integration_error`. Both files came from base via
sibling PRs #854/#855: `git diff --stat origin/dev..origin/full-1789933766/feature-1 --
.github/workflows/gitleaks.yml` is empty. The branch's net diff against `dev` was only
`.gitleaks.toml` comments, a new guard test, and two `tasks.md` checkbox flips. PR #856 was
correct and mergeable.

This is not a one-off: every `CONFLICTING` group in a multi-group run hits it, and it gets more
likely the more siblings have already merged — i.e. exactly when the run is going well. Each
false positive costs a human quarantine untangle, and the failure is silent-by-design in the
worst way: the guard's message asserts the worker did something it did not do.

`_detect_self_merge`'s pre/post `pre_sha` baseline is right for *attribution* (what changed
while this worker ran); it is wrong for *scope* (what this branch proposes to change). Scope is
a property of the branch relative to the base tip.

No active change under `openspec/changes/` covers this guard; the capability that owns it,
`resolve-worker-scope-discipline`, is archived, so this change adds to it.

## What Changes

- `_forbidden_paths_touched` narrows its touched set to paths that actually differ from the
  base tip: after computing `pre_sha..<gb>`, it drops every path whose content at `<gb>` equals
  its content at `<remote>/<base>`, computed as `git diff --name-only <remote>/<base>..<gb>` and
  intersected with the pre/post set. A path brought in unchanged by the merge is no longer a
  violation; a path the worker actually edited still is, because editing it makes it differ
  from base.
- The base tip is refreshed with a best-effort `git fetch <remote> <base>` first. If the fetch
  or the base diff fails, the guard falls back to today's unnarrowed set and logs that
  narrowing was unavailable — a possible false positive, but never a silently disarmed guard.
- Both deny-list tiers (absolute spec root, and declared-file-exempt everything-else) are
  evaluated on the narrowed set, since the observed false positive spanned both.

## Capabilities

### New Capabilities

### Modified Capabilities
- `resolve-worker-scope-discipline`: the forbidden-path guard judges scope against the base tip,
  so merging base in is not itself a violation.

## Impact

- `src/worktrail/orchestrator/verify.py` — `_forbidden_paths_touched` only; `_detect_self_merge`
  and the `pre_sha` capture in `_spawn_group_worker` are unchanged.
- `tests/orchestrator/test_verify.py` — existing `_forbidden_paths_touched` cases gain a base
  diff in their fake runner; new cases for merged-in paths and fetch/diff failure.
- Behavioral: groups that merge base in cleanly stop being quarantined for it. No group that
  actually edits a forbidden path becomes mergeable.

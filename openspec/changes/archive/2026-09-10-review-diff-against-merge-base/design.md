## Context

`LiveSpawn.__call__` (`src/worktrail/orchestrator/live.py`) builds the review worker's
`ctx["base_commit"]` as:

```
start_ref, _ = dependency_start_ref(self.repo, self.spec_id, task, self.by_id or {})
base_commit = _resolve_ref_to_sha(self.repo, start_ref)
```

`dependency_start_ref` returns the first materialized dependency branch, or the sentinel
`HEAD` for a root task. `_resolve_ref_to_sha` (handoff 20260829-215209, PR #837) turned the
sentinel into a canonical-repo SHA so the reviewer's `git diff {base_commit}..HEAD` stopped
being a no-op `HEAD..HEAD` inside the worktree. It resolves the ref *as of review time*, so
the value drifts from the fork point as soon as the base moves.

The task worktree and the canonical repo share one object database, so a
`git merge-base <ref> <sha>` run in the canonical repo can see the worktree's commits.

## Goals / Non-Goals

**Goals:**
- Hand the reviewer a `base_commit` whose two-dot range against the worktree `HEAD` is
  exactly the commits the task made.
- Keep the fallbacks that the existing tests pin: literal `HEAD` when `repo` is `None`, the
  start ref's SHA when the merge-base cannot be computed.

**Non-Goals:**
- Changing the prompt text or the two-dot form. A three-dot `{base_commit}...HEAD` would also
  work, but it would make the fix depend on the worker actually typing the range as
  instructed, and it would leave the `base_commit` value itself wrong for anything else that
  reads it (journals, tests, future roles). Fixing the value fixes every consumer.
- Touching `dependency_start_ref` or its `base_ref` pruning; worktree creation is unaffected.
- Rebasing or carrying the base into the task branch before review; that is integrate's job.

## Decisions

**Compute the merge-base in the canonical repo, against the worktree's `HEAD` SHA.**
`git -C <worktree> rev-parse HEAD` gives the task tip; `git -C <repo> merge-base <start_ref>
<tip>` gives the fork point. Both run through the existing `_git` helper with `check=False`.
Running merge-base in the canonical repo rather than the worktree keeps the resolution
consistent with `_resolve_ref_to_sha` and avoids depending on the worktree having the
dependency branch name visible.

**Fall back to the current value, never to the sentinel.** If either git call fails, keep
`_resolve_ref_to_sha(repo, start_ref)`. That is exactly today's behavior, so the worst case is
the status quo, and `test_no_repo_falls_back_to_head_sentinel` keeps passing unchanged.

**Dependent tasks go through the same path.** For a dependent task whose dependency branch has
not moved since the fork, `merge-base(dep_tip, task_tip) == dep_tip`, so
`test_dependent_task_resolves_base_commit_to_dependency_branch_tip` still holds. If the
dependency branch did receive later commits (a fix round on the dependency after the
dependent forked), the merge-base is the commit the dependent actually stacked on, which is
the correct review base.

## Risks / Trade-offs

- A task branch that was rebased onto a newer base after forking has a merge-base equal to
  the new base, which is still the correct diff base. No known worktrail path rebases task
  branches before review (`dependency_start_ref` docstring: "never rebased").
- One extra `git rev-parse` and one `git merge-base` per review dispatch; negligible against
  a worker spawn.

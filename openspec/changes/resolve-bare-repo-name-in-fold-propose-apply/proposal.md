## Why

`work_queue.py`/`create_handoff.py` write a brief's `repo:` frontmatter
verbatim — a bare name like `devops` or an `owner/name`-style value has no
filesystem meaning by itself, and `_resolve_repo_dir()`
(`src/worktrail/router/dashboard.py`) already exists specifically to resolve
such values by basename against a `repos_root` before treating them as a
directory. `queue_triage.py`'s `_apply_fold_into_change()` (line 1571) and
`_apply_propose_change()` (line 1647) skip that resolution: both do
`repo_path = Path(repo)` directly on the verdict's `repo` string, then feed
`repo_path` straight into `git -C <repo_path> worktree add` via
`_worktree_pr_close()`. For a bare-name `repo`, `Path("devops")` resolves
relative to the process's current working directory, so the worktree
creation fails (or, worse, silently targets an unrelated directory that
happens to exist at that relative path) instead of resolving to the actual
checkout. `src/worktrail/router/resolve_repo.py` and
`dashboard.py:_resolve_repo_dir()` already solve this exact problem
elsewhere in the codebase; queue-triage's apply path was never wired to
either.

## What Changes

- `_apply_fold_into_change()` and `_apply_propose_change()` resolve `v.repo`
  to an on-disk directory via `dashboard._resolve_repo_dir()` (absolute/
  home-relative paths resolve directly; a bare/`owner/name`-style value
  resolves by basename under a `repos_root`) instead of doing `Path(repo)`
  directly. An unresolvable `repo` now fails with a clear `error` status in
  the action-log entry (mirroring the existing "missing repo or
  target_change" error shape) instead of attempting a worktree op against a
  nonexistent or wrong path.
- `apply_verdicts()` grows a `repos_root` parameter (threaded to both
  functions above) and `queue-triage apply` grows a `--repos-root` CLI flag
  defaulting to `~/projects` — the same default `dashboard.py`'s own
  `--repos` flag uses — so a bare-name `repo:` value resolves against the
  same workspace layout convention the rest of the router/dashboard code
  already assumes.
- Regression tests: a bare repo name resolves to the matching sibling
  checkout under `repos_root` before a fold/propose apply's worktree op
  runs; an unresolvable bare name produces an `error` status action-log
  entry instead of a filesystem/git failure; an absolute-path `repo` value
  (today's only working case, and every existing test's shape) is
  unaffected.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `fold-propose-apply-repo-resolution`: `fold-into-change`/`propose-change`
  apply actions resolve a verdict's `repo` value to an on-disk checkout the
  same way `dashboard.py`'s brief-resolution path does, instead of treating
  it as a literal filesystem path.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`_apply_fold_into_change`,
  `_apply_propose_change`, `apply_verdicts`, `cmd_apply`, `main`)
- `src/worktrail/router/dashboard.py` (`_resolve_repo_dir` — reused, not
  modified)
- `tests/workqueue/test_queue_triage.py`

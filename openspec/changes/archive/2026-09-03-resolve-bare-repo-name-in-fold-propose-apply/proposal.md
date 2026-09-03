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

A second, related gap sits one step earlier in the same pipeline. When a
brief has no `repo:` frontmatter it is evaluated in the `__none__` group,
and `EVALUATOR_PROMPT_TEMPLATE` forbids `fold-into-change`/`propose-change`
for that group outright — the evaluator must answer `needs-decision` or
`keep` even when its own evidence names the owning repo unambiguously.
Observed 2026-09-03 on brief `20260903-115759`: the evaluator confirmed the
premise at high confidence, wrote "the fix clearly belongs in this repo's
own queue_triage.py", and still returned `keep` because propose-change was
not a valid verdict for a repo-less brief. The brief stayed queued with a
known target and no path forward except a human editing its frontmatter.
Once apply can resolve a bare repo name (above), the evaluator can be
allowed to name one — and apply can record that resolution on the brief
itself so every later triage pass runs it in the right group.

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
- The `__none__` evaluator group may return `propose-change` when the
  evidence identifies the owning repo: `evaluate_group()` presents the
  known workspace repos (the directory basenames under `repos_root`) to a
  repo-less group, and `parse_verdicts()` accepts a repo-less
  `propose-change` only when `target_repo` is one of those presented names
  (anything else, or a `fold-into-change`, still downgrades to `keep`, since
  no candidate changes were ranked for a repo-less brief). `needs-decision`
  remains the answer when the evidence cannot name the repo.
- `_apply_propose_change()` for a repo-less verdict resolves `target_repo`
  through the same `_resolve_repo_dir()` path and stamps the resolved bare
  name onto the brief's `repo:` frontmatter (via `work_queue._set_fm_fields`)
  before running the worktree/PR flow, so the brief carries its repo from
  then on even if the proposal PR later fails. Preview (no `--confirm`)
  reports the planned `repo:` stamp alongside the planned branch/PR title.
  The WIP-cap check for such a verdict uses the resolved repo, not the
  `__none__` group key.
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
- `queue-triage` evaluation: a repo-less (`__none__`) group may return
  `propose-change` naming a known workspace repo as `target_repo`; applying
  it stamps that repo onto the brief's `repo:` frontmatter before proposing.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`EVALUATOR_PROMPT_TEMPLATE`,
  `evaluate_group`, `parse_verdicts`/`_has_valid_target`,
  `_apply_fold_into_change`, `_apply_propose_change`, `_preview_verdict`,
  `_propose_change_over_cap`, `apply_verdicts`, `cmd_evaluate`, `cmd_apply`,
  `main`)
- `src/worktrail/workqueue/work_queue.py` (`_set_fm_fields` — reused, not
  modified)
- `src/worktrail/router/dashboard.py` (`_resolve_repo_dir` — reused, not
  modified)
- `tests/workqueue/test_queue_triage.py`

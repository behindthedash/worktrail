## Why

`_scope_escalation_files()` (`src/worktrail/orchestrator/live.py:3314-3354`) validates a
review/fix report's `missing_context` paths before granting a scope escalation: each candidate
must be a repo-relative, existing file under the worktree root, and must not collide with
another in-flight task's declared files. It never checks whether a candidate is gitignored.
A worker can list a scratch/cache path (e.g. `.claude/tsc-cache/<uuid>/affected-repos.txt`,
gitignored by `.gitignore:20`'s `.claude/tsc-cache/`) in `missing_context`, and the orchestrator
adds it to `task["files"]` and prints `fix scope widened once: ...` for it exactly as if it
were real source scope.

A run captured in `~/.worktrail/detached/orch-worktrail-triage-non-goals-scope-check.log`
shows this happening for real: line 13 logs a "fix scope widened once" admitting six
`.claude/tsc-cache/<uuid>/{affected-repos.txt,edited-files.log}` paths, and lines 16-21 log six
`dependency_file_drift` WARNs for task 1.2 at 17:36:00 citing exactly those paths — a downstream
task's worktree is missing files another task's `files` declared, because a gitignored path is
never committed and so never exists for a dependent worktree to find. A gitignored file can
never satisfy the declared-vs-actual contract `_check_dependency_file_drift` enforces
(`live.py:3590-3632` — a declared file is expected to reach `HEAD` via a committed change), so
granting scope over one is never useful and only manufactures spurious drift warnings for every
task downstream of the escalated one.

## What Changes

- `_scope_escalation_files()` additionally excludes any candidate path that `git check-ignore`
  reports as ignored in the task's worktree, before returning the candidate list. A candidate
  excluded this way is not counted toward "candidates found" for the purpose of deciding
  whether escalation fires at all — if every listed path is gitignored, escalation does not
  fire, exactly as if `missing_context` had contained no existing-file paths.

## Capabilities

### Modified Capabilities

- `fix-scope-escalation`: scope-escalation candidate validation now also excludes gitignored
  paths, alongside the existing existing-file and in-flight-collision checks.

## Impact

- **Code**: `src/worktrail/orchestrator/live.py` — `_scope_escalation_files()`.
- **Tests**: `tests/orchestrator/test_context_widening.py` — add coverage: a `missing_context`
  path matching a gitignored pattern does not widen scope, and a mix of one gitignored and one
  real path widens scope with only the real path.
- **Non-goals**: changing the in-flight-collision rule or the once-only escalation rule (both
  already correct and untouched by this fix); changing `_check_dependency_file_drift`'s WARN
  text or drift-detection logic itself (it correctly flags the symptom — the bug is upstream,
  in what gets declared in the first place); changing worker prompts to stop *naming*
  gitignored paths in `missing_context` (a worker can legitimately need to read a gitignored
  file for context without it becoming task scope — the fix is that such a path never becomes
  a scope escalation, not that it can never be mentioned).

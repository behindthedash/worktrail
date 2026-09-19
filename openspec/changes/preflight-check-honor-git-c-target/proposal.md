## Why

`worktrail-preflight check` gates the repo it is handed via `--repo` and never looks at the
command being gated. The machine-level PreToolUse hook (behindthedash/devops
`scripts/claude-hooks/preflight-gate.py`) resolves that `--repo` from a leading `cd <path>`
prefix else the session cwd, and its `GIT_PUSH_RE` tolerates a `-C <path>` token for *matching*
without ever using the path. So `git -C <worktree> push` is gated against the session's cwd, not
against `<worktree>`.

Reproduced live 2026-09-17 (brief `20260917-200826-preflight-gate-ignores-git`): after
`worktrail-preflight run --repo <sync-worktree>` recorded a pass marker whose state hash matched
that worktree's actual HEAD/status/diff, `git -C <sync-worktree> push` was still refused with
"pre-PR preflight gate has not passed against the current tree" (`preflight.py:647`); the
identical push succeeded immediately after `cd`-ing into the worktree first, with no new commits.
This is a false deny for any workflow that pushes from a worktree via `git -C` without `cd`-ing
first -- including worktrail's own sync-before-teardown step. It can also produce the mirror-image
false *allow*: a clean cwd with a valid marker would let a `git -C <other-dirty-repo> push`
through.

Human decision on the brief (`dec-20260917-200826-preflight-gate-ignores-083c7c7a45b8`) is
binding: keep the fix under worktrail and move `-C`/`--git-dir` parsing into
`worktrail-preflight check` itself, rather than teaching the devops hook to pre-resolve a
second, drift-prone repo root. Confirmed by inspection that `preflight.py` has no such parsing
today: its only `--git-dir` uses are `rev-parse --absolute-git-dir` for marker/lock location
(`preflight.py:453`, `:490`).

## What Changes

- `worktrail-preflight check` resolves its gate target from the gated command when that command
  is a `git` invocation carrying repo-redirecting global options (`-C`, `--git-dir`,
  `--work-tree`, in both `--opt value` and `--opt=value` forms), falling back to `--repo`
  otherwise. Multiple `-C` options compose the way git itself composes them.
- Resolution goes through `git <parsed global options> rev-parse --show-toplevel`, so a
  `--git-dir` pointing at a linked worktree's private git dir resolves to that worktree's root
  rather than being guessed from the path shape. Any failure (unparseable command, nonexistent
  path, not a repo) falls back to `--repo` -- the gate keeps its current behaviour rather than
  erroring.
- The verdict gains a `target_repo` key when, and only when, the command redirected the target,
  so a deny is diagnosable as "gated against a different repo than `--repo`".
- Companion (out of scope here, tracked as a devops handoff): the hook forwards the raw command
  instead of pre-resolving `repo_root`.

## Capabilities

### New Capabilities
- `preflight-command-target-repo`: the preflight gate checks the repo the gated command will
  actually act on.

### Modified Capabilities

## Impact

- `src/worktrail/router/preflight.py` (new command-target resolution; `check()` target
  selection).
- `tests/router/test_preflight.py`.
- Behaviour changes only for commands that carry `-C`/`--git-dir`/`--work-tree`; every other
  invocation (no `--command`, `gh pr create`, a bare `git push`) resolves to `--repo` exactly as
  today.

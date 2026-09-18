## Context

The worktree write guard is a Claude Code PreToolUse hook installed machine-wide
(`~/projects/devops/scripts/claude-hooks/worktree-write-guard.cjs`, wired in
`~/.claude/settings.json`), not part of worktrail. For a Bash call it (1) denies on any
explicit absolute path token inside a canonical checkout, else (2) when the command looks like a
mutation (`tee`, `sed -i`, `cp/mv/rm/touch/mkdir`, `git add/commit/rm/mv`, a redirect) and names
no absolute target, gates on the effective cwd (the Bash cwd, or a leading `cd <dir> &&`).
The `#orchestrator` block trips rule (2): `mktemp`, `tee`, `rm -f` with only `$VAR`-relative
paths. `worktrail-live full-real` itself runs detached and is never seen by the hook; only the
launching shell's cwd matters.

## Goals / Non-Goals

- Goals: make the implement pipeline launch reliably with the guard installed, without an
  override env var and without changing what `SPEC_ROOT` means; stop leaving empty throwaway
  parent dirs beside the repo.
- Non-Goals: changing the guard; changing `new`/`modify` (they already use `$WT`); removing
  `<repo>-worktrees/` (durable run state); adding `WORKTREE_GUARD_ALLOW=1` to any skill text
  (it would blanket-exempt every write for the call, defeating the guard's purpose).

## Decisions

- **Neutral cwd, not a linked-worktree `SPEC_ROOT`.** The brief offered both. `SPEC_ROOT` is
  what `full-real --repo` receives, and `default_worktree_base()`, the journal/status paths in
  `live.py`, `safety_net_report.py` and every `integrate.py` throwaway dir are derived from
  `repo.parent / f"{repo.name}-<suffix>"`. A linked worktree named `spec/<id>` therefore moves
  the whole run's state to `<id>-worktrees/` beside it, where dashboard/resume (which compute
  the same path from the canonical repo) do not look, and produces the `expected 'main'`
  warning. The guard only cares where the shell stands, so moving the shell is the minimal,
  side-effect-free fix, and it matches `#worktree-lifecycle`'s existing "operate by path,
  never `cd` in" rule.
- **Neutral dir = `$REPO`'s parent, verified.** `NEUTRAL_CWD="$(dirname "$REPO")"` is the
  directory that already holds `<repo>-worktrees/`, so it is outside every checkout on the
  documented layout. It is verified with `git -C "$NEUTRAL_CWD" rev-parse --is-inside-work-tree`;
  if that succeeds (the parent is itself a repo), fall back to `mktemp -d`. The step is a
  single `cd`, run once before `#precheck-gate`, and the pipeline text forbids any later
  `cd "$REPO"`/`cd "$WT"`.
- **Parent-dir removal inside the lock.** Each helper already does `git worktree remove` under
  `git_lock`; concurrent pipeline groups `git worktree add` into the same `<repo>-integrate/`
  under the same lock. Removing the leaf, then `os.rmdir(parent)` (ignoring `OSError`) inside
  that same locked region means a sibling's add either completed (parent non-empty, rmdir
  fails harmlessly) or has not started (it re-creates the parent, as `git worktree add` makes
  intermediate dirs). A single `_rmdir_if_empty(path)` helper in `integrate.py` serves all
  three sites.

## Risks / Trade-offs

- A host whose repo parent is itself a git work tree takes the `mktemp -d` fallback; the temp
  dir is tiny and never written to, so it is not cleaned up by the pipeline.
- `os.rmdir` on a parent that a non-worktrail process is using as cwd succeeds on Linux and
  leaves that process with a dangling cwd; nothing in worktrail stands in these dirs, and the
  `#worktree-lifecycle` rule already forbids operators doing so.

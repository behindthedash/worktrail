# Stale-Worktree Cleanup (Route picker action `cleanup-worktrees`)

`dashboard.py` is intentionally git-free, so it only **lists** worktree directories
(under `<repo>/../<repo>-worktrees/<branch>/`) and surfaces a count. The actual
staleness judgement and pruning are git operations and live here. Run them only when
the user picks the cleanup option (or asks to clean up worktrees).

A worktree is **stale** when its work has landed or been abandoned and the checkout
is just taking up disk. It is **NOT** stale — never prune — when it holds
uncommitted changes or unpushed commits. Pruning is destructive-ish (it deletes a
checkout), so classify first, show the user, and confirm before removing.

## 1. Enumerate (authoritative — from git, not the filesystem)

Run from the canonical repo (`$REPO`), which owns all its worktrees:

```bash
git -C "$REPO" worktree list --porcelain
git -C "$REPO" fetch --prune origin -q     # refresh merge/branch state first
BASE="$(git -C "$REPO" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#origin/##')"
BASE="${BASE:-main}"   # some repos integrate on dev; resolve_repo/policy knows the real base
```

## 2. Classify each worktree

For each worktree path `WT` with branch `BR` (skip the main checkout itself):

```bash
git -C "$WT" status --porcelain        # non-empty -> DIRTY, keep
# merged into base? (squash-merge aware)
git -C "$REPO" cherry "origin/$BASE" "$BR" | grep -q '^+' && echo UNMERGED || echo MERGED
git -C "$REPO" ls-remote --exit-code --heads origin "$BR" >/dev/null 2>&1 || echo GONE  # remote branch deleted
```

Buckets:
- **MERGED / GONE, clean** → stale, safe to prune.
- **UNMERGED but clean and the branch is fully contained in another merged branch** → likely an orchestrator task leaf; treat as prunable only if `git cherry` shows nothing unique. When in doubt, leave it and say so.
- **DIRTY or unpushed** → keep; list it but never auto-prune.

`git cherry "origin/$BASE" "$BR"` with all lines `-` (or empty) means every commit is
already upstream — the squash-merge-safe "is it merged" check (see memory
`[[feedback_git_main_squash_divergence]]`).

## 3. Present, confirm, prune

Show a compact table: branch · state (MERGED/GONE/DIRTY) · prunable? Group the
clearly-safe ones and ask for a single confirmation before removing. Then, for each
confirmed-stale worktree, run the shared
`subagent-prompts.md#worktree-deletion-liveness-guard` before removing it — this flow
has no `$RUN` of its own, so resolve the run-records directory from policy instead:

```bash
RUN_RECORDS_DIR=$(worktrail-policy --repo "$REPO" --json | python3 -c "import json,sys; print(json.load(sys.stdin)['run_record_dir'])")
```

Then, with `$RUN_RECORDS_DIR` set above and `$INVOCATION_CONTEXT_DISPATCH_ID` passed
through unchanged from the invoking shell, run the guard body from
`#worktree-deletion-liveness-guard` against this `$WT`. Only proceed to remove if the
guard did not block:

```bash
git -C "$REPO" worktree remove "$WT"          # refuses if dirty — that's the safety net
git -C "$REPO" branch -D "$BR" 2>/dev/null    # only after the worktree is gone and the branch is merged/gone
```

If `worktree remove` refuses (unexpected local changes), do **not** force; report it
and leave that one for the user. Finish with `git -C "$REPO" worktree prune` to clear
any now-dangling administrative entries.

## Notes

- Keep `<repo>-worktrees/run-*.json` orchestrator journals — they are not worktrees
  and `detect_stage` still reads them for verify/tail resume state.
- Never run cleanup against the base checkout or the directory the session launched
  from. Only worktrees under `<repo>-worktrees/` are in scope.
- This procedure also covers orphan recovery after a cancelled/crashed
  orchestrator run. `git worktree prune` only drops registrations whose
  directory is already gone — it never deletes a live worktree. A quarantined
  group intentionally keeps its worktree; when in doubt, mention it, don't
  auto-delete.

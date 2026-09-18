## Why

The `implement` pipeline (Route D, and Route E when it continues inline into D) runs
`#precheck-gate` and `#orchestrator` with `SPEC_ROOT=$REPO`, the canonical checkout
(`skills/worktrail-sdd-workflow/references/pipeline-details.md:150-153`). The `#orchestrator`
block contains `mktemp`/`tee`/`rm -f` and `worktrail-detach launch --cwd "$SPEC_ROOT"`
(`skills/worktrail-go/references/subagent-prompts.md:730-783`). On a Claude Code host with the
worktree write guard installed (`worktree-write-guard.cjs`, a machine-level PreToolUse hook that
lives outside this repo), any Bash call that looks like a mutation and names no absolute path is
gated on the shell's working directory, and a cwd inside the canonical checkout is denied with
`Worktree guard: blocked write to the canonical checkout of ...`. Nothing in the pipeline pins
where the shell is standing, so a `/go` session whose shell is (or has `cd`'d) inside `$REPO`
cannot even reach the launch — observed 2026-09-18 on run `go-20260918-074849` (spec
`apply-brief-triage-verdict-from-file`), where `git worktree add` from that cwd was blocked too
until re-run via `git -C` from a neutral cwd.

The workaround used that day — a linked worktree as `SPEC_ROOT` — trades the block for a mess:
`worktree.default_worktree_base()` and `live.py` derive the run-state dir from `SPEC_ROOT`'s
name, so run journal, status sidecar, runplan cache and task worktrees land in a
`<spec-worktree-name>-worktrees/` beside the linked worktree instead of the canonical
`<repo>-worktrees/`, where the dashboard and resume look; `full-real` warns `repo is on
'spec/...', expected 'main'`; and `integrate.py`'s throwaway checkouts are created under
`<spec-worktree-name>-integrate/` and `-checkbox-check/`/`-checkbox-sync/`. Those three parent
dirs are left behind empty after every run regardless of `SPEC_ROOT` (each helper removes its
own leaf checkout but never the parent it created) — the leftovers the brief asked teardown to
remove. (Work-queue brief `20260918-085957-implement-pipeline-spec-root-canonical`.)

## What Changes

- The `implement` pipeline keeps `SPEC_ROOT=$REPO` (the orchestrator is designed to run against
  the canonical base checkout) but pins **where the shell stands**: before `#precheck-gate`, the
  shell moves to a neutral directory outside every git work tree, and no later step `cd`s into
  `$REPO` or a worktree. Every repo-touching command is already by-path (`git -C "$REPO"`,
  `--repo "$SPEC_ROOT"`, `--cwd "$SPEC_ROOT"`), so the only change is the explicit neutral-cwd
  step plus a note in `#orchestrator` that the block assumes it. This is the same discipline
  `#worktree-lifecycle` already imposes for worktrees, extended to the canonical checkout.
- The pipeline documents why a linked worktree must **not** be substituted for `SPEC_ROOT`:
  run state and throwaway dirs follow `SPEC_ROOT`'s name, so the run becomes invisible to
  resume/dashboard and scatters `<name>-worktrees/` beside the linked worktree.
- `integrate.py`'s three throwaway-checkout helpers (`_integration_worktree`,
  `detect_checkbox_status_divergence`, `sync_checkbox_status`) remove the `<repo>-integrate/`,
  `<repo>-checkbox-check/`, `<repo>-checkbox-sync/` parent directory they created once it is
  empty, under the same registry lock as the leaf removal so a concurrent group's `worktree add`
  into the same parent cannot race it. A non-empty parent is left alone.
- `<repo>-worktrees/` is deliberately **not** removed: it holds the run journal, status sidecar
  and runplan cache that resume, the dashboard and `sync-before-teardown` read. The stray
  `<spec-worktree-name>-worktrees/` from the 2026-09-18 workaround is a one-time manual cleanup.
- `tests/test_plugin_surface.py` pins the neutral-cwd step and the absence of `cd "$REPO"` in
  the implement pipeline; `tests/orchestrator/` pins the parent-dir cleanup.

## Capabilities

### New Capabilities
- `implement-pipeline-neutral-cwd`: the implement pipeline runs precheck, compile and the
  orchestrator launch from a shell cwd outside every checkout, with `SPEC_ROOT=$REPO`.
- `throwaway-worktree-parent-cleanup`: `integrate.py` throwaway checkouts leave no empty
  `<repo>-integrate/`, `<repo>-checkbox-check/` or `<repo>-checkbox-sync/` directory behind.

### Modified Capabilities

## Impact

- `skills/worktrail-sdd-workflow/references/pipeline-details.md` (`#implement-pipeline` steps).
- `skills/worktrail-go/references/subagent-prompts.md` (`#orchestrator` cwd note).
- `src/worktrail/orchestrator/integrate.py` (one small helper, three call sites).
- `tests/test_plugin_surface.py`, `tests/orchestrator/test_integrate.py`,
  `tests/orchestrator/test_checkbox_status_divergence.py`.
- No CLI, flag, journal or on-disk format change. The `new`/`modify` pipelines (which use
  `SPEC_ROOT=$WT`) are untouched. The write guard itself lives in `behindthedash/devops` and is
  not changed.

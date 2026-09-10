## Why

Drain's unattended OpenSpec archive sweep (`archive_openspec_change` ->
`_run_openspec_archive` in `src/worktrail/drain/drain.py`) has exactly one
guard before it runs `openspec archive -y <change-id>`: it refuses if
`tasks.md` still has an unchecked task. It never runs
`openspec validate --strict` and never compares the change's delta specs under
`openspec/changes/<id>/specs/` against the canonical `openspec/specs/` tree.
`openspec archive` applies those deltas as part of archiving, so a delta that
has drifted -- a `MODIFIED`/`REMOVED`/`RENAMED FROM` requirement whose name no
longer exists in the canonical spec, or a delta overtaken by an archived
sibling that touched the same requirement more recently -- is discovered only
when `openspec archive` itself fails inside the sweep's throwaway worktree
(logged as an opaque error string and retried every night), or, worse,
succeeds and silently reverts the sibling's newer scenarios onto the canonical
spec and opens a PR carrying that regression.

The identical gap in the interactive path was closed by
`close-stale-archive-delta-precheck` (PR #1100 / `d64b265a`, archived under
`openspec/changes/archive/2026-09-09-close-stale-archive-delta-precheck`),
which added `_delta_precheck` to
`src/worktrail/router/close_stale_openspec.py`. That change scoped itself to
the close-stale command and its `worktrail-go` skill row only, and its
proposal explicitly listed `drain.py`'s `archive_openspec_change` as out of
scope. `drain.py` today imports only `flip_and_archive` from that module
(drain.py:126, used by `close_stale_bookkeeping`), so the drain archive path
remains uncovered. The unattended sweep is the path where a silent regression
matters most: nobody is watching it, and the PR it opens is labeled
`go:risk-low` and eligible for auto-merge.

## What Changes

- `_run_openspec_archive` runs the same read-only delta pre-check
  (`close_stale_openspec._delta_precheck`) after the existing unchecked-task
  refusal and before invoking `openspec archive -y`. Any pre-check refusal --
  validate failure, missing canonical target, or archived-sibling delta drift
  -- raises `RuntimeError` with the pre-check's error string, so the sweep
  engine's existing per-finding isolation logs it and skips the finding with
  no `openspec archive`, commit, push, or PR.
- Drain has no drift override. The sweep is unattended, so the
  `--allow-delta-drift` judgment call has no operator to make it; a drifted
  delta stays refused on every sweep until someone reconciles it via the
  interactive close-stale path.
- The drain skill (`.claude/skills/drain/skill.md`) documents the pre-check as
  part of the archive sweep's refusal conditions.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `drain-stage-remediation-table`: the "OpenSpec change archive remediation"
  requirement gains a delta pre-check refusal class alongside the existing
  unchecked-task refusal.

## Impact

- `src/worktrail/drain/drain.py` -- `_run_openspec_archive` calls
  `_delta_precheck`; new import from `router.close_stale_openspec`.
- `tests/drain/test_drain.py` -- the existing archive fakes gain an
  `openspec validate` stub; new coverage for each refusal class and the
  ordering guarantee (refusal happens before `openspec archive`, with no
  commit/push/PR).
- `.claude/skills/drain/skill.md` -- archive sweep refusal conditions.

Out of scope: changing `_delta_precheck` itself, adding a drift override to
drain, or changing the interactive close-stale command.

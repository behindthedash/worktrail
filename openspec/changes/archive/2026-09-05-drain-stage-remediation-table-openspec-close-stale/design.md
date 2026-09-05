## Context

`close_stale_bookkeeping` already owns the full short-lived-worktree lifecycle
(open-PR pre-check, `_reset_stale_bookkeeping_worktree`, `git worktree add -b
fix/close-stale-<id>`, commit, `push --force`, `_land_remediation_pr`,
best-effort worktree removal). Only the "what to edit in the worktree" step is
devkit-specific: it resolves `TASK-*.md` paths up front (before the worktree
exists) and calls `set_status_completed` on each. The OpenSpec equivalent
already exists as `worktrail.router.close_stale_openspec.flip_and_archive(wt,
change_id, task_ids)`, which flips `tasks.md` checkboxes and runs
`openspec archive -y <change-id> --json`, returning a result dict with
`flipped`, `already_checked`, `archived`, and `error`.

`dashboard.scan()` rows carry `format` (`"openspec"` for changes; devkit rows
may omit it). `find_stale_bookkeeping_specs` currently drops it.

## Goals / Non-Goals

**Goals:**
- Stop the per-sweep `RuntimeError` for OpenSpec stale-bookkeeping findings
  and actually close them out (flip + archive + docs-only PR).
- Keep the devkit path byte-for-byte in behavior.
- Reuse the existing worktree/PR lifecycle and `flip_and_archive`; no new
  helper module.

**Non-Goals:**
- Changing `archive_openspec_change` (complete-stage archive). It remains a
  separate row with its own finder and pre-archive refusal check.
- Changing `flip_and_archive`, `dashboard.scan()`, `REMEDIATION_TABLE` shape,
  the summary dict, or any CLI flag.
- Any new remediation category.

## Decisions

- **Carry `format` on the finding, default `"devkit"`.** The finder reads
  `row.get("format") or "devkit"`. Existing devkit findings and hand-built test
  findings without `format` keep working, so no existing test needs editing.
  Alternative: sniff the filesystem in the action (does
  `openspec/changes/<id>` exist?). Rejected — the dashboard already knows the
  format, and sniffing could misfire on a repo carrying both trees.
- **Branch inside `close_stale_bookkeeping`, not a second table row.** The
  stage is the same (`stale-bookkeeping`) and the finder is the same; only the
  edit step differs. A second row would duplicate the whole worktree/PR
  lifecycle. The `TASK-*.md` resolution moves under the devkit branch so an
  OpenSpec finding never reaches it.
- **OpenSpec edit step = `flip_and_archive(wt, spec_id, task_ids)` run in the
  worktree after `git worktree add`.** `result["error"]` is raised as
  `RuntimeError` (caught by the sweep's per-finding isolation, D2 of the
  original change). When `result["flipped"]` is empty and
  `result["archived"]` is false, return the existing `pr_url: None` no-op
  shape, mirroring the devkit "already completed on base" path. Otherwise
  `git add -A` (the archive is a directory move, so path-listing is not
  practical), commit, `push --force -u origin <branch>`, and
  `_land_remediation_pr` with a body stating the stale tasks were flipped and
  the change archived.
- **`flip_and_archive` is called with explicit `task_ids`** (the finding's
  `stale_task_ids`), not `None`, so only the dashboard-confirmed stale tasks
  are flipped. If the change still has other unchecked tasks after that,
  `openspec archive -y` proceeds with a warning; that matches
  `flip_and_archive`'s existing contract and the dashboard only labels a change
  `stale-bookkeeping` when its pending tasks' files are all shipped.

## Risks / Trade-offs

- **`openspec` binary availability in tests.** The regression test
  monkeypatches `drain.subprocess.run` (as the devkit test does for `gh`), and
  `flip_and_archive` invokes `subprocess.run` from its own module, so the test
  patches `worktrail.router.close_stale_openspec.subprocess.run` as well to
  fake `openspec archive` by moving the change directory under
  `openspec/changes/archive/`. This keeps the test hermetic.
- **`git add -A` scope.** The worktree is freshly created from `base` and only
  `flip_and_archive` touches it, so `-A` stages exactly the flip and the
  archive move.

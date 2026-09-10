## 1. Format-aware sync-pending remediation

- [x] 1.1 In `src/worktrail/drain/drain.py`, preserve the dashboard row format
      in `find_sync_pending_specs()` (defaulting absent format to `devkit`),
      then make `build_sync_command()` and `_run_sync_pending()` select the
      format-native agent operation: retain `opsx:sync` for OpenSpec changes
      and dispatch developerkit spec-sync for `docs/specs/` findings. Keep the
      existing isolated worktree, commit, push, and shared PR-landing path.
      When an exit-zero sync has no worktree diff, re-check the finding; raise
      a descriptive remediation error if it is still `sync-pending`, but allow
      a no-op if it was reconciled concurrently. (Requirement: Sync-pending
      remediation; design.md Decisions 1-3.)
      In `tests/drain/test_drain.py`, add coverage that the finder carries
      `devkit` for a format-less row and `openspec` for an OpenSpec row; exact
      command assertions prove each format dispatches its native sync
      operation; and an exit-zero/no-diff action whose re-check is still
      `sync-pending` fails without opening a PR while a concurrently
      reconciled no-diff finding remains a no-op.
  files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py` and
      confirm the sync-pending regression coverage is green.
- [ ] 2.2 [e2e] Run `openspec validate format-aware-drain-sync-pending --strict`
      and `worktrail-compile openspec/changes/format-aware-drain-sync-pending`.

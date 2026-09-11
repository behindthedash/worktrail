## 1. Durable ledger and sweep (`pr-ledger-recovery`)

- [x] 1.1 Implement the atomic URL-keyed PR ledger, watcher-heartbeat helpers, live-state
      query/classification, idempotent recovery-brief creation, and the
      `worktrail-pr-ledger` CLI (`register`, session query, and `sweep`) in
      `src/worktrail/router/pr_ledger.py`; register the console entry point in
      `pyproject.toml`. Add `tests/router/test_pr_ledger.py` covering atomic/upsert behavior,
      malformed-ledger preservation, merged removal, green-auto-merge retention, stale/red and
      BLOCKED recovery, query failure retention, and the required fake-opener red-CI repro that
      produces one `pr fix` brief across repeated sweeps. The CLI contract must be usable by the
      existing external five-minute reconciliation job without this repository modifying that
      host crontab.
      files: src/worktrail/router/pr_ledger.py, pyproject.toml, tests/router/test_pr_ledger.py
      Covers: Open pull requests have durable, deduplicated ledger entries; Periodic sweep creates one recovery brief for an unwatched non-terminal PR

## 2. Register all opening paths (`pr-ledger-recovery`, `pr-landing-pipeline`)

- [ ] 2.1 [depends: 1.1] Integrate the shared ledger helper into
      `src/worktrail/router/land_pr.py` after find-or-create returns a PR URL, including the
      existing-PR resume path and watcher heartbeat lifecycle. Extend
      `tests/router/test_land_pr.py` and/or `tests/router/test_land_pr_resume.py` to prove a
      created PR and a resumed PR register once before the landing outcome is returned, and that
      a ledger registration failure is surfaced without claiming successful durable recovery.
      files: src/worktrail/router/land_pr.py, tests/router/test_land_pr.py, tests/router/test_land_pr_resume.py
      Covers: Every PR-opening path lands through the shared pipeline

- [ ] 2.2 [depends: 1.1] Extend `src/worktrail/router/preflight.py`'s hook-facing successful
      `gh pr create` path to register the actual created PR through the shared ledger without
      recording a denied or failed command. Extend `tests/router/test_preflight.py` with command
      parsing and registration-success/failure cases, including session/run provenance when the
      hook supplies it.
      files: src/worktrail/router/preflight.py, tests/router/test_preflight.py
      Covers: Open pull requests have durable, deduplicated ledger entries

## 3. Prevent interactive abandonment (`pr-ledger-recovery`)

- [ ] 3.1 [depends: 1.1] Update `hooks/suggest_next_step.py` to query
      `worktrail-pr-ledger` for non-terminal PRs owned by the Stop-hook session before writing
      its normal suggestion sentinel, block with concise PR recovery guidance when one exists,
      and preserve the current headless and fail-open behavior. Extend
      `hooks/test_suggest_next_step.py` for same-session blocking, different-session pass,
      missing/failed command fail-open, and preservation of the normal once-per-session flow.
      files: hooks/suggest_next_step.py, hooks/test_suggest_next_step.py
      Covers: Interactive session end is guarded by its open PRs

## 4. Verification

- [ ] 4.1 [depends: 2.1, 2.2, 3.1] [e2e] Run the focused ledger, landing, preflight, and Stop-hook
      tests, then `pytest -q`. Run `openspec validate pr-ledger-recovery-sweep --strict` and
      `worktrail-compile openspec/changes/pr-ledger-recovery-sweep`; record the external
      five-minute cron deployment handoff rather than editing an unversioned host crontab from
      this repository.

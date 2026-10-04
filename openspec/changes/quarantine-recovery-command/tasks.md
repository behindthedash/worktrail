## 1. Recovery engine and command

- [ ] 1.1 Add `src/worktrail/orchestrator/recover.py`: selection resolution (each `--group`
      name must have a journal record in state QUARANTINED and resolve through
      `quarantine_selfcheck.group_task_ids`, refusing when that returns None; each named
      `--tasks` id must hold a failed/escalated entry, the `clear_tasks` typo guard; no
      selector at all is a refusal), branch repair for every selected task with failed or
      escalated entries (locate `worktree.task_branch(spec_id, task_id)`, use its existing
      checkout via `live._worktree_checkouts_on_branch` or a throwaway worktree modeled on
      `integrate._integration_worktree` when the branch has no checkout, refuse on uncommitted
      changes, resolve the base with `live._live_base_ref`, no-op when `git merge-base
      --is-ancestor <base> <branch>` already holds, else `git merge --no-edit <base>` and on
      conflict capture `git diff --name-only --diff-filter=U` before `git merge --abort` and
      refuse with those exact paths), in-memory journal composition (group records and the
      `resumed_quarantines` audit reuse `resume_group.clear_groups`; the entry clearing reuses
      a helper factored out of `clear_tasks`' completion-record guardrail and dependency-gate
      cascade so both commands share one implementation, including the nothing-to-clear
      refusal), and one `progress.atomic_write_text` write only after every repair succeeded
      and a RunLock re-check immediately before it passes; `--dry-run` performs no merge and
      no write; text output names each repair (task, resulting merge commit, already current,
      or no retained branch), the cleared entries and groups, the conflicted paths on
      refusal, and the `worktrail-live full-real --repo <repo> --spec <spec> --resume`
      invocation carrying the resolved base and remote.
      (Requirements: One command composes branch repair and journal recovery; Retained task
      branches are merged up to date with the current base; A conflicting branch repair fails
      closed and names the conflicted paths; A group selection resolves to the group's tasks
      from the cached RunPlan)
      Add `tests/orchestrator/test_recover.py` covering, with hermetic temporary-repo git
      fixtures and no network: a stale retained branch merged cleanly, an up-to-date branch
      left without a merge commit, a conflicting branch aborted with the exact path reported
      and the journal byte-identical, a dirty checkout refusing the whole recovery, a missing
      retained branch reported and skipped, group-to-task resolution from a seeded RunPlan
      cache plus the refusal when the cache is absent, a named non-QUARANTINED group refused,
      a completion record refused, dry-run merging and writing nothing, the single-write and
      mid-recovery-lock refusal paths, and byte-identical journals for every refusal.
      files: src/worktrail/orchestrator/recover.py tests/orchestrator/test_recover.py

- [ ] 1.2 In `src/worktrail/orchestrator/live.py`, register the `recover` subparser beside
      `clear-task` (`--repo`, `--spec`, repeatable `--group`, comma-separated `--tasks`,
      `--base` defaulting to `dev`, `--remote` defaulting to `_default_remote(repo)`, `--dry-run`)
      and dispatch it through a deferred import of `recover` so the `live`/`resume_group`
      import shape stays acyclic and the subcommand is not added to `_SPAWNING_SUBCOMMANDS`
      (a non-spawning subcommand must not resolve a default model).
      (Requirements: The journal is written once, only after every repair succeeds; Recovery
      refuses what the commands it composes refuse; One command composes branch repair and
      journal recovery)
      Add `tests/orchestrator/test_live_recover_command.py`: CLI-level exit codes and output
      for a recovered journal (the next-step line names `full-real --resume` with the resolved
      base and remote), the no-selector usage refusal, a live-run lock refusal with the
      journal unchanged, a `--dry-run` run whose wording promises no write or merge, and a
      `recover` invocation that exits 0 without resolving a default model on a routing table
      that has no target for the host's harness.
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_recover_command.py
      depends: 1.1

## 2. Operator reference

- [ ] 2.1 In `skills/worktrail-go/references/routes.md`, make the composed step the first
      instruction of Route E's quarantined-group paragraph: run `worktrail-live recover --repo
      <repo> --spec <spec> --group <name>`, resolve and re-run it when a conflict is reported,
      then run the existing `worktrail-live full-real --resume`; keep `worktrail-resume-group`
      documented as the journal-only alternative for a group whose branches need no repair.
      (Requirement: Operator references point at the composed recovery command)
      files: skills/worktrail-go/references/routes.md

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_recover.py tests/orchestrator/test_live_recover_command.py`,
      then `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`, then `python3.14 scripts/ci/ruff_pinned.py
      check .`, `python3.14 scripts/ci/ruff_pinned.py format --check .` and `python3.14
      scripts/ci/check_shebang_exec_bits.py`. Exercise the command end-to-end in a scratch
      clone whose journal records one QUARANTINED group with a failed task whose retained
      branch predates base: confirm one invocation leaves a merge commit on the branch, clears
      the failed entry and the group record, and prints the resume invocation, and that a
      conflicting fixture exits non-zero naming the path with the journal unchanged. Run
      `openspec validate quarantine-recovery-command --strict` and `worktrail-compile
      openspec/changes/quarantine-recovery-command`.
      depends: 1.1, 1.2, 2.1

## 4. Folded from 20261003-191621-quarantine-journal-never-cleared

Triage evidence for this fold is in `proposal.md`'s `## Folded from 20261003-191621-quarantine-journal-never-cleared` section.

- [ ] 4.1 worktrail's quarantine selfcheck re-files the same stale-group briefs after earlier ones are closed with archival-only prose, because closing a quarantine brief never clears its journal record.
      files: src/worktrail/router/quarantine_selfcheck.py, ../run-built-artifact-packaging-parity-gate.json, ../run-openspec-validate-ci-gate.json, openspec/changes/quarantine-recovery-command/proposal.md, tests/router/test_quarantine_selfcheck.py

## 5. Folded from 20260930-112541-worktrail-has-orchestrator-groups-stuck

Triage evidence for this fold is in `proposal.md`'s `## Folded from 20260930-112541-worktrail-has-orchestrator-groups-stuck` section.

- [ ] 5.1 worktrail has orchestrator groups stuck in QUARANTINED for spec `built-artifact-packaging-parity-gate` (worktrail-quarantine-selfcheck): tail-3.5 (merge_conflict, 31d); tail-4.2 (merge_conflict, 31d) Triage each group: repair and resume it, or discard it if the work already landed or is no longer wanted. worktrail-selfcheck-fleet-sweep: quarantine worktrail built-artifact-packaging-parity-gate
      files: ../run-built-artifact-packaging-parity-gate.json

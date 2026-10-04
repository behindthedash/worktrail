## Why

Returning a quarantined orchestrator group to the pipeline is a documented but fully manual
loop. Route E's CI/PR repair sub-mode (`skills/worktrail-go/references/routes.md`) tells the
operator to "fix the task branch first, then clear the group with `worktrail-resume-group --repo
<repo> --spec <spec> --group <name>` (or `--all-resumable` for budget-exhausted quarantines) and
re-run `worktrail-live full-real --repo <repo> --spec <spec> --resume`".

The pieces exist, uncomposed:

- `src/worktrail/orchestrator/resume_group.py` (shipped by
  `openspec/changes/archive/2026-09-25-resume-quarantined-orchestrator-groups`) is the journal
  half only: it pops the QUARANTINED record and `integrate_complete`, appends a
  `resumed_quarantines` audit entry, and prints the next `full-real --resume` command. There is
  no branch validation, no base merge, and no conflict surfacing.
- `clear_tasks` (`src/worktrail/orchestrator/live.py:1596`) is the entry half: it removes a
  task's failed/escalated entries (cascading to the dependency-gate entries they blocked) and
  refuses when a completion record would be discarded.
- Nothing validates or repairs the retained task branch. Group integration assembles a group
  branch in a throwaway worktree *started at the current base* and merges each task branch into
  it (`src/worktrail/orchestrator/integrate.py`: `_integration_worktree` plus `merge --no-edit
  <spec_id>/<task_id>`). A task branch forked from an older base therefore conflicts there and
  the resumed run quarantines again — "fix the task branch first" is exactly the step no command
  performs. No `recover` subcommand exists (`worktrail-live`'s subcommands include `precheck`,
  `skip`, and `clear-task`, and none of them touches a branch), and `pyproject.toml`'s
  `[project.scripts]` registers no recovery entry point.

The cost is recorded on the queue brief `20261003-173520-automate-quarantine-recovery-command`:
recovering one task of `python314-shebang-policy` on 2026-10-03 took three resume attempts, two
`clear-task` invocations, a crash whose retained branch lacked the current base, a merge
conflict on `.claude/skills/tests/skill.md`, and a hand-resolved merge before the resume took.
The fleet carries roughly 60 quarantined groups, several stuck 25-38 days (the brief's account;
the prior change's own 2026-09-25 count was 59, oldest ~56 days), and every one of them needs
this same expert loop. The existing quarantine commands are detection, teardown, or
journal-clearing only; no active change under `openspec/changes/` composes the repair.

## What Changes

- New `worktrail-live recover` subcommand: `--repo`, `--spec`, at least one explicit selection
  (`--group` names and/or `--tasks` ids), plus `--base`/`--remote` mirroring `full-real`'s
  resolution and a `--dry-run` mode. One invocation performs, in order: validate the selection
  against the run journal; repair the selected failed/escalated tasks' retained branches by
  fetching the base and, when the branch predates it, merging the current base into the branch
  as a merge commit; then clear the failed/escalated entries (with `clear_tasks`' cascade and
  guardrails) and the selected groups' QUARANTINED records (with `resume_group`'s
  `resumed_quarantines` audit) in a single journal write; then print the exact
  `worktrail-live full-real --resume` invocation to run next.
- Fail closed, in this order: a conflicting base merge is aborted (branch unchanged), its
  conflicted paths are printed, no journal state is cleared, and the command exits non-zero so a
  re-run after a hand fix is safe and idempotent. The journal is written at most once, only
  after every repair succeeded, with a RunLock re-check immediately before the write.
- Reuses the existing semantics rather than a parallel implementation: `clear_tasks`' refusal to
  drop completion records and its "nothing to clear" typo guard, `resume_group`'s
  QUARANTINED-only selection and audit shape, and `quarantine_selfcheck.group_task_ids` for
  resolving a named group to its tasks from the cached RunPlan (refused, never guessed, when
  that resolution fails).
- The recovery never launches the resume itself — a `full-real` run started under the recovering
  session dies to the harness's background-task reaper, which is why `worktrail-detach` exists —
  it prints the command, matching `worktrail-resume-group`'s established contract.
- Route E's quarantine-recovery paragraph in `skills/worktrail-go/references/routes.md` points
  at the composed command. Non-goals: auto-resolving conflicts, repairing the retained *group*
  branch (verify's resolve worker already owns that), a bulk `--all-resumable` analogue, and
  re-pointing `worktrail-quarantine-selfcheck`'s hint.

## Capabilities

### New Capabilities

- `quarantine-recovery-command`: one composed, fail-closed command that repairs retained task
  branches against current base and clears the journal so a quarantined group resumes through
  the ordinary path.

### Modified Capabilities

<!-- None. `worktrail-resume-group` and `clear-task` keep their contracts; recovery composes
     their semantics (including the resumed_quarantines audit) rather than changing them. -->

## Impact

- `src/worktrail/orchestrator/recover.py` (new): selection resolution, branch repair, journal
  composition, refusals.
- `src/worktrail/orchestrator/live.py`: the `recover` subparser and non-spawning dispatch.
- `tests/orchestrator/`: recovery-engine and CLI regression coverage.
- `skills/worktrail-go/references/routes.md`: Route E points at the composed command.
- No new console script, policy key, dependency, or journal schema; the audit reuses the
  existing `resumed_quarantines` list and no existing command's behavior changes.

## Folded from 20261003-191621-quarantine-journal-never-cleared

worktrail's quarantine selfcheck re-files the same stale-group briefs after earlier ones are closed with archival-only prose, because closing a quarantine brief never clears its journal record. Verified live 2026-10-03: specs built-artifact-packaging-parity-gate and openspec-validate-ci-gate were closed obsolete on 2026-09-21 (briefs 20260921-112552 / 20260921-112600), yet the 2026-10-03 selfcheck still flags them and re-filed 20260930-112541 / 20260930-112546; worktrail alone carries 18 findings (16 after brief 20260930-112543 cleared model-tier-routing's two).

Repo live: `gh repo view --json isArchived,name` -> {"isArchived":false,"name":"worktrail"}. Loop confirmed from live artifacts, not re-derivation: (1) src/worktrail/router/quarantine_selfcheck.py:222-277 (check_repo) scans journals for state==QUARANTINED and its only auto-resolution is reconcile_finding (lines 193-219, base-branch/merged-PR file match); there is no brief-dedup and nothing clears journals, and `grep -rn 'quarantine|QUARANTINED' src/worktrail/workqueue/` returns no hits, so the brief-close path cannot clear a journal record. (2) ~/work-queue/picked/20260921-112552 and 20260921-112600 (status: done, completed-at 2026-09-24) carry archival-only closure notes citing only archive commits `3386df4f` / `c0afde0f`; ~/work-queue/queue/20260930-112541 and 20260930-112546 re-filed the identical groups 6 days later with ages grown 22d->31d and 29d->38d, both still status: queued. (3) The journals are still quarantined today: ../run-built-artifact-packaging-parity-gate.json still holds tail-3.5 and tail-4.2 QUARANTINED/merge_conflict (file mtime 2026-08-30), ../run-openspec-validate-ci-gate.json still holds base/dropped and tail-4.1 QUARANTINED (mtime 2026-08-22), so a 2026-10-03 selfcheck run must still flag all four; the only code that clears QUARANTINED state is resume_group.py:56-65. Minor drift: the brief says the earlier briefs were closed 'on 2026-09-21'; they were created 09-21 and completed 2026-09-24 - immaterial to the thesis. Fold fit: the brief's root cause is the never-cleared journal record, and openspec/changes/quarantine-recovery-command/proposal.md:45 (quoted) is exactly the composed command that clears the selected groups' QUARANTINED records and wires Route E's quarantined-group paragraph to it, so the triage path for these stuck-group briefs stops leaving the record in place; the change's selfcheck non-goal concerns only the hint text, not this.

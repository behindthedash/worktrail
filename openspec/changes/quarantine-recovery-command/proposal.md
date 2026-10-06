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

## Folded from 20260930-112541-worktrail-has-orchestrator-groups-stuck

worktrail has orchestrator groups stuck in QUARANTINED for spec `built-artifact-packaging-parity-gate` (worktrail-quarantine-selfcheck): tail-3.5 (merge_conflict, 31d); tail-4.2 (merge_conflict, 31d)

Triage each group: repair and resume it, or discard it if the work already landed or is no longer wanted.

worktrail-selfcheck-fleet-sweep: quarantine worktrail built-artifact-packaging-parity-gate

Journal still quarantined (read via python3.14 json.load of $HOME/projects/worktrail-worktrees/run-built-artifact-packaging-parity-gate.json: tail-3.5 + tail-4.2 both state=QUARANTINED reason=merge_conflict, mtime 2026-08-30). OpenSpec change openspec/changes/quarantine-recovery-command is still active (not in openspec/changes/archive/) and its proposal.md already carries a '## Folded from 20261003-191621-quarantine-journal-never-cleared' section naming brief 20260930-112541, with tasks.md 4.1 listing ../run-built-artifact-packaging-parity-gate.json as a target file. The branch-repair+journal-clear command is exactly the triage path this brief asks to run.

## Folded from 20260930-112546-worktrail-has-orchestrator-groups-stuck

worktrail has orchestrator groups stuck in QUARANTINED for spec `openspec-validate-ci-gate` (worktrail-quarantine-selfcheck): base/dropped (task_failure, 38d); tail-4.1 (merge_conflict, 38d)

Triage each group: repair and resume it, or discard it if the work already landed or is no longer wanted.

worktrail-selfcheck-fleet-sweep: quarantine worktrail openspec-validate-ci-gate

Journal run-openspec-validate-ci-gate.json still holds base/dropped (task_failure) and tail-4.1 (merge_conflict) QUARANTINED (mtime 2026-08-22), plus tail-2.2 OPEN. Change openspec/changes/quarantine-recovery-command is active and its proposal.md folded section names brief 20260930-112546 directly; tasks.md 4.1 lists ../run-openspec-validate-ci-gate.json. The other listed candidate (tail-dispatch-require-merged-deps) is a poor fit — it gates tail dispatch on declared deps, which is unrelated to clearing stuck journal records.

## Folded from 20261004-112540-worktrail-has-orchestrator-groups-stuck

worktrail has orchestrator groups stuck in QUARANTINED for spec `built-artifact-packaging-parity-gate` (worktrail-quarantine-selfcheck): tail-3.5 (merge_conflict, 35d); tail-4.2 (merge_conflict, 35d)

Triage each group: repair and resume it, or discard it if the work already landed or is no longer wanted.

worktrail-selfcheck-fleet-sweep: quarantine worktrail built-artifact-packaging-parity-gate

Spec archived: openspec/changes/archive/2026-08-31-built-artifact-packaging-parity-gate. Journal run-built-artifact-packaging-parity-gate.json still holds tail-3.5 and tail-4.2 state=QUARANTINED (live json read, file mtime 2026-08-30). This brief is the next re-filing of the class already folded as '## Folded from 20260930-112541-worktrail-has-orchestrator-groups-stuck' in openspec/changes/quarantine-recovery-command/proposal.md:94 (commit 928a58d7 / #1421); recover.py still absent.

## Folded from 20260930-112548-worktrail-has-orchestrator-groups-stuck

worktrail has orchestrator groups stuck in QUARANTINED for spec `routing-target-selector` (worktrail-quarantine-selfcheck): tail-5.1 (merge_conflict, 34d); tail-6.5 (merge_conflict, 34d)

Triage each group: repair and resume it, or discard it if the work already landed or is no longer wanted.

worktrail-selfcheck-fleet-sweep: quarantine worktrail routing-target-selector

Repo not archived: `gh repo view --json isArchived,name` -> {"isArchived":false,"name":"worktrail"}. Premise re-confirmed live: python3 json.load of ../run-routing-target-selector.json (spec_id= routing-target-selector per run-routing-target-selector.status.json) shows tail-5.1 and tail-6.5 both state='QUARANTINED' reason='merge_conflict', pr_url='', integrate_complete=None, resumed_quarantines=null; file mtime 2026-08-27, untouched since. Change not landed: ls src/worktrail/orchestrator/recover.py -> No such file; no 'recover' entry in pyproject.toml; openspec/changes/quarantine-recovery-command/tasks.md 1.1/1.2 still '- [ ]'. Precedent for this brief class: openspec/changes/quarantine-recovery-command/proposal.md carries '## Folded from' sections for the identical sibling re-filings 20260930-112541, 20260930-112546, 20261004-112540 (commits 928a58d7/#1421, c9e923d1/#1422, 0935b3a1/#1441); `grep -rn 112548 openspec/` and `git log --all --grep=112548` both empty, so this sibling is the one not yet folded. Spec archive (openspec/changes/archive/2026-08-28-routing-target-selector) does not clear the journal record - the same reasoning the 20261004-112540 fold note applied - and the change's tasks.md 1.1 is exactly the branch-repair + QUARANTINED-record-clearing path this brief's triage asks for.

## Folded from 20261005-103628-stale-ancestry-repair-unreachable

Orchestrator's retained-task-branch stale-ancestry repair is unreachable on the documented recovery path (worktree removed, branch kept)

Repo live: `gh repo view --json isArchived,name` -> {"isArchived":false,"name":"worktrail"}. Premise confirmed in code: live.py:2199-2203 docstring -- the repair (merge start_ref into the retained branch) runs only `With wt (the branch's own retained worktree, already checked out on branch)`; `Without wt (no checkout to merge in), behavior is validate-and-raise, unchanged`. The worktree-removed path routes around it: _ensure_wt (live.py:6499+) does `if not wt.exists():` -> add_stacked_worktree, which at live.py:2569-2570 calls `_validate_retained_task_branch(repo, branch, start, expected_head_sha)` with no wt -> raises on stale ancestry; only the `else:` branch (worktree still present, live.py:5124/6561) passes wt=wt and can repair. Reproduced: `PYTHONPATH=src python3.14 -m pytest tests/orchestrator/test_retained_branch_auto_merge.py -q` -> 5 passed in 0.64s, incl. `test_stale_branch_without_worktree_raises_unchanged` (pins no-worktree -> raise, no merge attempt); its docstring: repair 'still fails loud on conflicts or when no worktree is available'. Not fixed/not folded: `git log --all --grep="103628"` empty; repair added by #810 (64b11389, scoped 'in its own worktree') and #825 (446eb606); no active change proposal mentions stale retained-branch repair. Fold fit: openspec/changes/quarantine-recovery-command/tasks.md 1.1 already specifies the no-checkout repair (`grep -F "when the branch has no checkout"` count 1) and 2.1 rewrites the Route E recovery paragraph ('fix the task branch first') this brief calls the documented recovery path; proposal.md: 'Nothing validates or repairs the retained task branch' / "'fix the task branch first' is exactly the step no command performs". Memory dir /home/briank/.claude/projects/-home-briank-projects-worktrail/memory/ is empty (no MEMORY.md) -- nothing recorded either way.

Disposition (2026-10-06): consolidated into task 1.1, which owns the recovery engine and now
explicitly requires a stale retained branch with no checkout to be repaired in a throwaway
worktree and covered by `tests/orchestrator/test_recover.py`. The standalone task 9.1 was
removed as duplicate; the behavior remains an acceptance criterion of this change and is not
marked complete ahead of implementation.

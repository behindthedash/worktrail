## Context

`quarantine_selfcheck.check_repo(repo)` scans `<repo>-worktrees/run-*.json` journals and returns
one finding per group whose `state == "QUARANTINED"`. The journal's `groups[name]` record carries
only `{head_branch, pr_url, state}` — no per-group file list. Group membership (which task ids
belong to which group) is a coordinator-time concept computed by `coordinator.plan_groups()` from
the task DAG; it is never persisted back onto the journal. This was flagged as an explicit Open
Question in the original `quarantined-group-visibility` design ("Should group→task attribution be
added later ... Deferred").

Separately, each spec's compiled RunPlan (`conductor/compile.py`, cached at
`<repo>-worktrees/runplans/<spec_id>-<fingerprint>.json`) carries exactly what's missing: every
task's `id`, `deps`, and `files`. `plan_groups(tasks, migration_patterns)` is a **pure,
deterministic function** of that same task list — re-running it against the cached RunPlan
reproduces the exact same group partition the orchestrator itself used at run time (same `name`
convention: `"base"`, `"feature-N"`), without needing any new persisted state.

Verified this session (2026-08-07) by hand across 7 quarantined `datalena` groups: 4 had already
landed on base via a merged PR whose own branch name never matched the quarantined group's
`head_branch` (065/feature-1 via #1327, 072/feature-1 via a re-run's #1481, both 076 groups via
#1573) — orchestrator PR titles follow `[<run_id>] <group>: <task ids>` and never include the
spec id, so a superseding re-run (different `run_id`) produces a PR whose title/branch bears no
textual relationship to the original quarantined group at all. The only reliable signal that
generalizes is: did the group's actual *files* land, and is a *merged* PR's file list a superset
of them.

## Goals / Non-Goals

**Goals:**
- Reconcile a raw QUARANTINED finding against two independent signals: (1) are the group's
  task-declared files present and git-tracked on the base branch, and (2) is there a merged PR
  whose changed-files set is a superset of the group's declared files.
- When either signal confirms the work landed, exclude the finding from what `check_repo()`
  surfaces to the dashboard/CLI, but retain a reconciliation record (which signal matched, what
  evidence) for audit — never a silent drop.
- Recompute group→file membership from the cached RunPlan via `coordinator.plan_groups()`, so no
  new persisted state is needed and the reconciliation logic can never drift from the actual
  partition the orchestrator used.
- Keep `sweep()`/CLI/exit-code contract unchanged; reconciliation is purely inside `check_repo()`.

**Non-Goals:**
- Auto re-triggering orchestration, retrying the group, or any write action. This stays a passive
  detector — reconciliation only narrows what it reports, it never acts.
- Searching PRs by free-text spec-id/run-id matching in title or body. Verified this session that
  signal is unreliable (superseding-rerun PR titles carry a *different* run_id and never the spec
  id at all) — file-set comparison against merged PRs is the only signal that generalizes.
- Reconciling a group whose RunPlan cache has been deleted (`<repo>-worktrees/runplans/` pruned).
  Falls through to today's raw-finding behavior — no cache means no group→file recomputation is
  possible; failing open (still flagged) is safer than guessing.

## Decisions

- **Group→file membership recomputed from the RunPlan cache, not persisted onto the journal.**
  Alternative considered: extend `integrate.py`'s journal writer to persist `groups[name]["files"]`
  at quarantine time. Rejected — it only fixes *future* quarantines and requires a schema change
  every existing/archived journal would need to tolerate being without. Recomputing from the
  RunPlan cache works retroactively on any journal still paired with its RunPlan, using code that
  already exists and is already the source of truth for grouping.
- **RunPlan lookup by content-addressed cache file, keyed off the journal's own spec_id.** The
  journal filename (`run-<spec_id>.json`) already gives the spec_id; glob
  `<repo>-worktrees/runplans/<spec_id>-*.json` for the cache, load its `tasks` list, and call
  `plan_groups()` to get `{name, tasks}` pairs. Match the finding's `group` name against this
  output to get its task ids, then each task's `files`.
- **Base-branch presence check is `git ls-tree`, not filesystem existence.** A file merely
  existing on disk in some worktree proves nothing about the base branch; `git -C repo ls-tree
  <base>:<path>` (or `git cat-file -e`) against the group's declared files on the actual base ref
  is the only way to confirm "this is really on base," matching how `#sync-before-teardown`
  verifies post-merge state elsewhere in this codebase.
- **PR-search signal: merged PRs targeting base, checked file-by-file via `gh pr view --json
  files`, not `gh pr list --search` text matching.** `gh pr list --search` has no "files touched"
  qualifier for issue/PR search (that's a code-search-only feature), and free-text title/body
  matching was verified unreliable this session (see Context). The reconciler instead lists
  recently-merged PRs (bounded by `--limit`, default matches the group's `age_days` window plus a
  small buffer) and checks each candidate's changed-file set against the group's declared files —
  expensive per-call but bounded (only runs for groups that already failed the base-branch check,
  and only over recently-merged PRs, not the whole PR history).
- **A finding is auto-resolved on EITHER signal, not both.** Base-branch presence alone is
  sufficient proof the work landed (it doesn't matter through which PR). PR-file-match is the
  fallback for the case a file was later modified/removed by unrelated work after landing (so it's
  no longer bit-identical on base) but a merged PR record still proves the original group's files
  did land at some point.

## Risks / Trade-offs

- [Risk] `gh pr view --json files` is a real network call per candidate PR — could be slow for a
  repo with many recent merges. → Mitigation: only runs for groups that already failed the cheap
  base-branch check (Non-Goal already narrows scope); bounded `--limit` on the merged-PR list.
- [Risk] A file that landed on base but was since renamed/moved by unrelated work would produce a
  false "not reconciled" from the base-branch check. → Mitigation: accepted — matches this design's
  own PR-file-match fallback signal, which doesn't require exact current-tree presence, only that
  a merged PR's file list once matched. A rename with no matching PR at all is a genuinely
  ambiguous case correctly left for human triage, not guessed at.
- [Risk] `gh` CLI must be authenticated and reachable; unlike the base-branch check this signal can
  fail closed (network/auth error). → Mitigation: on any `gh` invocation failure, treat that
  signal as inconclusive (not confirmed) rather than raising — the finding still surfaces to a
  human, same as today's behavior, never silently dropped on an error.

## Migration Plan

No data migration — pure read-side addition inside `check_repo()`. Existing callers
(`sweep()`, CLI, dashboard) see only a possibly-smaller `findings` list; the finding shape is
unchanged. Rollout is a normal PR behind this repo's CI gate.

## Open Questions

None — this change directly closes the Open Question left by `quarantined-group-visibility`'s
own design (group→task attribution), using the RunPlan cache instead of new persisted state.

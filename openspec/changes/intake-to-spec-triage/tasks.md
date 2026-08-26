## 1. Brief kind and dispatch gate

- [ ] 1.1 Add `brief_kind(frontmatter) -> "intake" | "execution"` to `work_queue.py` (execution iff non-empty `seeded-from:`), with unit tests covering handoff, seeded, and consolidated briefs
- [ ] 1.2 Add the `intake-untriaged` skip in `dashboard.py::auto_pick_brief` ahead of existing gates, logged through the existing miss log; tests: all-intake queue claims nothing, mixed queue claims only the execution brief
- [ ] 1.3 Route `worktrail-go <brief-id>` for an intake brief to single-brief triage (evaluate that brief, present verdict, apply on confirmation) instead of claim+dispatch; execution briefs unchanged; tests for both paths

## 2. Candidate ranking and verdict extension

- [ ] 2.1 Add `rank_change_candidates(brief, repo, top_k=5)` in `queue_triage.py` using `spec_overlap.scan()` for the OpenSpec root plus `tasks.md` task-line tokens and the `duplicate_brief_detection` overlap coefficient; null repo and no-active-changes cases return empty; tests
- [ ] 2.2 Extend `VALID_VERDICT_TYPES` and the verdict parser with `fold-into-change` (`target_change`), `propose-change` (`target_repo`, `proposed_change_name`), `work-directly`, `needs-decision` (`question`); missing/invalid target fields and fold targets outside the candidate list downgrade to `keep` with raw output as evidence; tests
- [ ] 2.3 Update the evaluator prompt and per-brief context to include the ranked candidates (id, feature summary, open-task count), the `work-directly` reproduction-evidence rule, and the `needs-decision` rule for `repo: null`
- [ ] 2.4 Extend the Markdown report and JSON verdict file with the new verdict counts, PRs opened, and briefs held by cap per repo

## 3. Apply actions

- [ ] 3.1 Implement `fold-into-change` apply: fresh worktree on a branch off the repo base, append `## Folded from <brief-id>` to `proposal.md` and a new unchecked task group to `tasks.md`, `openspec validate`, commit, push, open the PR via `gh`, then `done(..., triaged-to=..., note=<PR url>)`; any earlier failure leaves the brief untouched and reports the branch; tests with fake `gh`/git (pattern: `tests/drain/test_drain.py` archive test)
- [ ] 3.2 Implement `propose-change` apply: create the change via `openspec new change`, generate proposal/design/specs/tasks with the evaluator agent, validate, then the same branch/PR/close sequence as 3.1; tests
- [ ] 3.3 Implement `work-directly` apply: verify reproduction evidence (test/check/command reference), stamp `seeded-from: triage:<date>:direct` and `recommended-route: F` in place, brief stays queued; downgrade to `keep` without evidence; tests
- [ ] 3.4 Implement `needs-decision` apply via `decisions.pending_decision_envelope` (subject = brief id, question from verdict); brief stays queued and is skipped by later triage runs while the decision is pending; tests
- [ ] 3.5 Add `triaged-to` support to `work_queue.done()` (frontmatter stamp) and dry-run printing of branch, target change, and PR title for fold/propose without `--confirm`; tests
- [ ] 3.6 Add `max_active_changes` to `policy.py` defaults/validation (int, default 0); in apply, count active changes and downgrade `propose-change` to `keep` + `## Triage` note naming cap, count, and top fold candidates when over cap; tests: over cap, unset cap, fold unaffected

## 4. Drain pre-passes

- [ ] 4.1 Add `--intake-triage` and `--seed-backlog` to `drain.py`; run before iteration 1; each failure is captured into the summary and never aborts the drain; tests with stubbed triage/seeder
- [ ] 4.2 Add `intake_triage` and `seed_backlog` blocks to the drain JSON summary and to the digest/summary contract (`summary_contract.py`); tests
- [ ] 4.3 Update `worktrail-drain` help text and the drain docs section of `AGENTS.md`/README to describe the intake → change → seeded execution loop and the default-off flags

## 5. Verification and handoffs

- [ ] 5.1 Run the full `pytest` suite and `ruff check` / `ruff format --check`
- [ ] 5.2 Dry-run `worktrail-drain --intake-triage --seed-backlog --dry-run` against the live queue and confirm the summary blocks populate and no brief or repo is modified
- [ ] 5.3 Capture handoffs (not GitHub issues): `devops` nightly script flag flip; datalena `max_active_changes` and `allow_seeded_implementation` policy decision; optional `triaged-to` dangling-PR check in stale-bookkeeping detection

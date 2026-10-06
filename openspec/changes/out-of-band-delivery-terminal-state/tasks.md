## 1. Conclusive-containment evidence

- [ ] 1.1 In `src/worktrail/shared/git_merged.py`, add the conclusive-containment variant the
      terminal verdict needs: `branch_content_in_base()` gains a keyword parameter (e.g.
      `conflicted_counts_as_contained: bool = True`) that, when false, drops only the
      conflicted-merge-tree shortcut (`returncode == 1`), leaving ancestry and a clean
      `git merge-tree --write-tree` that reproduces `base_ref`'s own tree as the sole containment
      evidence; a merge-tree that failed to run (exit > 1, e.g. an unresolvable ref) stays "not
      contained" in both modes, and the default keeps every existing caller
      (`live._branch_content_in_base` / `dependency_start_ref`'s pruning,
      `router/sweep_stale_worktrees.py:215`, `integrate._deliverable_already_in_target`)
      byte-for-byte unchanged. Restate in the docstring why a caller would opt out: the shortcut
      exists so a *stacking* decision can fork a dependent from base instead of a branch that can
      never be a stacking point, but for a *terminal* verdict a conflicted merge whose branch
      genuinely lacks the base's content would silently record delivered-and-merged and never ship
      the work -- strictly worse than a visible quarantine (see design.md's decision on the
      terminal evidence standard).
      (Requirement: A merge conflict is never by itself evidence of delivery)
      Add `tests/orchestrator/test_stacked_worktree_squash_merged_dependency_branch.py` coverage
      beside the existing `branch_content_in_base` tests, against real git: the squash shape is
      still contained at the default; the reviewed-squash conflict shape is contained at the
      default (unchanged) and is NOT contained with the shortcut disabled; ancestry and a clean
      equal merge-tree are contained in both modes; and an unresolvable ref is contained in
      neither.
      files: src/worktrail/shared/git_merged.py, tests/orchestrator/test_stacked_worktree_squash_merged_dependency_branch.py

## 2. The delivered-out-of-band rule at every integration seam

- [ ] 2.1 In `src/worktrail/orchestrator/integrate.py`, generalize the existing delivered-out-of-band
      recognition (the empty-diff guard at `1687-1710` and `_deliverable_already_in_target` at
      `110`) into one rule consulted at the remaining seams of `integrate_one`, all recording the
      journal's existing terminal state (`_do_journal(name, "", gb, "MERGED")`, empty `pr_url`, no
      `quarantine_reason`), setting `group_branch[name] = gb`, printing a `MERGED [<name>]` line
      that names the seam's evidence and "delivered out-of-band", and never writing a quarantine
      record or a `dependency_quarantined` cascade:
      (a) PR-base seam -- after the group branch is resolved (`group_branch[name] = gb`, line 1799;
      both the freshly-built and the REUSE path, which today fetches an existing remote branch at
      `1644-1653` and drops straight to the PR reconcile) and before the PR reconcile block
      (line 1801), evaluate the branch against the ref its PR would target -- `f"{remote}/{base}"`
      when `pr_base == base`, else `pr_base` -- and record the terminal verdict when it has zero
      commits beyond that ref. This is GitHub's own `No commits between <base> and <branch>`
      precondition, so the shared `land_pr` open/update step is never called for it and the
      `gh pr create` refusal at `1936-1947` can no longer be the first evidence a branch was empty.
      Both refs must resolve (`git rev-parse --verify --quiet <ref>^{commit}`) and the
      `git rev-list --count <pr_ref>..<gb>` result must parse to 0 before concluding; anything else
      (unresolvable ref, non-zero count, unparseable output) falls through to today's path, and no
      other `gh pr create` failure changes classification.
      (b) Conflict seam -- before `quarantined[name] = f"merge conflict integrating {conflict}"`
      and its `QUARANTINE_MERGE_CONFLICT` write (`1673-1677`), consult conclusive containment only
      (ancestry, or the flag-off `branch_content_in_base` from task 1.1) for every deliverable task
      branch; only when all are contained is the attempt recorded delivered. A conflict alone is
      never evidence of delivery, so a branch whose changes the target lacks and which conflicts
      still quarantines with the merge-conflict reason and is retained, exactly as today.
      (c) Stacked-own-contribution seam -- before any merge is attempted, apply the same delivered
      test the empty-diff guard uses (the deliverable's content already in `target`), restricted to
      a group whose every deliverable task branch satisfies both: (i) its own commits are empty, i.e.
      `git rev-list --count <start_ref>..<branch>` is 0 for the dependency start ref it was stacked
      on -- resolved unpruned, as the first of the task's `deps` whose branch
      `f"{spec_id}/{dep.lower()}"` exists locally, never `dependency_start_ref` with `base_ref`
      (its base-containment pruning substitutes the target once the dependency lands and hides the
      stacked prefix) and never its literal `HEAD` fallback; and (ii) that start ref's content is
      already in `target` per the default `branch_content_in_base` (the conflicted-merge shortcut
      is deliberately kept here -- the shape it documents, a squash-merged dependency with fixups,
      is exactly this one; record that reasoning in the comment and cite design.md's accepted-boundary
      note). Then the attempt records the terminal verdict without merging. When the start ref does
      not resolve, or the task's own commits are non-empty, nothing is concluded and the ordinary
      path runs unchanged.
      (d) Teardown -- on every terminal delivered verdict, remove the group branch best-effort under
      the integration's own git lock: local `branch -D` plus `<remote> --delete` when the branch
      exists on the remote, mirroring `verify.cleanup_group`'s best-effort pair
      (`verify.py:1796-1810`); log failures and never turn them back into a quarantine. Task
      worktrees/branches are left to the existing post-merge cleanup and sweep paths.
      The empty-diff guard's existing behavior, the `QUARANTINE_EMPTY_DIFF` no-op-delegate
      classification, and the reuse path's skipping of the drift/smoke gates all stay as they are.
      (Requirements: A group whose content the target already contains is terminal, not quarantined;
      A group branch that contributes nothing to its PR base terminates before the PR is opened; A
      merge conflict is never by itself evidence of delivery; A stacked deliverable with nothing of
      its own is reconciled, not conflicted; A delivered-out-of-band group is torn down, not
      retained for review)
      Add `tests/orchestrator/test_integrate.py` coverage against real-git fixtures and the existing
      FakeRun seam (extending its fake git layer with the new `rev-parse`/`rev-list` responses),
      beside `DeliverableAlreadyInTargetTests`: a REUSE-path group whose remote branch is an
      ancestor of base records MERGED with an empty `pr_url`, no `quarantine_reason`, no
      `gh pr create` call, and a branch-delete call; the push-rejection rebase-collapse shape
      (push rejected, rebase drops already-upstream commits, retry push succeeds) terminates
      delivered instead of reaching `gh pr create`; a reused branch with real unshipped commits
      still opens its PR unchanged; an unresolvable PR-base ref and a non-zero commit count both
      fall through to the ordinary path; a conflicting merge whose deliverable branch is
      conclusively contained records MERGED while a divergent conflicting branch still records
      QUARANTINED/`merge_conflict`; a stacked task branch with no commits of its own beyond a landed
      start ref records MERGED without a merge attempt, while a missing start ref and an own-commit
      branch whose content base lacks both take the ordinary path; and the existing no-op-delegate
      fixture still classifies as not delivered. Add
      `tests/orchestrator/test_live_tail_reconciliation.py` coverage driving
      `reconcile_unreconciled_tail_evidence` end-to-end for the stacked verification-only finding:
      `reconcile_state == "merged"`, no `quarantine_reason`, and the run-complete note printing no
      unreconciled-evidence line for it (extending the existing
      `test_merged_finding_prints_no_unreconciled_evidence_warning` shape).
      files: src/worktrail/orchestrator/integrate.py, tests/orchestrator/test_integrate.py, tests/orchestrator/test_live_tail_reconciliation.py
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q tests/orchestrator/test_integrate.py
      tests/orchestrator/test_live_tail_reconciliation.py
      tests/orchestrator/test_stacked_worktree_squash_merged_dependency_branch.py`, then
      `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check` (extend the golden record/replay fixture if the new
      `rev-parse`/`rev-list`/branch-delete calls are not covered by the recorded cassettes -- do not
      skip or weaken the check, since it is what catches an unconditional new git call on an
      unrelated path), then `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .` and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then drive the cited run's
      shape end to end against a scratch repo: a group whose branch is already contained in base
      (its tip an ancestor of the base branch, zero commits beyond it) integrated through a resume,
      asserting the group reaches the journal's terminal MERGED state with an empty `pr_url`, no
      quarantine record, no retained branch locally or on the fake remote, and no `gh pr create`
      call; confirm the same shape with the pre-change code records QUARANTINED/`integration_error`.
      Then run `openspec validate out-of-band-delivery-terminal-state --strict` and
      `worktrail-compile openspec/changes/out-of-band-delivery-terminal-state`.
      depends: 2.1

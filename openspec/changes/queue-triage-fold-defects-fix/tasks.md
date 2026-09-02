## 1. Minimum candidate-score floor (`Candidate targets are ranked brief-to-active-change`)

- [ ] 1.1 Implement requirement: In `src/worktrail/workqueue/queue_triage.py`,
      add a module-level `_MIN_CANDIDATE_SCORE = 0.45` constant next to
      `rank_change_candidates()` (design.md Decision 1) and filter `scored`
      to `score >= _MIN_CANDIDATE_SCORE` before the existing sort/`top_k`
      truncation, so a change scoring below the floor is never included in
      the returned candidates.
- [ ] 1.2 Add a regression test in `tests/workqueue/test_queue_triage.py`
      asserting: (a) a change scoring below 0.45 against a brief's focus is
      excluded from `rank_change_candidates()`'s returned list even when it
      would otherwise rank in the top-`top_k`; (b) a change scoring at or
      above 0.45 is still returned, unchanged from current behavior; (c) a
      repo whose every active change scores below the floor returns `[]`,
      matching the existing "no active changes" empty-candidate-list
      behavior.

## 2. Single-line evidence for the folded tasks.md checklist item (`Fold and propose are applied as a pull request, fail-closed`)

- [ ] 2.1 Implement requirement: In `_apply_fold_into_change()`'s `prepare()`
      (queue_triage.py, the `tasks_path.write_text(...)` call), collapse
      `v.evidence` via `" ".join(v.evidence.split())` before interpolating
      it into the `f"- [ ] {group_number}.1 {...}\n"` line (design.md
      Decision 2). Leave the `proposal_path.write_text(...)` call's use of
      `v.evidence` unchanged — it stays verbatim in the `## Folded from
      <brief-id>` prose section.
- [ ] 2.2 Add a regression test asserting that applying a `fold-into-change`
      verdict whose `evidence` contains embedded newlines produces a
      `tasks.md` checklist line with no embedded newlines (single line,
      whitespace-collapsed), while the corresponding `proposal.md` section
      still contains the evidence's original line breaks.

## 3. Fetch-before-branch for fold/propose worktrees (`Fold and propose are applied as a pull request, fail-closed`)

- [ ] 3.1 Implement requirement: In `_worktree_pr_close()`
      (queue_triage.py), before the existing `git worktree add` call, run
      `git -C <repo_path> fetch origin <base_branch>`; on a non-zero return
      code, return the same `status="error"`/untouched-brief/reported-branch
      shape every other pre-PR failure in this function already returns,
      with the fetch's stderr/stdout as the error text (design.md
      Decision 3). On success, change the `git worktree add -b <branch>
      <worktree_dir> <base_branch>` call's final argument from
      `base_branch` to `f"origin/{base_branch}"`.
- [ ] 3.2 Add a regression test asserting a failing `git fetch origin
      <base_branch>` call short-circuits `_worktree_pr_close()` with
      `status="error"`, no worktree left behind, and the brief untouched
      (mirroring the existing "git worktree add failed" test's shape).
- [ ] 3.3 Add a regression test asserting `git worktree add` is invoked with
      `origin/<base_branch>` (not the bare local `base_branch`) as its base
      ref, for both the `fold-into-change` and `propose-change` apply paths
      that share `_worktree_pr_close()`.

## 4. Verification

- [ ] 4.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green,
      including the new tests from sections 1-3. Verification-only — no
      file changes expected.
- [ ] 4.2 [cleanup] Run `openspec validate queue-triage-fold-defects-fix
      --strict` and confirm it passes. Verification-only — no file changes
      expected.

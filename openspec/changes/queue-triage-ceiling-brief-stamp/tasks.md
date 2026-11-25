## 1. Report and stamp the no-PR landing ceiling

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`, replace `_worktree_pr_close()`'s opaque
      no-PR ceiling branch and record the failed landing on the brief.
      (a) In the `if not pr_url:` block (`queue_triage.py:3300-3306`, reached only by a
      non-`refused` outcome -- in practice a post-commit `ceiling` from a `push_ambiguous` or
      refused PR create/update), read `step = outcome.refused_step` and
      `detail = " ".join((outcome.detail or outcome.merge_result or "").split())`. Report
      `land_pr <outcome> at <step>: <detail>` when both are present,
      `land_pr <outcome> at <step>` or `land_pr <outcome>: <detail>` when only one is, and keep
      `land_pr <outcome> but no pr_url returned` as the neither-present fallback. Keep the
      existing `path: None`, add `rolled_back: True`, and add a `landing` sub-dict carrying
      `outcome`, `run`, `final_status`, `merge_result`, `failing_checks`, `refused_step`, and
      `detail` (the outcome's own values, not the collapsed fallbacks).
      (b) Before returning -- so before the outer `finally`'s existing `release()` -- locate the
      claimed brief with `_resolve_brief_path(v.brief_id)`, set through the existing
      `_set_fm_fields()` the fields `landing-failed-step` (`step or outcome.outcome`),
      `landing-failed-detail` (`detail or outcome.outcome`), and `landing-branch` (`branch`), and
      append to the brief body a `## Triage <today>` note whose first non-blank line is
      `verdict: <v.verdict>` and whose body carries `landing-failed: <step or outcome> --
      <detail or outcome>`, `landing-branch: <branch>`, and one sentence stating that `land_pr`
      obtained no pull request URL and the brief was returned to `queue/` to be re-applied after
      the cause is fixed. Use `datetime.date.today().isoformat()` as the note date. Wrap the
      resolution and both writes in one `try` catching `OSError`/`ValueError`: on failure record
      `stamp_error` (naming the missing brief when the path is `None`) in the returned entry
      instead of raising, and on success record `stamped` with the three fields; the release
      must still run in either case. Leave the `refused` branch, every pre-`land_pr` return, the
      worktree/branch teardown, the success path, and the rejected-closure stamps untouched. In
      `tests/workqueue/test_queue_triage.py`, extend `TestLandingTeardown` with: a `ceiling` at
      `refused_step="pr_create"` whose `detail` contains a newline and the missing-label text
      (assert the entry's error names the outcome, step, and cause; `rolled_back` is `True`;
      `landing.refused_step`/`landing.detail` are the outcome's; the brief is back in `queue/`
      as `status: queued` with the three stamped fields and the detail collapsed to one line; a
      most-recent `## Triage` note parses through `qt.triage_history()` with the applied verdict
      and names the step, detail, and branch; the worktree was removed and the local branch
      deleted as before); a `ceiling` with no step, detail, or merge result (fallback message,
      `landing-failed-step: ceiling`); the same ceiling for a `propose-change` verdict (note
      `verdict:` line reads `propose-change`); a post-stamp `qt.is_recently_triaged(queue/a.md,
      25)` assertion and a `qt.inventory(25, str(self.base))` assertion that the brief appears
      in the skipped list and in no group; a second `apply_verdicts()` run over the same brief
      with a merged `landed` outcome still completing (entry `status: executed`, brief in
      `done/` with `triaged-to`, proving the triage note does not block replay); a stamp write
      failure (patch `_set_fm_fields` to raise `OSError`) that reports `stamp_error` and still
      releases the brief exactly once; and a `refused` landing that carries no `landing-failed-*`
      field and no new `## Triage` note.
      (Requirement: A landing ceiling with no pull request is reported and recorded on the brief)
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py

## 2. Document the operator recovery

- [ ] 2.1 In `skills/worktrail-go/SKILL.md`, extend the interactive triage apply step
      (`:338-364`) with the fold/propose ceiling case: when the apply returns `status: error`
      with `landing.outcome: "ceiling"` and no `pr_url`, report the error's refused step and
      detail to the user, state that the brief is back in `queue/` carrying `landing-failed-step`
      / `landing-failed-detail` / `landing-branch` and a `## Triage` note, and state that a
      re-evaluate inside the dedup window will skip the brief (so it is not re-proposed). Give
      the remedy: fix the named cause and re-run the apply step with the same `$VERDICT_FILE`,
      since `apply` does not consult the evaluation dedup window. Leave the existing
      `code_defect`/`review_threads_blocking` continuation guidance unchanged.
      (Requirement: A landing ceiling with no pull request is reported and recorded on the brief)
      files: skills/worktrail-go/SKILL.md

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/workqueue/test_queue_triage.py`, then `PYTHONPATH=src python3.14 -m pytest -q`, then
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, then
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .`, and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Exercise the path
      on a scratch `WORK_QUEUE_DIR` and a scratch repo: claim a brief, drive `_worktree_pr_close()`
      with a stubbed `land_pr` returning `ceiling`/`pr_create` so the apply reports the cause and
      the released brief carries the three fields plus the triage note; confirm a following
      `worktrail-queue-triage evaluate` reports the brief as skipped rather than re-proposing it;
      confirm replaying the same verdict file with a stubbed successful `landed` closes the brief
      with `triaged-to`. Run `openspec validate queue-triage-ceiling-brief-stamp --strict` and
      `worktrail-compile openspec/changes/queue-triage-ceiling-brief-stamp`.
      depends: 1.1, 2.1

## 1. Record the rejected closure and add the recovery command

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`, record the outstanding closure on the
      brief and add the recovery engine plus its CLI.
      (a) In `_worktree_pr_close()`'s post-landing rejection branch -- the
      `done_res["status"] != "done"` block that returns `rolled_back: False`
      (`queue_triage.py:3326-3341`) -- stamp the still-claimed brief through the existing
      `work_queue._set_fm_fields` with `closure-rejected-pr` set to the `pr_url` already in hand
      and `closure-rejected-reason` set to `done_res["status"]`, immediately before building the
      error entry, so a stamping failure cannot change the reported entry; locate that brief with
      `work_queue.resolve(v.brief_id, work_queue.picked_dir())` rather than trusting the
      rejection's own `path`, which a refusal is free to leave unset. `status` stays
      `picked`, no closure note is appended, no `triaged-to` is stamped, and every other path
      (the no-PR release in the cleanup `finally`, the merged-landing success return,
      `code_defect`/`review_threads_blocking`) is untouched.
      (b) Add a module-level `recover_rejected_closure(brief_id, *, evidence=None, dry_run=False)
      -> dict` that resolves the identifier with `work_queue.resolve()` against
      `work_queue.picked_dir()` and, when it resolves nowhere, against `work_queue.queue_dir()` so
      "not claimed" is distinguishable from "no such brief"; refuses without writing (result
      `status: "refused"` plus a `refusal` slug and a message naming the brief and the reason)
      when the brief is not in `picked/` with `status: picked`, when it carries no
      `closure-rejected-pr`, or when that value fails `router.pr_ledger.parse_pr_url`; otherwise
      calls `work_queue.done(brief_id, triaged_to=<recorded url>, note=evidence)` and returns
      `status: "recovered"` when the closure completes, or `status: "refused"` carrying `done()`'s
      own status in `closure_status` and its `error` verbatim when it does not. It never calls
      `release()`, and `dry_run=True` performs no `done()` call. The result carries `brief_id`,
      `status`, `refusal`, `path`, `action` (`"recover-closure"`), `pr_url`, `reason`, `evidence`,
      `closure_status`, `error`, and `dry_run`.
      (c) Add the `recover-closure` subparser beside `evaluate`/`apply` in `main()` (`--brief`
      required, `--evidence`, `--dry-run`, `--json`) and the `cmd_recover_closure(args)` handler
      that calls the engine and prints either the result JSON or a one-line human summary naming
      the recorded PR, the reason, and the closure outcome, exiting 0 on `recovered` and non-zero
      on `refused`.
      In `tests/workqueue/test_queue_triage.py`, extend `TestLandingTeardown` with a case asserting
      the rejection path leaves the brief in `picked/` with `closure-rejected-pr` and
      `closure-rejected-reason` stamped and no `triaged-to`, and add engine coverage: a recovered
      ordinary brief (closure attempted with `triaged_to` = the recorded URL and `note` `None`),
      an operator note forwarded verbatim, each refusal (unresolvable identifier, brief still in
      `queue/`, brief not `status: picked`, no recorded landing, a recorded value that is not a PR
      URL, a closure that refuses again with its status and message surfaced), a byte-identical
      brief on every refusal, a dry run that attempts no closure, and that `release()` is never
      called. Add `tests/workqueue/test_queue_triage_recover_cli.py` covering CLI-level behavior on
      a hermetic queue fixture: exit 0 and the printed JSON/human line for a recovered brief, the
      non-zero exit and reason line for each refusal class, the `--evidence` pass-through, a
      `--dry-run` run that writes nothing, and a second run after a successful recovery refusing
      because the brief is no longer a picked brief with an unclosed recorded landing.
      (Requirements: One command closes a rejected-closure brief against its recorded landing;
      Recovery refuses everything it cannot close, leaving the brief unchanged; Merged
      fold/propose landing tears down its local branch)
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py tests/workqueue/test_queue_triage_recover_cli.py

## 2. Surface the recovery in the dashboard and the operator references

- [ ] 2.1 In `src/worktrail/router/dashboard.py`, carry the recorded landing through
      `inflight_briefs()` (`:2498`) -- read the brief's `closure-rejected-pr` and
      `closure-rejected-reason` beside the existing frontmatter reads and add
      `closure_rejected_pr` / `closure_rejected_reason` to the returned entry (absent, not
      `None`-valued, for a brief with no recorded landing) -- and let a brief carrying a recorded
      landing bypass the `stale_hours` freshness filter, leaving the filter and the returned shape
      for every other picked brief exactly as they are. In `build_category_items()` (`:2982`),
      give an in-flight entry carrying `closure_rejected_pr` the action `recover-closure`, with a
      label and description naming the recorded pull request, instead of the `resume` action and
      stalled-session wording it gets today (`:3071-3093`); an entry with no recorded landing
      keeps `resume` unchanged. In `tests/router/test_dashboard.py`, cover: `inflight_briefs()`
      annotating a rejected-closure brief and returning it while it is still inside the staleness
      window, an ordinary past-window stalled brief returning `resume` with no recorded-landing
      field, and the picker item's action and description for each.
      (Requirements: The dashboard surfaces a rejected-closure brief as a recovery action)
      files: src/worktrail/router/dashboard.py tests/router/test_dashboard.py

- [ ] 2.2 In `skills/worktrail-go/SKILL.md`, add a `recover-closure` row to the action-to-dispatch
      table (`:372-385`) mapping it to the installed `worktrail-queue-triage recover-closure
      --brief <id> --json` invocation, stating that it claims nothing, dispatches no Phase 3
      work, and may be re-run once with `--evidence '<the note the refusal asks for>'` when the
      closure gate refuses again; and in the interactive triage step (`:338-364`), where the apply
      already reports a landed `pr_url`, name the recovery for the case that apply produces -- an
      action-log entry with `status: error`, a `pr_url`, and `rolled_back: false` -- so an
      attended pickup runs the recovery instead of leaving the closure outstanding. In
      `skills/worktrail-go/references/dashboard-render.md`, add `recover-closure` to the
      enumerated `action` values (`:47-50`) and describe the recorded landing on an in-flight
      entry beside the existing staleness paragraph (`:112-118`).
      (Requirement: Operator references point at the composed recovery command)
      files: skills/worktrail-go/SKILL.md skills/worktrail-go/references/dashboard-render.md

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q tests/workqueue/test_queue_triage.py
      tests/workqueue/test_queue_triage_recover_cli.py tests/router/test_dashboard.py`, then
      `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`, then `python3.14 scripts/ci/ruff_pinned.py
      check .`, `python3.14 scripts/ci/ruff_pinned.py format --check .` and `python3.14
      scripts/ci/check_shebang_exec_bits.py`. Exercise the loop end-to-end on a scratch
      `WORK_QUEUE_DIR` and a scratch repo: claim a brief, drive `_worktree_pr_close()`'s rejection
      branch with a stubbed closure rejection so the brief lands in `picked/` carrying
      `closure-rejected-pr`, confirm `worktrail-queue-triage recover-closure --brief <id> --json`
      closes it -- `status: done` in place in `picked/` with `triaged-to` set to that URL and the
      closure reported as recovered -- confirm `worktrail-dashboard`'s inflight output offers
      `recover-closure` for a second such brief and `resume` for a plain stalled brief, and
      confirm a brief whose recorded value is not a PR URL exits non-zero with the brief
      byte-identical. Run `openspec validate queue-triage-rejected-closure-recovery --strict` and
      `worktrail-compile openspec/changes/queue-triage-rejected-closure-recovery`.
      depends: 1.1, 2.1, 2.2

## 1. Partition the run-complete note by recorded outcome

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, rewrite `_format_unreconciled_tail_note`
      (`:727`) to partition its findings by `reconcile_state` instead of rendering one fixed
      clause over all of them. Keep the existing `if not findings: return None` guard and the
      existing nested `_entry(f)` renderer (`task (sha <head_sha> @ <worktree><suffix>)`, where
      the suffix is ` reconcile=<state>`, plus ` <reconcile_pr_url>` for `opened`/`already-open`
      and ` by <reconcile_superseded_by>` for `superseded`) — its per-entry text is correct and
      the two tests below pin it.
      Build three buckets in one pass, in this order of precedence:
      `merged` → skipped entirely; `opened` / `already-open` / `superseded` → awaiting; anything
      else — `quarantined`, or a finding carrying no `reconcile_state` (the raw
      `detect_unreconciled_evidence` shape) — → manual. If the manual bucket is non-empty, emit
      the existing wording unchanged for it alone:
      `!! N tail task(s) completed with unreconciled evidence (commits never merged onto base --
      reconcile before worktree cleanup, see journal `unreconciled_tail_evidence`): <entries>`.
      If the awaiting bucket is non-empty, emit a second line describing only that bucket, with
      no `!!` prefix and no reconcile instruction — `N tail task(s) auto-reconciliation PR(s)
      awaiting merge (see journal `unreconciled_tail_evidence`): <entries>` — reusing `_entry`.
      Join the emitted lines with `\n`. Return `None` when both buckets are empty, which is the
      all-`merged` case (and the empty-findings case, via the existing guard). Update the
      docstring: it currently says the fuller per-state wording lives in `journal_selfcheck` and
      that this function only annotates, which stops being true once the leading clause is
      per-bucket; state the three buckets and that `merged` findings are deliberately not
      reported.
      At the call site in `_pipeline_scheduler` (`:6935`), replace the single
      `print(f"{_ts()} {unreconciled_note}")` with a loop over `unreconciled_note.splitlines()`
      so each emitted line carries its own timestamp. Do not touch
      `integrate.detect_unreconciled_evidence`, `integrate.reconcile_unreconciled_tail_evidence`,
      `journal_selfcheck.check_repo`, the journal shape, or `_format_checkbox_divergence_note`.
      (Requirement: Reconciliation outcome is recorded and reported)
      Add `tests/orchestrator/test_live_unreconciled_tail_note.py` — pure unit tests against
      `live._format_unreconciled_tail_note`, in the plain `unittest` style of the sibling
      `tests/orchestrator/test_tail_journal_group_survival.py` (module docstring stating the
      defect and the run it was observed on, imports from
      `worktrail.orchestrator import live`). Cover: an empty list returns `None`; a single
      `merged` finding returns `None` (the observed `go-20261004-093132` shape — assert the
      result is `None`, not merely that it lacks the phrase); all-`merged` mixed with
      `quarantined` omits the merged entry from the output entirely while the `!!` line keeps the
      exact "commits never merged onto base -- reconcile before worktree cleanup" wording for the
      quarantined one; a `merged` + `opened` set emits no `!!` line at all and the awaiting line
      carries the PR url; a lone `quarantined` and a lone finding with no `reconcile_state` each
      still produce the `!!` line (the no-state case is the pre-reconciliation journal shape);
      `superseded` lands in the awaiting bucket and renders ` by <descendant>`; an
      `opened` + `quarantined` set returns two lines with the `!!` manual line first; and the
      `_entry` rendering (`sha` prefix, `@ worktree`, ` reconcile=<state>`) is unchanged for a
      quarantined entry. Prove the suite fails against the pre-fix formatter before applying the
      fix — state in the task's report which assertion failed and on which case.
      Extend `tests/orchestrator/test_live_tail_reconciliation.py` with a call-site test in its
      existing `PipelineSchedulerReconciliationTest` style: patch
      `integrate.detect_unreconciled_evidence` to return one finding and
      `integrate.reconcile_unreconciled_tail_evidence` to return it enriched with
      `reconcile_state: "merged"` (as the run in the proposal's first bullet did), drive
      `_run_pipeline_scheduler`, capture stdout, and assert no line contains
      `unreconciled evidence` — the assertion must fail against the pre-fix formatter. Add the
      companion case where the enriched state is `quarantined` and assert the captured output
      does contain the `!!` line, so the test proves the warning was narrowed rather than
      silenced.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_live_unreconciled_tail_note.py, tests/orchestrator/test_live_tail_reconciliation.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_live_unreconciled_tail_note.py
      tests/orchestrator/test_live_tail_reconciliation.py`, then `PYTHONPATH=src python3.14 -m
      pytest -q` and `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`.
      Run `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Confirm the
      regression proof by reverting only the `_format_unreconciled_tail_note` body to the
      pre-fix formatter and re-running the two test files: both the unit merged-case assertion
      and the call-site `unreconciled evidence` assertion must fail, and restoring the fix must
      turn them green. Run `openspec validate
      tail-task-auto-reconciliation-log-note-reconcile-state --strict` and `worktrail-compile
      openspec/changes/tail-task-auto-reconciliation-log-note-reconcile-state`.

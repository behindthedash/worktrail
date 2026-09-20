## 1. Propagate the verdict into the brief body on apply

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, extend
      `EVALUATOR_PROMPT_TEMPLATE`: Step 2b gains a paragraph saying a `work-directly` verdict
      may carry `refuted_span` (verbatim from the brief's focus text) plus an optional
      `corrected_span` to correct one claim while still seeding the brief, and that
      `judgment_reason` remains `needs-update`-only; the output-shape block's `refuted_span`
      and `corrected_span` descriptions drop their "for a mechanical needs-update only"
      restriction and name both verdict types. Update `Verdict`'s docstring paragraph on the
      three fields to match.
      Rework `_apply_work_directly()` so that, after `_work_directly_accepted(v)` passes and
      the brief path resolves, it: reads the live focus (`_brief_focus`); when
      `_needs_update_is_mechanical(v, focus)` holds, computes
      `_needs_update_rewritten_focus(v, focus)` and -- if that result is blank after
      `.strip()` -- returns the existing `action: "noop"` / `status: "downgraded-to-keep"`
      shape with a new `_work_directly_whole_focus_note(v)` explaining the whole-focus
      refutation, writing nothing; otherwise writes the new focus via `_set_fm_fields`
      alongside the unchanged `seeded-from`/`recommended-route` stamp and records
      `rewrite: {"removed": ..., "replacement": ...}` on the entry. In every accepted case it
      then appends a `## Triage <run_date>` section to the brief, carrying a rewrite summary
      line (only when a rewrite happened) followed by the verdict's evidence -- reuse the
      "Rewrote focus: replaced/removed ..." wording `_apply_needs_update_mechanical()` already
      builds by lifting it into a shared `_focus_rewrite_summary(v)` helper both call. Keep
      the existing `OSError`/`ValueError` handling covering the new writes, and keep the
      not-found and not-accepted branches as they are. Update the docstring.
      Extend `_preview_verdict()`'s `work-directly` branch to mirror those branches: resolve
      the brief, and when the span is mechanical report either
      `status: "planned-downgrade-to-keep"` with the whole-focus note (blank rewrite) or the
      existing `planned` stamp entry with an added `planned_rewrite` key; a verdict with no
      usable span keeps today's entry exactly, as does a brief that no longer resolves.
      Update its docstring.
      (Requirements: An applied work-directly verdict corrects the brief's focus text; An
      applied work-directly verdict records its evidence in the brief body; A whole-focus
      refutation does not seed the brief; The preview shows the body changes the apply would
      make; The evaluator may pair a correction with a work-directly verdict.)
      Extend `tests/workqueue/test_queue_triage.py` (reusing the file's existing queue/brief
      fixtures) with apply-side cases: an accepted `work-directly` with a span plus
      replacement rewrites `focus` and still stamps; a span with no replacement drops it and
      preserves surrounding text; a stale span leaves focus unchanged but still stamps and
      notes; a spanless accepted verdict appends the `## Triage` evidence note; the appended
      note names the rewrite when there was one; a whole-focus span downgrades to `keep` with
      the brief byte-unchanged on disk; a verdict failing `_work_directly_accepted` still
      writes nothing; preview cases for the planned-rewrite and planned-downgrade entries
      asserting the brief file is untouched; and a prompt assertion that the rendered
      `EVALUATOR_PROMPT_TEMPLATE` no longer scopes `refuted_span` to `needs-update` alone.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`. Run `openspec validate
      triage-apply-propagate-verdict-into-brief-body --strict` and `worktrail-compile
      openspec/changes/triage-apply-propagate-verdict-into-brief-body`.

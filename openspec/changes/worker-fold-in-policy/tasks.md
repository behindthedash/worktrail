## 1. Worker clause, report-back schema, and code-enforced validation

- [ ] 1.1 In `src/worktrail/orchestrator/dispatch.py`: (a) extend the `_ROLE_ACTION` text for
      `ROLE_IMPLEMENT` and `ROLE_FIX` with the fold-in clause — a defect in a file already
      named in Scope that is verified (reproduced or directly evidenced, never a hypothesis or
      a "while I'm here" cleanup), mechanical to fix (restores documented/established intent;
      no new design, API, or behavior contract — behavior-contract changes are never
      fold-ins), and within the caps (at most 2 fold-ins and ~20 changed lines for the task)
      is fixed in a SEPARATE commit and listed in the report-back's `fold_ins`; everything
      else (unverified, out-of-scope file, non-mechanical, over cap) is reported in `notes`
      only — capture is the orchestrator's/authoring stage's job, never a scope expansion;
      the "Touch no files outside scope" rule and the clean role's text are unchanged; the
      clause must render in all three learned-notes spellings (absent / None / empty) so the
      `test_absent_notes_are_byte_identical` guarantee keeps holding. (b) Add a pure
      `fold_in_violations(task, report)` helper: a non-list `fold_ins` value, an entry that is
      not a mapping, and an entry whose `file` is missing/blank/non-string or whose
      `os.path.normpath(file)` is not a member of the task's declared `files:` scope
      (normalized the same way) are violations; valid entries are exposed for journaling.
      `parse_report_back` is NOT tightened — an absent key or `[]` stays zero fold-ins and
      never an error. (c) In `apply_report`, a non-empty violation list forces the task's new
      status to the terminal `failed` state instead of `transition`'s result, so the report is
      never accepted as success and the existing terminal stamping/clear/quarantine machinery
      classifies it exactly as other terminal failures; valid in-scope fold-ins leave the
      transition untouched. In `src/worktrail/orchestrator/live.py`: make `_would_land_terminal`
      consult the same `fold_in_violations` predicate (a doomed report must not trigger the
      missing-context recovery), and record non-empty `fold_ins` on the run-journal entry in
      `_apply_step_commit` (and `live_run`'s entry builder) as a conditional key on the
      entry's `report` dict, mirroring `terminal_status`'s conditional injection — a report
      with no fold-ins must journal byte-identically to today (`orchestrate.py`'s
      `_REPORT_FIELDS` projection and its golden records stay untouched).
      (Requirements: Implement and fix worker prompts carry the fold-in clause; Report-back
      declares fold-ins through an optional `fold_ins` field; Declared fold-ins are validated
      in code against the task's declared scope and fail the task closed)
      Extend `tests/orchestrator/test_dispatch.py` and `tests/orchestrator/test_dispatch_extras.py`
      and add `tests/orchestrator/test_live_fold_in_journal.py`: prompt text asserts the clause
      for implement and fix and its absence for cleanup (existing scope/notes assertions
      updated, not deleted; `test_absent_notes_are_byte_identical` stays green unchanged);
      parsing regression (no key / `[]` identical to today, key present parses); validation
      with synthetic reports (out-of-scope entry and malformed entry each drive the task to
      terminal `failed` with the offending entries journaled; all-in-scope entries transition
      to review exactly as without fold-ins; fix-role report same); `_would_land_terminal`
      true on a violating report; journal entry carries file/commit/summary when present and
      carries no `fold_ins` key at all when absent.
      files: src/worktrail/orchestrator/dispatch.py src/worktrail/orchestrator/live.py tests/orchestrator/test_dispatch.py tests/orchestrator/test_dispatch_extras.py tests/orchestrator/test_live_fold_in_journal.py

## 2. Reviewer validation duty and gate

- [ ] 2.1 In `src/worktrail/orchestrator/live.py`, extend `_REVIEWER_SYSTEM_PROMPT` (applied to
      review-role spawns on both harness branches) with the fold-in duty: validate each
      declared fold-in — the file is inside the task's declared scope, the change is
      mechanical (restores documented/established intent; no new design, API, or behavior
      contract), the task stays within the fold-in caps, and the task's tests still pass — and
      FAIL the review when a declared fold-in does not meet those conditions; a declared,
      validated fold-in is NOT scope drift; undeclared out-of-scope edits and unexplained
      drift continue to FAIL exactly as today. Keep the existing drift sentence verbatim and
      add the duty as new sentences so current assertions and gate behavior hold.
      (Requirement: Reviewer validates declared fold-ins while undeclared drift still fails)
      Extend `ReviewerSystemPromptTests` in `tests/orchestrator/test_live_extras.py`: new
      assertions for the in-scope / mechanical / caps / tests-still-pass validation duty and
      the fail consequence, the existing deviation-as-failure assertion unchanged, and the
      review gate's behavior for undeclared edits verified unchanged by the existing suite.
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_extras.py
      depends: 1.1

## 3. Authoring doctrine and PR declaration

- [ ] 3.1 In `skills/worktrail-sdd-workflow/SKILL.md`, add the three-tier defect doctrine: tier
      1 fold in (same change, same PR — the verified / in-scope / mechanical conditions, the
      caps of 2 fold-ins and ~20 changed lines, the separate-commit and `fold_ins` declaration
      requirements, and the PR's `## Fold-in Fixes` section as the public declaration); tier 2
      capture the brief and let the existing `fold-into-change` triage route it into a change;
      tier 3 a standalone brief (Route F) for everything else; plus the authoring-stage rule —
      when a defect found at propose/authoring time has its file already in the change's own
      file surface, fold in (extend a task's declared scope or add a task) instead of
      capturing a brief. Introduce no new `worktrail-*` command token that is not a real
      console script and no new triage verdict vocabulary.
      In `src/worktrail/router/land_pr.py`, add the `fold_ins` parameter to `render_pr_body`
      (sequence of pre-rendered entries; absent/empty renders `none`) and always render the
      `## Fold-in Fixes` section, appended after the existing sections; add the matching
      `LandRequest` field and a repeatable `--fold-in` flag to the `land-pr` CLI.
      (Requirements: Authoring stage folds in adjacent defects in the change's own file
      surface; Standard PR body)
      Extend `tests/router/test_land_pr.py`: the body with fold-in entries lists them, the
      body without carries the section with `none`, and the existing enforced-section
      assertions are unchanged.
      files: skills/worktrail-sdd-workflow/SKILL.md src/worktrail/router/land_pr.py tests/router/test_land_pr.py

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q` (full suite, including
      `tests/test_plugin_surface.py` and the updated prompt/journal/PR-body tests), then
      `python3.14 -m worktrail.orchestrator.orchestrate check`, then `python3.14
      scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py format --check
      .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`; run `worktrail-skill-prose-scan`
      and triage its advisory output. Confirm end-to-end on a scratch task report: a report
      carrying an out-of-scope `fold_ins` entry leaves the task terminal `failed` with the
      entry journaled, a report with in-scope entries proceeds to review with the entries
      journaled, and a report without the key produces a journal entry byte-identical to the
      pre-change shape. Run `openspec validate worker-fold-in-policy --strict` and
      `worktrail-compile openspec/changes/worker-fold-in-policy`.
      depends: 1.1, 2.1, 3.1

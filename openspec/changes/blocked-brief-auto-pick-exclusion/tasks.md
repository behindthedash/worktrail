## 1. External-blocker field, operator output, and the clear command (queue side)

- [x] 1.1 In `src/worktrail/workqueue/work_queue.py`, recognize the optional `blocked-on:`
      frontmatter field as an independent blocking reason. Add a small helper (beside
      `_blocked_by_refs`, `:816`) that reads the field, coerces it to `str`, and returns the
      stripped value or `None` when it is missing, empty, or whitespace-only. In
      `list_queue()`'s `_brief_dict` (`:999`), extend the `blocked` flag with
      `or bool(<external blocker>)` and add a `blocked_on` key carrying the value (or `None`)
      and its docstring; every other per-brief field stays unchanged. In `_claim_warnings()`
      (`:1117`), append a warning naming the blocker when the field is present, alongside the
      existing `blocked-by` warnings, without changing the claim's success. In `_print_human()`
      (`:2288`), print the blocker text under the `[blocked — waiting on prerequisites]`
      section for each blocked brief that carries one (the section already lists blocked
      briefs; only the per-brief line is added).
      Add a `unblock` subcommand to the CLI (`:2084`-`:2198`): `worktrail-work-queue unblock
      <identifier>`, dispatched to a new `unblock(identifier)` function that resolves the brief
      via the existing `resolve()`/`queue_dir()` path, removes its `blocked-on:` field via the
      existing `_remove_fm_field()` (leaving every other frontmatter key, including
      `blocked-by`, untouched), and returns a result dict the human
      and `--json` printers render (report `cleared: true|false`, the brief id, and its path;
      a brief with no `blocked-on:` is a successful no-op with `cleared: false`). Reuse the
      existing `_owned_error`/ownership conventions only if the other queue-mutating subcommands
      do; a queued brief is unowned, so no ownership check is required.
      In `tests/workqueue/test_work_queue.py`, cover: listing reports `blocked` and exposes
      `blocked_on` for a brief with the field; a brief without it is unchanged; an
      empty/whitespace value is treated as absent; a `blocked-by`-only brief is unchanged;
      claiming an externally-blocked brief succeeds and its `warnings` name the blocker;
      `unblock` removes the field and leaves `blocked-by` and every other key intact; a second
      `unblock` is a no-op reporting nothing cleared; and the human `list` output shows the
      blocker text.
      (Requirements: A brief can declare an external blocker; Operator output and claim
      warnings name the external blocker; An external blocker is cleared by an explicit command)
      files: src/worktrail/workqueue/work_queue.py, tests/workqueue/test_work_queue.py

## 2. Automatic selection names and skips the external blocker (dashboard side)

- [x] 2.1 In `src/worktrail/router/dashboard.py`, extend `_blocked_skip_reason` (`:2264`)
      so that it returns `blocked:external` -- rather than the bare `blocked` -- for a brief
      that carries a non-empty `blocked-on:` (the `blocked_on` key queue-listing data now
      emits) and has neither a malformed nor an ambiguous `blocked-by` reference; malformed
      outranks ambiguous outranks external. Keep the `blocked` prefix so `log_auto_pick_miss()`'s
      `:`-split aggregation is unchanged, and update the function's docstring to name the new
      qualifier. `auto_pick_brief()`'s existing `if b.get("blocked")` branch then excludes the
      brief with no further change -- confirm that path still returns a clean brief from the
      same queue. In `tests/router/test_dashboard.py`, cover: an externally-blocked brief is
      skipped with reason `blocked:external` and never picked; a brief carrying both a malformed
      `blocked-by` and a `blocked-on:` reports `blocked:malformed-dependency`; an
      ambiguous-plus-external brief reports `blocked:ambiguous-dependency`; a clean brief in the
      same queue is still picked.
      (Requirement: Automatic selection names the external blocker and never picks it)
      files: src/worktrail/router/dashboard.py, tests/router/test_dashboard.py

## 3. Triage: a keep verdict records the external blocker (triage side)

- [x] 3.1 In `src/worktrail/workqueue/queue_triage.py`, give `Verdict` (`:1752`) one new
      defaulted field `blocked_on: str | None = None`, documented as the `keep` verdict's
      optional single-line external blocker (the string is significant when empty: it means
      "cleared"; `None` means "not judged"). Carry it through `parse_verdicts()`'s accepted
      verdict construction (`:2055`) with the same `isinstance(..., str)` guard the neighbouring
      optional fields use, so an evaluator's `blocked_on` (including `""`) survives parsing, and
      leave the fail-open/downgrade paths unchanged (a `blocked_on` on a non-`keep` verdict is
      ignored downstream, not rejected here).
      In `EVALUATOR_PROMPT_TEMPLATE` (`:309`), add a `keep`-verdict rule: set `blocked_on` to a
      single line when the brief is valid but blocked by something that is not a queue-brief
      prerequisite (a dependency in another repo, an upstream PR/branch that must land, an
      out-of-band operator action) and cannot be given a date; never use it for a queue-brief
      prerequisite; echo the brief's current `blocked-on:` value when the blocker is still
      unresolved or you cannot verify it cleared; use an empty string only with evidence it
      cleared. Show each brief's current `blocked-on:` value in the prompt's per-brief block
      (where the brief text/premise is already rendered), and list `blocked_on` in the prompt's
      per-brief JSON output shape.
      In `_apply_keep()` (`:2567`), reconcile the brief's `blocked-on:` field to `v.blocked_on`
      after the ownership check and before/alongside the note append: non-empty writes it via
      the existing `_set_fm_fields()` (quoted as a YAML scalar via `_yaml_scalar` when
      needed), `""` removes it via `_remove_fm_field`, and `None` leaves any
      existing value untouched; the `## Triage` note append is unchanged. In
      `_apply_work_directly()` (`:2637`), remove any `blocked-on:` field on the accepted
      (seeding) branch only, leaving the downgrade-to-`keep` path to `_apply_keep`. In
      `apply_verdicts()`'s `keep` preview entry (`:3908`), include `"blocked_on": v.blocked_on`
      so a preview reports the pending change.
      In `tests/workqueue/test_queue_triage.py`, cover: a `keep` with a `blocked_on` value
      writes `blocked-on:` to the brief and still appends the keep note; a `keep` echoing an
      existing value leaves it unchanged; a `keep` with no `blocked_on` leaves an existing value
      unchanged; a `keep` with `""` removes the field; a `stale-close` carrying `blocked_on`
      does not write it; an accepted `work-directly` removes an existing `blocked-on:` and a
      downgraded `work-directly` leaves it; `parse_verdicts()` carries a `blocked_on` value
      (and `""`) from evaluator output; and the `keep` preview entry carries the value while
      writing nothing.
      (Requirement: A keep verdict may record an external blocker)
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 4. Document the field and the skip reason

- [x] 4.1 In `skills/worktrail-handoff/references/handoff-template.md`, add a `blocked-on:`
      rule bullet after the `blocked-by:` bullet (`:8-12`): optional, a single non-empty line
      naming a blocker that is not a queue-brief prerequisite and has no known date; distinct
      from `blocked-by` (queue-brief IDs) and `next-check-after` (a date); a brief carrying it
      is held out of automatic selection (it appears in the listing's blocked section) until it
      is cleared, while an explicit interactive claim still succeeds and warns; set by
      triage's `keep` verdict and cleared with `worktrail-work-queue unblock <id>`. In
      `skills/worktrail-go/references/auto-mode.md`, add a `blocked:external` bullet to the
      "Skip reasons (Phase 2)" list (after the `blocked:ambiguous-dependency` bullet,
      `:69-74`): the brief carries a non-empty `blocked-on:` naming an external blocker; wait
      for that blocker to clear, then `worktrail-work-queue unblock <id>` (or let the next
      triage pass clear it).
      (Requirements: A brief can declare an external blocker; Automatic selection names the
      external blocker and never picks it)
      files: skills/worktrail-handoff/references/handoff-template.md, skills/worktrail-go/references/auto-mode.md

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_work_queue.py
      tests/router/test_dashboard.py tests/workqueue/test_queue_triage.py`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, then
      `python3 scripts/ci/ruff_pinned.py check .`, `python3 scripts/ci/ruff_pinned.py format
      --check .`, and `python3 scripts/ci/check_shebang_exec_bits.py`. Probe by hand: write a
      brief with `blocked-on:` and confirm `worktrail-work-queue list --json` reports
      `blocked: true` with the value, a rendered auto-pick skips it as `blocked:external`, and
      `worktrail-work-queue unblock <id>` clears it (restoring eligibility) while leaving any
      `blocked-by:` untouched; confirm a brief with neither field is unchanged. Confirm the
      batch pipeline is unaffected: a `queue_triage` evaluate→apply pair round-trips, a `keep`
      verdict without `blocked_on` writes no `blocked-on:` key, and its `verdict.json` entries
      carry `blocked_on: null`. Run `openspec validate blocked-brief-auto-pick-exclusion
      --strict` and `worktrail-compile
      openspec/changes/blocked-brief-auto-pick-exclusion`.
      depends: 1.1, 2.1, 3.1, 4.1

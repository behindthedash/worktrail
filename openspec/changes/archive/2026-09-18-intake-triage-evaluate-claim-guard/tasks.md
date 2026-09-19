## 1. Ownership and empty-brief primitives (`queue_triage`)

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, add `class BriefOwned(Exception)`
      (carries `brief_id`, `claimed_by`, `claimed_at`), `class BriefMissing(Exception)`,
      `class EmptyBrief(Exception)` (carries `brief_id`, `reason`), and
      `brief_claim_holder(brief_id) -> tuple[Path, str | None, str | None] | None` that
      resolves the id via `_resolve_brief_path()` and, when the path is under `picked_dir()`,
      returns its `claimed-by`/`claimed-at` (a `status: done` brief reports as owned too).
      Add `brief_focus_strict(path)` that raises `EmptyBrief` on `OSError` or blank focus
      (design D3) and leaves `_brief_focus()` unchanged. In the `keep`/`needs-update`
      note-append branch of `apply_verdicts()`, call `brief_claim_holder()` and return a
      `status: error` entry naming the owner instead of writing (design D4).
      In `tests/workqueue/test_queue_triage.py`, add tests: `brief_claim_holder()` returns
      the owner for a picked brief with `claimed-by`, `None` owner for a `queue/` brief, and
      `None` for an unknown id; `brief_focus_strict()` raises `EmptyBrief` for a brief with
      no `focus:` and no `## Focus`, and for an unreadable path, while `_brief_focus()`
      still returns `''`; `apply_verdicts()` on a `needs-update` verdict whose brief sits in
      `picked/` with `claimed-by: queue-triage` returns `status: error` naming the owner and
      leaves the file unchanged.
      (Requirement: Interactive single-brief pickup refuses a brief owned by another
      claimant; Requirement: Interactive single-brief evaluate fails loud on an empty brief.)
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Guard the single-brief evaluate/apply gate (`skill_dispatch`)

- [x] 2.1 [depends: 1.1] In `src/worktrail/router/skill_dispatch.py`, at the top of
      `evaluate_single_brief()` derive `brief_id = Path(brief_path).stem`, call
      `brief_claim_holder()`: raise `BriefMissing` when it returns `None`, `BriefOwned` when
      the owner is set, and otherwise continue with the re-resolved path (design D2). Read
      focus via `brief_focus_strict()` so `EmptyBrief` propagates. Run the same
      `brief_claim_holder()` check at the top of `apply_single_brief_verdict()`. In `main()`,
      catch `BriefOwned` (print `null`, stderr
      `blocked_brief_owned: <id> owned by <claimed-by> (claimed-at <ts>)`, exit 2),
      `BriefMissing` (`blocked_brief_missing: <id>`, exit 2), and `EmptyBrief`
      (`blocked_empty_brief: <id> (<reason>)`, exit 2) for both the
      `--evaluate-brief-triage` and `--apply-brief-triage[-file]` branches (design D5).
      In `tests/router/test_skill_dispatch.py`, add CLI tests covering the spec scenarios:
      evaluate against a `queue/` path whose brief has moved to `picked/` with
      `claimed-by: queue-triage` prints `null`, writes the `blocked_brief_owned` line, exits
      2, spawns no evaluator, and leaves the picked file byte-identical; apply of a `keep`
      verdict file after the brief was claimed exits 2 with no note appended; an unknown id
      exits 2 with `blocked_brief_missing`; a brief with no focus text exits 2 with
      `blocked_empty_brief` and no evaluator spawn; an unclaimed `queue/` brief still
      evaluates as before.
      (Requirement: Interactive single-brief pickup refuses a brief owned by another
      claimant; Requirement: Interactive single-brief evaluate fails loud on an empty brief.)
      In `skills/worktrail-go/SKILL.md`, in the Phase 2 intake-brief triage gate, add the
      picked short-circuit (when the parse result's `brief_status` is `picked`, print
      `owned by <claimed-by>` and stop) and list the three new `blocked_` lines next to the
      existing `blocked_pending_decision` exit-2 case as outcomes that stop without
      proceeding to apply; in `tests/test_plugin_surface.py`, assert the gate text names
      all three.
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch.py, skills/worktrail-go/SKILL.md, tests/test_plugin_surface.py

## 3. Bare brief id resolves against picked/ (`parse_invocation`)

- [x] 3.1 In `src/worktrail/router/parse_invocation.py`, in `_resolve_brief()`, when
      `work_queue.resolve()` against `queue_folder` returns `none`, retry against
      `queue_folder.parent / "picked"`; on a match return `mode: brief` with
      `brief_status: "picked"`, `brief_path`, and `claimed_by` read from the picked brief's
      frontmatter. Do not retry an `ambiguous` result (design D6).
      In `tests/router/test_parse_invocation.py`, add tests: a bare timestamp prefix
      matching only a `picked/` brief returns `mode: brief`, `brief_status: picked`, the
      picked path, and `claimed_by`; a token matching neither folder still returns
      `mode: free_text` with the existing reason; an `ambiguous` `queue/` match is not
      retried against `picked/`.
      (Requirement: Bare brief id resolves against picked/ as well as queue/.)
      files: src/worktrail/router/parse_invocation.py, tests/router/test_parse_invocation.py

## 4. Verification

- [x] 4.1 [depends: 1.1, 2.1, 3.1] [e2e] Run `PYTHONPATH=src pytest -q`, then
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate intake-triage-evaluate-claim-guard --strict` and
      `worktrail-compile openspec/changes/intake-triage-evaluate-claim-guard`. Then verify
      against a real claim: copy a throwaway intake brief into `$WORK_QUEUE_DIR/queue/`,
      claim it with `worktrail-work-queue claim <id> --by queue-triage`, run
      `worktrail-skill-dispatch --evaluate-brief-triage $WORK_QUEUE_DIR/queue/<id>.md` and
      confirm exit 2 with `blocked_brief_owned`, then release and remove the brief.

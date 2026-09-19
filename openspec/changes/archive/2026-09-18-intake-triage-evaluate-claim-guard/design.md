## Context

Two writers can touch one intake brief: the scheduled `queue-triage` run (claims via
`work_queue.claim(by="queue-triage")` before fold/propose, PR #1191) and an interactive
`worktrail-go BRIEF-ID` pickup, which by design never claims. Today the interactive side
has no idea the other exists: it is handed a `queue/` path from the dashboard, and by the
time the evaluator reads it the file may have moved to `picked/`.

## Decisions

**D1 — Ownership is decided by location + `claimed-by`, not by identity.**
`work_queue._ownership_block()` compares `claimed-by` against a caller `--by`; that fits
`done`/`release`, where the caller may legitimately be the owner. The interactive intake
pickup never claims, so there is no "same owner" case: a brief found in `picked/` with any
`claimed-by` is owned by someone else. `queue_triage.brief_claim_holder(brief_id)` returns
`(path, claimed_by, claimed_at)` after resolving the id across `queue/` then `picked/`; a
non-`None` `claimed_by` blocks. `status: done` briefs in `picked/` also block (they are
closed; nothing should be appended), reported as `owned by <claimed-by> (done)`.

**D2 — Re-resolve by id, never trust the handed path.** `evaluate_single_brief()` derives
the id from `Path(brief_path).stem` and resolves it fresh through `_resolve_brief_path()`.
A path that no longer exists therefore either finds the moved brief (and hits D1) or is
reported `blocked_brief_missing` — never read as an empty file.

**D3 — Empty brief is an error, not a `keep`.** A new `brief_focus_strict(path)` wraps
`_brief_focus()` and raises `EmptyBrief(reason)` when the file cannot be read or both the
`focus:` frontmatter and `## Focus` body are blank. Only the single-brief path uses it;
`group_queue_by_repo()`/`evaluate_briefs()` keep the lenient reader because they only
ever group files they just listed from `queue/`.

**D4 — Guard both ends of the gate.** The go skill runs evaluate and apply as two CLI
invocations minutes apart, so the claim can land between them. `apply_single_brief_verdict()`
runs the same D1 check before `resolve_duplicate_targets()`; and the `keep`/`needs-update`
note-append branch inside `apply_verdicts()` (the branch `_resolve_brief_path()` exists for)
checks D1 too, so a scheduled `apply` cannot write into a brief another run claimed either.
Fold/propose already claim first (#1191) and are untouched.

**D5 — Exit codes mirror the existing block shape.** The new blocks reuse the
`blocked_pending_decision` contract: print `json.dumps(None)`, one
`blocked_<reason>: ...` stderr line, exit 2. The go skill treats any exit-2 line as
"report and stop"; the verdict file holds `null` so a stale apply also fails closed.

**D6 — Parse falls back to `picked/`.** `_resolve_brief()` in `parse_invocation.py` tries
`queue_folder.parent / "picked"` when `queue/` returns `none`. The result carries
`brief_status: "picked"` plus `claimed_by` so the skill can print `owned by <claimed-by>`
without another call. `ambiguous` in `queue/` is still ambiguous (not retried).

## Risks

- A brief in `picked/` with `claimed-by` but a dead owner (crashed run) is now refused
  interactively. That is the correct default: `worktrail-work-queue release` is the
  documented recovery, and silently evaluating it was the bug.

## 1. Verdict shape, evaluator prompt, and parsing for the mechanical/judgment split

- [ ] 1.1 Implement requirement: Evidence-required verdict per brief
      (MODIFIED) — `needs-update`'s optional `refuted_span`/`corrected_span`/
      `judgment_reason`. In `src/worktrail/workqueue/queue_triage.py`: add
      `refuted_span: str | None = None`, `corrected_span: str | None = None`,
      `judgment_reason: str | None = None` fields to the `Verdict` dataclass
      (after `escalation`); in `EVALUATOR_PROMPT_TEMPLATE`, add a new "Step
      2c — needs-update requires a mechanical-vs-judgment classification"
      paragraph instructing the evaluator to set `refuted_span` (copied
      verbatim from the brief's own focus text shown in the prompt above)
      plus an optional `corrected_span` when the correction is mechanical (a
      specific, quotable claim refuted by cited code/paths, or a stale/
      archived target reference), or `judgment_reason` instead when
      resolving the brief needs a genuine human call (ambiguous claim
      priority, conflicting requirements, a scope/policy decision) — never
      both, never a `refuted_span` it cannot quote verbatim from the focus
      text below — and extend the template's final per-brief JSON output
      field list to include all three as optional fields. In
      `parse_verdicts()`, copy `obj.get("refuted_span")`/
      `obj.get("corrected_span")`/`obj.get("judgment_reason")` onto the
      constructed `Verdict` the same way `target_change`/`target_repo`/
      `question` are already copied (string-typed or `None`; no further
      validation at parse time — validity for `needs-update` still requires
      only non-empty `evidence`, unaffected by these fields, per the
      unchanged first paragraph of this requirement).
      Test, in `tests/workqueue/test_queue_triage.py`: an evaluator JSON
      object for `needs-update` carrying `refuted_span` (and, separately,
      one also carrying `corrected_span`) round-trips onto the parsed
      `Verdict`; one carrying `judgment_reason` round-trips; one carrying
      both round-trips both unchanged (precedence between them is `apply`'s
      concern — task 2.1 — not parsing's); a `needs-update` object carrying
      neither still parses as valid (evidence-only, matching today's
      behavior) with all three new fields `None`.

## 2. Mechanical rewrite, judgment decision-filing, and preview

- [ ] 2.1 Implement requirement: Apply step never closes a brief without an
      approved verdict (MODIFIED) — `needs-update`'s mechanical rewrite/
      re-evaluate and judgment decision-filing branches. Depends on 1.1's
      `Verdict` fields. In `src/worktrail/workqueue/queue_triage.py`:
      add `replace` to the existing `from dataclasses import asdict,
      dataclass, field` line; add a module constant
      `_MIN_REFUTED_SPAN_LEN = 12` next to `Verdict` (mirrors
      `premise_check.py`'s own `_MIN_QUOTED_LEN` floor, kept as an
      independent literal rather than a cross-module import, matching how
      this file already keeps its own constants like `_MIN_CANDIDATE_SCORE`
      alongside the code that uses them); add
      `_apply_needs_update_judgment(v: Verdict, path: Path) -> dict` that
      builds a synthetic `Verdict(brief_id=v.brief_id,
      verdict="needs-decision", duplicate_of=None, evidence=v.evidence,
      confidence=v.confidence, repo=v.repo, question=(v.judgment_reason or
      an f-string noting `v.refuted_span` no longer matches the brief's
      current focus text))`, calls the existing `_apply_needs_decision()` on
      it, and returns that result with `"verdict"` overlaid back to
      `"needs-update"` and a `"routed_to": "needs-decision"` key added (so
      the action-log entry's `verdict` field still reflects the verdict that
      was actually confirmed); add `_apply_needs_update_mechanical(v:
      Verdict, path: Path, run_date: str, *, agent: str, repos_root) ->
      dict` that reads the brief's current focus via `_brief_focus(path)`,
      replaces `v.refuted_span` with `v.corrected_span or ""` (count `1`),
      routes to `_apply_needs_update_judgment()` with a generated "removing
      this claim would leave no remaining focus text" `judgment_reason` when
      the result is empty after stripping, otherwise writes the new focus
      via `_set_fm_fields(path, {"focus": new_focus})`, appends a `## Triage
      {run_date}` note describing what was removed/replaced plus
      `v.evidence` (same append shape `_apply_needs_update()` already
      writes), resolves `cwd` via `_effective_repo(v)` +
      `_resolve_repo_dir(..., repos_root)` (falling back to
      `_worktrail_repo_root()` for `NO_REPO_KEY`, mirroring
      `_propose_change_wip_cap_status()`'s own resolution), calls
      `evaluate_briefs(repo, [path], agent=agent, cwd=cwd,
      repos_root=repos_root)` inside a `try`/`except Exception as exc:  #
      noqa: BLE001` (a re-evaluation failure must not lose the
      already-written rewrite, matching `_record_skipped_cells()`'s existing
      posture), and returns an action-log dict with `"action":
      "mechanical-rewrite"`, `"status": "executed"`, `"rewrite": {"removed":
      v.refuted_span, "replacement": v.corrected_span or ""}`, and
      `"reevaluation": {"status": "produced"|"error", "verdict":
      <asdict(fresh) or None>, "error": <str or None>}`; rewrite
      `_apply_needs_update()` to resolve the brief path once (existing
      not-found handling unchanged), then dispatch in this order:
      `v.judgment_reason` truthy → `_apply_needs_update_judgment`; else
      `v.refuted_span` truthy, at least `_MIN_REFUTED_SPAN_LEN` characters,
      and found verbatim in the brief's live focus text →
      `_apply_needs_update_mechanical`; else `v.refuted_span` truthy but
      failing that check → `_apply_needs_update_judgment` with a generated
      stale-span `judgment_reason`; else the existing unconditional
      note-append (today's only behavior, left byte-for-byte as-is); add
      `agent: str = "claude", repos_root: str | Path | None = None` keyword
      parameters to `_apply_needs_update()` and thread
      `apply_verdicts()`'s own `agent`/`repos_root` parameters into its
      `_apply_needs_update(v, run_date)` call site so the mechanical branch
      can re-evaluate; extend `_preview_verdict()`'s `needs-update` handling
      (currently falling into the function's generic bottom branch) to
      preview each of the three branches without executing, claiming,
      spawning, or writing anything: mechanical → `"action":
      "mechanical-rewrite"`, `"status": "planned"`, `"planned_rewrite":
      {"removed": ..., "replacement": ...}`; judgment → the same
      `pending_decision_envelope()`-building shape the existing
      `needs-decision` preview branch already returns, with `"verdict"`
      still `"needs-update"`; neither field → the unchanged generic preview.
      Test, in `tests/workqueue/test_queue_triage.py`: `apply --confirm` on
      a mechanical `needs-update` verdict rewrites the brief's focus text
      (span removed; span replaced when `corrected_span` is given), appends
      the rewrite note, and calls `evaluate_briefs` for re-evaluation
      (monkeypatched to return a canned fresh `Verdict`) with the fresh
      verdict embedded under `"reevaluation"` and *not* itself applied (no
      claim/close/PR side effect attributable to the fresh verdict); a
      `refuted_span` not present verbatim in the brief's live focus text
      (simulate drift by editing the brief's focus between constructing the
      verdict and calling apply in the test) falls to filing a decision with
      a generated question, and the focus text is left unchanged; a
      `refuted_span` under 12 characters is treated the same as a
      non-matching one; a rewrite that would empty the focus text (current
      focus text equal to `refuted_span`) files a decision instead of
      writing an empty `focus:`; a `judgment_reason`-carrying verdict files
      a decision via the existing `_apply_needs_decision()` path (decision
      record created, brief stamped `awaiting-decision:`, `focus:`
      untouched) and the returned action-log entry's `"verdict"` field reads
      `"needs-update"` with `"routed_to": "needs-decision"`; a verdict with
      neither field behaves identically to before this change (every
      existing `needs-update` test in this file keeps passing unmodified);
      the no-`--confirm` preview path for each of the three branches matches
      its corresponding `--confirm` branch's plan without mutating the brief
      or spawning an evaluator agent.

## 3. Verification

- [ ] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm it is green,
      including the new tests from sections 1-2. Verification-only — no
      file changes expected.
- [ ] 3.2 [cleanup] Run `openspec validate
      queue-triage-autonomous-needs-update-rewrite --strict` and confirm it
      passes. Verification-only — no file changes expected.
- [ ] 3.3 [cleanup] Run `worktrail-compile
      openspec/changes/queue-triage-autonomous-needs-update-rewrite` and
      confirm it passes (no same-file-chain or missing-test-scope findings).
      Verification-only — no file changes expected.

## 1. Verdict provenance guard and single-use consumption (code)

- [ ] 1.1 Give the single-brief triage verdict a content digest and enforce it at apply time.
      In `src/worktrail/workqueue/queue_triage.py`, give `Verdict` one new defaulted field,
      `brief_digest: str | None = None`, whose docstring says it is the SHA-256 hex digest of
      the brief file's bytes as read for evaluation, set only by the single-brief interactive
      pickup (`None` for batch verdicts and directly built ones). In
      `src/worktrail/router/skill_dispatch.py`, add a stdlib `hashlib` helper
      `brief_digest(path)` returning that hex digest, and a `StaleVerdict(brief_id)` exception
      beside `_guarded_brief_path`. In `evaluate_single_brief()`, capture the digest of the
      re-resolved brief after the linked-decision/repo-inference pre-pass and immediately
      before the brief's evaluation, and stamp it on the verdict that is returned -- both the
      escalation-matrix return and the `evaluate_briefs()` return -- so
      `--evaluate-brief-triage`'s printed JSON carries it. In
      `apply_single_brief_verdict()`, keep the `_guarded_brief_path` ownership check first and
      then, when the verdict carries a non-empty digest, raise `StaleVerdict` if the brief's
      current content digest differs. In `main()`'s apply branch: a payload read from a file
      whose digest is absent or empty prints `null` and the
      `blocked_verdict_unattributed: <path>` line on stderr and exits 2; a `StaleVerdict`
      prints `null` and
      `blocked_verdict_stale: <brief-id> (brief content changed since evaluation)` on stderr
      and exits 2; with `--confirm` and the file form, remove the file once its contents have
      been read and parsed (a preview and an unreadable/unparsable file leave it in place).
      Cover all of it in `tests/router/test_skill_dispatch.py`: the printed verdict carries a
      digest matching the brief's bytes (and the escalation-matrix verdict carries one too); a
      digest-carrying file applies for an unchanged brief and matches the inline result; a
      rewritten brief refuses with `blocked_verdict_stale`, exit 2, and an untouched brief; a
      briefly-owned-and-changed brief refuses as `blocked_brief_owned`; a digest-less file
      refuses with `blocked_verdict_unattributed`, exit 2, applies nothing; a digest-less
      inline payload still applies; a confirmed file apply removes the file and a second run
      fails with the unreadable-file entry; a preview leaves the file. Update that file's
      existing file-form fixtures so their success-path payloads carry the digest (the
      unreadable-file and null-payload cases stay as they are).
      (Requirements: A verdict is applied only against the brief state it was evaluated from;
      Interactive apply reads the verdict from a file)
      files: src/worktrail/router/skill_dispatch.py, src/worktrail/workqueue/queue_triage.py, tests/router/test_skill_dispatch.py

## 2. Gate prose: dispatch-scoped path and the new refusals

- [ ] 2.1 In `skills/worktrail-go/SKILL.md`'s Phase 2 intake-brief triage gate, derive both
      `VERDICT_FILE` expressions from the brief id plus `$INVOCATION_CONTEXT_DISPATCH_ID`
      (`${TMPDIR:-/tmp}/triage-verdict-${BRIEF_ID}-${INVOCATION_CONTEXT_DISPATCH_ID}.json`) so
      two concurrent triages of one brief never share a file, and say so: the path is scoped
      to this dispatch, never derive or guess one from the brief id alone. Document that the
      apply consumes the file with `--confirm`, so a retried step 2 fails closed with the
      unreadable-file error and re-running step 1 is the remedy. Add to step 2's non-zero-exit
      list the two new exit-2 refusals and their remedies: `blocked_verdict_stale: <id> (brief
      content changed since evaluation)` -- the brief changed between the two steps (e.g.
      another triager applied its own verdict); report it and leave the brief alone, or re-run
      step 1 against the brief's current content -- and `blocked_verdict_unattributed: <path>`
      -- the file was not produced by this gate's evaluate step; do not apply it, re-run
      step 1. Update `tests/router/test_skill_prose_enforcement_coverage.py`'s Phase 2
      assertions to pin the new shape: the evaluate block's `VERDICT_FILE` expression names
      `$INVOCATION_CONTEXT_DISPATCH_ID`, the legacy brief-id-only literal no longer appears
      anywhere in the gate text, the apply call still passes `--confirm`, and both new
      `blocked_verdict_*` lines are documented.
      (Requirement: Interactive apply reads the verdict from a file)
      files: skills/worktrail-go/SKILL.md, tests/router/test_skill_prose_enforcement_coverage.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_skill_dispatch.py
      tests/router/test_skill_prose_enforcement_coverage.py tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, then `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`, and
      `python3 scripts/ci/check_shebang_exec_bits.py`. Probe the refusal paths by hand: run
      `worktrail-skill-dispatch --apply-brief-triage-file` against a digest-less verdict object
      and confirm `blocked_verdict_unattributed` with exit 2, and against a digest-carrying
      object for a since-edited brief and confirm `blocked_verdict_stale` with exit 2, each
      applying nothing. Confirm the batch path is unaffected: a `queue_triage` evaluate→apply
      pair's `verdict.json` round-trips and its entries carry `brief_digest: null`. Run
      `openspec validate triage-verdict-file-collision-safety --strict` and
      `worktrail-compile openspec/changes/triage-verdict-file-collision-safety`.
      depends: 1.1, 2.1

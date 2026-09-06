## 1. Signal exhaustion out of the spawn helper

- [x] 1.1 In `src/worktrail/orchestrator/spawnlib.py`: add `exhausted: bool = False` and
      `failure_class: str = ""` to `SpawnResult` (design D1), give the inner `finish()` helper
      keyword-only `exhausted`/`failure_class` parameters defaulting to `False`/`""` that it
      passes through to the `SpawnResult` it builds, and set them at all three give-up returns
      -- the session-limit "wait budget exhausted, giving up to caller" return (~line 1208),
      the "no alternate cell left in the row, giving up to caller" return (~line 1287), and the
      retry-loop fall-out return (~line 1318) -- carrying the failure class already computed by
      `agent_capacity.classify_failure()` for that cell (`billing` for a provider usage cap).
      Leave the success return at ~line 1220 (`not is_infra_failure`) passing neither, so it
      stays `exhausted=False` with an empty failure class.
      In a new `tests/orchestrator/test_spawn_exhaustion_signal.py`, assert: a spawn whose only
      cell fails with usage-limit text and has no alternate cell returns `exhausted is True`
      with `failure_class == "billing"`; a spawn that exhausts its session-limit wait budget
      returns `exhausted is True`; a spawn whose retry loop falls out returns
      `exhausted is True`; a spawn that succeeds returns `exhausted is False`,
      `failure_class == ""`, and unchanged `text`/`usage`/`session_id`/`served_*` fields; and
      `SpawnResult()` built with no new arguments defaults to not-exhausted
      (Requirements: A spawn that gave up is signalled as exhausted).
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawn_exhaustion_signal.py

## 2. Fail closed in the consumers

- [ ] 2.1 In `src/worktrail/workqueue/queue_triage.py`: add an `EvaluatorUnavailable(Exception)`
      carrying `repo`, `brief_ids`, and `failure_class`. In `evaluate_group()`, when
      `spawnlib.spawn_agent()`'s result has `exhausted` set, return the group dict with
      `raw_text=""` plus `"exhausted": True` and `"failure_class": <result.failure_class>`
      rather than the error stream (design D2); leave the archived short-circuit and the normal
      path untouched. In `evaluate_briefs()`, raise `EvaluatorUnavailable` for such a group
      before `parse_verdicts()` is called. In `cmd_evaluate()`, catch it per group: omit that
      group's briefs from the verdict file, count it as `groups_unevaluated` in both the
      `--as-json` object and the text summary line, keep every other group's verdicts, and
      return a non-zero exit when the count is non-zero (design D3).
      In a new `tests/workqueue/test_triage_evaluator_exhaustion.py` (patching `spawn_agent` as
      `tests/workqueue/test_queue_triage.py` already does), assert: an exhausted spawn makes
      `evaluate_group()` return `exhausted=True` with empty `raw_text` and the failure class;
      `evaluate_briefs()` raises `EvaluatorUnavailable` naming the repo, brief ids, and failure
      class, and `parse_verdicts()` is never called; the briefs' file bytes and their
      `consecutive_keep_count()` are identical before and after; and a two-group `cmd_evaluate`
      run whose second group is exhausted writes the first group's verdicts, writes none of the
      second group's briefs, reports `groups_unevaluated == 1`, and exits non-zero; depends on
      1.1 (Requirements: An exhausted evaluator spawn yields no verdict; Evidence-required
      verdict per brief; Capacity exhaustion exits non-zero and distinguishably).
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_triage_evaluator_exhaustion.py

- [ ] 2.2 In `src/worktrail/workqueue/create_handoff.py`: in `_semantic_slug_summary()`, return
      `None` when the spawn result has `exhausted` set, before reading `result.text`, so capture
      falls through to the existing `fallback_slugify()` path (design D5).
      In a new `tests/workqueue/test_handoff_slug_exhaustion.py`, assert: an exhausted summariser
      spawn whose text is a provider usage-limit message makes `_semantic_slug_summary()` return
      `None`, and a capture through that path names the brief from its focus text with none of
      the error message in the filename; a non-exhausted spawn still yields its summary
      unchanged; depends on 1.1 (Requirements: An exhausted spawn's output is never used as a
      result value).
      files: src/worktrail/workqueue/create_handoff.py, tests/workqueue/test_handoff_slug_exhaustion.py

- [ ] 2.3 In `src/worktrail/router/skill_dispatch.py`: let `EvaluatorUnavailable` propagate out
      of `evaluate_single_brief()` (documenting it in the docstring alongside the existing
      `None` return), and in `main()`'s `--evaluate-brief-triage` branch catch it, print `null`
      on stdout, print `blocked_no_capacity: <repo>/<failure_class>: <detail>` on stderr, and
      return 2 -- matching the existing `--routing` `NoExecutionTarget` branch's shape and
      leaving today's `None`-verdict exit 1 untouched (design D3). In the
      `--apply-brief-triage` branch, reject a payload that is `null`, is not a JSON object, or
      whose `verdict` field is null/empty by printing an `error` action-log entry naming the
      reason and returning 1, before `Verdict(**...)` is constructed (design D4).
      In a new `tests/router/test_skill_dispatch_triage_capacity.py`, assert: an
      `EvaluatorUnavailable` from the evaluator makes the CLI print `null`, emit
      `blocked_no_capacity:` on stderr, and exit 2; a `None` verdict still exits 1 with `null`;
      `--apply-brief-triage null` exits 1 with an `error` entry, raises no `TypeError`, and
      appends no triage note or `keep-count` to the brief; an object with `verdict: null` gives
      the same result; and a well-formed verdict still applies and exits 0; depends on 2.1
      (Requirements: Capacity exhaustion exits non-zero and distinguishably; The apply path
      refuses a verdictless payload).
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch_triage_capacity.py

## 3. Operator procedure

- [x] 3.1 In `skills/worktrail-go/SKILL.md`'s intake-triage step (the
      `--evaluate-brief-triage` block, ~lines 275-295): document that exit 2 with a
      `blocked_no_capacity:` stderr line means no model evaluated the brief -- report the
      capacity block, do NOT run the apply step, and leave the brief queued unchanged --
      keeping the existing exit-1/`null` "no identifiable verdict" wording as the separate case
      it is; depends on 2.3. Documentation only.
      files: skills/worktrail-go/SKILL.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 2.2, 2.3, and 3.1. Verification-only, no file changes expected.

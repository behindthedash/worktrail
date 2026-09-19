## 1. Normalise a pre-spawn selection failure into the existing unavailable outcome

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`: wrap `evaluate_group()`'s
      `spawnlib.spawn_agent(...)` call (~line 1565) in `try` / `except NoExecutionTarget` —
      imported from `..runtime.selection`, which also catches `spawnlib.SpawnExhausted` since
      it subclasses it — and on catch return the exact same exhausted group dict the
      `result.exhausted` branch below already builds (`raw_text=""`, `"exhausted": True`,
      `candidates_by_brief`, `premise_by_brief`, `known_repos_by_brief`,
      `dependency_freshness`), with `"failure_class"` taken from the exception:
      `getattr(exc, "failure_class", "")` (set by `SpawnExhausted`), falling back to the
      `failure_class` in the first attempted cell's `evidence` mapping when the exception is a
      plain `NoExecutionTarget` from `select_cell()`, and `""` when neither is available. Build
      the dict once (a small local helper or hoisted literal) rather than duplicating it, and
      leave the archived short-circuit, the normal path, and `evaluate_briefs()`/`cmd_evaluate()`
      untouched — they already turn `"exhausted"` into `EvaluatorUnavailable` and per-group
      `groups_unevaluated` accounting.
      In `tests/workqueue/test_triage_evaluator_exhaustion.py`, add cases (patching
      `spawn_agent` the way the existing cases in that file do) asserting: a `spawn_agent` that
      raises `NoExecutionTarget` before returning makes `evaluate_group()` return
      `exhausted=True` with empty `raw_text`; the failure class is carried through from a
      `SpawnExhausted` and from a plain `NoExecutionTarget` whose attempted evidence names one;
      `evaluate_briefs()` raises `EvaluatorUnavailable` naming the repo, brief ids and failure
      class, with `parse_verdicts()` never called; the briefs' file bytes and
      `consecutive_keep_count()` are identical before and after; and a two-group `cmd_evaluate`
      run whose second group's spawn raises writes the first group's verdicts, none of the
      second group's briefs, reports `groups_unevaluated == 1`, and exits non-zero without
      propagating the exception (Requirements: Capacity exhaustion exits non-zero and
      distinguishably).
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_triage_evaluator_exhaustion.py

## 2. Stop the single-brief CLI tracebacking on a selection failure

- [ ] 2.1 In `src/worktrail/router/skill_dispatch.py`: in `main()`'s `--evaluate-brief-triage`
      branch (~lines 1178-1199), add `except NoExecutionTarget as exc:` after the existing
      `EvaluatorUnavailable` handler — using the `NoExecutionTarget` already imported at line 35
      — that prints `json.dumps(None)` on stdout, prints
      `blocked_no_capacity: {parsed.triage_repo or '(no repo)'}/{getattr(exc, 'failure_class', '') or 'unknown'}: {exc}`
      on stderr, and returns 2, matching the `--resolve-routing` branch's shape at
      `:1325-1326` and the contract at `skills/worktrail-go/SKILL.md:306`. Order it so the more
      specific `EvaluatorUnavailable` catch still wins; this one is the backstop for the
      pre-pass spawns (repo inference) that are outside `evaluate_group()`'s try block. Note in
      `evaluate_single_brief()`'s docstring that a selection failure can also propagate, with
      the same meaning as `EvaluatorUnavailable`. Leave the exit-1 `None`-verdict path
      untouched.
      In `tests/router/test_skill_dispatch_triage_capacity.py`, add cases asserting: an
      evaluator that raises `NoExecutionTarget` makes the CLI print `null`, emit a
      `blocked_no_capacity:` stderr line naming the repo, and exit 2 with no traceback and the
      brief byte-for-byte unchanged; a `SpawnExhausted` raised from the pre-pass gives the same
      result with its failure class in the line; `EvaluatorUnavailable` still exits 2 with its
      own line; a `None` verdict still exits 1; and a well-formed verdict still exits 0
      (Requirements: Capacity exhaustion exits non-zero and distinguishably).
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch_triage_capacity.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, confirming both
      repository gates pass; depends on 1.1 and 2.1. Verification-only, no file changes
      expected.

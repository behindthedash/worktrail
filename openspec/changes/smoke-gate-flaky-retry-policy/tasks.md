## 1. Policy key (`integration-smoke-retry-policy`)

- [x] 1.1 Implement Requirement: integrate_smoke_retries is a policy key. In
      `src/worktrail/router/policy.py`, add `"integrate_smoke_retries": 0` to
      `DEFAULTS` with a comment stating it is opt-in, recommends `1`, and is
      consumed only by the orchestrator's integrated smoke gate; add the key
      to the `(key, minimum)` integer-validation loop with minimum `0` so a
      non-integer, boolean, or negative value is dropped to the default with
      the existing `must be an integer >= N; dropped` warning (design D1).
      In `tests/router/test_policy.py`, add tests for the absent-key default,
      a valid `1`, and each invalid form (`true`, `"1"`, `-1`) producing `0`
      plus a warning naming the key.
      files: src/worktrail/router/policy.py, tests/router/test_policy.py

## 2. Smoke gate retry and flake evidence (`integration-smoke-retry-policy`)

- [x] 2.1 Implement Requirements: The integrated smoke gate retries non-zero
      exits when configured; A pass after retry is recorded as flake evidence.
      In `src/worktrail/orchestrator/integrate.py`, give
      `_run_integration_smoke` a `retries: int = 0` keyword and loop up to
      `retries + 1` attempts: a `TimeoutExpired`/`OSError` returns failure
      immediately, a zero exit returns success, a non-zero exit with attempts
      left re-runs the same command in `iw`, and exhaustion returns failure
      with the last attempt's `exit N: <tail>`. On a pass after the first
      attempt, return the first attempt's detail so the caller can see it
      (design D3; keep the `(ok, detail)` shape working for existing callers).
      Add `_record_smoke_flake(journal_path, name, detail)` following
      `record_unreconciled_tail`'s direct atomic-write pattern, writing
      `journal["smoke_flakes"][name] = detail`. Give `integrate_one` a
      `smoke_retries: int = 0` keyword, pass it to the runner, and on a
      pass-after-retry print `FLAKY [<name>] smoke passed on attempt K of N;
      attempt 1: <detail>` and call `_record_smoke_flake`. Update the
      `integrate_one` docstring's `smoke_cmd` paragraph to describe the
      retry and evidence (design D3, D4).
      In `tests/orchestrator/test_integrate_complete.py`'s
      `IntegrationSmokeTest`, add coverage using a temp-dir counter script
      that fails on its first invocation and passes on its second: default
      count invokes once and fails; `retries=1` passes on the second attempt
      and returns the first-attempt detail; two hard failures invoke exactly
      twice and return the second exit; a timeout (patch
      `SMOKE_TIMEOUT_DEFAULT` small with a `sleep`) is not retried; through
      `integrate_one` with `smoke_retries=1` and a journal path, a
      pass-after-retry still pushes/opens the PR, prints the `FLAKY` line,
      and writes `smoke_flakes[group]` while a first-attempt pass writes no
      such entry; and a subsequent `_write_group_journal` for the same group
      leaves `smoke_flakes` intact.
      files: src/worktrail/orchestrator/integrate.py, tests/orchestrator/test_integrate_complete.py

## 3. Orchestrator plumbing (`integration-smoke-retry-policy`)

- [x] 3.1 Plumb the smoke retry count from policy; depends on 1.1, 2.1.
      Implement Requirement: The retry count reaches the orchestrator from
      policy. In `src/worktrail/orchestrator/live.py`, add
      `_default_smoke_retries(repo) -> int` next to `_default_smoke_cmd`
      (returns `load_policy(repo).get("integrate_smoke_retries", 0) or 0`),
      resolve it once where `smoke_cmd` is resolved for the run, thread it
      to the integrate stage, and set
      `integrate_kwargs["smoke_retries"]` beside `"smoke_cmd"` at the
      `integrate_one_fn` call site (design D1: policy only, no CLI flag).
      In `tests/orchestrator/test_default_smoke_cmd.py`, add tests that
      `_default_smoke_retries` returns `1` for a repo whose policy sets the
      key and `0` for a repo that does not.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_default_smoke_cmd.py

## 4. Documentation (`integration-smoke-retry-policy`)

- [x] 4.1 In `skills/worktrail-go/references/subagent-prompts.md`, extend the
      "Integrated smoke test (opt-in, from policy)" bullet with one sentence
      each: `integrate_smoke_retries` (default `0`, recommend `1`) re-runs
      the command on a non-zero exit before quarantining; a pass-after-retry
      is logged as `FLAKY` and recorded under the run journal's
      `smoke_flakes` map, so a repeated entry for the same suite is a signal
      to fix the flake, not to raise the count. In
      `.claude/skills/worktrail/skill.md`, add the same two facts to the
      smoke-gate notes near the `_default_post_merge_smoke_cmd` mention.
      files: skills/worktrail-go/references/subagent-prompts.md, .claude/skills/worktrail/skill.md

## 5. Verification

- [ ] 5.1 [e2e] Run `pytest -q tests/router/test_policy.py
      tests/orchestrator/test_integrate_complete.py
      tests/orchestrator/test_default_smoke_cmd.py tests/test_plugin_surface.py`,
      then `pytest -q` and `python3 -m worktrail.orchestrator.orchestrate
      check`; confirm all pass.
      depends on 1.1, 2.1, 3.1, 4.1. Verification-only; no file changes expected.
- [ ] 5.2 [e2e] Run `openspec validate smoke-gate-flaky-retry-policy --strict`
      and `worktrail-compile openspec/changes/smoke-gate-flaky-retry-policy`;
      confirm both pass.
      depends on 1.1, 2.1, 3.1, 4.1. Verification-only; no file changes expected.

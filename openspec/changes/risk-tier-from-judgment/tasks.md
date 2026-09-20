## 1. Judgment backend

- [x] 1.1 Add `src/worktrail/router/risk_judgment.py`: the question set (one
      4-level blast-radius Score plus the four red-line Nouls), a single-request
      `ask()` over the System One endpoint reading `TYPESAFE_API_KEY`,
      `is_configured()`, `parse_answers()` raising on any missing or
      non-numeric field, and the pure `compose_tier()` mapping onto
      `classify.RISK_ORDER` with red lines applied as a floor.
      (Requirement: The judgment observes, the code decides the tier)
      (Requirement: Risk tier has a keyword backend and a judgment backend over one mapping)
  files: src/worktrail/router/risk_judgment.py, tests/router/test_risk_judgment.py

- [x] 1.2 [depends: 1.1] Add `judge_risk()` returning `None` on a missing
      credential, `URLError`/`HTTPError`, `OSError`/timeout, `JSONDecodeError`,
      and a `ValueError` from `parse_answers()`, with an injectable `asker` so
      the failure modes are testable without a network.
      (Requirement: The judgment fails safe into the keyword table)
  files: src/worktrail/router/risk_judgment.py, tests/router/test_risk_judgment.py

## 2. Wire it into the classifier

- [x] 2.1 [depends: 1.2] In `src/worktrail/router/classify.py`: extract the
      existing body as `_classify_risk_by_keyword()`, give
      `classify_risk(text, *, judgment=False)` the second backend with the
      keyword fallback, and document both backends and why the default is off.
      (Requirement: Risk tier has a keyword backend and a judgment backend over one mapping)
  files: src/worktrail/router/classify.py, tests/router/test_risk_judgment.py

- [x] 2.2 [depends: 2.1] Add `classify(..., risk_judgment_enabled=False)`,
      pass it through to `classify_risk`, and have `main()` enable it with a
      `--no-risk-judgment` opt-out. Update `classify()`'s docstring purity
      claim to name the judgment alongside the existing `gh` lookup carve-out.
      (Requirement: Route classification stays pure and deterministic by default)
  files: src/worktrail/router/classify.py, tests/router/test_risk_judgment.py

## 3. Fixtures and tests

- [x] 3.1 Land `tests/fixtures/risk_probes.json` (24 adversarial probes, each
      with its `truth` tier, `_meta` stating it measures a capability gap and
      not a base rate) and `tests/fixtures/risk_probe_answers.json` (the
      recorded answer per probe, with model and capture date).
      (Requirement: The tier mapping is regression-testable without the service)
  files: tests/fixtures/risk_probes.json, tests/fixtures/risk_probe_answers.json

- [x] 3.2 [depends: 2.2, 3.1] Add `tests/router/test_risk_judgment.py`
      covering `compose_tier`'s baseline/rounding/clamping, each red line's
      forced tier, the floor-not-assignment rule, the threshold boundary, the
      label shape, every `parse_answers` rejection, every `judge_risk` failure
      mode, both `classify_risk` backends and the fallback, `classify`'s
      default-off flag, and a replay of the whole recorded probe set asserting
      zero unsafe auto-merges, zero false gates, every error off by one, and
      the keyword table's pinned 7/7 failure.
      (Requirement: The tier mapping is regression-testable without the service)
      (Requirement: The judgment fails safe into the keyword table)
  files: tests/router/test_risk_judgment.py

## 4. Verification

- [x] 4.1 [depends: 3.2] [e2e] Run `PYTHONPATH=src pytest -q`,
      `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`,
      `python3 scripts/ci/check_shebang_exec_bits.py`, and
      `python3 -m worktrail.orchestrator.orchestrate check`; then exercise
      `judge_risk()` against the live endpoint with a real key on a
      false-gate, an unsafe-merge and an auth-weakening case, confirming the
      tier moves in the intended direction on each.
      (Requirement: The judgment observes, the code decides the tier)
  files: src/worktrail/router/risk_judgment.py, tests/router/test_risk_judgment.py

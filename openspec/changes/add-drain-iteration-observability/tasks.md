## 1. Regression Coverage

- [ ] 1.1 Add focused drain-loop regressions in `tests/drain/test_drain.py` for a capacity-blocked iteration with no attributed brief and a failed iteration with one claimed brief plus a persisted transcript; assert stable empty values, diagnostic fields, and captured log context. (Requirement: Iteration summaries preserve structured diagnostic context) (Requirement: Human-readable iteration logs expose attribution evidence)

## 2. Drain Summary Logging

- [ ] 2.1 Update `src/worktrail/drain/drain.py` to emit `failure_class`, `claimed_delta`, and `claimed_brief_count` in every per-iteration summary entry and log line while retaining the existing nullable `transcript` pointer and all current outcome fields.

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py` and confirm the focused drain suite passes.
- [ ] 3.2 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm the repository regression gates pass.

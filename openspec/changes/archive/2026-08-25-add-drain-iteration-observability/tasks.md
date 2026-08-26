## 1. Drain Summary Logging

- [x] 1.1 Update `src/worktrail/drain/drain.py` to emit `failure_class`, `claimed_delta`, and `claimed_brief_count` in every per-iteration summary entry and log line while retaining the existing nullable `transcript` pointer and all current outcome fields. (Requirement: Iteration summaries preserve structured diagnostic context) (Requirement: Human-readable iteration logs expose attribution evidence)

## 2. Regression Coverage

- [x] 2.1 Add focused drain-loop regressions in `tests/drain/test_drain.py` for a capacity-blocked iteration with no attributed brief and a failed iteration with one claimed brief plus a persisted transcript; assert stable empty values, diagnostic fields, and captured log context. This task runs after 1.1 and its worktree already contains the implemented fields — the new tests are expected to pass. (Requirement: Iteration summaries preserve structured diagnostic context) (Requirement: Human-readable iteration logs expose attribution evidence)

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py` and confirm the focused drain suite passes.
- [x] 3.2 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm the repository regression gates pass.

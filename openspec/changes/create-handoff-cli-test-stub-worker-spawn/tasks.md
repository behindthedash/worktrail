## 1. Default spawn isolation for work-queue tests

- [ ] 1.1 Add `tests/workqueue/conftest.py` with a package-scoped autouse fixture that
      monkeypatches `worktrail.workqueue.create_handoff.spawn_agent` with a stub that records its
      calls and returns an object whose `.text` is empty and `.exhausted` is False -- so capture
      falls back to the deterministic slug. Document in the module docstring why this is needed:
      the suite-wide `tests/conftest.py` seeds a routing file with a `default_tier`, so
      `_semantic_slug_summary()` would otherwise spawn a real headless worker whose stderr
      contaminates `capsys` (2026-09-05, runs `go-20260905-182103` feature-2/feature-3), and note
      that a per-test patch still wins because a patch applied later takes precedence. Expose the
      recorded calls through a named fixture so tests can assert the stub was the one consulted.
      (Requirement: Work-queue tests never spawn a real headless agent.)
      files: tests/workqueue/conftest.py

- [ ] 1.2 In `tests/workqueue/test_create_handoff.py`, add regression tests for the isolation
      scenarios: capturing with a `repo` pointing at a real directory and no per-test spawn patch
      never reaches the real `spawn_agent` (assert via the conftest fixture's recorded calls that
      the stub was consulted and the slug is the deterministic fallback), and a test that patches
      `spawn_agent` itself still sees its own value. Replace the stale "observed failing under
      concurrent orchestrator smoke runs" comment block on
      `test_cli_human_mode_reports_overlap_warning_to_stderr_without_blocking`'s
      `len(warning_lines) == 1` assertion with a short note that the conftest spawn stub is what
      keeps the stream clean, keeping the `stderr_detail` failure message. Audit the remaining
      `main(...)` and `create_handoff(...)` call sites in the module for any other unstubbed
      real-world dependency and record the result in the PR description.
      (Requirement: Work-queue tests never spawn a real headless agent.)
      files: tests/workqueue/test_create_handoff.py

## 2. Verification

- [ ] 2.1 [depends: 1.1, 1.2] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate create-handoff-cli-test-stub-worker-spawn --strict` and
      `worktrail-compile openspec/changes/create-handoff-cli-test-stub-worker-spawn`.

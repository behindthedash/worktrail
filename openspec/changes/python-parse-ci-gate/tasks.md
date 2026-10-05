## 1. Parse-gate helper

- [ ] 1.1 Add a side-effect-free `scripts/ci/check_python_parse.py` that enumerates tracked
      `*.py` files, decodes and parses each with the executing interpreter, reports every
      repository-relative `SyntaxError` to stderr, and returns non-zero only after the full scan.
      Add colocated tests for valid files, multiple invalid files, deterministic relative
      diagnostics, tracked-file selection, and no bytecode artifacts. (Requirements: CI parses
      every tracked Python file; Parse failures identify every invalid file)
      files: scripts/ci/check_python_parse.py scripts/ci/test_check_python_parse.py

## 2. Required-job wiring

- [ ] 2.1 Invoke the parse helper in `Lint, Test & Build` after installation and before pytest,
      using the existing per-step non-bookkeeping condition; extend the workflow test to assert
      the named step's command, ordering, and condition without adding a job-level `if:` or a new
      required context. (Requirement: CI parses every tracked Python file)
      files: .github/workflows/ci.yml tests/test_required_check_jobs.py
      depends: 1.1

## 3. Verification

- [ ] 3.1 [e2e] Run `python3.14 scripts/ci/check_python_parse.py`, the new helper tests, the
      required-check workflow test, `pytest -q`, the orchestrator golden regression, pinned Ruff
      lint and format checks, shebang/exec-bit verification, and `python3.14 -m build`. Confirm
      the parse helper reports no invalid tracked Python file. (Requirements: CI parses every
      tracked Python file; Parse failures identify every invalid file)
      depends: 2.1

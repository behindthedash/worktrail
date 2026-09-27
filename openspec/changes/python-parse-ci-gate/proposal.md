## Why

The required `Lint, Test & Build` job can accept Python source that the interpreter cannot parse:
`python3 -m compileall -q src` currently reports 55 compilation errors, including Python-2-style
multi-exception handlers. Ruff lint and pytest do not provide a repository-wide parse guarantee,
so these broken modules can remain in the shipped source tree unnoticed.

CI needs an explicit, deterministic Python parsing gate so an unparseable tracked Python source
file fails before the package is considered healthy.

## What Changes

- Add a CI helper that checks the repository's Python source files with the configured Python
  interpreter and reports every parse failure with an actionable path and diagnostic.
- Run that helper in the existing required `Lint, Test & Build` workflow before pytest.
- Add hermetic tests for valid source, invalid source, discovery boundaries, diagnostics, and the
  committed CI workflow invocation.

## Capabilities

### New Capabilities

- `python-source-parse-ci-gate`: CI rejects a non-bookkeeping change when repository Python
  source cannot be parsed by the job's configured interpreter.

### Modified Capabilities

## Impact

- New `scripts/ci/` parse-gate helper and colocated tests.
- `.github/workflows/ci.yml` gains a step in the existing required check; its job-level gating
  behavior remains unchanged.
- The gate will expose and require follow-up remediation for currently unparseable source files.

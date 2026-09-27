## Context

The existing workflow runs Ruff, pytest, and build in its required `Lint, Test & Build` job, but
none establishes that every tracked Python file is syntactically valid for the job interpreter.
See `proposal.md` for the observed baseline failures and motivation.

## Goals / Non-Goals

**Goals:**

- Make parsing an explicit, reproducible CI gate using the same Python version that runs tests.
- Report all malformed tracked Python files in one invocation without creating repository artifacts.
- Restore the current package source to a parseable baseline before enforcing the gate.

**Non-Goals:**

- Add type checking, lint-rule changes, import-time execution, or bytecode compilation artifacts.
- Change runtime behavior while replacing invalid exception-handler syntax.
- Alter the required job's check-run or bookkeeping-only job-gating design.

## Decisions

### Use an in-repository CI helper and Python's parser directly

The helper will enumerate tracked `*.py` files and compile their decoded source in-memory with the
interpreter executing the helper. It will collect `SyntaxError` failures and emit each
repository-relative diagnostic before returning a non-zero exit status. This tests precisely the
contract while avoiding `compileall`'s bytecode output.

`compileall` was considered because it exposed the issue, but its normal operation writes
`__pycache__` files and its output format is less controlled for an actionable CI diagnostic.
Ruff and an import walk were rejected: Ruff does not provide this repository-wide interpreter
parse contract, and importing modules would execute package code.

### Gate the existing required job as an individual step

The workflow will invoke the helper after installation and before pytest, with the existing
`needs.changes.outputs.bookkeeping == 'false'` step condition. It will not add a job-level
condition or a new required context, preserving the branch-protection guarantee documented in
the workflow and tested by `tests/test_required_check_jobs.py`.

### Repair the baseline as mechanical syntax-only edits

Every current parse failure is a Python-2-style multi-exception handler. Each affected module will
be converted to the equivalent parenthesized Python 3 handler, grouped by package with the module's
existing tests used to confirm unchanged behavior. The remediation is part of this change because
otherwise the new gate would fail immediately on the base repository.

## Risks / Trade-offs

- [Tracked generated or fixture Python is intentionally invalid] → The helper's tracked-file
  selection makes that exception visible; any necessary exclusion must be explicit, narrowly
  documented, and covered by a test rather than silently skipped.
- [Large baseline remediation obscures a behavioral change] → Limit edits to exception-handler
  syntax and run the existing affected test modules plus the full suite.
- [Future CI matrix Python changes parser behavior] → The helper deliberately uses the job's
  interpreter, so the gate detects the compatibility change at the same point CI does.

## Migration Plan

1. Add and test the side-effect-free helper.
2. Convert the existing invalid handlers and run their affected tests until the helper passes.
3. Wire the helper into the existing CI job and verify workflow-step coverage.
4. Run the normal lint, test, golden regression, build, and parse commands before merge.

Rollback consists of reverting the workflow step and helper together; the syntax-only Python 3
repairs remain valid independently.

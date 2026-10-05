## Context

The existing workflow runs Ruff, pytest, and build in its required `Lint, Test & Build` job, but
none establishes that every tracked Python file is syntactically valid for the job interpreter.
See `proposal.md` for the motivation.

## Goals / Non-Goals

**Goals:**

- Make parsing an explicit, reproducible CI gate using the same Python version that runs tests.
- Report all malformed tracked Python files in one invocation without creating repository artifacts.

**Non-Goals:**

- Add type checking, lint-rule changes, import-time execution, or bytecode compilation artifacts.
- Alter the required job's check-run or bookkeeping-only job-gating design.
- Impose a style preference on exception-handler syntax (PEP 758 vs the parenthesized form).

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

### No baseline source remediation is required

An earlier draft of this change treated the repository's PEP 758 unparenthesized multi-exception
handlers as Python-2-style syntax defects and planned to rewrite roughly 58 modules into the
parenthesized form. That reading came from observing `python3 -m compileall` under an interpreter
older than the project's floor. Parsing the same 482 tracked Python files in-memory yields 58
syntax errors under Python 3.12 and zero under Python 3.14, while `pyproject.toml` declares
`requires-python = ">=3.14"` and the required job installs 3.14.

The handlers are therefore valid, intentional Python 3.14 syntax, and the baseline already
satisfies the gate. This change adds the gate only and edits no package source. Making scripts
directly executable on a host whose unversioned `python3` predates 3.14 is a separate concern with
its own change (`python314-shebang-policy`); this change does not pin interpreter shebangs.

## Risks / Trade-offs

- [Tracked generated or fixture Python is intentionally invalid] → The helper's tracked-file
  selection makes that exception visible; any necessary exclusion must be explicit, narrowly
  documented, and covered by a test rather than silently skipped.
- [Future CI matrix Python changes parser behavior] → The helper deliberately uses the job's
  interpreter, so the gate detects the compatibility change at the same point CI does.
- [A contributor runs the helper locally under the wrong interpreter] → The check deliberately
  parses with the executing interpreter, so a host whose `python3` is older than the project floor
  produces failures that do not reproduce in CI. Such a report is a version signal, not a source
  defect; confirm the interpreter before acting on it.

## Migration Plan

1. Add and test the side-effect-free helper.
2. Wire the helper into the existing CI job and verify workflow-step coverage.
3. Run the normal lint, test, golden regression, build, and parse commands before merge.

Rollback consists of reverting the workflow step and helper together.

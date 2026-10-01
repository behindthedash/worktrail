## Context

The package declares `requires-python = ">=3.14"`, and its current source uses PEP 758's
unparenthesized multi-exception syntax. A console-script entry point already runs under the
installed tool interpreter, but a file launched through its shebang instead follows whichever
interpreter the host calls `python3`. That distinction made the Stop hook fail silently on a host
where `python3` was 3.12; PR #1364 fixed that invocation only.

The repository already checks shebangs against executable index modes in
`scripts/ci/check_shebang_exec_bits.py`. It reads Git's index so WSL and CI evaluate the same
shipped content, and it is already run by the required CI job.

## Goals / Non-Goals

**Goals:**

- Make every currently executable PEP 758-bearing file select Python 3.14 when run directly.
- Prevent a future generic-`python3` shebang from being paired with the same syntax.
- Keep the check index-based and available to local developers through the existing CI command.

**Non-Goals:**

- Pin every Python shebang in the repository regardless of the syntax it uses.
- Change console-script generation, package metadata, Git executable modes, or CI's Python matrix.
- Detect every possible present or future Python-version-specific construct; the guard covers the
  concrete PEP 758 incompatibility that motivated this change.

## Decisions

### Use `#!/usr/bin/env python3.14` for the affected executable files

This names the minimum interpreter that can parse the files while continuing to use `env` and
`PATH` resolution, as the existing shebang convention does. It matches the successful Stop-hook
fix's command-side interpreter selection. A bare `python3` is rejected for this affected class
because its version is host-dependent.

### Extend the existing index-based checker

The shebang/exec-bit checker will additionally inspect indexed executable Python source for the
specific combination of a generic `python3` shebang and PEP 758 multi-exception syntax. This
avoids a second CI command and ensures the result does not vary with uncommitted worktree content
or WSL's executable-bit handling. Its tests will use temporary Git repositories, as its existing
mode checks do.

### Keep the policy narrow

The checker will not reject generic shebangs merely because the repository's supported minimum is
Python 3.14. Some executable files may remain compatible with older interpreters, and expanding
the pin without evidence would change their direct-execution contract unnecessarily. PEP 758 is
the verified parse-time hazard and the guard makes that precise condition durable.

## Risks / Trade-offs

- [A new Python 3.14-only construct is not PEP 758] → It is outside this narrow checker; the
  author must apply the direct-execution policy when introducing it, and a future change can add
  an evidenced detector.
- [`python3.14` is unavailable on a host] → Direct execution fails with a clear command lookup
  error instead of attempting an incompatible interpreter and failing with a parse error.
- [A mechanical 38-file edit changes an unintended line] → Limit the remediation to line-one
  shebangs, preserve index modes, and verify the affected source plus the full existing gates.

## Migration Plan

1. Add tests and the index-based PEP 758/shebang policy check.
2. Replace the 38 generic shebangs in the verified affected set with `python3.14`.
3. Run the checker, focused tests, full tests, lint/format, shebang/mode verification, and the
   orchestrator regression.

Rollback is a revert of the checker and the corresponding shebang-only edits; no data migration
or runtime state is involved.

# Security Policy

## Reporting a vulnerability

Report privately through GitHub's
[private vulnerability reporting](https://github.com/behindthedash/worktrail/security/advisories/new)
rather than opening a public issue.

Please include what the issue lets an attacker do, the affected version
(`pip show worktrail`), and the smallest reproduction you have.

## What this package does, and what that implies

`worktrail` orchestrates code-writing agents. By design it:

- creates git worktrees and branches, and commits to them
- pushes branches and opens pull requests via `gh`
- spawns headless agent subprocesses that run arbitrary commands in a worktree

It is intended to run against repositories you control, with an agent CLI you
have authenticated yourself. **Do not point it at untrusted spec or task input**:
a task definition is a set of instructions handed to an agent with shell access,
so it should be treated with the same care as a shell script from that source.

Reports that amount to "an agent given a malicious task did what the task said"
are working as designed. Reports that a malicious *spec* can escape the
guardrails that are meant to hold — the worker path deny-list
(`orchestrator/verify.py`'s `FORBIDDEN_WORKER_PATH_PREFIXES`), worktree
isolation, or the run lock — are in scope and worth reporting.

## Supported versions

The latest released version only. This package is pre-1.0 and consumers pin it
explicitly; fixes land on `main` and ship in the next version bump.

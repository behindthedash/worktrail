## 1. Canonical-checkout guard + core tests

Implements requirement: Codex dispatch refuses a canonical-checkout working root or
additional directory.

- [x] 1.1 In `src/worktrail/router/skill_dispatch.py`, add helpers that resolve a target
      directory to `(worktree_root, canonical_root)` via
      `git rev-parse --show-toplevel --git-common-dir` (mirroring `flagged-checkout.cjs`'s
      `getWorktreeIdentity`), walk upward to the nearest existing ancestor first (mirroring
      `nearestExistingAncestor`) since a `-C`/`--add-dir` target may not exist yet, and return
      whether a resolved target is a canonical (non-worktree) checkout root
      (`worktree_root == canonical_root`). In the codex branch of `build_command()`
      (`skill_dispatch.py:502-510`), resolve `cwd` (if set) and every `add_dirs` entry with
      these helpers before appending them to the argv, and raise (identifying the offending
      target path and repository name) instead of returning a command when any resolved
      target is a canonical checkout root. Leave the `claude` and `opencode` branches
      untouched. Add matching coverage in `tests/router/test_skill_dispatch.py`: a `cwd`
      pointing at a canonical (non-worktree) checkout raises (temporary git repo fixture); a
      `cwd` pointing at a linked worktree (`git worktree add`) builds the command normally; an
      `add_dirs` entry pointing at a canonical checkout raises even when `cwd` is a valid
      worktree; a `cwd`/`add_dirs` target outside any git repository builds the command
      normally (no false positive).

## 2. Escape hatch, remaining tests, spec sync

Implements requirement: Escape hatch overrides the canonical-checkout refusal.

- [x] 2.1 Define a worktrail-scoped environment variable (e.g.
      `WORKTRAIL_CODEX_CANONICAL_CHECKOUT_ALLOW`) that, when set to a truthy value, skips the
      1.1 refusal and builds the command normally; document it in `build_command()`'s
      docstring alongside the existing `-s danger-full-access` explanation. Add matching
      coverage in `tests/router/test_skill_dispatch.py`: the escape hatch env var set to a
      truthy value allows a target that would otherwise be refused; the `claude` and
      `opencode` branches are unaffected by a canonical-checkout target (guard is codex-only).
      Confirm `specs/codex-canonical-checkout-guard/spec.md`'s scenarios all have
      corresponding test coverage from 1.1/2.1. Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both
      are green.

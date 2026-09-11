## 1. Canonical-checkout detection

- [ ] 1.1 Add a small helper in `src/worktrail/router/skill_dispatch.py` that resolves a
      target directory to `(worktree_root, canonical_root)` via
      `git rev-parse --show-toplevel --git-common-dir` (mirroring
      `flagged-checkout.cjs`'s `getWorktreeIdentity`), returning `None` when the target is not
      inside any git repository or the command fails.
- [ ] 1.2 Add a helper that walks upward from a target path to the nearest existing ancestor
      directory before resolving it, since a `-C`/`--add-dir` target may not exist yet
      (mirroring `flagged-checkout.cjs`'s `nearestExistingAncestor`).
- [ ] 1.3 Add a helper that returns whether a resolved target is a canonical (non-worktree)
      checkout root: `worktree_root == canonical_root`.

## 2. Guard integration in `build_command()`

- [ ] 2.1 In the codex branch of `build_command()` (`skill_dispatch.py:502-510`), before
      appending `-C`/`--add-dir` values to the argv, resolve `cwd` (if set) and every entry in
      `add_dirs` using the helpers from section 1.
- [ ] 2.2 If any resolved target is a canonical checkout root and the escape hatch (section 3)
      is not set, raise an error identifying the offending target path and the repository name,
      instead of returning a command.
- [ ] 2.3 Confirm the `claude` and `opencode` branches are untouched — the guard applies only
      to the codex branch.

## 3. Escape hatch

- [ ] 3.1 Define a worktrail-scoped environment variable (e.g.
      `WORKTRAIL_CODEX_CANONICAL_CHECKOUT_ALLOW`) that, when set to a truthy value, skips the
      refusal in 2.2 and builds the command normally.
- [ ] 3.2 Document the override in `build_command()`'s docstring, alongside the existing
      `-s danger-full-access` explanation.

## 4. Tests

- [ ] 4.1 Add `tests/router/test_skill_dispatch.py` coverage: `cwd` pointing at a canonical
      (non-worktree) checkout raises, using a temporary git repo fixture.
- [ ] 4.2 Add coverage: `cwd` pointing at a linked worktree (created via `git worktree add`)
      builds the command normally.
- [ ] 4.3 Add coverage: an `add_dirs` entry pointing at a canonical checkout raises, even when
      `cwd` itself is a valid worktree.
- [ ] 4.4 Add coverage: a `cwd`/`add_dirs` target that is not inside any git repository builds
      the command normally (no false positive).
- [ ] 4.5 Add coverage: the escape hatch env var set to a truthy value allows a target that
      would otherwise be refused.
- [ ] 4.6 Add coverage: the `claude` and `opencode` branches are unaffected by a
      canonical-checkout target (guard is codex-only).
- [ ] 4.7 Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both are
      green.

## 5. Spec sync

- [ ] 5.1 Confirm `specs/codex-canonical-checkout-guard/spec.md`'s scenarios all have
      corresponding test coverage from section 4.

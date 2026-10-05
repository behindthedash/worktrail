## 1. Converge the bundled sync skill on the target repo's spec shape

- [ ] 1.1 In `skills/openspec-sync-specs/SKILL.md`, rework the "Create new main spec if
      capability doesn't exist yet" step (4d) and the trailing guardrails so a new main spec
      takes the heading shape the target repository already uses: read the other
      `openspec/specs/*/spec.md` files and, when they carry no top-level
      `# <capability> Specification` title line, begin the new spec with `## Purpose` and add no
      title line; write the title line only when those existing specs already carry one, or when
      there is no canonical spec to converge on. Drop the claim that `openspec validate` requires
      the title line and replace it with the true statement that a title-less main spec validates
      clean. Add a separate guardrail that an **existing** main spec is never restyled: no added,
      removed, reworded, or repositioned title line, and only requirement/scenario content the
      delta names is edited. Leave the delta-vs-main header rule (`## Requirements`, not
      `## ADDED Requirements`) and the validate-and-fix step intact.
      In `tests/test_plugin_surface.py`, add assertions that read
      `skills/openspec-sync-specs/SKILL.md` and fail if the file loses the convergence rule (new
      spec matches the repo's existing shape) or the no-restyle rule (an existing spec's title
      line is never added, reworded, or moved), and fail if the false `openspec validate`/
      title-line justification returns.
      (Requirements: A new main spec converges on the repository's existing shape; The sync never adds a title line to an existing main spec)
      files: skills/openspec-sync-specs/SKILL.md, tests/test_plugin_surface.py

## 2. Reject a title line added to an existing canonical spec

- [ ] 2.1 In `src/worktrail/drain/drain.py`, add a module-level predicate that reads the
      remediation worktree's draft with `git diff --unified=0` (or the equivalent numstat plus
      `--diff-filter`) over `openspec/specs/*/spec.md`, and reports a rejection when the diff
      shows an added top-level `# ... Specification` line on a path whose status is *modified*,
      not *added*. Call it in `_run_sync_pending` after the spawn returns `exit_code == 0` and
      the existing `git status --porcelain` check shows a dirty worktree, and before `git add
      -A`: on a reported rejection, raise a `RuntimeError` naming the offending spec path so the
      existing per-finding isolation logs it and no commit, push, or PR happens for that finding.
      Leave the already-open-PR fast path, the no-diff re-check, the commit/push/`_land_remediation_pr`
      sequence, the result dict shape, and the finally-teardown unchanged.
      In `tests/drain/test_drain.py`, extend the real-git sync-pending coverage: a spawner that,
      for an OpenSpec finding whose canonical `openspec/specs/<cap>/spec.md` exists at base,
      writes a draft that prepends `# <cap> Specification` above the existing `## Purpose` line
      — assert the action raises (no commit, no push, no `land_pr` call) and a second finding in
      the same sweep still completes through the generic sweep; a draft that creates a brand-new
      canonical spec carrying a title line — assert it lands as before; and a draft that edits
      only content beneath the existing spec's heading — assert it lands as before.
      (Requirements: Sync-pending remediation)
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 3. Verification

- [ ] 3.1 [depends: 1.1, 2.1] [e2e] Run `PYTHONPATH=src pytest -q tests/drain/test_drain.py
      tests/test_plugin_surface.py`, then `PYTHONPATH=src pytest -q`,
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      `python3 scripts/ci/ruff_pinned.py check .`, and
      `python3 scripts/ci/ruff_pinned.py format --check .`. Run
      `openspec validate sync-draft-converge-main-delta --strict` and
      `worktrail-compile openspec/changes/sync-draft-converge-main-delta`.

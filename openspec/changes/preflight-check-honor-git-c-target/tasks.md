## 1. Resolve the gate target from the gated command

- [ ] 1.1 In `src/worktrail/router/preflight.py`, add `command_target_repo(command: str | None)
      -> Path | None`: `shlex.split` the command (returning None on `ValueError`), require the
      first token to be `git`, then walk the global options before the first non-option token
      (the subcommand), collecting `-C`/`--git-dir`/`--work-tree` in both `--opt value` and
      `--opt=value` forms. Return None when none were seen. Otherwise replay exactly those
      options to `git <options> rev-parse --show-toplevel` (via `subprocess.run`, mirroring
      `_git()`'s timeout/`check=False` handling) and return the resolved `Path` on success,
      None on any failure. Wire it into `check()`: resolve `target = command_target_repo(command)
      or repo` as the first step, use `target` for every subsequent decision (`repo.is_dir()`,
      `duplicate_work_warning`, `dirty_tree_reason`, `load_policy`, `is_docs_only`, `tree_state`,
      `read_marker`, and the `worktrail-preflight run --repo` text in the final deny reason), and
      have `_verdict` add `"target_repo": str(target)` only when the command redirected it.
      Update `check()`'s docstring and the module docstring's `check` paragraph to state the new
      target-resolution rule.
      (Requirements: Preflight check gates the repo the command targets; Non-redirecting
      commands keep the --repo target.)
      In `tests/router/test_preflight.py`, add a `TestCommandTargetRepo` case
      covering both requirements against real temporary git repos (reuse the file's existing
      repo/marker fixtures): `git -C <worktree> push` allows on a marker recorded for that
      worktree while the `--repo` cwd has none, and reports `target_repo`; the same command
      denies with the dirty-tree reason when that worktree is dirty even though `--repo` is
      clean and marked; a real linked worktree addressed by `--git-dir=<main>/.git/worktrees/
      <name>` resolves to the worktree root; composed `git -C <parent> -C <child>` resolves like
      git; and the fallback cases each compute against `--repo` with no `target_repo` key --
      no command, `gh pr create` (existing label enforcement still applies), bare `git push`,
      `-C` only after the subcommand, an unparseable command, and `-C` on a non-repo path.
      files: src/worktrail/router/preflight.py, tests/router/test_preflight.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_preflight.py`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`. Run `openspec validate
      preflight-check-honor-git-c-target --strict` and `worktrail-compile
      openspec/changes/preflight-check-honor-git-c-target`.

## 1. Un-quarantine command

- [x] 1.1 Add `src/worktrail/orchestrator/resume_group.py`: a `main(argv=None) -> int` CLI
      (`--repo`, `--spec`, `--group` repeatable, `--all-resumable`, `--dry-run`, `--json`)
      resolving the run journal via `live.journal_path_for(repo, spec_rel)`. Expose the pure
      helper `select_groups(journal, names, all_resumable) -> (selected, problems)`: a named
      group with no record, or with a `state` other than `QUARANTINED`, becomes a problem entry
      (never a silent skip); `all_resumable` selects only records whose `state` is
      `QUARANTINED` and whose `quarantine_reason` equals
      `integrate.QUARANTINE_BUDGET_EXHAUSTED`. Expose `clear_groups(journal, selected) -> dict`
      which pops each selected name from `journal["groups"]`, pops `integrate_complete`, and
      appends one `resumed_quarantines` entry per cleared group (`group`,
      `quarantine_reason`, `quarantine_detail`, `pr_url`, `cleared_at` ISO-8601), preserving
      any existing entries; it SHALL NOT touch records that were not selected. `main` refuses
      (non-zero, no write) when the journal is missing/unparseable, when
      `journal_selfcheck._runlock_held(journal_path.with_suffix(".lock"))` is true, or when
      `select_groups` reported any problem; persists with
      `progress.atomic_write_text(path, json.dumps(journal, indent=2, sort_keys=True) + "\n")`
      only when not `--dry-run`; and on success prints the cleared group names plus the
      `worktrail-live full-real --repo <repo> --spec <spec> --resume` invocation to run next.
      Register `worktrail-resume-group = "worktrail.orchestrator.resume_group:main"` in
      `pyproject.toml`'s `[project.scripts]`.
      (Requirements: A named quarantined group can be returned to the resume path;
      Budget-exhausted quarantines can be cleared in bulk; The prior quarantine is recorded,
      not discarded; The command refuses to edit a journal a live run owns; The next step is
      reported and dry-run writes nothing.)
      In `tests/orchestrator/test_resume_group.py` (tmp_path-hermetic journals, no network,
      no git): cover clearing a QUARANTINED group while a MERGED and an OPEN record stay
      byte-identical; a non-QUARANTINED named group and an absent group/journal each leaving
      the file untouched with a non-zero exit; `--all-resumable` selecting only the
      `budget_exhausted` record and reporting "nothing to clear" when there is none;
      `resumed_quarantines` carrying the prior reason/detail/pr_url and appending on a second
      clear; a monkeypatched `_runlock_held` returning True leaving the journal unmodified;
      and `--dry-run` printing the same selection with the file unchanged.
      files: src/worktrail/orchestrator/resume_group.py, pyproject.toml, tests/orchestrator/test_resume_group.py

## 2. Surface the recovery action in quarantine triage

- [x] 2.1 In `src/worktrail/router/quarantine_selfcheck.py`, extend `main`'s
      non-JSON output only: name `worktrail-resume-group` as the recovery action in both the
      `findings` header (explicit `--group <name>` after fixing the branch) and the
      `resumable` header (`--all-resumable`), including the repo and spec id needed to run it.
      `check_repo`, `reconcile_finding`, the JSON payload, and the exit code are unchanged.
      (Requirements: Quarantine triage points at the recovery command.)
      In `tests/router/test_quarantine_selfcheck.py`, add tests asserting the non-JSON output
      for a human-triage finding and for a resumable finding each mention
      `worktrail-resume-group`, and that the JSON payload and exit code for a journal with a
      reconcilable, a resumable, and a human-triage quarantine are unchanged.
      depends: 1.1
      files: src/worktrail/router/quarantine_selfcheck.py, tests/router/test_quarantine_selfcheck.py

## 3. Route E front-door pointer

- [ ] 3.1 In `skills/worktrail-go/references/routes.md`, extend Route E step 4's
      quarantined-orchestrator-groups sentence to cite `worktrail-resume-group`: fix the task
      branch first, then clear the group and re-run `full-real --resume` rather than
      hand-landing the PR and hand-running checkbox-sync. Keep the existing
      `#worktree-lifecycle` citation intact.
      (Requirements: Quarantine triage points at the recovery command.)
      depends: 1.1
      files: skills/worktrail-go/references/routes.md

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator/test_resume_group.py
      tests/router/test_quarantine_selfcheck.py tests/test_plugin_surface.py`, then
      `PYTHONPATH=src pytest -q`,
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`, and
      `python3 scripts/ci/check_shebang_exec_bits.py`. Then run
      `openspec validate resume-quarantined-orchestrator-groups --strict` and
      `worktrail-compile openspec/changes/resume-quarantined-orchestrator-groups`.
      depends: 2.1, 3.1

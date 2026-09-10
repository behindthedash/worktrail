## 1. Delta pre-check in the close-stale command (`close-stale-archive-delta-precheck`)

- [x] 1.1 Implement Requirements: Close-stale runs a delta pre-check before
      mutating the worktree; Close-stale refuses on archived-sibling delta
      drift unless explicitly allowed. In
      `src/worktrail/router/close_stale_openspec.py`, add a `_delta_precheck`
      step to `flip_and_archive` that runs after `tasks.md` is located and
      before any `set_task_checked` call: run `openspec validate <change_id>
      --strict` in the worktree, compare every delta under the change's
      `specs/**/spec.md` against `openspec/specs/<capability>/spec.md` for
      missing MODIFIED/REMOVED/RENAMED-FROM targets (reusing
      `dashboard._iter_openspec_delta_sections`, `_OPENSPEC_REQUIREMENT`,
      `_OPENSPEC_RENAME`), and call `dashboard._openspec_delta_drift`. Add an
      `allow_delta_drift` keyword and the `--allow-delta-drift` CLI flag;
      always populate `result["precheck"]` per design.md D2; refuse with a
      class-naming `error` and no mutation per D1/D3.
      In `tests/router/test_close_stale_openspec.py`, mock `openspec validate`
      alongside the existing `openspec archive` mock and cover: validate
      failure leaves `tasks.md` unchanged and never runs archive; a MODIFIED
      requirement missing from the canonical spec refuses; a REMOVED and a
      RENAMED-FROM missing target refuses; ADDED-only under a new capability
      passes; archived-sibling drift (patch `_openspec_delta_drift`) refuses
      without the flag and proceeds with `drift_allowed` true when allowed;
      the flag does not bypass validate failure or missing targets; and the
      pass-through case still flips and archives with `validate_ok` true.
      files: src/worktrail/router/close_stale_openspec.py, tests/router/test_close_stale_openspec.py

## 2. Skill row (`close-stale-archive-delta-precheck`)

- [x] 2.1 Implement Requirement: The worktrail-go close-stale dispatch row
      documents the pre-check. In `skills/worktrail-go/SKILL.md`, extend the
      `close-stale` row's openspec branch so it says the command runs
      `openspec validate --strict` plus a delta-vs-canonical pre-check before
      flipping any checkbox, that a refusal leaves the worktree unmodified and
      reports the offending requirement in the JSON `precheck` field, and that
      `--allow-delta-drift` exists for the archived-sibling drift class only.
      In `tests/test_plugin_surface.py`, add a test that locates the
      `close-stale` row and asserts it names `openspec validate` and
      `--allow-delta-drift`.
      files: skills/worktrail-go/SKILL.md, tests/test_plugin_surface.py

## 3. Verification

- [x] 3.1 [e2e] Run `pytest -q tests/router/test_close_stale_openspec.py
      tests/test_plugin_surface.py`, then `pytest -q` and `python3 -m
      worktrail.orchestrator.orchestrate check`; confirm all pass.
      depends on 1.1, 2.1. Verification-only; no file changes expected.
- [x] 3.2 [e2e] Run `openspec validate close-stale-archive-delta-precheck
      --strict` and `worktrail-compile
      openspec/changes/close-stale-archive-delta-precheck`; confirm both pass.
      depends on 1.1, 2.1. Verification-only; no file changes expected.

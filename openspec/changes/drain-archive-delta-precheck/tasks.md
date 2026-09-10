## 1. Delta pre-check in the drain archive sweep (`drain-stage-remediation-table`)

- [ ] 1.1 Implement Requirement: OpenSpec change archive remediation (delta
      pre-check paragraph and its four new scenarios). In
      `src/worktrail/drain/drain.py`, import `_delta_precheck` from
      `..router.close_stale_openspec` next to the existing `flip_and_archive`
      import, and in `_run_openspec_archive` call it after the unchecked-task
      refusal and before the `openspec archive -y` subprocess, with
      `allow_delta_drift=False` and the caller's `timeout`; when it returns an
      error, raise `RuntimeError(f"refusing to archive {spec_id} (in {wt}):
      {error}")` (design.md D1-D3). Update the function docstring to name the
      new refusal class.
      In `tests/drain/test_drain.py`, extend
      `_fake_gh_and_openspec_archive_subprocess_run` with an `openspec
      validate` branch returning exit 0 and patch
      `dashboard._openspec_delta_drift` to `[]` so the existing archive tests
      keep passing; then add tests that (a) a non-zero `openspec validate`
      raises with the validate output and never runs `openspec archive`,
      commit, push, or `land_pr`; (b) a MODIFIED requirement missing from
      `openspec/specs/<capability>/spec.md` raises naming the requirement
      with the same no-side-effects guarantee; (c) a patched drift finding
      raises naming the archived change id with no override path; (d) the
      unchecked-task refusal still fires first, before `openspec validate`
      is invoked; and (e) the pass-through case still archives and opens a
      PR.
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py

## 2. Drain skill (`drain-stage-remediation-table`)

- [x] 2.1 In `.claude/skills/drain/skill.md`, extend the remediation-sweeps
      bullet so it states that the OpenSpec archive sweep runs
      `close_stale_openspec._delta_precheck` (`openspec validate --strict`
      plus the delta-vs-canonical and archived-sibling drift checks) after
      the unchecked-task refusal and before `openspec archive -y`, that any
      refusal raises and is logged as an `archive-openspec-change error:`
      line with no archive/commit/push/PR, and that drain deliberately has
      no `--allow-delta-drift` equivalent.
      files: .claude/skills/drain/skill.md

## 3. Verification

- [ ] 3.1 [e2e] Run `pytest -q tests/drain/test_drain.py
      tests/router/test_close_stale_openspec.py`, then `pytest -q` and
      `python3 -m worktrail.orchestrator.orchestrate check`; confirm all pass.
      depends on 1.1, 2.1. Verification-only; no file changes expected.
- [ ] 3.2 [e2e] Run `openspec validate drain-archive-delta-precheck --strict`
      and `worktrail-compile openspec/changes/drain-archive-delta-precheck`;
      confirm both pass.
      depends on 1.1, 2.1. Verification-only; no file changes expected.

## 1. Purpose check

- [x] 1.1 Add `src/worktrail/router/check_spec_purpose.py` with
      `check_changed_specs(repo: Path, changed_paths: list[str]) -> list[str]` and a `main()`.
      Select only paths matching `openspec/specs/<capability>/spec.md` (ignore
      `openspec/changes/**` delta specs and `docs/specs/**`); for each, read the `## Purpose`
      section body up to the next `## ` heading and return a failure message naming the
      capability when the section is absent, blank, or its first non-blank line begins with
      `TBD` (case-insensitive). A path listed in `changed_paths` that no longer exists on disk
      (deleted in the diff) is skipped. Explain the diff-scoping rationale in the module
      docstring, mirroring `check_clarification_integrity.py`. Add
      `tests/router/test_check_spec_purpose.py` covering: the archive placeholder fails; a
      missing `## Purpose` heading fails; a Purpose whose body is blank before the next heading
      fails; real prose passes; a `TBD`-containing Purpose that does not *start* with TBD
      passes; an unchanged placeholder spec not in `changed_paths` produces nothing; an
      `openspec/changes/**` delta spec path produces nothing; a deleted path produces nothing.
      (Requirements: A changed capability spec SHALL state a real Purpose; The check SHALL be
      scoped to the diff, never the whole tree.)
      files: src/worktrail/router/check_spec_purpose.py, tests/router/test_check_spec_purpose.py

- [x] 1.2 [depends: 1.1] Wire the check into `src/worktrail/router/pre_pr_gate.py`: import
      `check_changed_specs as check_spec_purpose_failures`, add
      `SPEC_PURPOSE_DRIFT_EXIT = 6` beside the other exit constants, and run it in
      `run_drift_checks` after the req/AC coverage block, printing
      `PRE-PR GATE: FAIL — capability spec(s) with no Purpose:` plus one line per failure and a
      remediation hint naming `worktrail-check-spec-purpose`, then returning the new constant.
      Update the module docstring's per-check paragraphs, its `Exit codes:` list, and the
      `--checks-only` paragraph (four deterministic checks become five). Register
      `worktrail-check-spec-purpose = "worktrail.router.check_spec_purpose:main"` in
      `pyproject.toml`'s `[project.scripts]`, keeping the block alphabetized. Extend
      `tests/router/test_pre_pr_gate.py` with a `run_drift_checks` case returning 6 for a
      changed placeholder spec and a case confirming an unchanged verdict when the Purpose is
      real.
      (Requirements: The pre-PR gate SHALL fail on Purpose drift with its own exit code.)
      files: src/worktrail/router/pre_pr_gate.py, pyproject.toml, tests/router/test_pre_pr_gate.py

## 2. Backfill

- [x] 2.1 Replace the placeholder Purpose in every `openspec/specs/*/spec.md` matched by
      `grep -rl "TBD - created by archiving change" openspec/specs/` (40 files as of
      2026-09-20) with a one-to-three-sentence Purpose read out of that spec's own
      requirements, in the shape the already-documented specs use (see
      `openspec/specs/drain-operator-config/spec.md` and
      `openspec/specs/codex-sandbox-confinement/spec.md`): what the capability does, and the
      failure it prevents. Change nothing outside the `## Purpose` section — no requirement,
      scenario, or heading. Verify with `grep -rl "TBD - created by archiving change"
      openspec/specs/` returning nothing and `git diff -U0 openspec/specs/` showing only
      Purpose-section hunks.
      (Requirements: Existing placeholder Purposes SHALL be backfilled.)
      files: openspec/specs/

## 3. Verification

- [x] 3.1 [depends: 1.2, 2.1] [e2e] Run `PYTHONPATH=src pytest -q
      tests/router/test_check_spec_purpose.py tests/router/test_pre_pr_gate.py`, then
      `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, `python3 scripts/ci/ruff_pinned.py check .`
      and `python3 scripts/ci/ruff_pinned.py format --check .`. Then run `openspec validate
      archived-spec-purpose-backfill-and-guard --strict` and `worktrail-compile
      openspec/changes/archived-spec-purpose-backfill-and-guard`.

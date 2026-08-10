## 1. Requirement-coverage module

- [x] 1.1 Create `src/worktrail/conductor/req_coverage.py` implementing, per
      design D1/D3/D4: parsing of `### Requirement: <Name>` headers under
      `## ADDED Requirements` / `## MODIFIED Requirements` (never `## REMOVED
      Requirements`) across a change directory's `specs/**/spec.md` files;
      case-insensitive name-presence matching against that change's
      `tasks.md` text; the non-retroactive ratchet comparing declared names
      against the existing `openspec/specs/<capability-path>/spec.md` on
      disk (absent entirely for a brand-new capability, so every requirement
      counts as newly declared); and a format guard that is a no-op for a
      devkit-format (`docs/specs/<id>/`) spec directory.
- [ ] 1.2 Expose a single entry point (for example
      `find_uncovered_requirements(spec_dir, repo) -> list[str]`) that
      `compile.py` can call directly — no new console script, no
      `[project.scripts]` entry (per design D2, this check is not meant to
      run standalone against a corpus).

## 2. Compile-step gate integration

- [ ] 2.1 Compose the new check into `compile.py`'s `main()` alongside the
      existing `gaps`/`collisions` checks: compute uncovered requirements
      after `merged`/`gaps`/`collisions`, and combine into the same non-zero
      exit-code branch (both the `--json` and human-output paths) without
      touching `compile_run_plan()` itself.
- [ ] 2.2 Add a `_print_req_coverage_gap_error(uncovered: list[str]) -> None`
      helper mirroring `_print_scope_gap_error`/`_print_ordering_gap_error`'s
      shape and stderr-only convention, naming the uncovered requirements and
      pointing at `tasks.md`.
- [ ] 2.3 Ensure the compile-marker write (`write_marker`) is skipped when
      uncovered requirements are reported, exactly as it already is skipped
      for `gaps`/`collisions`, so `CI: Scope Check`'s fingerprint backstop
      also catches a requirement-coverage failure that was never actually
      re-checked.

## 3. Correct the devkit sibling's spec

- [x] 3.1 `specs/devkit-requirement-coverage-gate/spec.md`'s `## MODIFIED
      Requirements` delta (already authored in this change) corrects the
      "Format Scoping" requirement's false "equivalent coverage is already
      enforced by the existing scope-check" claim.

## 4. Tests

- [ ] 4.1 Add `tests/conductor/test_req_coverage.py` covering: a requirement
      declared under `## ADDED Requirements` with zero `tasks.md` reference
      (uncovered); the same requirement referenced by name in `tasks.md`
      (covered); a requirement declared only under `## REMOVED Requirements`
      (never enforced); a brand-new capability path (every requirement
      newly-declared and enforced); a `## MODIFIED Requirements` delta whose
      requirement name already exists in `openspec/specs/<path>/spec.md`
      (not newly declared, not enforced regardless of `tasks.md` content); a
      change with no `tasks.md` (every declared requirement uncovered); and
      a devkit-format spec directory (no-op, zero uncovered reported).
- [ ] 4.2 Extend `tests/conductor/test_compile.py` (or add a focused test
      module) asserting `main()` returns non-zero and prints the uncovered
      requirement name when a fixture change directory has an uncovered,
      newly-declared requirement, and `0` when it does not — following the
      existing `_print_scope_gap_error`/`_print_ordering_gap_error` test
      pattern in that file.

## 5. Validation and release hygiene

- [ ] 5.1 [e2e] Run this change's own `worktrail-compile` against its own
      change directory (`openspec/changes/openspec-req-ac-coverage-gate`)
      once implemented, confirming it reports zero uncovered requirements —
      the change must pass the gate it introduces. Also run the full gate
      (`PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`) and confirm both pass.
- [ ] 5.2 [cleanup] Decide and record the version-bump handling for this PR
      per `AGENTS.md` (standalone `chore: bump` commit versus carrying the
      `go:no-version-bump` label for a later batch bump).

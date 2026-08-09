## 1. Coverage check module

- [ ] 1.1 Create `src/worktrail/router/check_req_coverage.py` implementing, per
      design D1/D2/D3: declaration-anchored prefix-agnostic identifier discovery
      recognising both the markdown-table-row shape (`| REQ-001 | ... |`) and
      the bullet shape (`- REQ-NR001: ...`) while excluding mid-sentence prose
      cross-references; task-reference collection as the union of every task
      file's `reqs`, `ac-mapping`, and `imp-requirements` frontmatter arrays
      (reading `reqs` directly, without touching `FIELD_SCHEMA`, per D6);
      uncovered-identifier detection; the newly-declared-only base-ref
      comparison that makes enforcement non-retroactive; graceful skip with a
      reported reason when the base ref cannot be resolved; and a `main()` CLI
      exposing `--repo`, `--base-branch`, and the opt-in repo-wide audit mode —
      mirroring `check_clarification_integrity.py`'s module shape
      (`check_changed_specs`, `_resolve_base_ref`, `_changed_paths_via_git`,
      `main`).
- [ ] 1.2 Register `worktrail-check-req-coverage` in `[project.scripts]` in
      `pyproject.toml` so `tests/test_plugin_surface.py`'s entry-point lockstep
      check passes.

## 2. Pre-PR gate integration

- [ ] 2.1 Compose the coverage check into `src/worktrail/router/pre_pr_gate.py`
      alongside the existing spec-sync, clarification-integrity, and
      DoD-verification checks, using the next free exit code (`5`; `1`–`4` are
      taken) and returning without opening a PR on failure. Enforce only
      newly-declared identifiers, and leave the OpenSpec-format path untouched.

## 3. Tests

- [ ] 3.1 Add `tests/router/test_check_req_coverage.py` covering: table-row and
      bullet declaration shapes; a non-`REQ` prefix (e.g. `FR-`, `AUTHZ-`); an
      `NR` sub-namespaced identifier treated as distinct from its plain
      counterpart; a prose-only cross-reference that must NOT be treated as a
      declaration; coverage satisfied via `ac-mapping` alone; a spec with no
      task files; the ratchet cases (newly-declared uncovered → failure,
      pre-existing uncovered on a touched spec → pass, requirement added
      together with its coverage → pass, brand-new spec → all identifiers
      enforced); base-ref-unresolvable → skip-not-fail; and audit mode
      enumerating pre-existing gaps. Include a fixture derived from the real
      `084-automation-health-digest` shape whose expected uncovered set is
      `REQ-023..REQ-028`.
- [ ] 3.2 Extend `tests/router/test_pre_pr_gate.py` with a real-git-repo case
      asserting the gate returns the coverage exit code when a diff declares an
      uncovered identifier, and `0` when it does not — following the existing
      `TestClarificationIntegrityGate` pattern in that file.

## 4. Validation and release hygiene

- [ ] 4.1 [e2e] Run the repo's full gate (`PYTHONPATH=src pytest -q` and `PYTHONPATH=src
      python3 -m worktrail.orchestrator.orchestrate check`) and confirm both
      pass; additionally run the new CLI's audit mode against a real devkit
      corpus and confirm it reports `REQ-023..REQ-028` for
      `084-automation-health-digest`, proving the check catches the originating
      defect rather than only its synthetic fixture.
- [ ] 4.2 Decide and record the version-bump handling for this PR per
      `AGENTS.md` (standalone `chore: bump` commit versus carrying the
      `go:no-version-bump` label for a later batch bump), so `CI: Version Bump
      Check` outcome is deliberate rather than incidental.

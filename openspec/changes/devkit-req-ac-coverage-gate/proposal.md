## Why

A devkit-format spec can declare a requirement or acceptance criterion that **no
task ever claims**, and nothing in the toolchain notices. Verified directly in
`datalena docs/specs/084-automation-health-digest`: the main spec doc declares
`REQ-001..REQ-030` plus `REQ-NR001..NR007`, but set-differencing those against
every ID referenced in that spec's `tasks/TASK-*.md` frontmatter leaves
`REQ-023..REQ-028` with **zero** task references. `worktrail-check-spec-sync`
reports that spec clean, because `check_spec_sync.py` only compares task-plan
summary-table status against task frontmatter status (Check A) and the
parent-spec `Status` header against completeness (Check B) — it never inspects
requirement coverage at all.

The failure mode is silent and repeats: a spec amendment adds requirements, the
author writes tasks for some of them, and the uncovered ones are discovered
months later by a human grepping by hand. Spec 084's own
`traceability-matrix.md` documents `REQ-023..028` as a known gap (DEC-009), and
the same session that captured this work nearly introduced an identical gap for
a fresh `REQ-029/030` addition — caught only by a manual cross-check.

OpenSpec-format changes already get an equivalent guarantee from
`worktrail-compile`'s scope-check, which exists because an under-scoped
`tasks.md` reaching the orchestrator caused a real incident (datalena PR
#2128 → #2130). Devkit-format specs — the older format, still the majority of
the spec corpus across consuming repos — have no equivalent gate.

## What Changes

- Add a **requirement/AC coverage check** that parses the requirement and
  acceptance-criterion IDs a devkit spec declares in its own main doc, joins
  them against the union of every `reqs:`, `ac-mapping:`, and
  `imp-requirements:` array in that spec's `tasks/TASK-*.md` frontmatter, and
  reports any declared ID with zero task references.
- The check is **prefix-agnostic**. It discovers IDs from the spec's own
  declaration tables rather than matching a fixed `REQ-`/`AC-` enum, because
  the real corpus uses an open-ended namespace (`REQ`, `CHG`, `FR`, `AUD`,
  `AUTHZ`, `MIG`, `GOV`, and ~100 more prefixes, plus `NR` variants of the form
  `REQ-NR003`).
- **Enforcement is a non-retroactive ratchet**: the gate fails only on
  requirement IDs *newly added in the current diff* relative to the base ref.
  Pre-existing gaps in the legacy corpus do not fail unrelated PRs, and no
  baseline file or allowlist has to be created or maintained. This is what
  makes the gate adoptable on day one across repos whose specs already contain
  known gaps. Rationale and rejected alternatives are recorded in `design.md`.
- Add an opt-in **repo-wide audit mode** (CLI only, never part of the blocking
  gate) so a deliberate cleanup sweep can enumerate every pre-existing gap
  across a spec corpus.
- Wire the check into `pre_pr_gate.py` alongside the existing spec-sync,
  clarification-integrity, and DoD-verification checks, with its own exit code.
- Register the new console script in `[project.scripts]`, satisfying
  `tests/test_plugin_surface.py`'s lockstep check.

**Not breaking.** The gate is additive and, by the ratchet rule above, a no-op
on every spec whose requirement set is unchanged by the diff.

## Capabilities

### New Capabilities
- `devkit-requirement-coverage-gate`: validates that every requirement and
  acceptance-criterion ID a devkit-format spec declares in its main doc is
  referenced by at least one of that spec's task files, enforced at the pre-PR
  gate for newly-added IDs and available as a repo-wide audit on demand.

### Modified Capabilities
(none — no existing `openspec/specs/` capability covers requirement-to-task
coverage. `docs/specs/001-task-ac-verification-gate` is a different capability:
it re-executes a task's own declared `dod-checks` assertions when that task
flips to `completed`, which by construction cannot detect a requirement that has
no task at all.)

## Impact

- **New**: `src/worktrail/router/check_req_coverage.py`, mirroring
  `check_clarification_integrity.py`'s shape (`check_changed_specs(repo,
  changed_paths)`, `_resolve_base_ref`, `_changed_paths_via_git`, `main()` with
  `--repo`/`--base-branch`).
- **Modified**: `src/worktrail/router/pre_pr_gate.py` — compose the new check
  and add the next free exit code (`1`–`4` are taken by spec-sync/scope-
  completeness, unconfigured, clarification-integrity, and DoD-verification).
- **Modified**: `pyproject.toml` — new `worktrail-check-req-coverage` entry
  point.
- **Possibly modified**: `src/worktrail/taskformats/devkit/schema.py` —
  `FIELD_SCHEMA` declares `ac-mapping` and `imp-requirements` but **not**
  `reqs`, which reaches the task dict via `source.py`'s passthrough list. Any
  schema-level work must account for that asymmetry rather than assuming `reqs`
  is already validated. Resolved in `design.md`.
- **No change** to the OpenSpec path: `worktrail-compile`'s scope-check keeps
  sole ownership of coverage for OpenSpec-format changes.
- Consuming repos (datalena, gracefully-giving-back, and others with
  `docs/specs/` trees) gain the gate automatically on their next
  `worktrail` upgrade; by the ratchet rule this does not retroactively fail
  their existing specs.

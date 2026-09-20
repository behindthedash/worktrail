## Why

`openspec archive` creates a capability's canonical `openspec/specs/<cap>/spec.md` with a
stub Purpose:

```
## Purpose
TBD - created by archiving change <change-name>. Update Purpose after archive.
```

Nothing ever replaces it. Confirmed in this repo on 2026-09-20:
`grep -rl "TBD - created by archiving change" openspec/specs/ | wc -l` -> **40** of
`ls openspec/specs | wc -l` -> **110** capability specs carry the placeholder verbatim at
`spec.md:4` (`queue-triage`, `model-tier-routing`, `brief-relatedness-judgment`,
`ci-check-classification`, ...). The handoff brief that raised this
(`20260919-181526-placeholder-purpose-lines-in-archived`) measured 36/106 one day earlier —
it grew by 4 in a single day, which is the claim "every archive adds another" demonstrated
rather than asserted.

The Purpose line is the only prose in a canonical spec that says *what the capability is for*.
Every other section is normative requirements. Agents (and humans) orienting on a capability —
`openspec show`, a spec-collision check, a reviewer deciding whether a new change belongs in an
existing capability — read a sentence that names the change that archived it and nothing about
the capability itself. With 40 of them the signal is not merely missing, it is uniformly
misleading: the placeholders all look alike, so a reader cannot tell an undocumented capability
from a documented one without opening the file.

Backfilling alone does not hold: the next archive reintroduces one. The repo already runs
deterministic drift checks at PR time (`pre_pr_gate.run_drift_checks` — spec sync,
clarification integrity, DoD verification, req/AC coverage), and this is the same shape of
defect, so the guard belongs there, scoped to specs **changed in the diff** for the same reason
the clarification-integrity check is: a repo-wide check would fail every PR until the backfill
lands, and the ratchet is what makes the fix durable.

No active change under `openspec/changes/` covers spec Purpose hygiene or this guard, so there
is no fold target; backfill and guard ship together as one change.

## What Changes

- New `src/worktrail/router/check_spec_purpose.py` exposing
  `check_changed_specs(repo, changed_paths) -> list[str]`: for each changed
  `openspec/specs/<cap>/spec.md`, fail when its `## Purpose` section is missing, empty, or
  begins with `TBD`. Console script `worktrail-check-spec-purpose` for standalone use.
- `pre_pr_gate.run_drift_checks` runs it as a fifth deterministic check, after req/AC coverage,
  with its own exit code **6**; `--checks-only` picks it up with the rest.
- All 40 existing placeholder Purpose lines are backfilled with a real one-to-three-sentence
  Purpose derived from each spec's own requirements, matching the shape of the specs that
  already have one (e.g. `drain-operator-config`, `codex-sandbox-confinement`).

## Capabilities

### New Capabilities
- `openspec-spec-purpose-hygiene`: a canonical capability spec states its own purpose, and a
  PR cannot merge one that does not.

### Modified Capabilities

## Impact

- `src/worktrail/router/check_spec_purpose.py` (new), `src/worktrail/router/pre_pr_gate.py`
  (wiring, exit code 6, module docstring), `pyproject.toml` (`[project.scripts]` entry).
- `tests/router/test_check_spec_purpose.py` (new), `tests/router/test_pre_pr_gate.py`.
- 40 `openspec/specs/*/spec.md` files, Purpose section only — no requirement text touched.
- PRs that add or edit a capability spec must write a Purpose; PRs that touch no
  `openspec/specs/**/spec.md` are unaffected.

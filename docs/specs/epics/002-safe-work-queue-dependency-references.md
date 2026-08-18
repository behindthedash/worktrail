# Epic 002: Safe Work-Queue Dependency References

**Status:** Proposed  
**Owner:** Worktrail maintainers  
**Origin:** Handoff `20260818-032528-harden-work-queue-py-s`  
**Incident:** A comma-joined `blocked-by` list item made genuinely blocked work eligible for automatic pickup on 2026-08-18

## Business objective

Prevent malformed work-queue dependency references from silently bypassing sequencing. Worktrail operators must be able to trust that `dashboard --auto` will not select work whose prerequisites are still active merely because a producer encoded several dependency IDs as one value.

The smallest complete outcome prevents the known malformed value at creation, evaluates malformed references conservatively at consumption, and tells an operator exactly which brief and value need repair. Syntactically valid references to deleted or retired briefs remain satisfied so stale queue history does not permanently deadlock work.

## Personas

- **Worktrail operator:** relies on automatic selection to respect active prerequisite work without manually auditing YAML.
- **Handoff producer:** needs an immediate, actionable CLI error when dependency arguments have the wrong shape.
- **Maintainer:** needs one dependency-reference contract shared by creation, resolution, warnings, and tests so safety does not depend on duplicated heuristics.

## Scope

- Define and test the accepted syntax for a single work-queue dependency identifier.
- Reject comma-joined `--blocked-by` CLI values with guidance to repeat the flag for each dependency.
- Distinguish malformed dependency values from well-formed references that resolve to no queue or picked brief.
- Treat malformed or ambiguous references as unresolved for eligibility and automatic pickup.
- Surface structured diagnostics in queue-list/dashboard data and human-readable output.
- Add regression coverage for the live comma-joined failure mode across creation, queue evaluation, and automatic selection.

## Non-goals

- Requiring every historical dependency reference to keep a corresponding brief forever.
- Changing the existing lenient behavior for a syntactically valid stale or deleted dependency ID.
- Automatically rewriting malformed hand-authored queue files, because splitting an arbitrary value can change intended dependency identity.
- Redesigning `related`, `watch`, triage, or task-DAG dependency formats.
- Specifying every feature in this epic up front; each feature enters Route C only when selected.

## Success metrics

- A comma-joined `--blocked-by` value fails before a brief is written and explains the repeated-flag form.
- A queued brief containing a malformed dependency reference is never reported eligible by automatic selection.
- Queue and dashboard output identify the affected brief and raw malformed value without requiring source inspection.
- Existing tests continue to prove that a well-formed dependency absent from both `queue/` and `picked/` is satisfied.
- Regression tests cover the original shape: three IDs supplied as one comma-delimited YAML list item while at least one real dependency remains active.

## Feature decomposition

### Feature 1 — Dependency-reference contract and producer validation

**Future spec id:** `work-queue-dependency-reference-contract`

Define a reusable validator for one dependency ID and apply it to `create_handoff.py`. The CLI rejects comma-delimited `--blocked-by` values before writing a brief and tells callers to pass one repeated flag per dependency. The Python API applies the same contract so non-CLI producers cannot recreate the malformed shape.

**Independent value:** newly created briefs cannot acquire the known corruption through the supported creation surface.

**Release evidence:** unit and CLI tests cover valid full IDs/prefixes, repeated flags, comma-joined values, whitespace/empty values, failure text, and absence of a partially written brief.

### Feature 2 — Conservative dependency resolution

**Future spec id:** `work-queue-conservative-dependency-resolution`

Introduce an explicit dependency-resolution result that separates `done`, `active`, `ambiguous`, `stale`, and `malformed`. Update `_dep_is_done()` / `_is_blocked()` consumers so only `done` and syntactically valid `stale` references are satisfied; malformed and ambiguous values remain blocked. Keep reference validation independent of whether a matching file currently exists.

**Independent value:** malformed hand-authored or legacy briefs cannot bypass dependency gates even before their files are repaired.

**Release evidence:** focused resolver tests preserve valid stale-ID leniency, cover queue/picked/done/ambiguous states, and reproduce the comma-joined incident with the real first dependency still active.

### Feature 3 — Operator diagnostics and auto-pick regression guard

**Future spec id:** `work-queue-dependency-diagnostics`

Expose malformed dependency details through `worktrail-work-queue list --json` and the dashboard surfaces that rank or skip briefs. Give automatic selection a stable skip reason and human output an actionable warning containing the brief ID and invalid value. Add an end-to-end test proving malformed blocked work is skipped rather than ranked as maximally eligible.

**Independent value:** operators can locate and repair legacy corruption without log archaeology, and the highest-risk consumer has direct regression coverage.

**Release evidence:** list/dashboard JSON contract tests, rendered warning tests, and an auto-mode selection test demonstrate a conservative skip with no silent eligibility.

## Dependencies

- Feature 1 establishes the identifier-validation contract used by later features.
- Feature 2 depends on Feature 1's contract but can ship without dashboard rendering; its conservative result protects eligibility immediately.
- Feature 3 depends on Feature 2's structured resolution states and integrates them with queue-list and router/dashboard presentation.
- All features preserve the current queue lifecycle and atomic claim semantics.

## Sequencing

1. Deliver `work-queue-dependency-reference-contract` to stop creating new malformed references.
2. Deliver `work-queue-conservative-dependency-resolution` to protect all consumers from legacy and hand-authored malformed values.
3. Deliver `work-queue-dependency-diagnostics` to complete operator visibility and end-to-end auto-pick evidence.

Each feature is independently selected and routed through Route C. Do not author all three feature specifications in advance.

## Risks and mitigations

- **False blocking from an overly narrow grammar:** derive accepted forms from the existing `resolve()` contract and cover full stems, frontmatter IDs, and supported unique prefixes before enforcing validation.
- **Accidental removal of stale-ID leniency:** represent `stale` separately from `malformed` and retain an explicit regression test for satisfied well-formed missing IDs.
- **Inconsistent consumers:** centralize classification and make boolean helpers adapt the structured result rather than adding comma checks at each call site.
- **Breaking JSON consumers:** add diagnostic fields compatibly, retain existing fields, and test dashboard parsing against both clean and malformed entries.
- **Unsafe automatic repair:** report malformed values but require an intentional edit; do not guess whether commas separate IDs or belong to an invalid identifier.

## Release strategy

1. Release producer validation first, including an actionable migration example using repeated `--blocked-by` flags.
2. Release conservative resolution next; monitor for newly surfaced blocked briefs and repair their frontmatter intentionally.
3. Release dashboard diagnostics and automatic-selection regression coverage after the structured result is stable.
4. Treat any malformed-reference warning as queue data requiring operator attention, while well-formed missing references continue to resolve leniently.

Rollback can remove the new producer rejection or diagnostic presentation independently, but must not restore silent eligibility for malformed references once conservative resolution ships.

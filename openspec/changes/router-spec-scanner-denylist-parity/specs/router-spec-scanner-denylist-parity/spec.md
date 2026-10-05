## Purpose

Keeps router scans of devkit spec roots aligned with the dashboard's authoritative
non-spec directory boundary, preventing shared documentation from becoming false candidates.

## ADDED Requirements

### Requirement: Devkit Router Scanners Share the Non-Spec Directory Boundary
Every router scanner that enumerates direct child directories of a devkit `docs/specs/` root
SHALL exclude a child whose lowercased name is in the dashboard's known non-spec directory set.
The requirement-coverage audit, handoff candidate classification, and devkit overlap extraction
SHALL consume the dashboard-owned set rather than maintaining independent copies. Their existing
behavior for readable, non-denylisted candidate directories SHALL otherwise remain unchanged.

#### Scenario: Shared addenda are absent from scanner results
- **WHEN** `docs/specs/addenda/` contains files that would otherwise provide declared identifiers,
  handoff token matches, or an overlap feature summary
- **THEN** requirement-coverage auditing reports no result from `addenda`, handoff classification
  returns no `addenda` candidate, and devkit overlap extraction returns no `addenda` entry

#### Scenario: A newly denylisted name takes effect everywhere
- **WHEN** the dashboard's known non-spec directory set gains a lowercased directory name
- **THEN** every affected devkit router scanner excludes a matching direct child without a
  scanner-local denylist edit

### Requirement: Non-Denylisted Devkit Children Retain Existing Eligibility
The shared boundary SHALL be name-based only. A direct `docs/specs/` child not in the known
non-spec directory set SHALL remain eligible for each scanner's pre-existing parsing and
selection rules, including when it lacks dashboard lifecycle markers such as `tasks/`,
`changes/`, or `user-request.md`.

#### Scenario: Unmarked candidate directory remains visible
- **WHEN** a non-denylisted direct child contains files that satisfy a scanner's existing
  candidate rules but contains no dashboard lifecycle marker
- **THEN** that scanner continues to process the child according to its existing rules

#### Scenario: OpenSpec container discovery is unchanged
- **WHEN** overlap extraction is invoked with an OpenSpec-shaped root containing its required
  `changes/` or `specs/` container
- **THEN** it continues to discover entries beneath that container rather than treating the
  container name as a devkit shared-directory exclusion

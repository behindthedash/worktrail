## Context

See proposal.md - Why. `dashboard._NON_SPEC_DIRS` already defines the case-insensitive names of
direct `docs/specs/` children that are shared documentation or scaffolding rather than spec
folders. `dashboard_selfcheck` imports that set, but the requirement-coverage audit,
handoff-classification candidate scan, and overlap extractor retain their own direct-child
enumeration. The latter also supports OpenSpec-shaped roots, where `changes/` and `specs/` are
required containers and must not be filtered as devkit children.

## Goals / Non-Goals

**Goals:**

- Give every affected devkit-root scanner one authoritative, case-insensitive name boundary.
- Prove the false-positive shape using scanner-shaped files in a denylisted child and preserve
  an unmarked, non-denylisted child where the scanner already accepts one.

**Non-Goals:**

- Replacing any scanner's existing document resolution, token scoring, identifier collection, or
  OpenSpec extraction rules.
- Reusing `_is_spec_folder()` or requiring lifecycle marker files; that would change the scope of
  non-denylisted, incomplete devkit spec folders.
- Applying the devkit denylist to OpenSpec's `changes/` or `specs/` containers.

## Decisions

### Import the dashboard-owned set at each affected devkit enumeration boundary

The affected router modules will import and test membership in `dashboard._NON_SPEC_DIRS` while
iterating direct children of a devkit `docs/specs/` root. The comparison stays lowercased, as in
the dashboard, so the source remains authoritative and additions automatically reach every
consumer.

*Alternative considered:* duplicate the current names in each scanner. Rejected because the
selfcheck regression demonstrated that separate lists drift when the dashboard gains a new
shared directory.

### Filter by name, not by folder contents

The filter will run before each scanner's current content inspection and will only examine the
direct child's name. It will not call `_is_spec_folder()` or add a new shared predicate.

*Alternative considered:* share `_is_spec_folder()`. Rejected because its lifecycle/content test
would suppress unmarked directories that individual scanners legitimately inspect today, turning
a false-positive fix into a broader visibility change.

### Keep OpenSpec traversal on its existing path

`overlap_check` will apply the boundary only to its devkit direct-child path. Its OpenSpec path
continues to enumerate the required `changes/` and `specs/` containers, even though those names
also appear in the devkit denylist.

*Alternative considered:* make one generic filter apply to every directory loop in the module.
Rejected because it would make valid OpenSpec changes invisible.

## Risks / Trade-offs

- [Risk] Importing the dashboard constant could create an import cycle. → Mitigation: confirm the
  current router import graph before implementation and retain a one-way dependency; the existing
  `dashboard_selfcheck` import is the precedent.
- [Risk] A name-only filter can skip a user-created folder whose name collides with a reserved
  shared-directory name. → Mitigation: this is the dashboard's established contract and keeps all
  router scans consistent; a folder intended as a spec must use a non-reserved direct-child name.
- [Risk] An overbroad change could filter OpenSpec containers. → Mitigation: cover OpenSpec
  extraction explicitly and scope the filter to devkit-root traversal.

## Migration Plan

This is a behavior correction with no data migration or CLI change. Land the scanner and focused
regression tests together. A revert restores prior enumeration behavior without touching stored
data or user-authored specs.

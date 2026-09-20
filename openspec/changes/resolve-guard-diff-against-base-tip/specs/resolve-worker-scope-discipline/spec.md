## ADDED Requirements

### Requirement: The forbidden-path guard SHALL judge scope against the base tip
`_forbidden_paths_touched()` SHALL treat a path as touched only when it both changed between the
worker's pre-run HEAD and the group branch's post-run HEAD **and** differs from that path's
content at the base tip (`<remote>/<base>`). A path whose post-run content at the group branch is
identical to the base tip's SHALL NOT be reported as a forbidden path, regardless of whether it
appears in the pre/post diff. Both deny-list tiers — the absolute spec root and the
declared-file-exempt remainder — SHALL be applied to this narrowed set.

#### Scenario: a path merged in unchanged from base is not a violation
- **WHEN** a resolve worker merges `<remote>/<base>` into the group branch, bringing in
  `.github/workflows/gitleaks.yml` and `openspec/changes/<spec>/tasks.md` exactly as base has
  them, and edits nothing else under a denied prefix
- **THEN** `_forbidden_paths_touched()` returns no forbidden paths and the worker is not struck

#### Scenario: a path the worker actually edited is still a violation
- **WHEN** the group branch's post-run content for a denied path differs from the base tip's,
  and the path also appears in the pre/post diff
- **THEN** that path is reported as a forbidden path exactly as it is today

#### Scenario: a base change outside the pre/post diff is not attributed to the worker
- **WHEN** a denied path differs from the base tip but did not change between the worker's
  pre-run and post-run HEADs
- **THEN** it is not reported, because the pre/post diff remains the outer bound of what this
  worker could have done

### Requirement: The guard SHALL refresh the base tip and fall back rather than disarm
Before comparing against the base tip, the system SHALL run a best-effort
`git fetch <remote> <base>`. If that fetch fails, or the base-tip diff command fails, the system
SHALL fall back to the unnarrowed pre/post touched set — today's behavior — and SHALL log that
base-tip narrowing was unavailable. It SHALL NOT return an empty result on that path, so a
transient git failure can never silently disable the guard.

#### Scenario: base tip unavailable
- **WHEN** the fetch of `<remote>/<base>` or the diff against it exits non-zero
- **THEN** the guard evaluates the deny list against the unnarrowed pre/post set and logs that
  narrowing was unavailable

#### Scenario: empty pre-run SHA still fails open unchanged
- **WHEN** `pre_sha` is empty because `rev-parse` failed
- **THEN** the guard returns no forbidden paths and runs no git diff at all, exactly as today

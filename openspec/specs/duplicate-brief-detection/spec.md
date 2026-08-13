# duplicate-brief-detection Specification

## Purpose
TBD - created by archiving change duplicate-brief-detection. Update Purpose after archive.
## Requirements
### Requirement: Cluster Signal Extraction
The system SHALL extract a Cluster Signal from each queued brief consisting
of: repo (nullable), target-spec, related-ID list, blocked-by-ID list,
descriptive slug (with any leading `YYYYMMDD-HHMMSS-` timestamp prefix
stripped), and focus-text tokens (lowercase alphanumeric tokens of length
>= 3).

#### Scenario: Timestamp prefix stripped from slug
- **WHEN** a brief's filename is `20260731-161140-promote-contract-sentinel-s-route.md`
- **THEN** the extracted slug is `promote-contract-sentinel-s-route`

#### Scenario: Block-scalar focus text is extracted
- **WHEN** a brief's frontmatter declares `focus` as a YAML block scalar
  (`focus: |-` or `focus: >-` followed by indented text lines — the format
  the handoff capture flow writes)
- **THEN** the focus-text tokens are extracted from the block's actual
  text, not from the literal block-scalar marker (which previously made
  every such brief contribute zero focus tokens, disabling focus-overlap
  detection for it entirely)

### Requirement: Duplicate-Slug Matching Is Repo-Independent
The system SHALL match two briefs as `duplicate-slug` whenever their
extracted slugs are identical, regardless of either brief's `repo` value
(including both being null), unless a `blocked-by` relationship exists
between them.

#### Scenario: Two null-repo briefs with identical slugs match
- **WHEN** two briefs both have `repo: null` and identical slugs
- **THEN** a `duplicate-slug` Signal Match is formed between them

### Requirement: Blocked-By Relationship Excludes All Signal Types
The system SHALL exclude a brief pair from every Signal Match type
(duplicate-slug, same-target-spec, related-link, focus-overlap) whenever a
`blocked-by` relationship exists between them, regardless of repo or
overlap score.

#### Scenario: Blocked-by pair never matches
- **WHEN** brief A lists brief B in its `blocked-by` field
- **THEN** no Signal Match of any type is formed between A and B, even if
  their focus-overlap coefficient exceeds every threshold in this spec

### Requirement: Repo-Scoped Matching Treats Null-vs-Null As Same-Repo
The system SHALL compute same-target-spec, related-link, and focus-overlap
Signal Matches for a brief pair when both briefs share the same non-null
`repo` value, OR when both briefs have `repo: null`. The system SHALL NOT
compute these three signal types for a pair where exactly one brief has a
null `repo` and the other has a non-null `repo`.

#### Scenario: Two null-repo briefs compared via focus-overlap
- **WHEN** two briefs both have `repo: null` and their focus-text overlap
  coefficient is >= `OVERLAP_THRESHOLD` (0.45)
- **THEN** a `focus-overlap` Signal Match is formed between them

#### Scenario: One null-repo and one real-repo brief never compared
- **WHEN** brief A has `repo: null` and brief B has `repo: "worktrail"`
- **THEN** no same-target-spec, related-link, or focus-overlap Signal
  Match is formed between A and B, even with identical focus text

### Requirement: Focus-Overlap Threshold
The system SHALL form a `focus-overlap` Signal Match between two
repo-eligible briefs (per the requirement above) when their token overlap
coefficient (intersection size / smaller brief's token-set size) is >=
`OVERLAP_THRESHOLD` (0.45).

#### Scenario: Below-threshold overlap forms no edge
- **WHEN** two repo-eligible briefs have a focus-overlap coefficient of
  0.40
- **THEN** no `focus-overlap` Signal Match is formed

### Requirement: Cluster Assembly
The system SHALL assemble clusters as connected components over the graph
formed by all Signal Matches (any type) across the queued-brief set.

#### Scenario: Transitive clustering
- **WHEN** brief A matches brief B, and brief B matches brief C, but A and
  C have no direct Signal Match
- **THEN** A, B, and C are assembled into one cluster

### Requirement: Uniform Reporting Threshold Across Cluster Sizes
The system SHALL surface every assembled cluster, regardless of size. A
cluster of exactly size 2 SHALL be surfaced under the same per-signal
thresholds that form its edges (e.g. a `focus-overlap` edge at
`OVERLAP_THRESHOLD` (0.45)) — no additional near-identical bar applies to
size-2 clusters. (Full-recall decision, 2026-08-13: the report is
advisory, and the prior size-2 near-identical bar hid 2 genuine duplicate
pairs on the live queue while surfacing 0.)

#### Scenario: Size-3 cluster always surfaced
- **WHEN** a cluster contains 3 or more briefs matched by any signal type
- **THEN** the cluster is surfaced regardless of overlap coefficients

#### Scenario: Size-2 focus-overlap cluster at 0.46 surfaced
- **WHEN** a 2-brief cluster's only Signal Match is a `focus-overlap` edge
  with coefficient 0.46
- **THEN** the cluster is surfaced (previously hidden by the size-2
  near-identical bar)

#### Scenario: Size-2 non-scored-signal cluster surfaced
- **WHEN** a 2-brief cluster's only Signal Match is a `same-target-spec`
  or `related-link` edge (no `focus-overlap` edge at all)
- **THEN** the cluster is surfaced

### Requirement: LLM Verification Gate for Borderline Size-2 Candidates
For a candidate 2-brief pair where both briefs have `repo: null`, neither
brief already belongs to an assembled cluster, and the pair's
focus-overlap coefficient is >= `LLM_GATE_FLOOR` (0.35) and <
`OVERLAP_THRESHOLD` (0.45), the system SHALL invoke an LLM verification
call asking whether the two briefs describe the same underlying work. A
positive verdict SHALL cause the pair to be surfaced as a cluster; a
negative verdict SHALL leave the pair unsurfaced. (The band's upper bound
was `NEAR_IDENTICAL_THRESHOLD` (0.50) before the full-recall decision;
pairs at/above `OVERLAP_THRESHOLD` now form an ordinary edge and surface
without LLM involvement.)

#### Scenario: Real missed pair (PR #93) surfaced via LLM verification
- **WHEN** two briefs both have `repo: null`, differing slugs, a
  focus-overlap coefficient of 0.44, and the LLM verification call returns
  a positive verdict (same underlying work: finishing contract-sentinel's
  route-existence-gate rollout)
- **THEN** the pair is surfaced as a 2-brief cluster

#### Scenario: LLM gate not triggered outside its band
- **WHEN** a null-vs-null pair's focus-overlap coefficient is 0.30 (below
  `LLM_GATE_FLOOR`)
- **THEN** no LLM verification call is made and the pair is not surfaced

#### Scenario: LLM gate not triggered for non-null-repo pairs
- **WHEN** a same-repo (non-null) pair's focus-overlap coefficient is 0.44
- **THEN** no LLM verification call is made (the LLM gate only applies to
  null-vs-null size-2 pairs); the pair forms no `focus-overlap` edge
  (below `OVERLAP_THRESHOLD`) and is not surfaced

### Requirement: LLM Verification Gate Fails Open
The system SHALL treat any LLM verification call that times out (10
seconds), exits non-zero, returns empty or unparseable output, or cannot
run because no headless agent CLI is configured or available, as a
negative verdict. The system SHALL NOT crash, hang, or raise an unhandled
exception in the dashboard render path as a result of an LLM verification
failure.

#### Scenario: LLM call timeout degrades to not-surfaced
- **WHEN** an LLM verification call for an eligible candidate pair exceeds
  10 seconds without responding
- **THEN** the pair is treated as a negative verdict and not surfaced, and
  cluster computation for the rest of the queued-brief set continues
  unaffected

#### Scenario: No agent CLI configured
- **WHEN** the LLM Verification Gate would fire but no headless agent CLI
  is configured or available on the machine
- **THEN** the pair is treated as a negative verdict and not surfaced, and
  no error is surfaced to the dashboard caller


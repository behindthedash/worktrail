## Context

See `proposal.md` — Why. The design-relevant facts, all verified against the
real corpus rather than assumed:

- **Declarations take at least two shapes.** In
  `datalena docs/specs/084-automation-health-digest`, functional requirements are
  declared as markdown table rows
  (`| REQ-001 | The system SHALL ... | Generic |`) while negative requirements
  are declared as bullet items (`- REQ-NR001: The system SHALL NOT ...`). A
  table-only parser finds 30 of that spec's 37 requirement identifiers and
  silently misses every `NR` one.
- **The prefix namespace is open-ended.** Task frontmatter across datalena uses
  ~100 distinct requirement prefixes (`REQ` 1083, `CHG` 439, `FR` 273, `CI`,
  `AUD`, `MIG`, `GOV`, `AUTHZ`, …) and ~60 acceptance-criterion prefixes (`AC`
  1528, `DRF`, `EMB`, …). Any fixed enum is wrong on arrival.
- **Identifiers appear in prose too.** In spec 084 the same identifiers recur
  inside descriptive text and inside `traceability-matrix.md`. Counting every
  occurrence as a declaration would invent identifiers that were only
  cross-referenced.
- **Coverage data already exists.** Devkit task frontmatter carries `reqs`,
  `ac-mapping`, and `imp-requirements`. `reqs` reaches the task dict through
  `source.py`'s passthrough list (`"reqs": fm.get("reqs", [])`), *not* through
  `FIELD_SCHEMA`, which declares only `ac-mapping` and `imp-requirements`.
- **The legacy corpus is already dirty.** Spec 084 alone carries six uncovered
  identifiers its own `traceability-matrix.md` documents as a known gap
  (DEC-009). Datalena has 43 active specs. Any enforcement model that fails on
  pre-existing state is unadoptable.
- **Sibling precedent exists.** `check_clarification_integrity.py` and
  `check_dod_verification.py` are both diff-scoped checks composed into
  `pre_pr_gate.py` with their own exit codes (3 and 4); `1`, `2` are taken by
  spec-sync/scope-completeness and unconfigured.

## Goals / Non-Goals

**Goals:**
- Catch a requirement or acceptance criterion added without task coverage at the
  moment it is introduced, not months later by hand.
- Be adoptable immediately across repos whose specs already contain gaps, with
  no baseline artifact to generate or maintain.
- Survive the corpus's real formatting variance (two declaration shapes, open
  prefix namespace, sub-namespaced `NR` identifiers).

**Non-Goals:**
- Judging whether a task *adequately* implements a requirement. This checks that
  a claim of ownership exists, nothing about its quality.
- Parsing free-text prose for implied requirements.
- Retroactively cleaning up existing gaps. The audit mode reports them; closing
  them is separate work.
- Touching the OpenSpec path, where `worktrail-compile`'s scope-check already
  owns this guarantee.

## Decisions

### D1 — Declaration-anchored, prefix-agnostic identifier discovery

An identifier counts as *declared* only when it anchors a line-level construct
that introduces its normative text: the first cell of a markdown table row, or
the head of a bullet item followed by a separator. Identifiers appearing
mid-sentence are cross-references, not declarations.

The identifier pattern itself is structural (an uppercase prefix, a hyphen, an
optional sub-namespace segment, and a digit sequence) rather than an enumerated
list of known prefixes.

- *Alternative — fixed `REQ-`/`AC-` enum (what the originating brief literally
  proposed):* rejected. It would miss `FR`, `CHG`, `AUD`, `AUTHZ` and ~100 other
  prefixes actually in use, i.e. most of the corpus.
- *Alternative — count every occurrence anywhere in the doc:* rejected. It
  inflates the declared set with cross-references and would report
  never-declared identifiers as uncovered.
- *Trade-off:* a spec that declares requirements in some third shape neither
  anchor recognises will under-report. That fails open (a missed warning), not
  closed (a false block), which is the correct direction for a gate.

### D2 — Non-retroactive ratchet: enforce only newly-declared identifiers

The blocking gate compares the spec's declared-identifier set in the working
tree against the same set at the base ref, and enforces coverage only on the
difference. Pre-existing uncovered identifiers never fail a PR.

This is the decision that makes the gate shippable. It also matches the incident
that motivated the work exactly: the brief's author was about to add `REQ-029`/
`REQ-030` without coverage — a *newly-declared* identifier — while `REQ-023..028`
had been uncovered for months.

- *Alternative — repo-wide enforcement:* rejected. Would fail essentially every
  PR in datalena on day one.
- *Alternative — changed-specs-only (the `check_clarification_integrity` model):*
  rejected. A PR touching spec 084 for an unrelated reason would fail on
  DEC-009's pre-existing gap, teaching operators to bypass the gate.
- *Alternative — a committed baseline/allowlist file:* rejected. It is a second
  artifact to generate, review, and keep in sync; it rots, and it makes adoption
  a migration project instead of a version bump.
- *Trade-off:* existing gaps stay invisible to the gate until someone edits that
  requirement. Accepted deliberately — the audit mode (D5) is the deliberate
  cleanup path, and a ratchet that stops the bleeding beats a gate nobody can
  turn on.

### D3 — Coverage is the union of all three frontmatter arrays

A declared identifier is covered if it appears in any task's `reqs`,
`ac-mapping`, or `imp-requirements`. No array is privileged and no
identifier-kind-to-array mapping is enforced.

- *Alternative — require requirements in `reqs` and acceptance criteria in
  `ac-mapping`:* rejected. The corpus does not honour that split (`AC` prefixes
  appear in `reqs`, `CHG` in both), so enforcing it would produce failures that
  are about bookkeeping convention rather than real coverage.

### D4 — New sibling module, not an extension of `check_spec_sync`

The check ships as its own module composed into `pre_pr_gate.py` with its own
exit code, mirroring `check_clarification_integrity.py`'s shape.

- *Alternative — extend `check_spec_sync.py` (the brief's first suggestion):*
  rejected. That module's two checks are both status-drift comparisons sharing
  one exit code; folding in a structurally different check would make a single
  exit code mean three unrelated things and complicate the diff-scoped/base-ref
  logic that spec-sync does not need.

### D5 — Audit mode is a CLI flag, never part of the gate

Repo-wide enumeration is opt-in and reports pre-existing gaps. It is
deliberately excluded from the blocking path so that D2's ratchet cannot be
accidentally converted into repo-wide enforcement.

### D6 — Do not modify `FIELD_SCHEMA`

The check reads `reqs` from task frontmatter directly, exactly as `source.py`
already does via its passthrough list. `FIELD_SCHEMA` is left alone.

- *Rationale:* adding `reqs` to `FIELD_SCHEMA` would change validation behavior
  for every devkit task in every consuming repo — a behavior change well outside
  this change's purpose, with real risk of failing existing task files. The
  asymmetry is noted in `proposal.md` as an observation; closing it is separate
  work if it is ever wanted.

## Risks / Trade-offs

- **A third declaration shape exists somewhere in the corpus and is missed** →
  Fails open, not closed. Validate the parser against real specs from more than
  one repo, and include the bullet and table shapes as explicit test fixtures.
- **Base-ref resolution fails in some CI or worktree context** → The spec
  requires skipping the comparison and reporting the skip rather than failing
  the run, matching how sibling checks degrade.
- **A newly-added spec (no base-ref version) makes every identifier "new"** →
  Correct and desirable: a brand-new spec should declare task coverage for
  everything it introduces. Worth an explicit fixture so the behavior is
  intentional rather than incidental.
- **An author games the gate by adding a token reference** → Accepted. This gate
  checks that ownership is claimed; `001-task-ac-verification-gate` is the
  mechanism that checks claims are true.
- **Renaming an identifier reads as one removal plus one addition** → The
  addition must carry coverage. This is the intended behavior, not a bug.

## Migration Plan

No migration artifact and no per-repo setup. Consuming repos pick the check up
with their next `worktrail` version bump; by D2 it is a no-op until a diff
declares a new identifier. Rollback is reverting the `pre_pr_gate.py`
composition — the standalone CLI can remain installed without gating anything.

The known pre-existing gaps (spec 084's `REQ-023..028` and whatever the audit
surfaces elsewhere) stay tracked where they already are; this change does not
close them and does not pretend to.

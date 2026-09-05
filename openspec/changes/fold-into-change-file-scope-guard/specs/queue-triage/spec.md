## MODIFIED Requirements

### Requirement: Evidence-required verdict per brief
For every brief passed to a group's evaluator agent, the `evaluate` step SHALL require a
verdict of exactly one of `keep`, `stale-close`, `needs-update`, `duplicate-of`,
`fold-into-change`, `propose-change`, `work-directly`, or `needs-decision`, and SHALL
require non-empty `evidence` text for every verdict. `fold-into-change` SHALL additionally
require a non-empty `target_change` (`<repo>:change:<id>` naming an active change presented
as a candidate) and SHALL require `evidence` to cite at least one file-path-shaped token
(the same path-probe extraction `run_premise_check()` already uses against a brief's `focus:`
text) -- since `apply`'s fold action appends `evidence` verbatim as the target change's new
task text, and that task must carry file scope for `worktrail-compile` to accept it;
`propose-change` SHALL additionally require a non-empty `target_repo` and a kebab-case
`proposed_change_name`; `needs-decision` SHALL additionally require a non-empty `question`.
For a brief evaluated in the repo-less (`__none__`) group, the evaluator prompt SHALL list
the known workspace repos (the directory basenames under the configured repos root),
`propose-change` SHALL be valid only when `target_repo` is one of those listed names, and
`fold-into-change` SHALL remain invalid since no candidate changes are presented. For a
brief evaluated in a repo-bearing group, the evaluator prompt SHALL state `propose-change`'s
`target_repo` as that group's own repo with no known-repos allowlist, rather than reusing the
repo-less group's "valid only when `target_repo` is one of these known repos" wording with a
placeholder value standing in for "no restriction" — since no such allowlist applies to a
repo-bearing group, wording that implies one is misleading regardless of the placeholder used.
A verdict that is missing, malformed, or missing required evidence or required
target fields SHALL be recorded as `keep` with the evaluator's raw output retained as
evidence text, never silently dropped from the output. Every recorded verdict SHALL carry
the brief's `premise_check` results (empty for a no-repo brief). For a brief in the no-repo
group, any verdict that would be recorded as `keep` — including every fail-open fallback —
SHALL instead be recorded as `needs-decision` with the question "which repo does this brief
belong to?" and the would-be `keep` evidence retained as evidence, so a brief with no repo is
never left idle.

`needs-update` MAY additionally carry `refuted_span` (the exact verbatim substring of the
brief's `focus:` text that the evidence refutes) and, only alongside `refuted_span`, an
optional `corrected_span` (replacement text; empty or absent means the span is removed
outright rather than replaced) — signaling that the correction is mechanical: a specific,
quotable claim refuted by cited code/paths, or a specific target reference that is
stale/archived. `needs-update` MAY instead carry `judgment_reason` (a non-empty explanation
of why resolving the brief requires a human decision rather than a quotable correction —
e.g. an ambiguous choice between conflicting claims, or a scope/policy call). These two
fields are mutually exclusive signals consumed by `apply` (see "Apply step never closes a
brief without an approved verdict"); neither is required for a `needs-update` verdict to be
valid — validity for `needs-update` still requires only non-empty `evidence`, as before this
change. `evaluate` SHALL NOT reject, downgrade, or otherwise alter a `needs-update` verdict
for carrying, omitting, or malforming either field. When an evaluator's output for a brief
sets both `refuted_span` and `judgment_reason`, `judgment_reason` SHALL take precedence at
apply time and `refuted_span` SHALL be ignored, since a verdict declaring a human decision is
needed must never also silently auto-rewrite the brief.

#### Scenario: Well-formed stale-close verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "stale-close", "evidence":
  "PR #42 merged 2026-07-01 delivers this", "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `stale-close` with that evidence

#### Scenario: Well-formed fold verdict
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "fold-into-change",
  "target_change": "worktrail:change:work-queue-dependency-diagnostics", "evidence":
  "overlaps open tasks touching src/worktrail/router/land_pr.py", "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `fold-into-change` with that target

#### Scenario: Fold verdict names a change that was not a candidate
- **WHEN** an evaluator returns `fold-into-change` with a `target_change` that is not an
  active change in the brief's repo
- **THEN** the verdict is recorded as `keep` with the raw verdict retained as evidence

#### Scenario: Fold verdict's evidence names no file
- **WHEN** an evaluator returns `fold-into-change` naming a presented candidate but whose
  `evidence` cites no file-path-shaped token
- **THEN** the verdict is recorded as `keep` with the raw verdict retained as evidence,
  exactly as for a `target_change` that was not a presented candidate

#### Scenario: Undecidable case fails open
- **WHEN** an evaluator cannot find evidence to confirm or refute a repo-resolved brief's
  premise within its tool-call budget and the brief is not due for escalation
- **THEN** the verdict for that brief is `keep`, with the evaluator's stated reason for
  inconclusiveness recorded as evidence

#### Scenario: Malformed verdict from an evaluator
- **WHEN** an evaluator's output for a repo-resolved brief cannot be parsed as a valid
  verdict object
- **THEN** the verdict file records that brief as `keep` with the raw unparsed text retained
  as evidence, and the brief is never left out of the verdict file

#### Scenario: Repo-less brief proposes into a known repo
- **WHEN** the evaluator for the `__none__` group cites evidence identifying the owning repo
  and returns `propose-change` with `target_repo` equal to one of the known repo names it was
  shown and a kebab-case `proposed_change_name`
- **THEN** the verdict is accepted as-is

#### Scenario: Repo-less brief proposes into an unknown repo
- **WHEN** the evaluator for the `__none__` group returns `propose-change` whose
  `target_repo` is not one of the known repo names it was shown
- **THEN** the verdict is downgraded to `keep` with the evaluator's output as evidence

#### Scenario: Repo-less brief cannot fold
- **WHEN** the evaluator for the `__none__` group returns `fold-into-change`
- **THEN** the verdict is downgraded to `keep`, since no candidate changes were presented for
  a repo-less brief

#### Scenario: Keep for a no-repo brief becomes needs-decision
- **WHEN** an evaluator returns `keep` (or an unparsable verdict) for a brief in the no-repo
  group
- **THEN** the verdict file records that brief as `needs-decision` with the question "which
  repo does this brief belong to?" and the evaluator's evidence or raw output retained as
  evidence

#### Scenario: Premise check travels with the verdict
- **WHEN** a repo-resolved brief's premise check produced two entries and the evaluator
  returns any valid verdict for it
- **THEN** the verdict file's entry for that brief carries both entries under
  `premise_check`

#### Scenario: Repo-bearing group's prompt states target_repo without an allowlist
- **WHEN** `_evaluate_group()` formats `EVALUATOR_PROMPT_TEMPLATE` for a group whose `repo` is
  not the no-repo key
- **THEN** the formatted prompt's `propose-change` guidance states `target_repo` as that
  group's own repo and does not contain the no-repo group's "valid only when ... one of these
  known repos" restriction wording

#### Scenario: needs-update with a mechanical refuted_span
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "needs-update", "evidence":
  "src/foo.py:12 shows this was fixed in PR #99", "refuted_span": "the bug reported in
  #foo is still open"}`
- **THEN** the verdict file records brief `X` as `needs-update` carrying
  `refuted_span: "the bug reported in #foo is still open"`

#### Scenario: needs-update with a judgment_reason
- **WHEN** an evaluator returns `{"brief_id": "X", "verdict": "needs-update", "evidence":
  "...", "judgment_reason": "the brief cites two conflicting target repos and neither is
  clearly current"}`
- **THEN** the verdict file records brief `X` as `needs-update` carrying that
  `judgment_reason`

#### Scenario: needs-update with both fields set
- **WHEN** an evaluator returns `needs-update` with both `refuted_span` and
  `judgment_reason` set
- **THEN** the verdict file records only `judgment_reason` for that brief

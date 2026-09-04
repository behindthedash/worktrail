## MODIFIED Requirements

### Requirement: Evidence-required verdict per brief
For every brief passed to a group's evaluator agent, the `evaluate` step SHALL require a
verdict of exactly one of `keep`, `stale-close`, `needs-update`, `duplicate-of`,
`fold-into-change`, `propose-change`, `work-directly`, or `needs-decision`, and SHALL
require non-empty `evidence` text for every verdict. `fold-into-change` SHALL additionally
require a non-empty `target_change` (`<repo>:change:<id>` naming an active change presented
as a candidate); `propose-change` SHALL additionally require a non-empty `target_repo` and a
kebab-case `proposed_change_name`; `needs-decision` SHALL additionally require a non-empty
`question`. For a brief evaluated in the repo-less (`__none__`) group, the evaluator prompt
SHALL list the known workspace repos (the directory basenames under the configured repos
root), `propose-change` SHALL be valid only when `target_repo` is one of those listed names,
and `fold-into-change` SHALL remain invalid since no candidate changes are presented. A
verdict that is missing, malformed, or missing required evidence or required
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
  "target_change": "worktrail:change:work-queue-dependency-diagnostics", "evidence": "...",
  "confidence": "high"}`
- **THEN** the verdict file records brief `X` as `fold-into-change` with that target

#### Scenario: Fold verdict names a change that was not a candidate
- **WHEN** an evaluator returns `fold-into-change` with a `target_change` that is not an
  active change in the brief's repo
- **THEN** the verdict is recorded as `keep` with the raw verdict retained as evidence

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

### Requirement: Apply step never closes a brief without an approved verdict
The `apply` step SHALL only ever act on verdicts present in a verdict file supplied via
`--verdict-file`, SHALL require an explicit `--confirm` flag before executing any
`stale-close`, `needs-update`, `duplicate-of`, `fold-into-change`, `propose-change`,
`work-directly`, `needs-decision`, or `keep` action, and for a `keep` verdict SHALL do
nothing beyond appending a `verdict: keep` triage note to the brief's body (never claiming,
closing, moving, or editing the frontmatter of the brief). `apply` SHALL NOT expose any flag
or code path that closes or edits a brief without both an existing verdict file entry and
the `--confirm` flag. Without `--confirm`, every planned action — including the branch,
target change, and pull-request title a fold or propose would create, and the keep note a
`keep` would append — SHALL be printed and nothing SHALL be modified in the queue or in any
target repo. For `fold-into-change` and `propose-change`, a verdict's `repo` value SHALL be
resolved to an on-disk checkout directory the same way the router/dashboard resolve a brief's
`repo:` frontmatter (an absolute or home-relative path resolves directly; a bare name or
`owner/name`-style value resolves by basename under a configurable repos root, defaulting to
`~/projects`) before any worktree or git operation runs against it. A `repo` value that cannot
be resolved to an existing directory SHALL fail with an error action-log entry and SHALL NOT
attempt any worktree or git operation.

For a `needs-update` verdict, `--confirm` execution branches deterministically on which of
`refuted_span`/`judgment_reason` (per "Evidence-required verdict per brief") is present,
each re-checked against the brief's current on-disk state rather than trusted from
evaluation time. A `refuted_span` shorter than 12 characters (the same minimum-length floor
`premise_check`'s own quoted-needle extraction already uses) SHALL be treated as not found,
regardless of whether it appears verbatim, since a short span is too likely to match
unrelated text coincidentally:
- When `judgment_reason` is present (or `refuted_span` is present but does not — re-checked
  at apply time, including the length floor above — appear verbatim in the brief's current
  `focus:` text), `apply` SHALL file a
  decision through the same path a `needs-decision` verdict already uses (`decisions.ask()`),
  using `judgment_reason` when present, or otherwise an apply-generated question stating that
  the verdict's `refuted_span` no longer matches the brief's current focus text, as the
  decision's question, and the verdict's `evidence` as its background/context. The brief
  SHALL be left queued, blocked pending a human answer, exactly as an ordinary
  `needs-decision` verdict already behaves; its `focus:` text SHALL NOT be modified.
- Otherwise, when `refuted_span` is present and does appear verbatim in the brief's current
  `focus:` text, `apply` SHALL rewrite `focus:` by removing that substring (or replacing it
  with `corrected_span`, when given), SHALL append a `## Triage <run-date>` note recording
  what was removed or replaced and the verdict's evidence, and SHALL then re-run evaluation
  once, immediately, against the corrected brief using the same evaluator pipeline `evaluate`
  uses. The freshly produced verdict (or an error, if re-evaluation itself fails) SHALL be
  included in the action-log entry for the caller to review. `apply` SHALL NOT execute,
  claim, close, or otherwise act on that freshly produced verdict as part of this action — a
  human or a subsequent `apply` invocation supplying it via a new verdict file and its own
  `--confirm` remains required, exactly as for any other verdict, so a mechanical rewrite can
  never itself cascade into closing a brief, folding it, or opening a pull request.
- When a rewrite (per the previous bullet) would leave the brief's `focus:` text empty after
  removing `refuted_span`, `apply` SHALL instead file a decision (per the first bullet) with
  a question stating that removing the refuted claim would leave the brief with no remaining
  focus text, rather than writing an empty `focus:`.
- When neither field is present, `apply` SHALL fall back to appending the `## Triage
  <run-date>` note containing the verdict's evidence and leaving the brief otherwise
  untouched, unchanged from this requirement's behavior before this capability existed.

#### Scenario: Apply without --confirm is a dry run
- **WHEN** `apply` is invoked with a verdict file but without `--confirm`
- **THEN** every planned action (claim+done for stale-close/duplicate-of, in-place edit for
  needs-update, branch+PR for fold-into-change/propose-change, in-place stamp for
  work-directly, decision envelope for needs-decision, triage note for keep) is printed but
  no brief in `queue/` or `picked/` is modified and no target repo is written to

#### Scenario: Apply with --confirm executes stale-close
- **WHEN** `apply --confirm` runs against a verdict file containing a `stale-close` verdict
  for brief `X`
- **THEN** brief `X` is claimed and marked done, with the verdict's evidence recorded as the
  closure note

#### Scenario: Apply with --confirm executes needs-update
- **WHEN** `apply --confirm` runs against a verdict file containing a `needs-update` verdict
  for brief `Y` carrying neither `refuted_span` nor `judgment_reason`
- **THEN** a `## Triage <run-date>` section containing the verdict's evidence is appended to
  brief `Y`'s body in place, and brief `Y` remains in `queue/` with `status: queued`

#### Scenario: Apply with --confirm executes a mechanical needs-update rewrite
- **WHEN** `apply --confirm` runs against a verdict file containing a `needs-update` verdict
  for brief `Y` whose `refuted_span` is found verbatim in `Y`'s current `focus:` text
- **THEN** that span is removed (or replaced with `corrected_span`, if given) from `Y`'s
  `focus:` text, a `## Triage <run-date>` note records the rewrite, evaluation is re-run
  immediately against the corrected brief, and the action-log entry for `Y` carries the
  freshly produced verdict without that verdict having been executed

#### Scenario: Apply with --confirm executes fold-into-change
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change`
  verdict for brief `Z`
- **THEN** the fold is executed per the `intake-triage` capability's fail-closed
  pull-request semantics, and brief `Z` is closed only after the pull request exists

#### Scenario: A stale refuted_span falls back to filing a decision
- **WHEN** `apply --confirm` runs against a `needs-update` verdict whose `refuted_span` is
  not found verbatim in the brief's current `focus:` text
- **THEN** `apply` files a decision as if `judgment_reason` had been set, the brief's
  `focus:` text is left unchanged, and the brief remains queued pending that decision

#### Scenario: Apply with --confirm files a decision for a judgment needs-update
- **WHEN** `apply --confirm` runs against a verdict file containing a `needs-update` verdict
  for brief `Z` with `judgment_reason` set
- **THEN** a pending decision is filed with that reason as its question and the verdict's
  evidence as its background, brief `Z` is stamped `awaiting-decision` and remains queued,
  and `Z`'s `focus:` text is not modified

#### Scenario: Apply with --confirm records keep
- **WHEN** `apply --confirm` runs against a verdict file containing a `keep` verdict for
  brief `K`
- **THEN** a `## Triage <run-date>` section beginning `verdict: keep` and `keep-count: <n>`
  is appended to brief `K`'s body, brief `K` remains in `queue/` with unchanged frontmatter,
  and the action-log entry reports `append-triage-note` / `executed`

#### Scenario: Fold-into-change resolves a bare repo name
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change`
  verdict whose `repo` is a bare name (e.g. `devops`) that uniquely matches a sibling
  checkout under the configured repos root
- **THEN** the worktree, branch, and pull request are created against that matching
  checkout, not against a path relative to the current working directory

#### Scenario: Propose-change resolves a bare repo name
- **WHEN** `apply --confirm` runs against a verdict file containing a `propose-change`
  verdict whose `repo` is a bare name that uniquely matches a sibling checkout under the
  configured repos root
- **THEN** `openspec new change` and the subsequent worktree/PR flow run against that
  matching checkout

#### Scenario: Repo-less propose-change stamps the brief's repo before proposing
- **WHEN** `apply --confirm` runs against a verdict file containing a `propose-change`
  verdict evaluated in the repo-less (`__none__`) group whose `target_repo` is a bare name
  that resolves to a checkout under the configured repos root
- **THEN** the brief's `repo:` frontmatter is set to that bare name before any worktree or
  git operation runs, and the `openspec new change` / worktree / PR flow runs against the
  resolved checkout; a later failure in that flow leaves the brief queued with `repo:` still
  stamped

#### Scenario: Repo-less propose-change with an unresolvable target stamps nothing
- **WHEN** `apply --confirm` runs against a repo-less `propose-change` verdict whose
  `target_repo` does not resolve under the configured repos root
- **THEN** the action-log entry reports an error status naming the unresolvable repo, the
  brief's frontmatter is unchanged, and no worktree, git, or `gh` command runs

#### Scenario: Unresolvable repo value fails closed
- **WHEN** `apply --confirm` runs against a verdict file containing a `fold-into-change` or
  `propose-change` verdict whose `repo` value does not resolve to an existing directory,
  either directly or by basename under the configured repos root
- **THEN** the action-log entry for that verdict reports an error status naming the
  unresolvable repo, no worktree is created, and no git or `gh` command runs against a
  guessed path

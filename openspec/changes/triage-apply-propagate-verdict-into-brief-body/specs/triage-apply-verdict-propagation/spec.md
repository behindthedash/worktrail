## ADDED Requirements

### Requirement: An applied work-directly verdict corrects the brief's focus text
When a `work-directly` verdict is applied with `--confirm` and carries a `refuted_span` that is
at least the minimum span length and still present verbatim in the brief's live focus text, the
apply step SHALL rewrite that focus before stamping the brief, replacing the span with
`corrected_span` or removing it when no correction was given. A `refuted_span` that no longer
matches the brief's live focus text SHALL NOT be rewritten; the brief is stamped and noted
without a focus change.

#### Scenario: a refuted claim is corrected before the brief is seeded
- **WHEN** an accepted `work-directly` verdict quotes a span of the brief's focus verbatim and
  supplies a replacement
- **THEN** the brief's `focus` frontmatter has that span replaced by the replacement, and the
  brief is still stamped `recommended-route: F`

#### Scenario: a refuted claim with no replacement is dropped
- **WHEN** an accepted `work-directly` verdict quotes a span and supplies no `corrected_span`
- **THEN** that span is removed from the brief's focus and the surrounding text is preserved
  verbatim

#### Scenario: a stale span leaves the focus untouched
- **WHEN** an accepted `work-directly` verdict's `refuted_span` is not present in the brief's
  current focus text
- **THEN** the focus is written unchanged, and the apply still stamps and notes the brief

### Requirement: An applied work-directly verdict records its evidence in the brief body
Every accepted `work-directly` apply SHALL append a `## Triage <run_date>` section to the brief
carrying the verdict's evidence, and -- when a focus rewrite was made -- a summary line naming
what was replaced or removed. This SHALL happen whether or not the verdict carried a span, so
the Route F worker always reads the run's evidence alongside the brief.

#### Scenario: evidence reaches a seeded brief with no correction
- **WHEN** an accepted `work-directly` verdict carries no `refuted_span`
- **THEN** the brief keeps its focus and gains a `## Triage <run_date>` section containing the
  verdict's evidence

#### Scenario: a rewrite is summarized alongside the evidence
- **WHEN** an accepted `work-directly` verdict rewrites the focus
- **THEN** the appended section names the span that was replaced or removed, followed by the
  verdict's evidence

#### Scenario: a downgraded verdict writes nothing
- **WHEN** a `work-directly` verdict fails the existing reproduction-evidence acceptance check
- **THEN** it remains a no-op downgraded to `keep`: no focus rewrite, no appended note, and no
  frontmatter stamp

### Requirement: A whole-focus refutation does not seed the brief
When a `work-directly` rewrite would leave the brief's focus text empty after stripping, the
apply step SHALL NOT rewrite, stamp, or seed the brief. It SHALL instead downgrade to `keep`,
reporting that the refutation covers the brief's entire focus.

#### Scenario: refuting the entire focus downgrades to keep
- **WHEN** an accepted `work-directly` verdict's `refuted_span` covers the brief's whole focus
  text and no `corrected_span` is given
- **THEN** the brief is left unstamped and unmodified, and the action entry reports a
  downgrade to `keep` naming the whole-focus refutation

### Requirement: The preview shows the body changes the apply would make
A no-`--confirm` preview of a `work-directly` verdict SHALL report the same branch the apply
would take -- planned rewrite, plain planned stamp, or planned downgrade -- and SHALL write
nothing to the brief.

#### Scenario: preview reports a planned rewrite
- **WHEN** a `work-directly` verdict carrying a mechanically usable span is previewed
- **THEN** the entry reports the planned frontmatter stamp together with the planned rewrite's
  removed span and replacement, and the brief on disk is unchanged

#### Scenario: preview reports a planned whole-focus downgrade
- **WHEN** a previewed `work-directly` verdict's span covers the brief's entire focus
- **THEN** the entry reports a planned downgrade to `keep` rather than a planned stamp

### Requirement: The evaluator may pair a correction with a work-directly verdict
The evaluator prompt SHALL instruct that `refuted_span` and `corrected_span` are valid on a
`work-directly` verdict as well as a `needs-update` one, under the same rule that the span be
quoted verbatim from the brief's focus text. `judgment_reason` SHALL remain valid only for
`needs-update`.

#### Scenario: the prompt documents the pairing
- **WHEN** the evaluator prompt is rendered
- **THEN** its `work-directly` guidance states that a correction to the focus text may be
  supplied alongside the verdict, and its output-shape block does not restrict
  `refuted_span`/`corrected_span` to `needs-update`

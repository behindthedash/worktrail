## Context

See `proposal.md` for motivation and `docs/specs/epics/002-safe-work-queue-dependency-references.md` for the owning epic and business objective. Today `create_handoff()` creates the queue directory before cleaning `blocked_by`, and `_clean_lines()` silently drops blank values while retaining comma-joined values. The CLI uses `action="append"`, so repeated flags already arrive as an ordered list; both CLI and non-CLI callers converge on `create_handoff()`.

The existing resolver accepts full brief stems and unique prefixes without requiring a match. Producer validation must therefore enforce the shape of one reference without conflating syntax with resolution or existence.

## Goals / Non-Goals

**Goals:**

- Establish one producer-side validation path shared by the Python API and CLI.
- Reject empty/whitespace-only and comma-containing entries before queue directories or files are created.
- Preserve trimmed, valid iterable values in order and serialize them as a YAML list.
- Produce a CLI-visible error that names the invalid value and explains repeated `--blocked-by` usage.

**Non-Goals:**

- Resolve a reference or require a matching queue/picked brief at creation time.
- Normalize, split, repair, or rewrite already-malformed references.
- Change `_dep_is_done()`, `_is_blocked()`, queue selection, dashboard diagnostics, `watch`, `related`, triage, or task-DAG dependencies.
- Introduce a broader identifier grammar than is necessary to stop the known ambiguous comma-joined shape.

## Decisions

### Validate and materialize `blocked_by` at the API boundary

Add a small single-reference validator in `create_handoff.py` and materialize the optional iterable once near the start of `create_handoff()`, before resolving or creating queue paths. It will require string values whose trimmed form is non-empty and comma-free, return the trimmed list, and raise `ValueError` with the invalid raw value plus repeated-flag guidance for comma-containing input.

This placement makes the Python API authoritative and automatically gives CLI callers identical behavior through the existing exception handling. It also prevents generator inputs from being consumed more than once. The alternative—an argparse-only type or post-write validation—would leave API producers unprotected or allow partial filesystem effects.

### Keep reference shape separate from reference resolution

Validation will not call `resolve()` and will not require a timestamp-shaped ID. Full stems, frontmatter identifiers, supported prefixes, and syntactically valid stale references remain accepted. This matches the epic's separation between malformed syntax and valid-but-missing references.

The alternative—a narrow timestamp regex or existence check—would reject supported prefixes and stale IDs, coupling Feature 1 to runtime semantics reserved for later Epic 002 features.

### Preserve repeated arguments as list entries

The existing `argparse` `append` behavior remains unchanged. Validated values are passed to frontmatter serialization as an ordered list, so `--blocked-by dep-a --blocked-by dep-b` remains structurally distinct from the rejected single value `dep-a,dep-b`.

The validator will not split on commas because doing so guesses intent and would turn an invalid reference into multiple dependencies without explicit caller consent.

### Concentrate regression coverage in the existing handoff test module

Focused tests in `tests/workqueue/test_create_handoff.py` will cover API acceptance/rejection and CLI behavior, including error text, return status, list preservation, and absence of queue storage or brief output after invalid input. This keeps the change narrow and demonstrates failure before filesystem mutation.

## Risks / Trade-offs

- [The minimal comma-free contract accepts unusual non-empty identifiers] → Preserve compatibility with the resolver's broad full-ID/prefix inputs; later Epic 002 work may centralize a richer classifier based on evidence.
- [Rejecting blanks changes prior silent-dropping behavior] → Fail explicitly so producer mistakes cannot disappear from metadata unnoticed, and cover the message in regression tests.
- [Error text could expose raw caller input] → Dependency identifiers are queue metadata rather than secrets; represent the value safely and keep the message concise.
- [An iterator could behave differently if inspected repeatedly] → Materialize and validate it exactly once before any filesystem mutation.

## Migration Plan

No stored-data migration is performed. Deploy the producer validation with its tests; valid callers continue unchanged, while callers sending comma-joined values must repeat `--blocked-by`. Rollback consists of reverting the validation change, though doing so would reopen the known producer-side data-integrity gap.

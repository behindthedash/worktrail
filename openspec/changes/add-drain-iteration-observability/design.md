## Context

The drain loop already computes `failure_class`, `claimed_delta`, `claimed_briefs`, and `transcript_path` before it emits the human-readable iteration line and appends the JSON-compatible iteration summary. Only the outcome projection is currently retained: summary entries include the inferred brief and transcript path, while the classification and claim-attribution inputs are discarded. See `proposal.md` for the incident motivation and `specs/drain-iteration-observability/spec.md` for the observable contract.

## Goals / Non-Goals

**Goals:**

- Preserve the already-computed classification and attribution inputs without adding another inference path.
- Give log readers and structured-summary consumers the same compact evidence needed to distinguish a capacity block with no claim from a failed claimed item.
- Keep new summary fields additive and stable for downstream digest consumers.

**Non-Goals:**

- Changing outcome classification, queue claiming, circuit-breaker behavior, transcript retention, or digest rendering.
- Introducing a new summary-contract version or requiring transcripts when transcript persistence is disabled or fails best-effort.
- Adding database, telemetry, or external logging dependencies.

## Decisions

### Emit the existing classification inputs directly

Each iteration summary will add `failure_class`, `claimed_delta`, and `claimed_brief_count`; the log line will render the same values. `claimed_brief_count` is derived from the exact `claimed_briefs` snapshot used for attribution rather than inferred from the selected `brief`, because `brief=None` is intentionally ambiguous today for zero-claim and multi-claim iterations.

This uses values already in scope and keeps classification as the single source of truth. Re-deriving attribution later from outcome state or queue snapshots was rejected because those inputs may no longer exist when a nightly digest runs.

### Keep the existing transcript field as the durable pointer

The summary's existing `transcript` field remains the canonical durable diagnostic pointer, and the log continues to render it when present. It stays explicitly null when transcript persistence is disabled or a best-effort write fails. Adding a second alias such as `diagnostic_pointer` was rejected because it would duplicate an established field without creating a new artifact or stronger durability guarantee.

### Preserve explicit empty values

All new structured fields are emitted for every completed iteration. `failure_class` is nullable; claim measurements are integer zero when no claim occurred. Stable keys let consumers distinguish a known absence from records created before this capability and avoid treating `brief=None` as sufficient attribution evidence.

### Test at the drain-summary boundary

Regression tests will drive the drain loop with fake spawns and assert both the returned summary entry and captured iteration log. One fixture will model a capacity-classified blocked iteration with no claim; another will model a nonzero-exit failed iteration that claims exactly one known brief and has a persisted transcript. This tests the durable consumer-facing boundary without expanding into digest implementation.

## Risks / Trade-offs

- [Existing consumers compare summary objects exactly] → The fields are additive, and focused tests will verify current fields remain unchanged; consumers should ignore unknown keys.
- [A null transcript cannot preserve raw subprocess output] → The summary still retains classification and attribution context, while transcript persistence remains an explicit operator configuration and best-effort behavior outside this change.
- [Claim delta and attribution count can differ under concurrent queue movement] → Preserve both observed values rather than collapsing them, making ambiguity visible without changing current attribution rules.

## Migration Plan

Deploy the additive producer change with its regression tests. Existing summaries remain readable, while consumers can feature-detect the new keys. Rollback removes only the new fields and log tokens; no stored state or schema migration is required.

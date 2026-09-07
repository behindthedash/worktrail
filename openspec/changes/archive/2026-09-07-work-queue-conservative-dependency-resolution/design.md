## Context

See `proposal.md` for motivation and the capability spec for normative behavior. Feature 1 established the producer contract in `create_handoff.py`: a single dependency reference is a non-empty string after trimming and contains no comma. Runtime reads remain permissive today. `_dep_is_done()` searches `picked/` and then `queue/`, collapsing done, active, ambiguous, and not-found outcomes into a boolean; a comma-joined legacy value therefore misses both locations and is incorrectly treated as a satisfied stale ID. `_is_blocked()`, `list_queue()`, and claim warnings consume that boolean.

The queue lifecycle represents completed dependencies as files in `picked/` with `status: done`; there is no separate done directory. Existing compatibility intentionally treats a well-formed reference absent from both locations as satisfied. This feature must protect eligibility and improve the existing claim-warning seam without adding Feature 3's list/dashboard diagnostic fields or rewriting stored data.

## Goals / Non-Goals

**Goals:**

- Make dependency state an explicit structured result with the raw value, classification, satisfaction decision, and matching candidates needed for diagnostics.
- Apply the Feature 1 single-reference shape contract before filesystem lookup.
- Route blocked-state and current claim-warning consumers through the same classifier.
- Preserve valid stale-reference leniency and the existing queue lifecycle.

**Non-Goals:**

- Add list/dashboard JSON diagnostic fields, rendered dashboard warnings, or a new auto-pick skip-reason contract; those belong to Epic 002 Feature 3.
- Split or repair comma-joined values, migrate historical briefs, or mutate files while reading them.
- Narrow accepted identifiers beyond Feature 1's evidence-based non-empty, comma-free shape.
- Change claim authorization: direct claim may continue with warnings even when normal eligibility marks a brief blocked.

## Decisions

### Introduce one structured dependency classifier

Add a small immutable or dictionary-shaped resolution result in `work_queue.py` with `raw`, `status`, `satisfied`, and `candidates` data. Its status vocabulary is `done`, `active`, `stale`, `ambiguous`, and `malformed`. `_dep_is_done()` remains as a compatibility adapter returning the result's satisfaction decision, while `_is_blocked()` and warning generation consume structured results where state-specific behavior matters.

This centralizes precedence and avoids duplicating comma checks in each consumer. Keeping the boolean helper as an adapter minimizes churn for private callers and focused tests. The alternative—teaching only `_dep_is_done()` to return false for commas—would fix eligibility but discard the diagnostic distinction required by this feature.

### Validate shape before attempting resolution

The classifier applies the same observable contract established by Feature 1: the raw value must be a string, non-empty after trimming, and comma-free. Malformed values return immediately with no attempt to split or partially resolve them. The shared rule may be extracted to a dependency-reference helper module so producer and consumer cannot drift, with `create_handoff.py` continuing to own producer-facing error guidance.

The alternative—looking up first and deciding malformed only after no match—would preserve the incident: a comma-joined value would look stale. A strict timestamp regex is also rejected because existing full IDs, frontmatter IDs, and unique prefixes are supported.

### Preserve lifecycle lookup precedence while making ambiguity explicit

For valid references, inspect `picked/` first, matching current behavior. A unique picked match is `done` only when its frontmatter status is done and otherwise `active`. Ambiguity in either searched location is `ambiguous`. If picked has no match, a unique queue match is `active`; absence from both is `stale` and satisfied.

This preserves established semantics and avoids redefining unusual duplicate data across lifecycle locations. Candidate paths are retained in the structured result for later Feature 3 diagnostics, but this feature does not expose them in public list/dashboard JSON.

### Define satisfaction as an explicit property of classification

Only `done` and `stale` produce `satisfied: true`; `active`, `ambiguous`, and `malformed` produce false. Queue eligibility continues to use the existing `blocked` field, now derived from these rules, so all current ready-count and auto-pick consumers inherit fail-closed behavior without parallel policy changes.

Claim warnings use the same results and include the raw value plus state for unresolved references. This makes malformed versus ambiguous problems diagnosable immediately while preserving the existing ability to claim blocked work intentionally.

### Verify read-only behavior with content snapshots

Focused tests will create real queue/picked fixtures for each state, exercise both the structured resolver and public `list_queue()`/claim-warning behavior, and compare malformed brief bytes before and after reads. The incident regression will use one comma-joined YAML list item containing multiple IDs while the first real ID remains active, proving the value is not split and cannot be mistaken for stale.

## Risks / Trade-offs

- [Producer and runtime syntax checks could drift] → Extract or reuse one shape predicate and independently test producer compatibility if the helper moves.
- [Changing warning strings may affect callers that assert exact text] → Preserve the existing `blocked by <value>` prefix and append a stable state marker; update focused contract assertions.
- [A non-string YAML scalar was previously stringified and could resolve accidentally] → Classify it malformed before lookup; this is the intended fail-closed boundary for malformed legacy data.
- [Candidate details may tempt premature public API exposure] → Keep the structured result internal in Feature 2; reserve list/dashboard schema changes for Feature 3.
- [A malformed brief may become newly ineligible after deployment] → Surface the raw value and state through claim diagnostics, then require an intentional operator edit rather than automatic repair.

## Migration Plan

Deploy without a data migration. Existing valid references retain their behavior; malformed and ambiguous references become blocked immediately through the existing queue `blocked` field. Operators may intentionally repair diagnosed briefs later. Rollback can revert the classifier and consumers without stored-data conversion, but would reopen silent eligibility for malformed references.

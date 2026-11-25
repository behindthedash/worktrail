## Context

See `proposal.md` for the motivating defect. `LiveSpawn.__call__` resolves the authoritative
available cell with `select_cell()` and then uses `spawnlib.explicit_cell_override()` to turn an
explicit role model or effort into a temporary one-cell routing table. A fallback can therefore
change the serving harness after the caller supplied a role model. The routing table already is
the repository's source of truth for target, harness, and model combinations; no portable catalog
can say which model identifiers every installed provider version accepts.

## Goals / Non-Goals

**Goals:**

- Fail before any child process when an explicit worker model cannot be served by the selected
  fallback harness.
- Preserve the exact resolved routing snapshot used for selection as the compatibility authority.
- Keep compatible model overrides and effort-only overrides on their existing temporary-cell path.

**Non-Goals:**

- Validate every configured routing cell against a provider's live model catalog; existing routing
  readiness checks own that concern where available.
- Infer compatibility from model-name prefixes or maintain a package-resident model catalog.
- Change automatic fallback behavior, model-map parsing, capacity gates, or compile's separate
  explicit `--agent` / `--model` path.

## Decisions

### Validate after authoritative fallback selection

The check belongs immediately after `select_cell()` returns the actual winner and before the
temporary routing override is made. Validating against the preferred cell earlier would miss the
defect: capacity or independence routing can select another harness. Validating in a spawned child
would spend a worker launch on an invalid request and lose the selected-cell context needed for an
actionable diagnostic.

### Use declared same-harness cells as the compatibility set

A requested model is accepted when it occurs in a tier cell for any target with the winner's
harness in the selected routing snapshot. A harness can intentionally have multiple targets
(subscription, API, or profile-backed), so constraining the set to the one target would reject a
model the same CLI is configured to serve elsewhere. Conversely, accepting a model merely because
it appears anywhere in routing permits the cross-harness mismatch this change prevents.

The alternative of prefix rules (`gpt-*` for codex, `opus` for claude) would reject legitimate
custom/provider models and become stale. A live provider query is slower, side-effectful, absent
for some harnesses, and would not reflect what the operator explicitly configured. The resolved
routing table is therefore both sufficient and deterministic.

### Keep the check on the live-worker explicit-model branch

Only `LiveSpawn` combines a role-level model override with fallback selection. The conductor's
explicit invocation path has different input semantics and is not implicated by this defect, so
it remains unchanged. The compatibility helper, if extracted in `spawnlib`, receives the live
snapshot rather than independently loading policy; that preserves the existing selected-routing
contract and keeps thread-local override isolation intact.

## Risks / Trade-offs

- [An operator intentionally supplies an unlisted but provider-supported model] → It now fails
  loudly; adding that model to an appropriate same-harness routing cell makes the supported intent
  explicit and keeps all worker launches reproducible.
- [A same-harness model is unavailable for one pool/profile] → Existing provider readiness and
  spawn failure handling remain responsible for endpoint-specific availability; this guard only
  prevents a model known to another harness from crossing the CLI boundary.
- [A diagnostic's compatible-model list is large] → Report the deterministic, de-duplicated
  routing-derived set so the operator can remediate without inspecting the temporary file.

## Migration Plan

No data or configuration migration is needed. Existing compatible model maps continue unchanged;
an incompatible map must be corrected by choosing a model declared for the serving harness or by
configuring the intended model on that harness. Rollback is a normal revert of the validation and
tests.

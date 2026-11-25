## ADDED Requirements

### Requirement: Explicit worker model overrides remain compatible with the serving harness
When a live worker has an explicit model override, the orchestrator SHALL validate the requested
model after it selects the available fallback cell and before it creates a temporary override or
launches a worker. A requested model is compatible only when the resolved routing table declares
that model in at least one tier cell whose target uses the selected cell's harness. If it is not
compatible, dispatch SHALL fail before a worker is launched with an actionable error that names
the requested model, the selected target and harness, and the routing-derived compatible models.
The validation SHALL use the same resolved routing snapshot that selected the fallback cell.

#### Scenario: Fallback rejects a model belonging only to a different harness
- **WHEN** an explicit `opus` model override selects a fallback cell served by the `codex` harness
  and the resolved routing table declares `opus` only for `claude` targets
- **THEN** dispatch SHALL fail before creating the temporary override or launching a codex worker,
  and the error SHALL identify `opus`, the selected codex target and harness, and the codex models
  declared by that routing snapshot

#### Scenario: Compatible explicit model survives fallback selection
- **WHEN** an explicit model override selects a fallback cell whose harness has that model declared
  in the resolved routing table
- **THEN** dispatch SHALL create the one-cell override for the selected target and launch that
  target using the requested model

#### Scenario: Effort-only override preserves existing routing behavior
- **WHEN** a live worker applies only an explicit reasoning-effort override and has no explicit
  model override
- **THEN** dispatch SHALL not reject the selected cell for model compatibility and SHALL retain
  the existing effort-only override behavior

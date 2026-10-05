## MODIFIED Requirements

### Requirement: Capacity gates key on target and model
`agent-capacity.json` entries SHALL be keyed `<target>:<model>` (or bare `<target>` for a
target-wide gate). `spawn_agent` SHALL record outcomes under the served cell's key; the
selector SHALL consult the cell key and its bare-target key.

Exactly one resolution SHALL decide whether a cell is capacity-gated, and every reader SHALL
resolve through it: `spawn_agent`'s in-spawn selection, the drain's candidate selection, the
front-door dispatch's cell resolution, `worktrail-agent-capacity check-agent`, and the
dashboard's capacity snapshot. A query for a cell (`<target>:<model>`) SHALL be gated by an
active entry under that exact key or by an active bare `<target>` entry, and a bare-provider
entry SHALL win over a per-model entry that contradicts it. A query for a bare target or
harness SHALL be gated by an active bare entry, or by every entry under a `<query>:*` key being
active. An entry SHALL gate only while its `retry_after`/`reset_at` window is absent or still
in the future; an expired window SHALL NOT gate.

An account-level block SHALL be recorded under the bare target key, because it applies to every
model that target could serve, including models the reader does not know about. No reader SHALL
require a model-qualified entry to honour a bare one, and a gate recorded by the drain SHALL
therefore be visible to the spawn path, exactly as a gate recorded by a spawn is visible to the
drain.

The exhausted-row diagnostic (`NoExecutionTarget`'s attempted-cell list) SHALL name each
attempted cell by its gate key verbatim, so the string an operator is handed is the key
`worktrail-agent-capacity status` prints and `worktrail-agent-capacity clear` accepts. The
cell's harness MAY be named alongside that key.

#### Scenario: Subscription gate leaves the API lane open
- **WHEN** `claude-sub` carries an active `billing` gate and `claude-api:opus` is ungated
  and opted in
- **THEN** a `t1-deep` selection SHALL return `claude-api:opus` (after any earlier ungated
  targets)

#### Scenario: A drain-recorded target-wide gate is visible to the spawn path
- **WHEN** the cache holds a bare `claude-deepseek` entry whose `billing` gate is still active,
  and the resolved row's `claude-deepseek` cell declares model `deepseek-flash[1m]`
- **THEN** `spawn_agent`'s in-spawn selection SHALL skip that cell rather than serve it,
  `worktrail-agent-capacity check-agent` SHALL report the resolved target gated, and the
  dashboard's capacity snapshot SHALL report the cell gated -- all without any entry under the
  model-qualified key

#### Scenario: An expired target-wide gate gates nothing
- **WHEN** the bare `claude-deepseek` entry's window has already passed
- **THEN** a `claude-deepseek:deepseek-flash[1m]` query SHALL NOT be gated and that cell SHALL be
  selectable again, with no cache mutation required

#### Scenario: A per-model entry does not weaken a target-wide gate
- **WHEN** the bare `claude-sub` entry is gated and `claude-sub:opus` itself is recorded
  `available`
- **THEN** a query for `claude-sub:opus` SHALL still be gated, because a provider-wide gate is
  never weaker evidence than a per-model entry

#### Scenario: A bare query is gated only when every model of the target is
- **WHEN** a bare `claude-sub` query is made while `claude-sub:opus` is gated and
  `claude-sub:sonnet` is not
- **THEN** `claude-sub` SHALL NOT be reported gated, so the still-available model keeps the
  target selectable

#### Scenario: The exhausted-row diagnostic names the gate key
- **WHEN** every cell in the requested tier row is gated and `NoExecutionTarget` is raised
- **THEN** each attempted cell SHALL be named by its `<target>:<model>` gate key verbatim,
  alongside its gate class and retry time

## ADDED Requirements

### Requirement: A zero-API-call result is classified as an infra failure, not a completed run

A spawn whose worker process exits 0 but made no API call SHALL NOT be recorded as a completed
run. The spawn's infra-failure classification (`is_infra_failure`) SHALL recognize the claude
runtime's zero-API-call result shape — a stream-json `result` event carrying `duration_api_ms:
0`, `num_turns: 0`, and zero `input_tokens`/`output_tokens`/cache-token counts — and classify
it as an infra failure, so the existing retry-then-hop path (bounded retries on the same cell,
then same-row re-selection) runs before any capacity gate is recorded or any success is
reported. The detection SHALL read the parsed usage dict, so `_parse_stream_json` SHALL retain
`duration_api_ms` from the result event.

The detection SHALL be specific to the zero-work shape. A legitimate completed result — any
result event with `num_turns >= 1` or a non-zero token count — SHALL remain a healthy spawn and
SHALL be recorded `available` exactly as before this change. A stream that carries no claude
`result` event (the opencode-synthesized usage dict, codex's plain last-message text) SHALL
never satisfy the detection.

#### Scenario: A zero-API-call result is an infra failure
- **WHEN** a spawned claude cell exits 0 with a result event reading `duration_api_ms: 0`,
  `num_turns: 0`, and every token count 0
- **THEN** `is_infra_failure(0, <that output>)` SHALL return True, the cell SHALL be retried
  rather than recorded `available`, and no successful run SHALL be returned for it

#### Scenario: A real completed turn is still classified healthy
- **WHEN** a result event carries a non-zero `duration_api_ms` and/or `num_turns >= 1` with
  non-zero `input_tokens`
- **THEN** the existing classification SHALL return False, the cell SHALL be recorded
  `available`, and the run SHALL be returned exactly as before this change

#### Scenario: An empty-but-real turn is not a no-op
- **WHEN** a completed turn reports no output text but carries `num_turns >= 1`
- **THEN** the spawn SHALL NOT be classified as a zero-API-call failure

### Requirement: An exhausted zero-API-call spawn gates its cell with a short-cooldown class

When the zero-API-call shape recurs through the served cell's whole retry budget, `spawn_agent`
SHALL record that cell's gate with the short-cooldown `startup` failure class (default cooldown
60 seconds) and continue through the existing same-row hop to the next ungated cell. It SHALL
NOT record `auth` (24-hour cooldown, gates without retry) or `model_unavailable` (24-hour
cooldown, never probed) for this shape, and no failure class's existing cooldown value SHALL
change. When the row has no servable cell left, the spawn SHALL return an exhausted result
carrying `failure_class: startup` rather than a successful-but-empty run.

#### Scenario: No-op exhausts its budget and the row hops to a healthy cell
- **WHEN** the first cell of a row returns the zero-API-call shape for every attempt, its
  retry budget is exhausted, and a later cell in the row is ungated
- **THEN** the same spawn call SHALL complete on the later cell, and the first cell's recorded
  gate SHALL carry `failure_class: startup`

#### Scenario: No alternate cell is left
- **WHEN** every attempt on the row's only servable cell returned the zero-API-call shape
- **THEN** `spawn_agent` SHALL return an exhausted result with `failure_class: startup`, which
  a caller that fails closed on exhausted results treats as a failed spawn rather than a
  completed empty run

#### Scenario: A coerced auth classification never gates the cell
- **WHEN** a zero-API-call result is exhausted through its retry budget and the exhausted
  classification runs
- **THEN** the recorded gate's class SHALL be `startup` (60-second default), never `auth` or
  `model_unavailable`, so the cell remains reachable within the same run instead of being
  sidelined for a day

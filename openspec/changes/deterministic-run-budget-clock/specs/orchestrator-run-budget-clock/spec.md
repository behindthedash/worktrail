## ADDED Requirements

### Requirement: The run-budget decision reads one injectable clock
`live_run_real` and `_pipeline_scheduler` SHALL each accept a private `_clock` parameter: a
zero-argument callable returning elapsed seconds as a float, defaulting to `time.time`. Every
value that feeds the run-budget decision -- the run start reference, the current time read
inside the budget check, and the time recorded when the budget is found exhausted -- SHALL be
obtained by calling that callable. `_pipeline_scheduler` SHALL pass its own `_clock` to the
`live_run_real` call it makes for the tail pass, so a single run uses a single clock.

#### Scenario: Default clock preserves today's behaviour
- **WHEN** a run is started without `_clock`
- **THEN** the budget is evaluated against real wall-clock seconds exactly as before, and the
  journal's `budget_stopped_at` is an absolute wall-clock timestamp

#### Scenario: Injected clock decides the cut
- **WHEN** a run is started with a manual `_clock` whose value is advanced past `run_budget`
  between two scheduler ticks
- **THEN** the budget stop fires on the first tick after that advance, regardless of how much
  real time the run consumed

#### Scenario: Injected clock that never advances never cuts
- **WHEN** a run is started with a `_clock` whose value never changes and a non-zero
  `run_budget`
- **THEN** the budget stop never fires and the fan-out runs to completion

#### Scenario: Tail pass shares the scheduler's clock
- **WHEN** `_pipeline_scheduler` dispatches the tail pass with an injected `_clock`
- **THEN** the `live_run_real` call for the tail evaluates its budget against that same clock,
  not against `time.time`

### Requirement: Non-budget timings keep using wall-clock time
The system SHALL NOT route the progress emitter's elapsed-time line, per-task
`started_at`/duration measurements, or journal event timestamps through `_clock`. These SHALL
continue to call `time.time()` directly so an injected test clock cannot make audit records or
operator-facing durations report fictional times.

#### Scenario: Journal event timestamps under an injected clock
- **WHEN** a run with an injected `_clock` records a task event in the journal
- **THEN** the event's timestamp is a real wall-clock time, not the injected clock's value

### Requirement: Budget tests do not sleep in real time
The run-budget tests in `tests/orchestrator/test_pipeline_budget_partial_group.py` SHALL place
the budget cut using an injected manual clock rather than real sleeping, and SHALL NOT call
`time.sleep` to consume budget. Whether a given task is dispatched before the cut SHALL be
determined solely by the injected clock's advances, not by how long git operations, worktree
creation or thread scheduling take on the host.

#### Scenario: No real sleeping remains in the budget tests
- **WHEN** the module is inspected for budget-consuming delays
- **THEN** no `time.sleep` call is used to push elapsed time past `run_budget`

#### Scenario: Outcomes unchanged under a stalled host
- **WHEN** the converted tests run on a host where every git operation takes arbitrarily long
- **THEN** the same tasks are dispatched, the same group is quarantined `budget_exhausted`, and
  the same resume behaviour is asserted as before the conversion

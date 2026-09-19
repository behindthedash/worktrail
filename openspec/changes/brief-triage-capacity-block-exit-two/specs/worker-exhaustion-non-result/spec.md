## MODIFIED Requirements

### Requirement: Capacity exhaustion exits non-zero and distinguishably
The single-brief evaluate entrypoint SHALL, on an evaluator-unavailable outcome, print a null
verdict on stdout, print a `blocked_no_capacity:` diagnostic naming the failure on stderr, and
exit with status 2 -- the same status and diagnostic shape the dispatch entrypoint already uses
when no execution target has capacity. Exit status 1 SHALL retain its existing, narrower
meaning: the evaluator ran and produced no identifiable verdict for the requested brief id.

This outcome SHALL be determined by the condition, not by where in the spawn the condition was
detected. A routing row in which no cell has capacity at the moment the evaluator spawn is
selected -- before any worker is launched, so no per-spawn exhaustion flag is ever set -- SHALL
produce the same null verdict, the same `blocked_no_capacity:` diagnostic, and the same status 2
as a row exhausted after one or more attempts. In particular, a selection failure raised out of
the spawn helper SHALL NOT escape the single-brief evaluate entrypoint as an unhandled
exception, and the brief SHALL be left byte-for-byte unchanged and queued, exactly as it is
when the block is detected mid-spawn.

The batch evaluate command SHALL NOT discard the verdicts of groups that did evaluate
successfully: it SHALL omit each unavailable group's briefs from the verdict file, report the
number of unevaluated groups in both its JSON and its text summary, and exit non-zero whenever
that number is greater than zero. This SHALL hold for a group blocked before its spawn started
as well as for one blocked during it.

#### Scenario: Single-brief evaluation is capacity blocked
- **WHEN** `--evaluate-brief-triage` is run for a brief whose evaluator spawn is exhausted
- **THEN** stdout is `null`, stderr carries a `blocked_no_capacity:` line, and the exit status
  is 2

#### Scenario: Every cell is already gated before the spawn starts
- **WHEN** `--evaluate-brief-triage` is run for a brief whose repo's routing row has every cell
  capacity-gated at selection time, so cell selection fails before any worker is launched
- **THEN** stdout is `null`, stderr carries a `blocked_no_capacity:` line naming the repo and
  the failure class, the exit status is 2, no traceback is printed, and the brief file is
  unchanged

#### Scenario: No identifiable verdict still exits 1
- **WHEN** the evaluator runs to completion but emits nothing identifiable for the requested
  brief id
- **THEN** stdout is `null` and the exit status is 1, unchanged by this change

#### Scenario: One gated group does not lose another group's work
- **WHEN** a batch evaluate run has two groups and only the second one's spawn is exhausted
- **THEN** the verdict file contains the first group's verdicts, contains none of the second
  group's briefs, the summary reports one unevaluated group, and the command exits non-zero

#### Scenario: A pre-spawn block does not abort the whole batch run
- **WHEN** a batch evaluate run has two groups and the second group's cell selection fails
  before its spawn starts
- **THEN** the first group's verdicts are written, the summary reports one unevaluated group,
  the command exits non-zero, and the run does not terminate with an unhandled exception

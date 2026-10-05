## Why

`max_active_changes` currently counts every OpenSpec change directory that has a
`proposal.md`. That turns parked roadmap proposals into in-progress work: in the
verified datalena case, 45 proposal directories made a cap of 10 permanently
unreachable even though only 12 changes had started implementation. The cap then
held every `propose-change` verdict rather than throttling actual WIP.

OpenSpec proposals must remain visible to overlap and duplicate detection, so
moving unstarted proposals out of `openspec/changes/` would trade this false WIP
pressure for a regression in candidate discovery.

## What Changes

- Define started OpenSpec work as a change whose `tasks.md` contains at least one
  checked checklist task.
- Make the `max_active_changes` WIP-cap count use that started-work definition
  instead of treating every proposal directory as active.
- Preserve the existing overlap scan and fold-candidate behavior: unstarted
  proposals remain active candidates and are not hidden from evaluators.
- Add regression coverage for checked and unchecked OpenSpec task lists, including
  cap decisions that distinguish parked proposals from started work.

## Capabilities

### New Capabilities

- `started-openspec-wip-count`: WIP-cap enforcement counts only OpenSpec changes
  with started checklist work while overlap detection retains all proposals.

### Modified Capabilities

<!-- None. The existing queue-triage specification does not define the WIP-cap
     counting contract, so this change introduces that contract explicitly. -->

## Impact

- **Code**: `src/worktrail/workqueue/queue_triage.py` will count started OpenSpec
  changes for `max_active_changes`; `src/worktrail/router/overlap_check.py` may
  expose the started-work signal from its OpenSpec extraction path for that
  consumer.
- **Tests**: `tests/workqueue/test_queue_triage.py` and
  `tests/router/test_overlap_check.py` will cover the split between proposal
  visibility and WIP-cap counting.
- **Compatibility**: repos with a cap now admit new proposals when their existing
  proposal directories are unstarted; changes with completed checklist work retain
  their current throttling effect. No policy format or CLI changes are proposed.

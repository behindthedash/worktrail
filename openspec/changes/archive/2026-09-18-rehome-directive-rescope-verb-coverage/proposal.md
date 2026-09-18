## Why

`consume_repo_decision()` recognizes a free-form re-home directive via
`_REHOME_DIRECTIVE_RE` (`src/worktrail/workqueue/queue_triage.py:64-68`), whose verb
alternation is only `(?:re-?home|move|retarget|reassign)`. An operator answer phrased as
"Re-scope the brief's repo to worktrail" therefore cannot match: the decision is never
consumed, the brief's `repo:` is never rewritten, and the brief silently stays grouped under
the wrong repo run after run. (Work-queue brief
`20260917-162135-rehome-directive-regex-misses-rescope`.)

## What Changes

- The re-home directive verb set gains `re-scope` / `rescope` (hyphen optional, case
  insensitive, inflections such as "rescoped" not required), alongside the existing
  re-home, move, retarget, and reassign verbs. Everything after the verb is unchanged: "to",
  optional "the", repo name, optional "repo", and the name must still resolve to an on-disk
  checkout.
- Regression tests cover a re-scope answer being consumed, and a re-scope answer naming an
  unknown repo being ignored.

## Capabilities

### New Capabilities

### Modified Capabilities
- `queue-triage`: the "Free-form repo re-home decisions are consumed" requirement's directive
  verb set adds re-scope.

## Impact

- `src/worktrail/workqueue/queue_triage.py` — `_REHOME_DIRECTIVE_RE` and its comment.
- `tests/workqueue/test_queue_triage.py` — new regression tests.
- No CLI, schema, or on-disk format changes.

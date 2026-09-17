## Why

`queue_triage.consume_repo_decision()` only auto-consumes an answered decision whose
`question` is exactly `REPO_ASSIGNMENT_QUESTION` ("Which repo should this brief target?"),
and the inventory only calls it for briefs with no `repo:`. A repo re-home decision the
evaluator files in its own words (e.g. `dec-20260917-101016-negative-latency-clock-00b67a5114d1`,
answered "Re-home the brief to the devops repo group...") is therefore never consumed: the
brief stays blocked by `has_unresolved_decision()` until someone hand-edits `repo:` and runs
`worktrail-decision consume`. Observed live: ~6 hours answered-but-unconsumed on 2026-09-17.

## What Changes

- `consume_repo_decision()` additionally recognizes an answered decision with any question
  text when its **answer** carries an explicit re-home directive ("re-home / move / retarget
  / reassign ... to [the] `<name>` repo") whose `<name>` resolves to an on-disk checkout.
- The inventory calls `consume_repo_decision()` for every brief carrying an answered
  `awaiting-decision` link, not only repo-less ones, so a brief with a wrong `repo:` can be
  re-homed; a consumed re-home overwrites `repo:` and regroups the brief in the same run.
- A free-form answer with no directive, or whose named repo does not resolve, is left
  untouched and NOT reported as unresolvable (it is some other kind of decision). The
  canonical-question behaviour is unchanged.

## Non-Goals

- No LLM interpretation of answers; recognition is a deterministic pattern.
- No change to how decisions are filed (`_apply_needs_decision`) or to `has_unresolved_decision()`.

## Impact

- Code: `src/worktrail/workqueue/queue_triage.py`; tests in `tests/workqueue/test_queue_triage.py`.
- Spec: `queue-triage` (one added requirement).

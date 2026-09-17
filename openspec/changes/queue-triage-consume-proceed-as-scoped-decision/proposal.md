## Why

Queue-triage escalates every due brief whose `repo:` is empty with the canonical question
"Which repo should this brief target?", and files it with the standard `needs-decision`
options — the first of which is "Proceed with the brief as currently scoped".
`consume_repo_decision()` only consumes a canonical answer that `_resolve_repo_dir()` can map
to an on-disk checkout. "Proceed with the brief as currently scoped" names no repo, so the
answer is reported as unresolvable and brief and decision are left untouched — forever. A
brief that is intentionally multi-repo (`repo: null`) therefore loops: the human picks an
offered option, triage cannot consume it, the brief stays `awaiting-decision`, and if the
decision is archived by hand the next escalation re-asks the same question (same
deterministic decision id, so `decisions.ask()` answers `already-resolved`). Source: work-queue
brief `20260917-163157-queue-triage-needs-decision-apply`.

## What Changes

- `consume_repo_decision()` recognises a canonical-question answer that is the
  "Proceed with the brief as currently scoped" option (case-insensitive, surrounding
  whitespace and a trailing period ignored) as a **repo-less confirmation**: it stamps
  `repo-less: confirmed` on the brief, appends a `verdict: repo-less-confirmed` triage note
  with rule `decision`, and archives the decision. `repo:` stays empty and the brief stays in
  the repo-less group. The outcome is reported as neither inferred nor unresolvable.
- The escalation matrix no longer issues the repo-assignment `needs-decision` for a repo-less
  brief carrying `repo-less: confirmed`; such a brief is not escalated on the repo-less rule
  and continues through ordinary repo-less evaluation.
- Any other canonical answer that does not resolve keeps today's behaviour (reported
  unresolvable, nothing touched).

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `queue-triage`: adds consumption of the "proceed as scoped" answer to the repo-assignment
  decision and exempts confirmed repo-less briefs from the repo-assignment escalation.

## Impact

- `src/worktrail/workqueue/queue_triage.py` — `consume_repo_decision()`,
  `group_queue_by_repo()` (outcome handling), the repo-less branch of the escalation matrix.
- `tests/workqueue/test_queue_triage.py` — new scenarios.
- New optional brief frontmatter key `repo-less: confirmed`; briefs without it behave as today.

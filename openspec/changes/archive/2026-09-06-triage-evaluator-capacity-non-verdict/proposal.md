## Why

Nothing in the triage evaluator pipeline can tell a worker's *answer* from a worker's
*failure*. `spawnlib.spawn_agent()` gives up in three places -- session-limit wait budget
exhausted (`spawnlib.py:1208`), no alternate cell left in the row after the retry budget
(`:1287`), and the retry loop falling out (`:1318`) -- and every one of them returns
`finish(last_raw)`, a `SpawnResult` indistinguishable from a successful spawn: the same
`text` field, no return code, no failure class, even though `agent_capacity.classify_failure()`
has just classified the failure and gated the cell.

Both of this package's brief-lifecycle consumers then read `result.text` as the answer:

- `queue_triage.evaluate_group()` (`queue_triage.py:1259`) puts it straight into `raw_text` as
  "untouched worker output", and `parse_verdicts()` treats unparsable text as a malformed
  verdict and falls open to `keep` with that text as evidence.
  `grep -n 'capacity\|usage limit\|exhaust' src/worktrail/workqueue/queue_triage.py` returns
  nothing -- there is no exhaustion detection in the module at all.
- `create_handoff._semantic_slug_summary()` (`create_handoff.py:69`) returns it as the brief's
  filename slug.

Observed 2026-09-05 on an interactive `worktrail-go` pickup of brief
`20260905-215729-capacity-crash-resume-retryable`: after three `claude worker exit 1` and three
codex usage-limit failures, `worktrail-skill-dispatch --evaluate-brief-triage` exited **rc=0**
with `verdict="keep"`, `confidence=null`, `judgment_reason=null`, and `evidence` containing only
the codex error stream (`"You've hit your usage limit ... try again at Sep 6th, 2026 8:15 PM"`).
No model ever produced a verdict. `skill_dispatch.main()` exits 0 for any non-`None` `Verdict`,
so `worktrail-go`'s unconditional apply step then wrote that error text into the brief as a
`## Triage` note and set `keep-count: 1` -- a never-evaluated brief now carries a bogus triage
record and has advanced the keep-escalation counter that exists precisely to detect briefs a
*model* has repeatedly decided to keep.

The second call site fired while the brief for this change was being filed: the brief's own file
is named `20260905-220446-you-ve-reached-your-fable.md`, slugged from the summariser worker's
usage-limit message rather than from its focus text. Same defect, same root cause -- which is why
both call sites are fixed here rather than patching the evaluator alone.

`keep` is the pipeline's deliberate fail-open for a model that answered badly. It is the wrong
outcome for a model that never answered: a capacity failure is transient and carries no
information about the brief, so recording it as a verdict is both a false triage record and a
corruption of the escalation counter.

## What Changes

- **A given-up spawn says so.** `SpawnResult` gains `exhausted: bool = False` and
  `failure_class: str = ""`. All three give-up returns set `exhausted=True` and carry the
  failure class `agent_capacity.classify_failure()` already computed (`billing` for a provider
  usage cap); the success return (`spawnlib.py:1220`) is unchanged, so every other caller sees
  today's behaviour.
- **The evaluator refuses to derive a verdict from an exhausted spawn.** `evaluate_group()`
  marks the group `exhausted` instead of returning the error stream as `raw_text`;
  `evaluate_briefs()` raises `EvaluatorUnavailable` rather than calling `parse_verdicts()`, so
  no fail-open `keep` is ever fabricated for a brief no model read.
- **Both entrypoints surface it as a non-verdict, non-zero outcome.**
  `worktrail-skill-dispatch --evaluate-brief-triage` prints `null` and exits **2** with a
  `blocked_no_capacity:` stderr line (the exact shape `--routing` already uses for
  `NoExecutionTarget`), distinct from today's exit 1 "evaluator emitted no verdict for this id".
  `queue-triage evaluate` skips the exhausted group, keeps every other group's verdicts, reports
  `groups_unevaluated`, and exits non-zero.
- **The apply path refuses a verdictless payload.** `--apply-brief-triage null` (and a payload
  whose `verdict` is null or empty) returns an `error` action-log entry and exit 1 instead of
  raising a `TypeError` out of `Verdict(**json.loads(...))`. No triage note is appended and no
  `keep-count` is incremented, because no verdict was ever produced.
- **Handoff capture falls back to its deterministic slug.** `_semantic_slug_summary()` returns
  `None` for an exhausted spawn, which the existing fallback path (`fallback_slugify()`) already
  handles.
- **`worktrail-go`'s intake-triage step reads the new exit code**, reporting the capacity block
  and stopping without applying anything.

## Capabilities

### New Capabilities

- `worker-exhaustion-non-result`: a spawn that gave up without a model answer is signalled as
  such, and no consumer converts its output into a result value (a triage verdict, a brief
  slug, or anything else).

### Modified Capabilities

- `queue-triage`: "Evidence-required verdict per brief" -- the fail-open-to-`keep` rule is
  scoped to output an evaluator actually produced; an exhausted evaluator spawn yields no
  verdict for any brief in its group.

## Impact

- **Code**: `src/worktrail/orchestrator/spawnlib.py` (two `SpawnResult` fields; three give-up
  returns), `src/worktrail/workqueue/queue_triage.py` (`evaluate_group`, `evaluate_briefs`,
  new `EvaluatorUnavailable`, `cmd_evaluate`), `src/worktrail/router/skill_dispatch.py`
  (`evaluate_single_brief` passthrough, both CLI branches),
  `src/worktrail/workqueue/create_handoff.py` (`_semantic_slug_summary`).
- **Docs**: `skills/worktrail-go/SKILL.md`'s intake-triage step (exit-2 handling).
- **Tests**: `tests/workqueue/test_triage_evaluator_exhaustion.py` (new), plus the existing
  spawnlib and skill-dispatch suites.
- **Non-goals**: changing when a cell is gated, how long a gate lasts, or the retry/hop budget
  (`agent-capacity-gate-liveness-reprobe`'s scope); re-running the evaluator automatically once
  capacity returns; the orchestrator's own journal classification of a capacity crash
  (`capacity-crash-resume-retryable`'s scope); auditing the remaining `spawn_agent` callers,
  which do not convert worker text into a stored record.

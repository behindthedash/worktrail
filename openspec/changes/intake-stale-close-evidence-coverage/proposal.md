## Why

Brief `20261123-083103-route-codex-launch-sandbox` reported two independent Route D dispatch
failures: a guard rejected the canonical checkout as Codex `-C`, and a seeded executor then hit
`EROFS` in `write_marker` while rewriting an unchanged `.compile-ok` on a cache hit. Two intake
evaluations recommended `stale-close` on the strength of PR #1383, which repaired only the
linked-worktree Git metadata writes; neither verdict accounted for the compile-marker failure.
The repair that actually closed both shipped later in PR #1511.

The verdict contract requires non-empty `evidence`, but treats it as one undifferentiated blob.
A stale-close can therefore cite a repair for one reported failure and close a brief whose other
reported failure is still live. Nothing mechanically ties a closure to the full set of failures
the brief reported.

## What Changes

- Derive a brief's independently reported failures deterministically from its `focus:` text:
  top-level items split at sentence ends, semicolons, commas, list-item line breaks, and the
  stand-alone conjunction `and`.
- Require a `stale-close` verdict to carry `failure_coverage`: one entry per reported failure,
  each pairing a verbatim focus-item span with the evidence that resolves that specific failure.
- Enforce coverage at apply and preview time against the brief's live focus. An uncovered item
  downgrades the verdict to `keep`: the brief is not claimed, closed, or modified, and the action
  log names the uncovered items.
- State the rule in the evaluator prompt and its per-brief JSON shape, and synthesize mechanical
  coverage in the archived/renamed-repo short-circuit rather than exempting it from the gate.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `queue-triage`: a `stale-close` verdict may only close a brief when its evidence accounts for
  every independently reported failure in that brief's focus.

## Impact

- `src/worktrail/workqueue/premise_check.py`: deterministic focus-item extraction.
- `src/worktrail/workqueue/queue_triage.py`: `Verdict.failure_coverage`, parse plumbing, the
  evaluator prompt rule and JSON shape, archived-repo coverage synthesis, and the apply/preview
  downgrade gate.
- `tests/workqueue/test_premise_check.py`, `tests/workqueue/test_queue_triage.py`, and the other
  workqueue/router triage suites whose `stale-close` fixtures need coverage entries.
- No CLI flags, policy keys, spec-format changes, or new dependencies. `duplicate-of`,
  `needs-update`, `work-directly`, and keep escalation are unchanged.

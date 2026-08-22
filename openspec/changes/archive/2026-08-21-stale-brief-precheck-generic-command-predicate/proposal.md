## Why

Phase 5.5's predicate re-check (`check_brief_predicate.py`) can only auto-resolve a brief whose
`drift-source` has a bespoke Python re-check function registered in `PREDICATE_RECHECKS`, and
exactly one is registered today (`checkbox-drift-sweep`). Every other sweep-filed brief — most
of the sweep-filed volume in the queue right now comes from `devops`'s
`fleet-workflow-hygiene-guard.py` — falls through to the probe-based text search and an operator
prompt, even though the sweep that filed it *has* a deterministic, re-runnable detector.

PR #601 (2026-08-21) made the cost of that gap concrete. Its `work_queue.py done()` evidence gate
now stops a *new* unverified re-verification claim from being written, but it does nothing to
produce the verification automatically: the nine sibling briefs closed on the same prose-only
template had to be swept **by hand**, re-running each detector one at a time. The one real miss
in that batch (`20260817-101013`) is exactly what an automated Phase 5.5 recheck would have
caught at dispatch time. A bespoke Python predicate cannot close this gap, because the detector
lives in a separate repository (`behindthedash/devops`) and cannot be imported into worktrail —
the predicate has to be command-shaped.

## What Changes

- **New generic, command-based predicate kind.** A brief may declare `predicate-kind: command`
  in its frontmatter and give each `drift-findings` entry a `predicate-cmd` argv list — the exact
  command that re-derives whether that finding still holds. `check_brief_predicate.recheck()`
  runs it and classifies by exit status (`0` = predicate still true, `1` = resolved, anything
  else = error), so any external sweep in any language can register a brief for automatic Phase
  5.5 re-verification without worktrail importing its detector.
- **`PREDICATE_RECHECKS` gains a documented fallback rather than a second registry.** Lookup
  order stays deterministic: an exact `drift-source` hit in `PREDICATE_RECHECKS` wins (so
  `checkbox-drift-sweep` behaves byte-for-byte as it does today); otherwise a brief carrying
  `predicate-kind: command` dispatches to the generic command handler; otherwise the outcome is
  `unrecognized` and Phase 5.5 falls through unchanged, exactly as now.
- **The sweep keeps its own `drift-source` identity.** Command-based briefs are *not* forced onto
  a single shared `drift-source` literal — `drift-source` stays the filing sweep's own name (it
  is what the dedup path and every evidence line key on), and `predicate-kind` selects the
  mechanism. See design.md Decision 1 for why this differs from the `drift-source:
  verify-cmd-sweep` shape sketched in the originating request.
- **Command execution is bounded and shell-free by construction**: argv list only (a string
  `predicate-cmd` is an error, never shell-split), `shell=False`, cwd pinned to the brief's repo,
  a per-command timeout, and a cap on how many commands one brief may run. Every one of those
  limits classifies as *error* — which falls through to the existing human-in-the-loop flow —
  never as *resolved*.
- **Evidence lines carry the actual re-run transcript.** `format_still_true_evidence` and
  `format_resolved_closure_note` grow a transcript section (command line, exit code, truncated
  output) for command-based predicates, so an auto-closure satisfies PR #601's `done()` closure
  evidence gate by construction instead of tripping it.
- **Phase 5.5's skill doc documents the capture-side contract** an external sweep must satisfy to
  opt in, alongside the existing outcome table.

Not in scope: changing `behindthedash/devops`'s `fleet-workflow-hygiene-guard.py` to stamp the
new frontmatter. That is a separate repo and a separate PR; this change defines and ships the
contract it will adopt.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `stale-brief-precheck`: the predicate capture, re-check dispatch, still-true, and resolved
  requirements generalize from "a registered Python re-check function over task files" to "a
  registered predicate *or* a captured command", and both evidence-recording requirements
  additionally require the re-run transcript when the predicate is command-based. Two new
  requirements cover the command predicate's exit-status classification and its execution bounds.

## Impact

- `src/worktrail/router/check_brief_predicate.py` — generic command handler, dispatch fallback,
  transcript-aware evidence formatters, JSON `evidence` field on `recheck()`'s result.
- `tests/router/test_check_brief_predicate.py` — coverage for classification, every error path,
  the execution bounds, and an assertion that the generated closure note passes
  `work_queue._reverification_claim_missing_evidence`.
- `skills/worktrail-go/references/brief-staleness-check.md` — outcome table, evidence/closure
  examples, and the capture-side contract for external sweeps.
- `pyproject.toml` — version bump (`src/worktrail/**` changes; `CI: Version Bump Check`).
- No new console script, no new dependency: `worktrail-recheck-brief-predicate` and its
  `--repo/--brief/--json` interface are unchanged.
- Downstream (separate PR, separate repo): `behindthedash/devops`
  `scripts/fleet-workflow-hygiene-guard.py` stamps `predicate-kind`/`predicate-cmd` on the briefs
  it files.

## Why

The `worktrail-go BRIEF-ID` intake-triage gate is two Bash calls: step 1 redirects
`--evaluate-brief-triage`'s stdout to a verdict file, step 2 applies that file
(`skills/worktrail-go/SKILL.md`, Phase 2). The documented path is derived from the brief id
alone -- `${TMPDIR:-/tmp}/triage-verdict-${BRIEF_ID}.json` -- and the gate runs for any direct
`worktrail-go BRIEF-ID` dispatch, which is also what a scheduled queue-triage one-shot is. Two
concurrent triages of the same brief therefore share one file, and the apply side reads
whatever path it is given (`--apply-brief-triage-file`): the claim guard refuses a second
claim on the brief, but the verdict file has no such guard.

Observed twice on 2026-10-02 (work-queue brief `20261002-203443-triage-verdict-temp-file-race`):

- brief `20260930-191011`: an interactive session and a scheduled dispatch both ran with the
  identical `.../triage-verdict-20260930-191011.json` path in argv; whichever evaluate wrote
  last won, and the loser's apply could consume the winner's verdict (benign only because both
  evaluates reached the same verdict).
- brief `20260930-190934`: two writers interleaved into one file -- the reader saw its own
  JSON followed by the other writer's prose -- and the path was then removed under it
  (`Errno 2`). The other triager had already applied a `work-directly` verdict, rewriting the
  brief's focus; this session held a stale `keep` verdict against the rewritten brief and
  would have applied it had its apply succeeded.

The batch pipeline is not exposed to either failure: it writes a run-scoped
`<out_dir>/verdict.json` (`queue_triage.write_verdict_file`). The collision is specific to the
SKILL.md-documented per-brief temp path.

## What Changes

- **Scope the interactive verdict file to the dispatch.** The gate derives the path from the
  brief id *and* `$INVOCATION_CONTEXT_DISPATCH_ID` -- the claim identity the same dispatch
  already threads through every `claim` call -- so two concurrent triages of one brief write
  two different files, and each applies its own. The path stays re-derivable in both steps
  (the archived file-form design's requirement); no mktemp/pid state has to be carried between
  Bash calls.
- **Give the verdict a provenance guard.** `--evaluate-brief-triage` records a `brief_digest`
  (SHA-256 of the brief's bytes as read for evaluation) on the verdict it prints; the
  single-brief apply verifies it against the brief's current content before applying or
  previewing. A mismatch refuses with `blocked_verdict_stale: <id> (brief content changed
  since evaluation)` (exit 2), and a file-form payload that carries no digest refuses with
  `blocked_verdict_unattributed: <path>` (exit 2). The check sits behind the existing
  brief-ownership guard, so `blocked_brief_owned` keeps precedence, and the inline
  `--apply-brief-triage` form is unchanged for digest-less payloads.
- **Consume the verdict file on a confirmed apply.** With `--confirm`, the file form removes
  the file once it has been read and parsed, so one verdict file is applied at most once and a
  retried apply fails closed; a preview leaves the file in place.
- The `worktrail-go` gate documents the dispatch-scoped path and the two new refusals as cases
  that do not proceed to apply (remedy: re-run the evaluate step against the brief's current
  content).

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `intake-triage`: the interactive evaluate/apply handoff gets a dispatch-scoped verdict path
  and a content-provenance guard, so a verdict produced by another dispatch -- or against a
  brief state that no longer exists -- can never be applied silently.

## Impact

- `skills/worktrail-go/SKILL.md` -- Phase 2 gate: both `VERDICT_FILE` expressions, the
  hold-the-path note, and the new exit-2 branches.
- `src/worktrail/router/skill_dispatch.py` -- digest computation and stamping in
  `evaluate_single_brief()`, verification in `apply_single_brief_verdict()`, the two refusals
  and file consumption in the `--apply-brief-triage-file` branch.
- `src/worktrail/workqueue/queue_triage.py` -- `Verdict.brief_digest` (a defaulted field; the
  batch pipeline is unchanged apart from the new null key in its `verdict.json` entries).
- `tests/router/test_skill_dispatch.py` and
  `tests/router/test_skill_prose_enforcement_coverage.py` -- provenance, refusal, consumption,
  and gate-prose coverage.

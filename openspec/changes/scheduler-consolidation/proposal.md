## Why

`full-real` carries two schedulers: the serial path (fan-out → integrate →
verify, inline in `_full_real_inner`) and `_pipeline_scheduler` (integrate+
verify per group, overlapping fan-out). Every state-machine bug has had to be
found and fixed twice — at least four documented pairs: quarantine journal
persistence (#221/#223), tail dispatch on resume (#235/#245), post-verify
MERGED stamping (fixed for serial only in #252, pipeline had it all along),
and the pipeline-only `_record_group_fn` semantics. The duplicated surface is
the single largest structural driver of the fix-the-fix loop the v1.0 freeze
exists to end.

## What Changes

Two stages, deliberately split so the deletion happens with fresh context and
full harness protection rather than at the tail of a stabilization push:

- **Stage 1 (this change, v1.0):** the pipelined engine becomes the DEFAULT
  for `worktrail-live full-real`. `--pipeline` is kept as a no-op affirmation;
  a new `--sequential` escape hatch runs the legacy serial path with a loud
  deprecation warning. Policy: the serial path is FROZEN — it receives no new
  fixes; every state-machine fix lands on the pipelined engine only. (GO-driven
  runs already passed `--pipeline` explicitly, so production behavior is
  already pipelined; this aligns the bare CLI with practice.)
- **Stage 2 (v1.1):** delete the serial branch of `_full_real_inner` (the
  fan-out tick loop + `=== INTEGRATE ===`/`=== VERIFY ===` tail), route
  `--sequential` to a hard error, migrate the ~10 test files that pin serial
  seams, and decide `integrate.finish_real`'s disposition (it becomes
  live-unused; keep only if an external consumer exists, else remove with its
  tests).

## Capabilities

**Modified Capabilities**
- `orchestrator-full-real`: single supported scheduler (pipelined), serial
  deprecated → removed.

## Impact

- `src/worktrail/orchestrator/live.py` (CLI defaults, deprecation warning;
  stage 2: serial-branch deletion)
- `skills/worktrail-go/references/subagent-prompts.md`,
  `skills/worktrail-sdd-workflow/references/pipeline-details.md` (docs)
- Stage 2: `tests/orchestrator/{test_live_pipeline_flag,test_full_real_tail_dispatch,
  test_quarantine_journal_persistence,...}` and `integrate.finish_real`.
- The lifecycle harness (`tests/orchestrator/lifecycle/`) covers BOTH engines
  across formats × fresh/kill-resume and is the safety net for stage 2; its
  sequential matrix leg is deleted together with the path it exercises.

## Context

`$WORK_QUEUE_DIR/queue/` (default `~/work-queue/queue/`) holds handoff briefs captured by
`worktrail-handoff` across every repo the operator works in. Nothing currently re-evaluates a
brief after capture: it sits until an operator manually claims it (interactively, or via
`worktrail-drain`'s `worktrail-go auto` loop) or manually notices it is stale. The 2026-07-31
pilot (documented only in a work-queue handoff brief, not in code) showed that a fleet of
per-repo evaluator agents can do this judgment reliably and cheaply enough to run periodically,
provided the evaluation stays bounded (evidence-required, capped tool calls, fail-open) and a
human stays the only one who can actually close a brief.

Existing building blocks this reuses directly, unchanged:
- `worktrail.workqueue.work_queue`: `list_queue()` (queue inventory), `claim()`/`done()`
  (the only supported way to close a brief), `read_frontmatter()` (via
  `worktrail.shared.brief_frontmatter`) for per-brief metadata (`repo:`, `created:`,
  `recommended-route:`).
- `worktrail.orchestrator.spawnlib.spawn_agent()`: cold headless-worker invocation with
  infra-retry, capacity-gate awareness, and structured `SpawnResult` (`.text`, `.usage`) —
  the same primitive `drain.py` and the parallel orchestrator's task fan-out use.

## Goals / Non-Goals

**Goals:**
- Turn the pilot's manual repo-grouped evaluation into a repeatable, scriptable `evaluate`
  step producing a report + machine-applyable verdict file.
- Turn the pilot's manual claim→done / in-place-edit cleanup into a scriptable `apply` step
  that only ever executes verdicts a human has already reviewed — never a step that closes a
  brief on its own initiative.
- Preserve every pilot lesson learned (repo-fetch-first, evidence citation, tool-call cap,
  fail-open-to-keep, archived-repo check, memory check before "alarm") as durable prompt
  text, not tribal knowledge in a handoff brief.
- Make repeated runs cheap: a brief triaged recently is skipped, not re-spent.

**Non-Goals:**
- Auto-closing briefs without operator review. The pilot's entire value was the operator
  reviewing a proposed kill list before anything closed; this change does not remove that
  gate, ever — there is no `--yes`/`--auto-apply` flag.
- Real-time or per-brief-capture evaluation. This is a periodic batch process (monthly /
  pre-drain-weekly), not a replacement for `worktrail-drain`'s per-iteration work.
- A new orchestration engine. `spawn_agent` and `work_queue.py`'s claim/done already cover
  everything needed; this change only composes them.

## Decisions

**Module home: `src/worktrail/workqueue/queue_triage.py`, console script
`worktrail-queue-triage`.** The capability's data model (queue briefs, claim/done) is
`workqueue/`'s; `drain/` is a sibling consumer of the same queue and of `spawnlib`, not a
place this logic needs to live inside. Keeping it in `workqueue/` also means it can call
`work_queue.claim()`/`work_queue.done()` as plain in-process function calls (both modules
already live in the same package) instead of spawning `worktrail-work-queue` as a subprocess
the way `drain.py` does — `drain.py` shells out because its evaluator step *is* a full
`worktrail-go auto` one-shot subprocess with its own process boundary; `queue_triage.py`'s
apply step is plain Python running in the invoking process, so an in-process call is simpler
and doesn't pay a second subprocess's startup cost per brief.

**Two subcommands, not one (`evaluate` then `apply`), with the verdict file as the only
hand-off between them.** This is the mechanism that keeps "never auto-close" true: `evaluate`
cannot mutate the queue at all (no `claim`/`done` import even used in that code path), so a
bug in the evaluator prompt or in verdict parsing has no destructive blast radius — worst
case is a wrong recommendation in a file the operator reads before doing anything. `apply`
takes an explicit `--verdict-file` and, by default, also requires `--confirm` per verdict
batch (see Risks) so a scripted/cron `evaluate` can run unattended while `apply` never can.

**One evaluator spawn per repo group, not per brief.** Mirrors the pilot exactly (6 agents
for 65 briefs, grouped by repo) and amortizes the "fetch the target repo" step the lessons
call out as mandatory — fetching once per repo instead of once per brief is both cheaper and
avoids a group's briefs being judged against inconsistent repo state if evaluated
sequentially. Briefs with `repo: null` (cross-cutting, this change's own brief being one
example) form their own group evaluated without a repo fetch step.

**Verdict schema.** Each brief's verdict is:
```json
{"brief_id": "...", "verdict": "keep|stale-close|needs-update|duplicate-of",
 "duplicate_of": "brief-id or null", "evidence": "...", "confidence": "high|medium|low"}
```
`evidence` is mandatory non-empty text (a PR/commit/file reference) for every verdict except
`keep` on a fail-open/undecidable case, where the evaluator records why it could not decide
(this is itself the evidence for why `keep` was chosen). A verdict missing required evidence
fails validation and the brief falls back to `keep` — matching fail-open, not silently
dropped.

**Dedup marker.** `## Triage <ISO date>` as a body section (matching the pilot's own format).
`evaluate`'s inventory step skips (never fetches into a group, never spends model tokens on)
any brief whose most recent such section is newer than `--skip-if-triaged-within-days`
(default 25, i.e. skip re-evaluating within a monthly cadence). Implemented as a plain regex
scan over the brief body, not a new frontmatter field — the pilot already wrote this section
shape by hand for its 14 `needs-update` briefs; codifying the same shape means those pilot
edits are already correctly recognized as "triaged" without a migration.

**Evaluator prompt template, one per repo group.** A single structured prompt listing every
brief in the group (id + focus + created date), instructing the evaluator to: (1) `gh repo
view --json isArchived,name -- <repo>` first — an archived/renamed repo invalidates every
brief in the group as `stale-close` immediately, no per-brief evidence needed beyond the
archival fact itself; (2) otherwise, for each brief, spend at most ~3-4 tool calls
(`git log`, `gh pr list --search`, `grep`) confirming or refuting the brief's premise; (3)
before flagging anything as a live operational concern, check whether a relevant memory file
already documents it as expected/known state (grep the operator's memory index passed in the
prompt) rather than raising a false alarm; (4) fail open to `keep` whenever evidence is
inconclusive. The prompt is a module-level template string (not a file) so it stays under
`tests/workqueue/test_queue_triage.py`'s direct assertion reach, matching how `drain.py`
keeps `PROMPT` as an importable constant.

**Report + verdict file live outside the repo**, alongside run records: default
`~/.go/triage/<run-id>/report.md` + `verdict.json` (override via `--out-dir`), not committed
anywhere — this is operational output, not a durable project artifact (same reasoning
`run_record_dir` already uses for `~/.go/runs`).

## Risks / Trade-offs

- [Risk] A wrong verdict closes a brief that still mattered → [Mitigation] `apply` never
  runs unattended by default: it prints every `stale-close`/`duplicate-of`/`needs-update`
  action it is about to take and requires `--confirm` (or, for interactive use via `/go`,
  the operator reviews the Markdown report first). `keep` requires no confirmation since it
  is a no-op.
- [Risk] Evaluator cost scales with queue size; an unbounded queue makes a run expensive →
  [Mitigation] per-brief tool-call cap in the prompt, dedup marker skips recently-triaged
  briefs, and the design doc's own recommended cadence (monthly/pre-drain-weekly) is stated
  in the module docstring and `--help`, mirroring `drain.py`'s cost-conscious docstring
  precedent.
- [Risk] `duplicate-of` verdicts could point at a brief id that itself gets closed in the
  same run, leaving a dangling reference → [Mitigation] `apply` resolves `duplicate_of`
  targets against the *pre-apply* queue snapshot and refuses (falls back to `keep`, logs a
  warning) a verdict whose target is not `keep`/unresolved in the same batch.
- [Risk] Archived-repo detection depends on `gh` auth/availability → [Mitigation] a `gh`
  failure here is treated as inconclusive (fail-open to `keep`), never as "confirmed
  archived" — never infer archival from an error.

## Migration Plan

Purely additive; no existing data or behavior changes. First real run should be invoked
interactively with `apply` reviewed by hand at least once before considering it for a
scheduled cron/`/loop`, matching how `drain.py` was hardened before being trusted unattended.

## Open Questions

None outstanding — scope, schema, and mitigations above are sufficient to implement.

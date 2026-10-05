## Context

See proposal.md — Why for motivation. State this design builds on, verified in this
checkout on 2026-10-05 (branch `spec/mechanical-fix-direct-path`, off `main` @ `6cfc7f51`):

- The modify pipeline (`skills/worktrail-sdd-workflow/references/pipeline-details.md#modify-pipeline`)
  always reaches step 6's orchestrator launch; `skills/worktrail-go/references/routes.md` §F
  step 5 describes the 1-task case as "single-worker orchestrate for 1-task fixes" and
  sanctions a direct fix-branch worktree (no orchestration) only for **unspecced** code.
- The only existing direct-execution precedent is that Route F fix-branch path
  (`subagent-prompts.md#fix-branch-worktree-setup`), whose trust level this design reuses.
- The sibling change `worker-fold-in-policy` (branch `spec/worker-fold-in-policy`, not yet
  merged) fixes the "mechanical" bar for in-run fold-ins: verified defect, files already in
  the unit's declared scope, mechanical (restores documented/established intent; no new
  design/API/behavior contract), capped at 2 fold-ins / ~20 changed lines per task.
- Specimen measurement (used to calibrate D2): change
  `active-conflicts-staleness-reconciliation-missing-root-fail-loud` (PR #1431) — 1 task,
  2 declared files (`src/worktrail/router/run_record.py`,
  `tests/router/test_run_record.py`), 15 added / 1 deleted source lines. Its delta spec is
  63 non-blank lines but only **24** of them are absent from the pre-change base spec
  (`git show 4f0a36dd^:openspec/specs/active-conflicts-staleness-reconciliation/spec.md`).
- The routing cassette is a single file,
  `src/worktrail/router/cassettes/routing_cassette.json`. (The path
  `scripts/cassettes/routing_cassette.json` that `routes.md` §J's prose names does not
  exist — stale prose, out of this change's scope; captured separately.)
- `taskformats/openspec/schema.py`'s `parse_tasks_md` already parses a `tasks.md` into
  tasks with declared `files:` and tail kinds — the same parser `worktrail-compile` uses to
  seed RunPlans; `test_plugin_surface.py` validates every `worktrail-*` command token in
  skill docs against real entry points.

Constraints: router scripts are stdlib-only deterministic verdict surfaces (`classify.py`
is the model); thresholds live as named constants, no policy knobs; docs and script ship in
lockstep (plugin-surface test).

## Goals / Non-Goals

**Goals:**

- A change that reduces to one mechanical fix skips orchestrator launch, task worktrees,
  and worker spawns, while every existing gate (compile/`.compile-ok`, `openspec validate`,
  pre-PR gate, CI) still runs.
- The branch decision is computed by a registered script and recorded, never
  prose-remembered or prose-judged.
- "Mechanical" means the same thing here as in the sibling fold-in policy.

**Non-Goals:**

- Speeding up multi-task runs, or changing the orchestrator, compile, land-pr, sync, or any
  routing/classification behavior (the routing cassette is untouched).
- Devkit-format specs: the modify pipeline authors OpenSpec changes via `/opsx:propose`,
  and the gate reads that layout only.
- Any configurability: no policy keys, no environment overrides, no per-repo thresholds.

## Decisions

### D1 — The gate is a stdlib-only router script reusing the OpenSpec tasks parser

Chosen: `src/worktrail/router/modify_direct_gate.py` exposing
`worktrail-modify-direct-gate <change-dir> [--json]`, parsing `tasks.md` with
`worktrail.taskformats.openspec.schema.parse_tasks_md` (declared `files:` and task count
come from the parser the rest of the system already trusts). Alternatives considered:
(a) fold the logic into `conductor/compile.py` — rejected because compile is the scope
*gate* (and may spawn a model for undeclared changes), while this must be a cheap, pure,
model-free verdict with its own contract; (b) shell out to the `openspec` CLI — rejected:
an undeclared runtime dependency and version drift for data the repo already parses
in-process (`check_compile_markers.py` does the same for `.compile-ok`); (c) a prose
checklist in the skill — rejected: code-enforcing the decision is this change's point.

### D2 — "Small delta" is measured as new-to-base lines across the delta specs

The gate's size metric counts, across the change's `specs/**/spec.md` files, the non-blank
lines not present in the corresponding base spec: `<openspec-root>/specs/<capability>/spec.md`,
where the openspec root is the nearest ancestor of the change directory that contains a
`specs/` directory (this resolves both `openspec/changes/<id>` and the archived
`openspec/changes/archive/<id>` layout); a missing base spec counts every line as new
(conservative). Rationale: OpenSpec deltas restate MODIFIED requirements in full, so
raw delta length does not measure the change — the specimen's raw 63 lines exceed any cap
that still rejects obviously non-mechanical changes, while its true new text is 24 lines.
Alternatives considered: raw non-blank delta lines (rejected — fails the motivating
specimen); requirement/scenario counts (rejected — the specimen has 8 scenarios yet was
mechanical; structure does not correlate); a model estimate (rejected — breaks
determinism/stdlib-only). The `## MODIFIED Requirements` heading itself counts as 1–2 new
lines; negligible at the cap and documented rather than special-cased.

### D3 — Thresholds and the routing surface are named module constants

`MAX_TASKS = 1`, `MAX_FILES = 3`, `MAX_ESTIMATED_CHANGED_LINES = 40`, and
`ROUTING_SURFACE` — the route classifier and its classification-support modules, the
risk/policy modules, and the cassette locations:

- `src/worktrail/router/classify.py`, `classify_handoff.py`, `risk_judgment.py`,
  `policy.py`, `routing_cli.py`
- `src/worktrail/router/cassettes/` and `scripts/cassettes/` (the legacy path `routes.md`
  §J's prose names, kept defensively so a cassette reintroduced there is still caught)

The surface's rule is "the modules that decide a request's route, risk, gates, or agent
selection, plus the routing cassettes" — deliberately **not** the whole `router/` package:
the specimen's fix was `run_record.py`, so a package-wide ban would exclude the motivating
class. Skills prose is also not on the surface: a one-line stale-prose fix is exactly a
mechanical candidate, and the brief scopes the ban to classification/policy/cassettes.
Alternatives: whole `src/worktrail/router/` (rejected, kills the motivation); Route J's
full "production code" framing including skills (rejected, same reason). Widening the list
later is a one-constant change with tests, not a knob.

### D4 — Verdict contract: exit 0 for both verdicts, exit 2 for usage error

`--json` prints exactly the four documented keys; `reason` is `<token>: <detail>` with the
stable tokens `eligible`, `change_shape`, `task_count`, `files_undeclared`,
`files_over_cap`, `routing_surface`, `delta_over_cap`, evaluated in that fixed order so the
reported first failure is deterministic. Nonzero exit never means ineligible — the same
convention `classify.py` follows (a verdict is an answer, not an error). Tests can assert
token + numbers without pinning prose.

### D5 — The script decides; the pipeline records; the script never mutates

The gate is a pure, read-only predicate (no writes, no run-record coupling, no `--run`
flag): the modify pipeline's new step invokes it and branches on the JSON, then appends the
`decisions` entry itself — the same way every other run-record write in this pipeline works
(`run_record.py append "$RUN" decisions ...`, per `routes.md`). Alternatives: give the
gate a `--run` flag that appends — rejected: a side effect inside a verdict surface, and
it exceeds the four-key contract the brief fixed. The recording step takes the gate's
`reason` string verbatim, so the audit trail is mechanically derived from the decision.

### D6 — The direct branch lands one PR through the existing landing machinery, checkpoint-mode

Direct mode reuses `worktrail-land-pr` (with the pre-PR gate and label computation exactly
as the docs' landing blocks already orchestrate them) rather than opening a PR by hand.
One PR carries the change artifacts and the implementation, mirroring what the
orchestrator's single group PR would have contained for a 1-task change. Landing runs in
checkpoint mode so the run record stays open for the identical sync step
(`#sync-before-teardown`, whose own sync PR also lands checkpointed) and teardown, then the
route's normal completion finishes the record — verified against `land_pr.py`'s
`_finish_or_checkpoint` (terminal mode finishes; checkpoint mode appends a decision).
Alternative: terminal landing — rejected: it would finish the record before sync's own
`--run`-attached landing and before teardown.

### D7 — The gate runs before the compile step

The gate is inserted as the modify pipeline step immediately before the existing
scope-check/compile step; compile, the pre-launch uncommitted-output guard, and every
later step then run unchanged on the eligible path. Because the gate requires declared
`files:` on the single task, a direct-eligible change always takes compile's no-model
seed path — the direct branch pays no model call until implementation itself. Compile's
`.compile-ok` still lands in the change directory and is committed, so CI's `Scope check`
marker requirement is satisfied on both branches. Alternative: branch after compile —
rejected: it would pay the pipeline's heaviest preparation before deciding not to use it,
and it would blur which step owns the decision.

### D8 — Documentation surface: pipeline details own the branch; routes.md and SKILL.md name it

`pipeline-details.md#modify-pipeline` gains the gate step and the direct branch (with the
retained gates listed); `skills/worktrail-sdd-workflow/SKILL.md`'s route table row for F/G
names the direct branch; `routes.md` §F step 5's "(single-worker orchestrate for 1-task
fixes)" parenthetical is replaced with the direct-or-orchestrated description and §G's
pointer names the same pipeline. Rationale: leaving §F as-is would have the playbook
contradict the pipeline it points at on the exact case this change handles.

## Risks / Trade-offs

- **The accepted trade-off (explicit).** A direct run forgoes the orchestrator's
  independent reviewer and its worktree isolation. This is acceptable for a single
  mechanical fix under the caps (1 task, ≤3 files, ≤40 new delta lines, no routing
  surface) because it is the same trust level `routes.md` §F already extends to direct
  fix-branch worktrees for unspecced code, and because no gate — compile/`.compile-ok`,
  `openspec validate`, pre-PR gate, CI, or land-pr's re-compile — is skipped; only the
  fan-out machinery is.
- **Adverse-effect argument (Route J).** This change adds a new script and a new pipeline
  branch; it weakens no existing gate and alters no routing behavior — the cassette is
  untouched, and the routing-surface exclusion makes the classification/policy code
  structurally unreachable from direct mode. The spec pins "direct mode relaxes no
  existing gate" as a requirement with its own scenarios.
- **The gate checks declared scope only** — a misdeclared file set is outside any
  predicate's reach. Mitigation: the same trust model compile and fold-in validation
  already use (declared scope is the unit of discipline), plus the caps, the recorded
  reason, the structural routing exclusion, and the PR still passing normal CI and review
  surfaces.
- **Proxy false negatives** (a mechanical fix whose delta spec restates with wording churn
  exceeds the cap) → it falls back to the orchestrated path: costs time, never
  correctness. False positives (small prose delta hiding a larger implementation) are
  bounded by the 1-task/≤3-file caps and still land through every gate; this is a
  heuristic bound, not a proof.
- **Doc drift** (the branch is prose-executed) → the gate's JSON is the documented sole
  input, the command token is pinned by `test_plugin_surface.py`, and the run record's
  `modify-direct-mode:` decision entry makes any divergence auditable.

## Migration Plan

- No data or config migration; nothing persists between runs. Ship in one PR: gate script
  + tests first (the entry point must exist before docs reference it, or
  `test_plugin_surface.py` fails), then the docs edits.
- Rollback is a straight revert — the orchestrated path is untouched, and no runtime state
  or policy key depends on the gate.

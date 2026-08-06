---
name: worktrail-sdd-workflow
description: "Internal SDD workflow executor invoked by /go only. Do not call directly. Executes classified routes A–J: feature planning, implementation (parallel orchestration, PR generation), defect repair, refactoring, spec changes, investigation, and workflow evolution."
allowed-tools: Bash, Read, Glob, Grep, AskUserQuestion, Skill, Agent
argument-hint: "[repo-name-or-path|handoff|handoff:ID] [new|implement|continue|pr|brainstorm|route:A-J] [spec-id]"
---

# `sdd-workflow` — the SDD conductor (v2)

## Overview

Pipeline: `intake (route:X) → record start → execute route → validate → PR → merge gate → outcome`

Route playbooks: `../worktrail-go/references/routes.md`.

## When to Use

**INTERNAL ONLY.** Do not invoke directly — all SDD work must go through `/go`.

## Instructions

- **Authority:** code + tests = truth for what *is*; spec/contracts = what
  *should be*; `knowledge-graph.json` = self-healing cache; **never read
  `reviews/*-review.md` as current state.**
- **Business outcome first:** before planning/implementing, identify the
  user/business problem, who benefits, the smallest complete outcome, and
  whether the request fits the product's current scope. Record material
  assumptions in the run record. Don't build technically interesting work with
  no defined outcome.
- **Terse output:** one short status line per phase; print script output, don't
  paraphrase; batch independent commands in one message.
- **Token discipline:** retrieval order = instructions → controlling artifact →
  GitNexus (base-branch map; worktree diff wins) → exact files → targeted grep →
  tests → merged PRs → broad search last. Load only the selected route playbook.
  Hand subagents pointers, not blobs. The run record is the working memory —
  update it instead of re-reading materials.

**No script resolution needed.** Every command below is a console script installed by the
`worktrail` package (`worktrail-run-record`, `worktrail-live`, `worktrail-pre-pr-gate`, …), on
`PATH`. If one is missing, stop and report that `worktrail` is not installed.

### Step 0 — Direct-invocation guard

IF no `route:X` positional arg is present, THEN print the redirect message and stop:

```
sdd-workflow is an internal executor. Use /go for all engineering work.
```

Otherwise, proceed to Phase 1.

### Phase 1 — Intake (parse args; bypass interactive screens)

Three entry paths, detected in order:

- **Seeded-dispatch** — input contains all five labels (`Repo:`, `Base branch:`, `Route:`, `Spec:`, `Run record path:`). Detect, parse fields, skip to Phase 6. See `#seeded-dispatch` in `../worktrail-go/references/subagent-prompts.md` for detection criteria and bypass behavior.
- **Handoff-seed** — first arg is `handoff` or `handoff:ID`. See `#handoff-seed` in subagent-prompts.md.
- **Direct-intent** — positional args for repo hint, intent, spec name. Standard flow below.

Positional args (all optional); hold as `$ARG_REPO`, `$ARG_INTENT`, `$ARG_SPEC`.
Unrecognised value → warn once and fall back.

| Pos | Values | Effect |
|---|---|---|
| 1 | repo name or path hint | passed to resolver as `--hint` |
| 1 | `handoff` or `handoff:ID` (case-insensitive) | handoff-seed mode — NOT a repo hint; see `#handoff-seed` in subagent-prompts.md. The brief's optional `recommended-route:` frontmatter feeds classification (`--handoff-route`). |
| 2 | `new` `implement` `continue` `pr` `brainstorm` | v1 intent — skips classification, maps to routes C+D / D / E / E(pr) / A |
| 2 | `route:A`..`route:J` | explicit route override |
| 3 | spec folder name (e.g. `003-payments`) | skips spec picker |

### Phase 6 — Start the run record

```bash
worktrail-run-record start --repo "$REPO" --request "<summary>" \
  --route <X> --risk <level> --reason "<classifier reason>" \
  --base-branch "$BASE" --base-commit "$(git -C "$REPO" rev-parse --short HEAD)"
```

Hold the returned `path` as `$RUN`. Through the run: `set $RUN status <phase>`
at transitions; `append $RUN decisions|assumptions|deferred_work|...` as they
happen. End every run with `finish --status <one of the ten completion states>`
— vague completion language is forbidden.

**Log every manual rescue** via `run_record.py intervention $RUN --category <category> --minutes <est> --tokens <est> --note "..."` (categories + rationale: go-design.md §interventions).

### Phase 7 — Execute the route

Read **only** the selected playbook section in `../worktrail-go/references/routes.md`:

| Route | Playbook | Engine |
|---|---|---|
| A idea-discovery | routes.md §A | brainstorm (discovery framing) |
| B epic-planning | routes.md §B | epic doc + feature decomposition |
| C feature-planning | routes.md §C | `new` pipeline (pipeline-details.md#new-pipeline), then explicit C→D transition |
| D implementation | routes.md §D | `new`/`implement` pipelines (pipeline-details.md) |
| E continue/resume | routes.md §E | state restore + re-entry (incl. PR/CI repair) |
| F defect-repair | routes.md §F | `modify` pipeline (pipeline-details.md#modify-pipeline), change-spec bugfix |
| G spec-change | routes.md §G | `modify` pipeline (pipeline-details.md#modify-pipeline), change-spec delta |
| H refactor/debt | routes.md §H | characterization tests → narrow implement |
| I investigation | routes.md §I | evidence only → recommended next route |
| J workflow-evolution | routes.md §J | this repo, cassette-gated |

Specialist skills (security review, typescript/react review, render, …) are
loaded by the playbook at the phase that needs them — never all up front.

### Pipeline — `new` (Route C, and Route D when no spec exists)

Step sequence: overlap-check → worktree → brainstorm → spec-check → precheck → (optional technical-plan) → spec-to-tasks → stale-spec-check → orchestrator → sync. See `references/pipeline-details.md#new-pipeline` for full steps with bash commands and guards.

Route C closeout: spec PR + implementation-intent transition. Requested intent
continues into Route D in the same run; planning-only intent finishes as
`planned_ready_for_implementation`; unknown intent asks once and records the
decision. Never close a requested Route-C brief or create a follow-up handoff
at the spec/task boundary.

### Pipeline — `implement` (Route D, spec already on base)

Step sequence: pick spec → stale-spec-check → precheck → orchestrator → sync. See `references/pipeline-details.md#implement-pipeline` for full steps.

### Phase 8 — Merge gate and outcome

For every route whose documented completion requires a PR (`completed_pr_open`,
`completed_and_merged`, or `completed_awaiting_human_approval`), the run is not
done when implementation and validation finish locally. The executor must pass
the scope-completeness gate and pre-PR test gate, commit, push, open or update
the PR, then evaluate the merge gate before calling `run_record.py finish`.

**Scope completeness gate — before any PR-producing route.** Re-read the
controlling request, handoff Focus/Suggested approach, specification acceptance
criteria, and route playbook. For every requested outcome, record one
`scope-review` entry in the run record with implementation evidence:

```bash
worktrail-run-record scope-review "$RUN" \
  --item "<requested outcome>" --status complete --evidence "<test or artifact>"
```

Do not create a follow-up handoff for an incomplete requested outcome. Implement
it before the PR, or stop with a real blocker/product-decision state. An item
may be recorded as `out-of-scope` only with a reason beginning `different
purpose:` or `user approved:`. A `blocked` item fails the pre-PR gate.

**Mandatory pre-PR test gate — every PR-producing route, every repo, one-off
claude/codex workers included.** Before `gh pr create` (or updating an existing
PR's head), run the gate from the worktree root and require exit 0:

```bash
worktrail-preflight run --repo "$PWD" --run "$RUN" --risk "$RISK_LEVEL" --gates "$GATES" --target-branch "$BASE" --route "$ROUTE"
```

`worktrail-preflight run` executes `pre_pr_gate.py` in-process (identical
checks and exit codes to calling `worktrail-pre-pr-gate` directly — it is a
strict superset, not an alternate path) and, on a zero exit, additionally
records a pass marker for the exact current tree **plus** the `go:risk-*`/
`go:no-automerge` labels this risk/gates combination requires. That marker is
what the machine-level `gh pr create` PreToolUse hook (delegating to
`worktrail-preflight check`) reads: a `gh pr create` invocation whose
`--label` flags don't match the recorded labels is denied at the tool-call
level, independent of whether this SKILL.md section ran correctly. Do not
call `worktrail-pre-pr-gate` directly for this step — it performs the same
checks but never records the marker, so the label enforcement below silently
never engages.

The gate resolves `pre_pr_cmd` (fallback: `integrate_smoke_cmd`) from the
target repo's `docs/specs/go-policy.yaml` and runs it, streaming output. Rules:

- Non-zero exit → do **NOT** open the PR. Fix the failures and re-run, or
  finish with a failure state quoting the gate output. Never bypass silently.
- Unconfigured repo → the gate fails by design (exit 2) with instructions;
  add `pre_pr_cmd` to the repo policy, or `pre_pr_cmd: skip` to opt out
  explicitly and record the skip in the PR body.
- Paste the gate's summary line (command + PASS, or the explicit skip) into
  the PR template's "Pre-PR Test Gate" section as evidence.
- The parallel-orchestrator group path already enforces its own gate via
  `--smoke-cmd`; group PRs opened by the orchestrator satisfy this
  requirement without a second run. Self-chosen "tests for the files I
  touched" runs do NOT satisfy it. Group PR labels are refreshed immediately
  before each new PR creation: `integrate.py` calls `pre_pr_gate.py
  --labels-only` per-group (via `_refresh_pr_labels()`), so every fresh PR
  reflects the current policy and required-check state without the
  orchestrator recalculating policy internally. Recycled/open PRs keep their
  original labels.

**Automerge labels (code-enforced at two points, not agent-narrated).** On a
PASS, the gate above also prints an `AUTOMERGE LABELS:` line —
`go:risk-<level>` always, plus `go:no-automerge` when
`policy.automerge_eligible()` is false. Pass those exact labels to
`gh pr create --label <label> [--label <label>]`; they must already exist in
the target repo (bootstrapped once via `gh label create`, not per-PR). Two
independent enforcement points now back this, so a skipped or wrong copy of
the labels doesn't silently reach GitHub: (1) locally, the `gh pr create`
PreToolUse hook denies the tool call itself when the labels it carries don't
match the marker `worktrail-preflight run` just recorded; (2) on GitHub, a
repo's own auto-merge automation (e.g. `.github/workflows/auto-merge.yml`)
reads `go:no-automerge` to skip/undo arming, covering PRs created outside
this hook's reach (e.g. a headless worker on a machine without it wired up).

For every PR produced:

1. PR body uses the template in routes.md (route, spec lineage, AC→evidence
   map, pre-PR gate evidence, risk, rollback, auto-merge recommendation).
2. Deterministic eligibility = `policy.automerge` × classifier `gates` × risk
   (see `policy.py automerge_eligible`), now also encoded as the PR's
   `go:risk-*`/`go:no-automerge` labels above; live conditions = required
   checks green, no unresolved threads, no conflicts, approvals satisfied.
3. Ineligible → deliver the PR and state the exact remaining approval needed.
4. Record `merge_decision` + `merge_result`, then
   `run_record.py finish --status <state> [--pr <url>]` and, whenever a PR
   was produced, immediately run `worktrail-ensure-pr-label --run "$RUN"` --
   the same post-hoc `go:risk-*` correction drain.py applies after its own
   spawned one-shots (`router/pr_labels.py`), reusing the risk level and PR
   URL the run record already carries (no new plumbing). This is what closes
   the gap for interactive and Codex in-session dispatch, which never spawn a
   subprocess for go's Phase 7 `poll_run.py` to observe -- headless
   Claude/OpenCode workers get the identical correction there instead. Then
   report the completion state + PR link + deferred handoffs as the final
   line.

Only routes with non-PR completion states (for example
`planned_ready_for_implementation` or `investigation_complete`) may stop
without commit/push/PR creation.

### Artifact policy

See `docs/design/history/go-v1-design.md` §6 (unchanged in v2): commit the durable SDD
record (spec, tasks, contracts, KG, epics); gitignore point-in-time scratch
(reviews, run scratch). Run records live outside the repo (`~/.go/runs`).

## Examples

**Example: /go dispatches a defect-repair request.** User types `/go fix the donation receipt date bug`. The `/go` skill classifies it as route F (defect-repair), then invokes: `sdd-workflow <repo> route:F <spec-id>`. sdd-workflow executes routes.md §F (reproduce → regression test → narrowest fix → PR) and finishes with `completed_pr_open`.

**Example: Direct invocation redirects.** User types bare `sdd-workflow`. Step 0 guard detects no `route:X` arg and prints: "sdd-workflow is an internal executor. Use /go for all engineering work." Stops without action.

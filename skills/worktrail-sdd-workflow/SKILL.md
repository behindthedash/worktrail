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

**INTERNAL ONLY.** Users do not invoke this skill directly — all SDD work enters
through `/go`. A prompt containing `[WORKTRAIL INTERNAL DISPATCH]` is the supported
adapter form produced after `/go` has selected this executor: honor it, execute
the supplied arguments directly, and never re-enter `worktrail-go`.

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
- **GitNexus/worktree boundary:** GitNexus normally indexes the canonical base checkout,
  not Worktrail's generated task/spec worktrees. In a worktree, use its actual files,
  `rg`, and tests as ground truth for the current branch; use GitNexus only for
  base-branch context and broader callers/dependents. If they disagree, assume the graph
  is stale relative to the branch and let the worktree win. Do not require or create a
  worktree-local `.gitnexus/` index. Before a rename, deletion, or PR, combine an
  unfiltered `rg "<name>" .` from the worktree root with GitNexus query/impact against
  the canonical repository.

**No script resolution needed.** Every command below is a console script installed by the
`worktrail` package (`worktrail-run-record`, `worktrail-live`, `worktrail-pre-pr-gate`, …), on
`PATH`. If one is missing, stop and report that `worktrail` is not installed.

### Step 0 — Direct-invocation guard

If the prompt contains `[WORKTRAIL INTERNAL DISPATCH]`, treat it as an authorized
adapter-to-executor handoff. Do not redirect to `/go`; apply the same `route:X`
argument validation below and proceed. `worktrail-skill-dispatch` independently
bounds this path to one nested dispatch and fails with
`blocked_internal_dispatch_recursion` if a child re-enters the front door.

IF no `route:X` positional arg is present, THEN print the redirect message and stop.
This applies to handoff-seed invocations too: callers must pass both
`handoff:<id>` and the resolved `route:X`:

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
| any | `by:<dispatch-id>` (labeled, matched by prefix — not positional) | handoff-seed mode only: the caller's `$INVOCATION_CONTEXT_DISPATCH_ID`. Hold as `$GO_DISPATCH_ID` and pass `--by "$GO_DISPATCH_ID"` on the handoff-seed claim call (`#handoff-seed` Step 3) so `claim()`'s `same_owner` result can tell this dispatch's own claim apart from a different dispatch's. Every `handoff:<id>` dispatch from `worktrail-go` carries this token (Dispatch Contract, `worktrail-go/SKILL.md`); its absence means an older caller or a non-`/go` invocation — fall back to an unqualified claim call (no `--by`), which yields `same_owner: null` and must be treated as "not confirmed mine," never as true. |
| any | `run:<path>` (labeled, matched by prefix — not positional) | native-skill dispatch only (both the handoff-seed and direct-intent forms): the caller's already-open run record path, i.e. `worktrail-go`'s own Phase 6 `$RUN`. Hold as `$GO_RUN_PATH` — Phase 6 below reuses it instead of starting a second run record. Its absence means an older caller or a non-`/go` invocation; fall back to Phase 6's normal `start` call. The seeded-dispatch entry path is unaffected — it already carries `$RUN` directly from the seed and never reaches this token. |

### Phase 6 — Start the run record

If `$GO_RUN_PATH` was parsed in Phase 1 (native-skill dispatch — see the Dispatch Contract
in `worktrail-go/SKILL.md`), **reuse it instead of starting a second run record** — this is
the fix for the orphaned-parent-record bug documented in
`docs/specs/research/dead-dispatch-backlog-investigation.md` (observations 5/6: every
native-skill dispatch that instead called `start` here left the parent's record stuck at
`route_selected` forever, since only the child's record ever reached `finish()`):

```bash
RUN="$GO_RUN_PATH"
worktrail-run-record set "$RUN" base_branch "$BASE"
worktrail-run-record set "$RUN" base_commit "$(git -C "$REPO" rev-parse --short HEAD)"
```

Otherwise — no `$GO_RUN_PATH` (an older caller or a non-`/go` invocation; the
seeded-dispatch path already skipped straight here in Phase 1 with its own attached `$RUN`
and never reaches this branch either) — start a fresh run record as before:

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
decision (`$AUTO_MODE=true`: no ask — take the planning-only default; every ask
site in route execution follows
`../worktrail-go/references/subagent-prompts.md#auto-mode-ask-fallbacks`).
Never close a requested Route-C brief or create a follow-up handoff
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
purpose:` or `user approved:` (any other reason is rejected at write time). A
`blocked` item fails the pre-PR gate. Entries are append-only, but the gate
judges only the latest entry per `--item`, so re-record an item to supersede an
earlier `blocked` or mis-phrased entry.

**Commit, compile, gate, push, open PR, watch CI.** Run the shared landing
pipeline from the worktree root; it atomically handles commit, compile-marker
verification, pre-PR gate, push, PR open/update, CI watch to a terminal
outcome, and run-record finish. Requires `--repo`, `--base`, `--title`,
`--summary` or `--summary-file`, `--route`, `--risk`, optional `--gates` and
`--commit-message`, and `--json`:

```bash
worktrail-land-pr --repo "$PWD" --base "$BASE" --run "$RUN" --title "$TITLE" \
  --summary-file /path/to/pr/body --route "$ROUTE" --risk "$RISK_LEVEL" \
  --gates "$GATES" --json
```

| Exit code | Outcome | State |
|---|---|---|
| 0 | `landed` | PR merged or auto-merge armed; run record finished. |
| 2 | `refused` | Rejected before push (dirty tree, compile gaps, preflight fail); remote untouched, re-run with fixes. |
| 3 | `code_defect` | PR open, CI failed, run record open for repair. |
| 3 | `review_threads_blocking` | PR open, review threads unresolved, run record open for gate action. |
| 4 | `ceiling` | Push succeeded but PR create failed, or 5 code-defect iterations exhausted. |

**On `code_defect` outcome:** Repair the failing check per
`../worktrail-go/references/ci-watch-loop.md` case 3 (diagnose root cause,
apply minimal patch, disarm auto-merge, push), then re-run the same
`worktrail-land-pr` command.

**On `review_threads_blocking` outcome:** Review and act on the unresolved
review threads per `../worktrail-go/references/ci-watch-loop.md` review-thread
gate (fix in code, push + re-run the gate, or record an explicit decision),
then re-run the same `worktrail-land-pr` command.

Only routes with non-PR completion states (for example
`planned_ready_for_implementation` or `investigation_complete`) may stop
without commit/push/PR creation. If a non-PR-completion run produces a PR
anyway — e.g. Route I committing its investigation note as a PR per §I — the
same `run_record.py finish --pr <url> ...` call above applies the correction
automatically; it keys off `$RUN`'s own `pull_request` field (a no-op when
unset), not the route or completion state, so there is nothing extra to do
here either. Gap observed live before this was code-enforced: datalena PR
#2228 (Route I, `investigation_complete`) merged a docs-only investigation
note with no `go:risk-*` label because the correction was then only a prose
instruction reachable from the PR-producing-route branch above, and a human
had to apply the label by hand before `CI: Auto-merge on open` would arm.

### Artifact policy

See `docs/design/history/go-v1-design.md` §6 (unchanged in v2): commit the durable SDD
record (spec, tasks, contracts, KG, epics); gitignore point-in-time scratch
(reviews, run scratch). Run records live outside the repo (`~/.worktrail/runs`).

## Examples

**Example: /go dispatches a defect-repair request.** User types `/go fix the donation receipt date bug`. The `/go` skill classifies it as route F (defect-repair), then invokes: `sdd-workflow <repo> route:F <spec-id>`. sdd-workflow executes routes.md §F (reproduce → regression test → narrowest fix → PR) and finishes with `completed_pr_open`.

**Example: Direct invocation redirects.** User types bare `sdd-workflow`. Step 0 guard detects no `route:X` arg and prints: "sdd-workflow is an internal executor. Use /go for all engineering work." Stops without action.

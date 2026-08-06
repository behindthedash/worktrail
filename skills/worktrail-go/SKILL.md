---
name: worktrail-go
description: >
  Use when the user invokes worktrail-go, asks to pick up queued work, resume active specs,
  implement or fix a spec, route engineering work, or orient across repositories.
  Renders the orientation dashboard, classifies free-text requests,
  claims/resumes handoff briefs, and dispatches SDD work without requiring the user
  to know sdd-workflow. Triggers: worktrail-go, worktrail-go help,
  worktrail-go fix, worktrail-go implement, worktrail-go route:F,
  worktrail-go BRIEF-ID, bare worktrail-go, multi-repo orientation.
argument-hint: "[help | free-text request | route:X | BRIEF-ID | REPO [intent keywords]]"
allowed-tools: Read, Bash, AskUserQuestion, Skill, Agent
---

# Go — Universal Engineering Front Door

## Overview

Universal engineering front door for a Worktrail workspace. Work invocations start with an orientation dashboard (active specs, in-flight briefs, ready queue), then classify the request and dispatch to the right executor. The `help` invocation is the read-only exception and delegates to `worktrail-help` without rendering or claiming work. `worktrail-sdd-workflow` is an internal executor — users access it only via `worktrail-go`.

## When to Use

- Bare `worktrail-go` — orientation dashboard + `AskUserQuestion` picker
- `worktrail-go help` — delegate to `worktrail-help`
- `worktrail-go drain [max-items] [repo]` — delegate to the unattended queue drain
- `worktrail-go fix X` — classify and dispatch a free-text request
- `worktrail-go implement spec 003` — dispatch to spec execution
- `worktrail-go route:F` or `worktrail-go REPO route:D spec-folder` — explicit route, no classification
- `worktrail-go BRIEF-ID` — claim or resume a specific queued brief
- `worktrail-go auto` or `worktrail-go REPO auto` — auto-pick the next ranked queue brief and start it, no selection prompt (spec 017)
- `worktrail-go REPO` — check what's active in a specific repo

Claude Code exposes this as `/worktrail-go`; Codex exposes it as
`$worktrail:worktrail-go`. Substitute the host command in the forms below.

## Instructions

Artifact authority and commit policy: `references/artifact-policy.md`. (The GO v1/v2 design records live in the repo at `docs/design/history/` — they are history, not procedure, and are deliberately not part of this skill bundle. Consult them only for Route J workflow-evolution changes.)

### Phase 0 — Environment

Every command this skill runs is a console script installed by the `worktrail` package
(`worktrail-classify`, `worktrail-dashboard`, `worktrail-policy`, `worktrail-resolve-repo`,
`worktrail-run-record`, `worktrail-work-queue`, …) — they are on `PATH`, so there is no
script-directory resolution step and no `$CLAUDE_PLUGIN_ROOT` fallback chain.

```bash
BASE="${WORK_QUEUE_DIR:-$HOME/work-queue}"
REPO="${REPO:-$PWD}"
```

If `worktrail-classify` is not on `PATH`, stop and report that the `worktrail` package is not
installed (`pip install worktrail`) rather than attempting to locate scripts on disk.
`$REPO` starts as `$PWD` and is formally resolved and overwritten by Phase 3.

New spec format: `/go new` uses OpenSpec by default. Set
`WORKTRAIL_SPEC_FORMAT=devkit` for a repository that must author the legacy
`docs/specs/` format; existing specs are always detected by their on-disk format.

### Invocation Context (Mandatory — Resolve Once, Carry Explicitly)

The Go front door resolves a durable **invocation context** before any dispatch. This
context is the single source of truth for agent CLI selection — every downstream stage
(sdd-workflow, parallel orchestrator, headless workers) consumes the resolved value
instead of re-detecting the host from ambient environment variables.

Resolve the agent CLI once:

```bash
INVOCATION_CONTEXT_AGENT="${AGENT_CLI:-}"
if [ -z "$INVOCATION_CONTEXT_AGENT" ]; then
  POLICY_DATA=$(worktrail-policy --repo "$PWD" --json 2>/dev/null || echo '{}')
  POLICY_AGENT=$(echo "$POLICY_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('agent_cli') or '')")
  if [ -n "$POLICY_AGENT" ]; then
    INVOCATION_CONTEXT_AGENT="$POLICY_AGENT"
  elif [ -n "${GO_AGENT_CLI:-}" ]; then
    INVOCATION_CONTEXT_AGENT="$GO_AGENT_CLI"
  elif [ -n "${ORCH_AGENT:-}" ]; then
    INVOCATION_CONTEXT_AGENT="$ORCH_AGENT"
  elif [ -n "${OPENCODE_PARENT:-}" ]; then
    INVOCATION_CONTEXT_AGENT="opencode"
  elif [ -n "${CODEX_CI:-}" ] || [ -n "${CODEX_THREAD_ID:-}" ]; then
    INVOCATION_CONTEXT_AGENT="codex"
  else
    INVOCATION_CONTEXT_AGENT="claude"
  fi
fi
```

Every downstream consumer (go_seed.py `--agent`, `live.py full-real --agent`,
sdd-workflow seed prompt `Agent CLI:`) MUST use `$INVOCATION_CONTEXT_AGENT`.
Do NOT re-detect the host or re-read env vars in child processes — the resolved
value is the durable per-invocation identity.

Hold `$INVOCATION_CONTEXT_AGENT` for the entire session. When the policy
`run_record_dir` is set, pass `--agent "$INVOCATION_CONTEXT_AGENT"` to every
`worktrail-run-record` call so the run record captures the resolved agent.

### Phase 1 — Orientation Dashboard (Always First)

Detect mode via `resolve_repo.py --start "$PWD" --json`. Then fetch queue JSON first and pass it to dashboard so the picker options are computed by the script, not by Claude:

```bash
QUEUE_JSON=$(worktrail-work-queue list --json 2>/dev/null)
if [ "$RESOLVE_MODE" = "in-repo" ]; then
  DASHBOARD_JSON=$(worktrail-dashboard \
    --root "$REPO/docs/specs" --picked-dir "$BASE/picked" \
    --queue-json "$QUEUE_JSON" --json 2>/dev/null)
else
  REPOS_DIR="${HOME}/projects"; [ -d "$REPOS_DIR" ] || REPOS_DIR="$PWD"
  DASHBOARD_JSON=$(worktrail-dashboard \
    --repos "$REPOS_DIR" --picked-dir "$BASE/picked" \
    --queue-json "$QUEUE_JSON" --json 2>/dev/null)
fi
```

`$DASHBOARD_JSON` carries three pre-computed, deterministic fields — hold it for Phases 1b/2: **`rendered`** (the dashboard text), **`category_actions`** (Level-1 picker options), and **`category_items`** (Level-2 items per category, each carrying the dispatch fields its `action` needs — `id` for queue items, `spec_id`/`repo`/`path` for specs). Field contract: `references/dashboard-render.md`.

Handle a missing/empty dashboard gracefully (the `rendered` field already prints a "nothing active → brainstorm" line).

**Print `$DASHBOARD_JSON.rendered` verbatim.** Do NOT re-render, reorder, regroup, or summarize it — rendering it yourself reintroduces the non-determinism this field exists to remove.

**Help invocations** (`help`): delegate to `worktrail-help` and stop before the dashboard.

**Brief-ID invocations** (`handoff:ID`, `route:X`, or a bare/prefix positional argument that
resolves to exactly one queued brief): print only a one-line summary (e.g. `Dashboard: N specs,
M in-flight`) and skip the picker — go straight to Phase 2. **Determine this here, before
Phase 1b runs** — do not defer the check to Phase 2, which only executes after the picker for
invocations Phase 1 didn't already classify as Brief-ID. For a positional argument not already
identified as `help`/`drain`/`auto`/`route:X`/a v1 intent keyword/a resolved repo name, run the
resolve check now: see Phase 2's **Bare or prefix brief ID** rule for the exact command and
match semantics. A `match` makes this a Brief-ID invocation; `none`/`ambiguous` falls through to
Phase 1b's picker.

**Auto invocations** (`auto` argument, spec 017): print the `rendered` dashboard as usual, skip BOTH picker levels, and add `--auto` (plus `--auto-repo "$ARG_REPO"` when a repo was named) to the dashboard.py call so `$DASHBOARD_JSON.auto_pick` is populated. Full flow: `references/auto-mode.md`.

**Drain invocations** (`drain [max-items] [repo]`): skip the dashboard and picker, read
`references/drain.md`, and run the installed `worktrail-drain` console script with the
resolved invocation agent. The console script is an internal executor; users enter
drain requests through `worktrail-go` only.

### Phase 1b — Two-Level Picker (AskUserQuestion)

For bare `/go` or free-text with no explicit route/brief — i.e. Phase 1's Brief-ID check
(including the bare/prefix resolve check) found no match — use a **two-level picker**:

**Level 1 — Category.** Call `AskUserQuestion` with options from `$DASHBOARD_JSON.category_actions` verbatim (header: `What to work on`). Each option is a work category: Ready / in-progress, Needs tasking, Work queue, New work. Only categories with items are present. The tool's automatic **"Other"** covers free-text, a specific spec id, or "see more".

**Level 2 — Item.** After the user picks a category, call `AskUserQuestion` again with options from `$DASHBOARD_JSON.category_items[chosen_category]` verbatim (header: the category label the user picked). Each option is a specific spec or brief with full dispatch data. The tool's automatic **"Other"** covers items beyond the ≤4 shown (user can type a spec id or free-text).

**Single-option guard (mandatory).** `AskUserQuestion` rejects an `options` array with
fewer than two entries, so never call it with one. One entry in `category_actions` →
skip Level 1, go straight to Level 2 for it, printing `Only one category active:
<label>. Reply with anything else to redirect.` (typed replies route to Phase 5). One
entry in `category_items[chosen_category]` → skip Level 2, dispatch that item directly
(Phase 2), confirming with `Picking up <label>…`. Two or more entries at either level:
use the verbatim-options rule as normal.

If the user selects **"Other"** at either level, treat the typed input as a free-text request and proceed to Phase 5.

### Phase 2 — Intake

Resolve the user's choice and dispatch by its `action`:

- **Level-2 item selected** — dispatch directly from the `category_items` entry; it carries `action`, `spec_id`, `repo`, `path`, `id`, and other dispatch data.
- **"Other" / typed reply at level 1** — treat as free-text (Phase 5).
- **"Other" / typed reply at level 2** — a bare spec id or integer routes to that item; anything else is free-text (Phase 5).

**Action → dispatch:**
| `action` | dispatch |
|---|---|
| `resume` | stalled in-flight brief (claimed ≥48h ago with no completion — likely an abandoned session; freshly-claimed briefs are hidden as actively owned). Before continuing, verify what the prior session already landed (merged PRs / commits / spec status referencing the brief id) so you resume the remainder, not redo it → Phase 3 (no re-claim) |
| `implement` | active spec → `Skill("worktrail-sdd-workflow", args="<path> route:E <spec_id>")`, where `<path>` is the item's `path` (multi-repo) or `$REPO` (in-repo) |
| `close-stale` | stale-bookkeeping spec → **do NOT run the orchestrator** (files already merged on base). Confirm the spec's pending impl tasks are truly shipped (their `files:` exist + are git-tracked on the base branch — `next_action` lists the task ids), then flip those `TASK-*.md` `status:` → `completed` and land a docs-only PR (the way the 068 stale-status case was closed). Re-run the dashboard to confirm the spec drops to sync/complete. |
| `claim` | queue item → batch-claim it plus any related queued briefs (see **Batch consumption** below), then Phase 3 |
| `consolidate-cluster` | detected brief cluster → run `consolidate_cluster.py preview <members...>` to re-validate + draft a consolidated brief, show the draft via `AskUserQuestion` requiring an explicit confirm (no default-yes), then run `consolidate_cluster.py execute <members...> --draft '<preview JSON>' --confirm` (or `--decline`, which performs zero writes) |
| `brainstorm` | Route A (new feature) |
| `see-backlog` | list the unspec'd backlog (from `rendered` / re-scan), let the user pick one → brainstorm it (Route A) |
| `cleanup-worktrees` | run the stale-worktree review — see `references/worktree-cleanup.md` |
| `see-more` | list the full queue with new numbers |
| `freetext` | prompt for the request → Phase 5 |

**Batch consumption (`claim` action).** One brief per run is the floor, not the ceiling —
detect batchable neighbours (same repo, similar spec/module surface), offer via
multiSelect, claim primary + companions with `claim-batch`, execute the union as ONE
run, mark each brief done individually. Full procedure: `references/batch-consumption.md`.

**Auto mode (`auto` argument, spec 017).** No selection prompt: take
`$DASHBOARD_JSON.auto_pick` (never improvise), auto-fold only `related-link`/
`same-target-spec` companions, `claim-batch`, continue Phases 3–8 as an interactive
claim. Null pick → report and STOP. Full flow, guards, race handling: `references/auto-mode.md`.

Parse the positional arguments to detect:
- **help** — delegate to `Skill("worktrail-help", args="<remaining topic>")` and stop; do not render the dashboard or claim work
- **drain** — run the internal queue-drain procedure from `references/drain.md`; do not claim a brief in the interactive process
- **auto** — auto mode (spec 017): skip the picker, use `$DASHBOARD_JSON.auto_pick` per the Auto mode flow above; combinable with a repo arg (`/go REPO auto`)
- **Bare integer** — resolves within the active Level-2 category picker (the level-2 rule above); there is no global numbered list, so a standalone `/go N` argument is treated as free-text (Phase 5)
- **handoff:ID** — explicit brief ID from queue (delegate to sdd-workflow or direct work)
- **Bare or prefix brief ID** — an argument not otherwise consumed by the rules above or below (not `help`/`drain`/`auto`/`route:X`/a v1 intent keyword, and not the resolved `$ARG_REPO`): before treating it as free-text, check it against the queue with `worktrail-work-queue resolve "$ARG" --json`. A `match` (full filename, stem, unique leading prefix, or `id` frontmatter — the same resolution `claim` uses) makes it `$BRIEF_ID`, with identical treatment to `handoff:ID`. `none` or `ambiguous` falls through to free-text (Phase 5) as before.
- **route:X** — explicit route override A-J (skip classification, dispatch directly)
- **v1 intent keywords** — new, implement, continue, pr, brainstorm (map to routes, skip classification)
- **Free-text** — unstructured request (run classify.py)

Extract from the arguments:
- `$ARG_REPO` — first positional arg if it looks like a repo path or keyword
- `$ARG_INTENT` — free-text request or intent keyword
- `$ARG_SPEC` — spec folder name (e.g., `003-payments`) if provided
- `$ROUTE_OVERRIDE` — route:X if provided
- `$BRIEF_ID` — handoff:ID if provided, or a bare/prefix argument that resolved via `worktrail-work-queue resolve` (see **Bare or prefix brief ID** above)

### Phase 3 — Resolve Repo

If a repo is not explicit in args, resolve it from the current working directory via resolve_repo.py.

```bash
worktrail-resolve-repo --start "$PWD" --hint "$ARG_INTENT" --json
```

Hold the resolved repo path as `$REPO` and base branch as `$BASE`. If resolution is ambiguous (multi-repo parent), present a picker. If resolution fails, prompt the user for a repo name.

**Staleness guard.** `resolve_repo.py` only inspects files, so it cannot tell a checkout
missing upstream commits from one that legitimately has no policy/spec. Run
`check_repo_freshness.py` against `$REPO` right after resolution; a stale result means Phase
4's policy read (and any "no policy configured" finding) may be describing a phantom gap,
not a real one. Best-effort, never blocking. Full procedure: `references/repo-freshness.md`.

### Phase 4 — Load Policy

Load the resolved repo's policy via policy.py to surface warnings and hold policy for risk assessment.

```bash
POLICY=$(worktrail-policy --repo "$REPO" --json)
```

Surface any warnings from the policy. Hold `$POLICY` for risk assessment in Phase 6.

### Phase 5 — Classify (If No Explicit Route)

If no explicit route (route:X or v1 intent keyword) and not a handoff:ID resumption, run classify.py to classify the free-text request.

**Claimed-brief hint.** If this dispatch came from a `claim` action (interactive or
`auto`), extract the PRIMARY claimed brief's `recommended-route` frontmatter before
classifying — the classifier can only weigh it if it's actually passed in:

```bash
RECOMMENDED_ROUTE=$(worktrail-handoff-seed seed "<primary-claimed-path>" --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin).get('recommended_route') or '')")
HANDOFF_ROUTE_FLAG=(); [ -n "$RECOMMENDED_ROUTE" ] && HANDOFF_ROUTE_FLAG=(--handoff-route "$RECOMMENDED_ROUTE")

worktrail-classify --request "$ARG_INTENT" --state "$DASHBOARD_JSON" "${HANDOFF_ROUTE_FLAG[@]}" --repo "$REPO" --json
```

For free-text/level-2-item dispatches with no claimed brief, omit `--handoff-route` (no
hint to weigh):

```bash
worktrail-classify --request "$ARG_INTENT" --state "$DASHBOARD_JSON" --repo "$REPO" --json
```

`--repo "$REPO"` lets classify.py resolve the state of any `PR #NNN` cited in the
request (best-effort `gh pr view`, fail-open) so a PR number mentioned in passing
doesn't force Route E once that PR is already merged/closed.

**Route-C implementation transition.** A claimed brief's optional
`implementation-intent:` frontmatter controls the post-spec transition:
`requested` continues inline to Route D after a clean spec; `planning-only`
stops at the planning result; missing or `unknown` requires one explicit
decision. Do not mark a Route-C brief done without passing either
`work_queue.py done ... --planning-only` or
`work_queue.py done ... --implementation-complete`. If implementation is
requested, do not create a follow-up handoff merely because Route C finished.

Returns `{"route": "F", "route_name": "...", "confidence": "high|medium|low", "ambiguous_between": [...], "question": "...", "reason": "...", "risk": "...", "gates": [...], "route_source": "classifier|handoff-recommended-override"}` (`ambiguous_between` is an empty list when unambiguous; `question` is non-null only when a clarification is needed).

`route_source: "handoff-recommended-override"` means classify.py itself judged its own
organic pick low/medium-confidence and deferred to the brief's `recommended-route`
instead — this is not a caller decision to make; just log it (Phase 6) for visibility.

**Confidence handling:**
- **`high` or `medium`** — state the route and proceed to Phase 6. When `route_source`
  is `handoff-recommended-override`, say so in the status line (e.g. "Route H (brief's
  recommended-route; classifier's own guess was a low-confidence B)") so the override
  isn't silent.
- **`low`** — ask one clarifying question to pin intent, then re-run classify.py. (The Phase 1b category picker covers bare `/go`; this path applies to ambiguous free-text only.) Auto mode has no one to ask — if a claimed brief carries no `recommended-route` and classify.py still returns `low`/ambiguous even with the hint applied, that's outside this override's coverage; stop and report rather than guessing (same STOP discipline as a null `auto_pick`).
- **`ambiguous_between` non-empty** — ask exactly the one clarifying `question` classify.py provides, then re-run with the answer pinned. Never ask more than one.

### Phase 5.5 — Collision & Staleness Guard

One question — "has this already been done?" — asked two ways, by two mutually exclusive,
route-gated branches that share no state. A Route C/D dispatch checks for **spec collision**
(does a shipped spec already cover this request?); a brief-sourced Route E/F dispatch checks for
**brief staleness** (did the work this brief describes already land while it sat in the queue?).
Every other route skips both branches entirely.

**Route C/D branch: spec collision.**

Gated on Phase 5's resolved route (`$ROUTE`) being `C` or `D` only — every other route skips
this branch. Before starting Phase 6's run record, check whether an existing,
already-`Implemented` spec under `docs/specs/` already covers the request: run
`check_spec_collision.py --repo "$REPO" --json` for the candidate list, judge each candidate
against the dispatch's comparison text (a claimed brief's `focus` field, or the free-text
`$ARG_INTENT`/its classify.py-derived summary when there's no claimed brief) using the same
actor + capability + primary domain rule as `references/subagent-prompts.md#overlap-check`, and
run `--verify <spec_id> --json` on any judged match. On a confirmed collision (`verify()`'s
`confirmed: true`), a brief-sourced dispatch auto-closes the brief via `work_queue.py done ...
--implementation-complete --note "..."` and stops (no fresh dispatch); a brainstorm-sourced
dispatch (no claimed brief) instead asks the user via `AskUserQuestion` before proceeding. Any
non-confirmed outcome — `checked: false`, no candidate judged a match, or `confirmed: false` —
leaves Phase 6/7 unmodified and un-delayed. Full procedure, including both dispatch-source
branches and exact command syntax: `references/spec-collision-check.md`.

**Route E/F branch: brief staleness.**

Gated on the dispatch being brief-sourced (a claimed brief is in play) **and** Phase 5's resolved
route being `E` or `F`. A free-text dispatch with no claimed brief skips this branch even on
route E/F — the check is built on a brief's `created:` timestamp and captured prose, which free
text has no equivalent of. Before starting Phase 6's run record, run:

```bash
worktrail-check-brief-staleness --repo "$REPO" --brief "<claimed-brief-path>" --json
```

It extracts bounded path/symbol/PR probes from the brief's prose and searches the base branch's
commit history — plus, best-effort, merged PRs — for anything matching them since the brief was
captured. Read the result the same way as the sibling branch: `checked: false` means the question
was unanswerable (not a git checkout, missing/malformed `created:`, no probes, a timeout) and
Phase 6/7 proceed unmodified; `checked: true` with empty `matches` is a definite searched-and-clean
negative and also proceeds silently. `gh` being missing, unauthenticated, erroring, or slow is
**not** a `checked: false` cause — it degrades to an empty `pull_requests` list plus a warning
while any git-history `matches` are kept.

On `checked: true` with non-empty `matches` (or `pull_requests`), **never auto-close the brief** —
unlike the spec-collision branch, evidence that a commit touched the named symbols is not proof
the brief is satisfied, so the operator is always asked via `AskUserQuestion` (close as
already-delivered, or proceed anyway) before Phase 6/7 continues. Full procedure — command,
how to read the result, the prompt shape, and the run-record entries:
`references/brief-staleness-check.md`.

### Phase 6 — Run Record (Start)

Start an audit trail for the dispatch via run_record.py. This captures the request summary, resolved route, risk level, repo, and base commit.

```bash
REQUEST_SUMMARY="${ARG_INTENT:-queue item}"
RISK_LEVEL="${RISK_LEVEL:-medium}"
GATES="${GATES:-}"   # comma-joined classify.py "gates" array; empty if none

RUN_JSON=$(worktrail-run-record start \
  --repo "$REPO" \
  --request "$REQUEST_SUMMARY" \
  --route "$ROUTE" \
  --risk "$RISK_LEVEL" \
  --agent "$INVOCATION_CONTEXT_AGENT")
RUN=$(echo "$RUN_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['path'])")
```

Hold `$RISK_LEVEL` and `$GATES` for Phase 8's mandatory pre-PR gate
(`sdd-workflow/SKILL.md`'s `pre_pr_gate.py --run --risk --gates` call) — they are
classify.py's `risk`/`gates` fields verbatim, not re-derived later.

Risk level: **low** (queue items, docs), **medium** (bug fixes, refactors), **high** (major features, spec rewrites). Hold `$RUN` for Phase 8. If the policy sets `run_record_dir`, pass it as `--dir` on every `run_record.py` call.

### Phase 7 — Dispatch

Route the request to the right handler. Per-route playbooks: `references/routes.md`. Worktree and subagent procedures: `references/subagent-prompts.md`.

Dispatch policy is simple:

- Resolve the routing decision before dispatching the route: `ROUTING_JSON=$(worktrail-policy --repo "$REPO" --resolve-routing "$ROUTE:$RISK_LEVEL" --json)`. Use that helper result as the repository-policy tier for the orchestrator invocation.
- Map the helper outputs directly to the dispatch flags: `agent_cli` -> `--agent`; `agent_model` -> `--model`; each `roles.<role>.agent_cli` -> `--role-agent-map`; each `roles.<role>.agent_model` -> `--model-map`. Carry the helper's `fallback` list through unchanged as the fallback chain for the dispatch layer.
- Tiers resolve on a separate axis (per-task `(complexity, domain)`, not `$ROUTE:$RISK_LEVEL`): call `policy.resolve_tier_map(policy)` and map each `(complexity, domain) -> {agent_cli, agent_model}` entry onto `live.py full-real`'s `--tier-map` flag, joined `,`-separated as `complexity:domain=agent_cli[:agent_model]` (omit the `:agent_model` segment when unset). A domain-less tier (`domain is None`) has no CLI-string form — `live.py`'s `_parse_tier_map()` always yields an empty-string domain, never `None` — so a domain-less `routing.tiers` entry only reaches `dispatch.agent_for` through a native in-process `tier_map` dict (e.g. `RoutingFakeSpawn`/test fixtures), not this flag.
- Purpose maps onto a tier on a third axis: the helper result's `purpose_tiers` (`routing.purpose_tiers`, from the same `resolve_routing()` call) maps each `purpose -> tier` entry onto `--purpose-tier-map`, joined `,`-separated as `purpose=tier`. A task whose `purpose` resolves through this table has that tier looked up in `--tier-map` in place of its `complexity` for implement/fix/cleanup spawns only; `review`/`resolve`/`ci-fix`/`assembly-resolve` never consult it (DEC-003).
- Explicit invocation flags or caller-supplied `AGENT_CLI` always win over the derived routing values. The routing table slots into the existing precedence at the repository-policy tier: explicit invocation > repository policy (routing table here) > machine-wide env > detected host > `claude`.
- no routing table → behavior identical to today (flat keys, single fallback). Likewise, no `routing.tiers` → omit `--tier-map` entirely, and no `routing.purpose_tiers` → omit `--purpose-tier-map` entirely (dispatch behavior unchanged, REQ-NR005).

  Example `routing.tiers` in `~/.go/routing.yaml` — a 3-tier complexity fallback keyed by a
  task's `complexity` frontmatter value (`trivial` / `standard` / `hard`), routing each to a
  progressively more capable model on the same CLI:

  ```yaml
  routing:
    tiers:
      trivial:
        agent_cli: codex
        agent_model: gpt-5.6-luna
      standard:
        agent_cli: codex
        agent_model: gpt-5.6-terra
      hard:
        agent_cli: codex
        agent_model: gpt-5.6-sol
  ```

  Model names above are illustrative — substitute whatever tier models the operator's provider
  actually exposes. A key may carry a `/domain` suffix (e.g. `trivial/frontend`) to scope by
  `(complexity, domain)` instead of complexity alone; omit `/domain` to match every domain at
  that complexity. Tasks whose format has no `complexity` field, or whose value has no matching
  tier entry, fall through to `routing.defaults`/`routing.roles` unaffected — `routing.tiers` is
  purely additive.

  **Routing by task purpose instead of complexity.** `routing.purpose_tiers` maps a task's own
  `purpose` frontmatter value (set by `conductor/compile.py`'s inference pass, or by hand) to a
  `routing.tiers` key, so a task routes on *what it is* — architecture design, terminal-heavy
  automation, security review, CRUD scaffolding — rather than a human-assigned `complexity`
  label. `dispatch.agent_for()` consults it ahead of `complexity` for implement/fix/cleanup
  spawns only; a task whose `purpose` has no matching entry (or carries no `purpose` field at
  all) falls through to `complexity` unaffected, same as an unmatched `routing.tiers` entry.

  Worked example mapping a starter six-value purpose taxonomy onto the manual T1–T4
  (Deep/Build/Bulk/Trivia) tier scheme `routing.tiers` examples above already use:

  ```yaml
  routing:
    purpose_tiers:
      architecture-design: t1-deep
      security-review: t1-deep
      agentic-automation: t2-build
      scaffolding: t2-build
      bulk-mechanical: t3-bulk
      trivial: t4-trivia
    tiers:
      t1-deep:
        agent_cli: claude
        agent_model: opus
      t2-build:
        agent_cli: codex
        agent_model: gpt-5.6-terra
      t3-bulk:
        agent_cli: codex
        agent_model: gpt-5.6-sol
      t4-trivia:
        agent_cli: codex
        agent_model: gpt-5.6-luna
  ```

  These six purpose values are a starter set, not a fixed enum — `compile.py` reads
  `routing.purpose_tiers`' own keys as the closed vocabulary it classifies a task's `purpose`
  into, so the operator renames, adds, or removes categories by editing this table alone, no
  code change required. No `routing.purpose_tiers` table configured → `compile.py` never asks
  for `purpose` at all, identical to today's output.
- Record the resolved routing decision at dispatch time with `worktrail-run-record start ... --routing-decision "$ROUTING_JSON"` so the audit trail captures the exact route/risk-derived policy.
- Codex / in-session host: call `Skill("worktrail-sdd-workflow", ...)` directly.
- OpenCode parent sessions use the seeded subprocess path with `opencode` when the harness
  supplies the explicit `OPENCODE_PARENT` marker; explicit invocation, repository policy,
  and machine-wide provider overrides still win.
- Other hosts with a working headless CLI: use the seeded subprocess path from
  `references/subagent-prompts.md#subprocess-dispatch`.
- If subprocess dispatch is unavailable: fall back to the direct Skill call and say so.

**Pinning a role to a specific agent/model.** `routing.roles` overrides one JUDGMENT_ROLE
(`review`/`resolve`/`ci-fix`/`assembly-resolve`) or task role (`implement`/`fix`/`cleanup`)
independently of the run's default agent — for example, to force an independent
code-reviewer model regardless of which agent implemented the task. Add it under either the
repo-local policy's `routing:` block or the machine-wide `~/.go/routing.yaml`
(`GO_ROUTING_FILE`) — same wholesale, block-level fallback as `routing.fallback`: a
non-empty repo-local `routing:` block is used in full and the machine-wide file is not
read at all, so it is not merged per-role with the machine-wide file:

```yaml
routing:
  roles:
    review:
      agent_cli: claude
      agent_model: opus
```

This resolves through `resolve_routing()` into `roles.review = {agent_cli: "claude",
agent_model: "opus"}`, which the bullet above maps onto `--role-agent-map`/`--model-map` for
the orchestrator invocation. For `review`/`resolve`/`ci-fix`/`assembly-resolve` this is the
*only* override that can beat the run default (DEC-003) — a per-task `agent` field or
`routing.tiers` match is never consulted for those roles, by design (independent-reviewer
guarantee, 13.3). No `routing.roles` entry for a role → unchanged pre-spec behavior for that
role.

**Native Skill capability fallback.** `Skill(...)` is a host capability, not a shell
command. If the current host does not expose it (for example, an embedded Codex
session), run `worktrail-skill-dispatch` with the resolved `--agent`, `--skill`,
and `--args` values. It executes that same provider without shell interpolation
and never silently falls back to a different provider. Keep native `Skill(...)`
primary when the host exposes it, and report adapter use in the run status.

**Provider-capacity gate:** a headless dispatch may raise the orchestrator's
`AllProvidersUnavailable` result after the primary and configured fallback have
both been gated. Catch that result at this boundary; do not retry or launch a
new worker. Record its provider/model-safe identifiers, failure class, and
earliest known retry time with:

```bash
worktrail-run-record capacity-gate "$RUN" \
  --provider "<agent:model>" --failure-class "<class>" \
  [--retry-after "<ISO-8601>"] --note "all configured providers unavailable"
worktrail-run-record finish "$RUN" \
  --status blocked_external_dependency \
  --merge-result "provider capacity gate; no headless worker launched"
```

The note and identifiers must never contain credentials. The dashboard reads
the machine-local capacity cache and renders the same blocked status, failure
class, and retry time for operators returning to the workspace.

**Capacity-cache operator commands.** The machine-local capacity cache
(`~/.go/agent-capacity.json`, override with `GO_AGENT_CAPACITY_CACHE`) can be
inspected and explicitly cleared:

```text
worktrail-agent-capacity status [--cache PATH]
worktrail-agent-capacity clear PROVIDER_KEY [--reason TEXT] [--cache PATH]
worktrail-agent-capacity clear --all [--reason TEXT] [--cache PATH]
```

- `status` prints provider keys, status, failure class, check time, and retry
  window — no credentials or raw cache content.
- `clear` removes a specific provider's gate or all gates (`--all`). Every clear
  requires `--reason` (non-empty, ≤500 characters). Unknown keys, blank reasons,
  and malformed cache on mutation fail without changing the file.
- Normal dispatch never clears or retries around a persisted gate implicitly.
  Only clear after the external condition (auth, billing, sandbox, startup, or
  transport) has been corrected.

Route-specific spawn, poll, and fallback mechanics live in
`references/subagent-prompts.md`; do not duplicate them here.

### Phase 8 — Finish

Close the run record with one of the ten real `run_record.py` completion states — never
vague completion language (`finish` rejects an invalid status and lists the allowed
ten). **PR-owning routes** (completion is `completed_pr_open`, `completed_and_merged`,
or `completed_awaiting_human_approval` per `routes.md`) are not done at "implemented and
tested": commit → push → PR creation/update → **CI watch loop** → `finish`. Green local
validation alone is not a terminal state for those routes. Only routes whose documented
completion does **not** require a PR (e.g. Route C `planned_ready_for_implementation`,
Route I `investigation_complete`) may finish without commit/push/PR creation.

**CI watch loop.** After opening a PR on any PR-owning route, run the loop in
`references/ci-watch-loop.md` before closing the run record: wait with
`gh pr checks --watch` (never a sleep loop), then classify the settled checks — pass →
finish; transient infra → rerun; code defect → minimal patch (≤5 iterations); product
decision → `blocked_product_decision`; ceiling → `failed_recoverable`.

## Dispatch Contract (to worktrail-sdd-workflow)

```
Skill("worktrail-sdd-workflow", args="<repo-path> route:<X> [spec-folder]")
Skill("worktrail-sdd-workflow", args="handoff:<id>")
```

sdd-workflow accepts `route:X` to skip its own classification and proceed directly to route execution.

## Related Briefs

When a brief is claimed, surface any related briefs from its `related` frontmatter field before beginning work. Related briefs still sitting in `queue/` for the same repo are batch candidates — the Phase 2 batch-consumption flow ranks them first.

## Examples

**Free-text defect repair**
```
/go fix the upload timeout
```
→ Dashboard + category picker → classify.py: Route F, high confidence → dispatch to sdd-workflow

**Bare /go (returning session)**
```
/go
```
→ Two-level picker (`category_actions` → `category_items`) → Phase 2 dispatches the chosen item

**Explicit route**
```
/go ggb route:D 003-payments
```
→ route:D detected, Phase 5 skipped → `Skill("worktrail-sdd-workflow", args="<ggb-path> route:D 003-payments")`

**Queue claim by ID**
```
/go 20260613-001000-feature-x
```
→ One-line dashboard summary → claim → dispatch to sdd-workflow

**Auto mode (spec 017)**
```
/go auto
```
→ No picker → `auto_pick` selects the oldest unblocked brief → `claim-batch` folds in link/spec companions → classify/dispatch/CI-watch as normal

## Best Practices

- Always print the `rendered` dashboard first, then drive the choice with the two-level `AskUserQuestion` picker (`category_actions` → `category_items[chosen]`), dispatching the selected item directly (Phase 1b/2). Never hand-render the dashboard or a "type a number" list — the script owns both.
- Classify once; reuse the result for dispatch (do not re-classify after ambiguity resolution).
- Claim one PRIMARY brief per invocation; fold related queued briefs in via the Phase 2 batch-consumption flow (`claim-batch`) when they share the repo and surface. Ownership and completion stay per-brief: every batched brief is individually claimed, stamped (`batch-primary:`), and individually marked done or released.
- Surface related briefs before starting work.
- Prefer delegation (sdd-workflow) for any work touching `docs/specs/`, route letters, or SDD concepts.
- Close the run record with an explicit completion state, not silence.
- A Route-C brief is not complete at the spec/task boundary unless the user
  explicitly selected planning-only; continue inline to Route D for requested
  implementation work.
- For routes that end in a PR or merge outcome, keep going through the CI watch loop until CI is green (or the loop reaches a terminal state); "tests passed locally" is not closeout.

## Constraints and Warnings

- Never move queue files manually; use `work_queue.py` for all queue operations (claim, claim-batch, done, release, link).
- Batch only briefs that share the same repo AND would ride the same route/worktree/PR; a batch is an execution convenience, never a scope expansion. When in doubt, leave a candidate in the queue.
- Do not invoke SDD stage skills (brainstorm, spec-to-tasks, orchestrator) directly; let sdd-workflow coordinate them.
- If sdd-workflow is not installed, decline SDD-route requests gracefully; still serve non-SDD queue items.
- Never skip the orientation dashboard — even brief-ID invocations show a one-line summary.
- When resuming a picked brief, do not call `work_queue.py claim` again.
- Auto mode (spec 017) removes the selection prompt ONLY: it never resumes in-flight briefs, never picks blocked/`no-repo`/busy-repo briefs, never retries a lost claim race more than 3 times, and never bypasses policy approval gates or risk tiers. When `auto_pick.pick` is null, report and stop — do not fall back to interactive selection or invent work.
- The run record MUST be started for every dispatched invocation and closed with an explicit completion state.

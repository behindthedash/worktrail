---
name: worktrail-go
description: >
  Use when the user invokes worktrail-go, asks to pick up queued work, resume active specs,
  implement or fix a spec, route engineering work, or orient across repositories.
  Renders the orientation dashboard, classifies free-text requests,
  claims/resumes handoff briefs, and dispatches SDD work without requiring the user
  to know sdd-workflow. Grammar: worktrail-go [REPO] <noun> <verb>, nouns
  handoff / spec / pr. Triggers: worktrail-go, worktrail-go help,
  worktrail-go spec fix, worktrail-go spec implement, worktrail-go spec route F,
  worktrail-go handoff new, worktrail-go handoff auto, worktrail-go BRIEF-ID,
  bare worktrail-go, multi-repo orientation.
argument-hint: "[help | BRIEF-ID | REPO | [REPO] handoff|spec|pr <verb> [args] | free-text request]"
allowed-tools: Read, Bash, AskUserQuestion, Skill, Agent
---

# Go — Universal Engineering Front Door

## Overview

Universal engineering front door for a Worktrail workspace. Work invocations start with an orientation dashboard (active specs, in-flight briefs, ready queue), then classify the request and dispatch to the right executor. The `help` invocation is the read-only exception and delegates to `worktrail-help` without rendering or claiming work. `worktrail-sdd-workflow` is an internal executor — users access it only via `worktrail-go`.

## When to Use

The grammar is `worktrail-go [REPO] <noun> <verb> [args]` with three nouns — `handoff`
(the work queue), `spec` (spec-driven work), `pr` (an open pull request) — plus two bare
shortcuts (`BRIEF-ID`, `REPO`) and free text. The full form table, including the older
spellings that are still accepted (`auto`, `drain`, `new`, `implement spec`, `fix`,
`route:X`, `handoff:ID`, …), is `worktrail-go-parse --forms` / `worktrail-help`.

- Bare `worktrail-go` — orientation dashboard + `AskUserQuestion` picker
- `worktrail-go help` — delegate to `worktrail-help`
- `worktrail-go handoff new "<focus>"` — capture a brief via `worktrail-handoff`, no dispatch
- `worktrail-go handoff list` — list queued briefs, no dispatch
- `worktrail-go BRIEF-ID` (or `handoff start BRIEF-ID`) — claim or resume a specific queued
  brief; an untriaged intake brief (no `seeded-from:`) is triaged interactively instead
  (spec `intake-to-spec-triage`)
- `worktrail-go handoff auto` or `worktrail-go REPO handoff auto` — auto-pick the next ranked queue brief and start it, no selection prompt (spec 017)
- `worktrail-go handoff drain [max-items] [repo]` — delegate to the unattended queue drain
- `worktrail-go spec new X` — plan a new feature (Route C+D)
- `worktrail-go spec implement 003` — dispatch to spec execution
- `worktrail-go spec fix X` — defect repair, Route F, no classification
- `worktrail-go spec route F` or `worktrail-go REPO spec route D spec-folder` — explicit route, no classification
- `worktrail-go pr fix` — PR / CI repair
- `worktrail-go REPO` — check what's active in a specific repo
- Anything else — classified and dispatched as a free-text request

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

New spec format: `/go spec new` uses OpenSpec by default. Set
`WORKTRAIL_SPEC_FORMAT=devkit` for a repository that must author the legacy
`docs/specs/` format; existing specs are always detected by their on-disk format.

### Invocation Context (Mandatory — Resolve Once, Carry Explicitly)

The Go front door resolves a durable **invocation context** before any dispatch. This
context is the single source of truth for dispatch selection — every downstream stage
(sdd-workflow, parallel orchestrator, headless workers) consumes the resolved values
instead of re-detecting the host from ambient environment variables.

The context carries **two independent values**. Do not derive either from the other:

- **`agent_cli`** — which provider CLI a child process would be launched as.
- **`native_skill_available`** — whether *this* host exposes a native `Skill(...)` tool.

They are independent facts about different things. An embedded Codex host resolves
`agent_cli: codex` and exposes **no** `Skill(...)`; a Claude Code session resolves
`agent_cli: claude` and does. Treating the provider as a proxy for the capability picks
the wrong dispatch path for exactly the hosts that need the adapter.

**Only you can observe your own tool surface**, so supply the capability explicitly:
pass `--native-skill` when `Skill` is among your available tools and
`--no-native-skill` when it is not. Never omit the flag to mean "probably yes" — an
omitted signal resolves to `false`, deliberately, because assuming the capability is
present is a speculative `Skill(...)` call you cannot retry out of.

```bash
POLICY_AGENT=$(worktrail-policy --repo "$PWD" --json 2>/dev/null \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('agent_cli') or '')" 2>/dev/null)

# Substitute --no-native-skill when this host exposes no Skill tool.
CONTEXT_JSON=$(worktrail-invocation-context \
  ${AGENT_CLI:+--agent "$AGENT_CLI"} \
  ${POLICY_AGENT:+--policy-agent "$POLICY_AGENT"} \
  --native-skill --json)

INVOCATION_CONTEXT_AGENT=$(echo "$CONTEXT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['agent_cli'])")
INVOCATION_CONTEXT_NATIVE_SKILL=$(echo "$CONTEXT_JSON" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['native_skill_available']).lower())")
INVOCATION_CONTEXT_DISPATCH_MODE=$(echo "$CONTEXT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['dispatch_mode'])")
INVOCATION_CONTEXT_DISPATCH_ID=$(echo "$CONTEXT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['dispatch_id'])")
```

Exit 2 means `dispatch_mode: blocked` — neither capability is available. Stop and
report the `blocked_reason` verbatim; do not fall back to a different provider.

`agent_cli` precedence is unchanged: explicit invocation > repository policy >
machine-wide env > detected host > `claude`.

Every downstream consumer (go_seed.py `--agent`, `live.py full-real --agent`,
sdd-workflow seed prompt `Agent CLI:`) MUST use `$INVOCATION_CONTEXT_AGENT`.
Do NOT re-detect the host or re-read env vars in child processes — the resolved
values are the durable per-invocation identity.

Hold all four values for the entire session. Only `worktrail-run-record start` accepts
`--agent` (with `--native-skill-available "$INVOCATION_CONTEXT_NATIVE_SKILL"` and
`--dispatch-mode "$INVOCATION_CONTEXT_DISPATCH_MODE"`), so the audit record captures
which path was taken and why; the agent is stamped on the record once there. Every other
subcommand (`scope-review`, `set`, `finish`, ...) rejects `--agent` — do not pass it.
On `finish`, pass `--pr <url>` for any PR-owning completion state: the brief-closure
evidence gate (`worktrail-work-queue done --run`) reads the record's `pull_request`
field, and `--merge-result` prose alone does not satisfy it.

`$INVOCATION_CONTEXT_DISPATCH_ID` is the stable identity for this one `/go` invocation.
Pass it as `--by "$INVOCATION_CONTEXT_DISPATCH_ID"` to every `worktrail-work-queue
claim`/`claim-batch` call this dispatch makes (`references/batch-consumption.md`), and
forward it to the dispatched executor (native `Skill(...)` args or the adapter's `--args`,
Phase 7) as a `by:$INVOCATION_CONTEXT_DISPATCH_ID` token so sdd-workflow's own
handoff-seed claim call (`references/subagent-prompts.md#handoff-seed` Step 3) uses the
identical value — `claim()`'s `same_owner` result only means "this dispatch" when both
calls of the pair pass the same `--by`. Also pass it as `--dispatch-id
"$INVOCATION_CONTEXT_DISPATCH_ID"` on the Phase 6 `worktrail-run-record start` call, which
stamps it onto the run record for a later invocation's Active-run-resume check (below) to
compare against. Never regenerate or re-derive a dispatch id downstream; a mismatched value
defeats the whole guarantee (see
`docs/specs/research/concurrent-go-dispatch-brief-claim-race.md`).

### Phase 1 — Classify the Invocation, Then Show the Orientation Dashboard

**Classify the raw positional argument(s) first — before fetching or printing the dashboard, and before any `AskUserQuestion` call.** This is the single place invocation parsing happens; nothing later in this skill re-derives it.

**The grammar is owned by `worktrail-go-parse`, not by this prose.** Run it and act on the result — do not re-implement the precedence here, and do not hand-classify an argument you think you recognize:

```bash
REPO_NAMES=$(worktrail-resolve-repo --start "$PWD" --json \
  | python3 -c 'import json,os,sys; print(",".join(os.path.basename(c) for c in json.load(sys.stdin).get("candidates", [])))')
worktrail-go-parse "$ARGS" --repos "$REPO_NAMES"
```

`$ARGS` is the raw positional argument string exactly as typed. Add `--picker-active` when a Level-2 category picker is already open (Phase 1b); without it a bare integer is free text, because no global numbered list exists.

The result always carries every field, so no existence checks are needed. `canonical` is the
noun-verb spelling of what was typed (`auto` → `handoff auto`); when it differs from `raw`,
print it once as `(reads as: worktrail-go <canonical>)` so the older spellings teach the
current grammar. Act on `mode`:

| `mode` | Action |
|---|---|
| `dashboard` | Proceed to the orientation dashboard below. When `repo` is set, scope it to that repository. |
| `help` | Delegate to `Skill("worktrail-help", args="<help_topic>")` and stop; do not fetch or render the dashboard. A bare noun (`worktrail-go handoff`) or an unrecognised verb lands here too, with the noun as `help_topic`. |
| `capture` | Delegate to `Skill("worktrail-handoff", args="<free_text>")` and stop; do not fetch or render the dashboard, and do not claim or dispatch anything — this is issue capture, not work. |
| `list` | Run `worktrail-work-queue list` and print its output verbatim, then stop; do not fetch or render the dashboard. |
| `drain` | Read `references/drain.md` and run the installed `worktrail-drain` console script with the resolved invocation agent, passing `drain_max_items` and `drain_repo`. Do not fetch or render the dashboard, and do not claim a brief in the interactive process. The console script is an internal executor; users enter drain requests through `worktrail-go` only. |
| `auto` | Hold `$AUTO_MODE=true` for the rest of the dispatch (spec 017). `repo`, when set, scopes it. |
| `route` | Explicit route override: hold `route` as `$ROUTE_OVERRIDE` and `spec` as `$ARG_SPEC`. Skip classification later (Phase 5) and dispatch directly. |
| `intent` | v1 intent keyword in `intent` — maps to routes, skips classification later. `spec` carries the spec id when one was given. |
| `brief` | Hold `brief_id` as `$BRIEF_ID` and `brief_path` as its resolved path. `brief_status: ambiguous` → show `brief_candidates` and ask which; `brief_status: none` → that id isn't in the queue, say so and re-list. |
| `picker_index` | A Level-2 picker selection (Phase 1b); the choice is in `picker_index`. |
| `free_text` | Unstructured request in `free_text`, classified later by `classify.py` (Phase 5). |

Then hold `$ARG_REPO` from `repo`, `$ARG_INTENT` from `intent` (or `free_text` when no intent keyword was given), and `$ARG_SPEC` from `spec`.

**Now fetch the dashboard** (already skipped entirely above for `help`/`capture`/`list`/`drain`). Detect mode via `resolve_repo.py --start "$PWD" --json`. Fetch queue JSON first and pass it to dashboard so the picker options are computed by the script, not by Claude — pass `--auto`/`--auto-repo` here when `$AUTO_MODE=true`, since that changes what the script itself computes.

**Pass the queue/decisions JSON via file, not inline argv.** Linux caps a single argv
string at ~128KB (`MAX_ARG_STRLEN`); a personal queue with 100+ handoffs routinely
exceeds that and `worktrail-dashboard --queue-json "$QUEUE_JSON"` fails at `exec()`
before Python even starts (`Argument list too long`) — confirmed live 2026-08-24. Write
both payloads to temp files and use `--queue-json-file`/`--decisions-json-file`
instead, which have no such limit. Also drop the blanket `2>/dev/null` on the
`worktrail-dashboard` call itself — swallowing stderr there is exactly what turned that
exec failure into a silent empty `$DASHBOARD_JSON` instead of a visible error; check the
exit status and surface stderr on failure instead:

```bash
QUEUE_JSON_FILE=$(mktemp)
DECISIONS_JSON_FILE=$(mktemp)
trap 'rm -f "$QUEUE_JSON_FILE" "$DECISIONS_JSON_FILE"' EXIT
worktrail-work-queue list --json 2>/dev/null > "$QUEUE_JSON_FILE"
worktrail-decision list --status open --json 2>/dev/null > "$DECISIONS_JSON_FILE"
AUTO_FLAGS=()
if [ "$AUTO_MODE" = "true" ]; then
  AUTO_FLAGS=(--auto)
  [ -n "$ARG_REPO" ] && AUTO_FLAGS+=(--auto-repo "$ARG_REPO")
fi
if [ "$RESOLVE_MODE" = "in-repo" ]; then
  DASHBOARD_JSON=$(worktrail-dashboard \
    --root "$REPO/docs/specs" --picked-dir "$BASE/picked" \
    --queue-json-file "$QUEUE_JSON_FILE" --decisions-json-file "$DECISIONS_JSON_FILE" \
    "${AUTO_FLAGS[@]}" --json) || {
    echo "worktrail-dashboard failed (exit $?) — see stderr above" >&2
  }
else
  REPOS_DIR="${HOME}/projects"; [ -d "$REPOS_DIR" ] || REPOS_DIR="$PWD"
  DASHBOARD_JSON=$(worktrail-dashboard \
    --repos "$REPOS_DIR" --picked-dir "$BASE/picked" \
    --queue-json-file "$QUEUE_JSON_FILE" --decisions-json-file "$DECISIONS_JSON_FILE" \
    "${AUTO_FLAGS[@]}" --json) || {
    echo "worktrail-dashboard failed (exit $?) — see stderr above" >&2
  }
fi
```

`$DASHBOARD_JSON` carries three pre-computed, deterministic fields — hold it for Phases 1b/2: **`rendered`** (the dashboard text), **`category_actions`** (Level-1 picker options), and **`category_items`** (Level-2 items per category, each carrying the dispatch fields its `action` needs — `id` for queue items, `spec_id`/`repo`/`path` for specs). With `auto`, it also carries **`auto_pick`**. Field contract: `references/dashboard-render.md`.

Handle a missing/empty dashboard gracefully (the `rendered` field already prints a "nothing active → brainstorm" line) — this now means the queue/decisions calls themselves returned nothing, not a swallowed `worktrail-dashboard` failure.

**Branch on the classification from above — this decides what gets printed, not the other way around:**

- **Brief-ID** (`$BRIEF_ID` held, from `handoff start ID`, a bare `BRIEF-ID`, `spec route X` naming a brief, or the queue-resolve match above): print only a one-line summary (e.g. `Dashboard: N specs, M in-flight`) — never the full `rendered` dashboard — and skip the picker entirely. Go straight to Phase 2.
- **Auto** (`$AUTO_MODE=true`): print the `rendered` dashboard as usual, skip BOTH picker levels, and use `$DASHBOARD_JSON.auto_pick`. Phase 5.5's collision branches (`references/spec-collision-check.md`, `references/related-brief-collision-check.md`) and its already-implemented branch (`references/subagent-prompts.md#already-implemented-check`) branch on `$AUTO_MODE` to skip `AskUserQuestion` — that tool is not registered in the headless one-shot processes `worktrail-go drain` spawns (verified 2026-08-10: a direct `claude -p` probe found no such tool available to call, not merely unanswered), so a Phase 5.5 prompt reached from an auto/drain dispatch would fail outright or leave the agent guessing an answer with no human to catch a bad one. Full flow: `references/auto-mode.md`.
- **Everything else** (`route` mode not naming a brief, `intent` mode, a bare integer with no active picker, or free-text): **print `$DASHBOARD_JSON.rendered` verbatim.** Do NOT re-render, reorder, regroup, or summarize it — rendering it yourself reintroduces the non-determinism this field exists to remove. Proceed to Phase 1b.

### Phase 1b — Two-Level Picker (AskUserQuestion)

For bare `/go` or free-text with no explicit route/brief — i.e. Phase 1's classification found no Brief-ID/Auto/help/drain match — use a **two-level picker**:

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

**Intake-brief triage gate (direct `worktrail-go BRIEF-ID` dispatch only, spec `intake-to-spec-triage`).**
When `$BRIEF_ID` was held from Phase 1's `brief` mode (the user named a specific brief id
directly, not a Level-2 picker selection), look up that brief's `kind` and `repo` in
`$QUEUE_JSON_FILE`'s `briefs[]` (match on `filename`, 1.1's `work_queue.brief_kind()`;
the same entry's `repo` field is the brief's own `repo:` frontmatter, hold it as
`$BRIEF_REPO` — this is **not** `$ARG_REPO`, which is only set when the user typed a
repo token in the invocation itself) before doing anything else:

- **`kind: execution`** (a `seeded-from:` brief) — unaffected; continue to the `claim`
  action below exactly as before.
- **`kind: intake`** (a raw handoff or consolidated brief with no `seeded-from:`) — there
  is nothing to implement yet, only a triage decision. Do **not** claim or dispatch it.
  Instead:

  1. Evaluate it in place — a single-brief scope over `queue_triage`'s own per-repo
     evaluator, so the same evidence-required verdict rule (2.x) governs an interactive
     pickup as a scheduled `evaluate` run:
     ```bash
     VERDICT_JSON=$(worktrail-skill-dispatch \
       --evaluate-brief-triage "$BRIEF_PATH" \
       ${BRIEF_REPO:+--triage-repo "$BRIEF_REPO"} \
       --triage-agent "$INVOCATION_CONTEXT_AGENT")
     ```
     A brief with no `repo:` frontmatter (`$BRIEF_REPO` empty) omits `--triage-repo`
     (evaluated in the `NO_REPO_KEY` group, same as a full `evaluate` run). Exit 1 with `VERDICT_JSON`
     printing `null` means the evaluator produced no identifiable verdict for this
     brief id at all — report that and stop rather than guessing one.
  2. Present the verdict (`verdict`, `evidence`, `confidence`, and any target field —
     `target_change`/`target_repo`/`proposed_change_name`/`question`) to the user and ask
     for confirmation via `AskUserQuestion` before acting on it — never auto-apply a
     triage verdict from an interactive pickup, even a high-confidence one. `auto`/drain
     dispatches never reach this gate (1.2's `intake-untriaged` skip already keeps an
     intake brief out of `auto_pick`), so there is no unattended branch to cover here.
  3. Apply on confirmation via the same `queue_triage` apply path 3.x's scheduled runs
     use (`resolve_duplicate_targets()` + `apply_verdicts()`), scoped to this one verdict:
     ```bash
     ACTION_LOG_JSON=$(worktrail-skill-dispatch \
       --apply-brief-triage "$VERDICT_JSON" \
       --triage-agent "$INVOCATION_CONTEXT_AGENT" \
       --confirm)
     ```
     A decline leaves the brief queued untouched — do not call `--apply-brief-triage`
     at all. Report the resulting action-log entry (`action`, `status`, `path`/`error`)
     to the user and STOP; a triaged intake brief is never carried forward into Phase
     3's claim+dispatch flow in the same invocation, regardless of what the verdict was
     (including `work-directly`, which converts it into a future execution brief for a
     *later* `worktrail-go` pickup, not this one).

Resolve the user's choice and dispatch by its `action`:

- **Level-2 item selected** — dispatch directly from the `category_items` entry; it carries `action`, `spec_id`, `repo`, `path`, `id`, and other dispatch data.
- **"Other" / typed reply at level 1** — treat as free-text (Phase 5).
- **"Other" / typed reply at level 2** — a bare spec id or integer routes to that item; anything else is free-text (Phase 5).

**Action → dispatch:**
| `action` | dispatch |
|---|---|
| `resume` | stalled in-flight brief (claimed ≥48h ago with no completion — likely an abandoned session; freshly-claimed briefs are hidden as actively owned). Before continuing, verify what the prior session already landed (merged PRs / commits / spec status referencing the brief id) so you resume the remainder, not redo it → Phase 3 (no re-claim) |
| `implement` | active spec → `Skill("worktrail-sdd-workflow", args="<path> route:E <spec_id>")`, where `<path>` is the item's `path` (multi-repo) or `$REPO` (in-repo) |
| `close-stale` | stale-bookkeeping spec → **do NOT run the orchestrator** (files already merged on base). If the spec being closed is an epic doc under `docs/specs/epics/` (or is linked from one), first run the epic-closure PROVISIONAL check (`references/routes.md` §B "Closing an epic") before flipping any status to completed. Confirm the spec's pending impl tasks are truly shipped, then branch on the item's `format`: **devkit** (`files:` exist + are git-tracked on the base branch — `next_action` lists the task ids) — flip those `TASK-*.md` `status:` → `completed` and land a docs-only PR (the way the 068 stale-status case was closed). **openspec** — OpenSpec's `tasks.md` carries no per-task `files:` frontmatter, so confirming "truly shipped" stays a judgment call (re-run the referenced tests, check the citing PR — actually run them and cite the real output; "re-verified" without a pasted re-run is exactly the unverified-closure pattern `worktrail-work-queue done`'s evidence gate now rejects for handoff-brief closures, see `worktrail-handoff/SKILL.md`'s "Closing with a re-verification claim" note); once confirmed, create a fix-branch worktree (`references/subagent-prompts.md#fix-branch-worktree-setup`, slug e.g. `close-stale-<spec_id>`) and run `worktrail-close-stale-openspec --worktree "$WT" --change-id <spec_id> [--task-ids <comma-joined stale_task_ids>] --json` — it flips the checkboxes and runs `openspec archive -y --json` in one step (defaults to every pending task id when `--task-ids` is omitted). Land the same PR through the normal Phase 8 flow (pre-PR gate, `go:risk-*` labels, CI watch loop) — never a hand-rolled `gh pr create`, which would bypass the label-enforcement hook (mirrors PR #547/#548's shape, scripted instead of hand-rolled each time). Both branches: re-run the dashboard to confirm the spec drops to sync/complete. **After the docs-only PR lands**, this spec's task/verify worktrees are otherwise never revisited by anything (the orchestrator's own `cleanup_group()` only runs on the delivered-merge path, which this action deliberately skips) — run `references/worktree-cleanup.md`'s scoped invocation against `<spec_id>-*` to tear them down through its normal classify-then-confirm flow. Skip the teardown (report it, don't fail) when `<spec_id>-*` has no worktrees on disk. |
| `claim` | queue item → batch-claim it plus any related queued briefs (see **Batch consumption** below), then Phase 3 |
| `answer-decision` | open decision → present it interactively and record the answer — see `references/answer-decision.md` |
| `consolidate-cluster` | detected brief cluster → run `consolidate_cluster.py preview <members...>` to re-validate + draft a consolidated brief, show the draft via `AskUserQuestion` requiring an explicit confirm (no default-yes), then run `consolidate_cluster.py execute <members...> --draft '<preview JSON>' --confirm` (or `--decline`, which performs zero writes) — for a cluster whose combined member bodies are large, write the preview JSON to a file first and pass `--draft-file <path>` instead of `--draft '<preview JSON>'`: the inline form fails with `OSError: Argument list too long` once the payload nears the kernel's ~128KB `MAX_ARG_STRLEN` argv limit |
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

Argument classification and extraction (`$ARG_REPO`, `$ARG_INTENT`, `$ARG_SPEC`, `$ROUTE_OVERRIDE`,
`$BRIEF_ID`) already happened in Phase 1, before the dashboard was fetched — see its "Classify
the raw positional argument(s) first" step. Nothing here re-derives it.

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

If no explicit route (Phase 1 `route` or `intent` mode) and not a brief resumption, run classify.py to classify the free-text request.

**`--state` is a small signals object, not the dashboard blob.** classify.py's `--state`
only reads two keys (`active_specs`, `handoff_queue`); it never reads `rendered`,
`category_items`, `repos`/`specs`, or `recent_runs`. `$DASHBOARD_JSON` carries all of
those, and in a multi-repo or long-history workspace it can be large enough that passing
it as a raw argv string overflows the OS argument-list limit (`Argument list too long`,
exit 126) — the call fails outside classify.py's own error handling, with no route
returned at all. Extract just the two signal keys into `$STATE_JSON` first, and pass
that instead — piping through `python3`'s stdin avoids the argv limit regardless of how
large `$DASHBOARD_JSON` is:

```bash
STATE_JSON=$(echo "$DASHBOARD_JSON" | python3 -c \
  "import sys, json; d = json.load(sys.stdin); \
   print(json.dumps({'active_specs': d.get('active_specs', 0), 'handoff_queue': d.get('handoff_queue', 0)}))")
```

**Claimed-brief hint.** If this dispatch came from a `claim` action (interactive or
`auto`), extract the PRIMARY claimed brief's `recommended-route` frontmatter before
classifying — the classifier can only weigh it if it's actually passed in:

```bash
RECOMMENDED_ROUTE=$(worktrail-handoff-seed seed "<primary-claimed-path>" --json \
  | python3 -c "import sys, json; print(json.load(sys.stdin).get('recommended_route') or '')")
HANDOFF_ROUTE_FLAG=(); [ -n "$RECOMMENDED_ROUTE" ] && HANDOFF_ROUTE_FLAG=(--handoff-route "$RECOMMENDED_ROUTE")
```

**Resumable-state pre-check (mandatory for every brief-sourced dispatch, not just an `E`
hint).** classify.py's Route E signals (`resume`, `handoff`, `worktree`, `open pr`, ...) are
plain keyword regexes with no negation awareness — a brief *reporting* that no resumable
state exists ("no worktree", "no open PR") trips the exact same signals as one describing
real in-flight work, and a fresh claim can organically outscore every other route on E
before `--handoff-route` even gets a say (observed: brief 20260812-163747 scored E=11 at
high confidence purely from its own bug-report prose). Run the mechanical check and always
pass its result — `resumable: false` disqualifies E outright regardless of text score or a
stale `recommended-route: E` in the brief's own frontmatter; omit the flag only for a
free-text dispatch with no claimed brief at all. Always pass `--repo "$REPO"` (already
resolved by Phase 3) as a fallback — the brief's own `repo:` frontmatter still wins when
present, but a brief captured without it (e.g. a meta-brief about worktrail itself) no
longer silently disables this check:

```bash
RESUMABLE_JSON=$(worktrail-check-resumable-state --brief "<primary-claimed-path>" --repo "$REPO" --json)
RESUMABLE_RC=$?
if [ "$RESUMABLE_RC" -eq 2 ]; then
  MALFORMED_WARNING=$(echo "$RESUMABLE_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin).get('warning') or '')")
  echo "BLOCKED: malformed brief path passed to resumable-state check: $MALFORMED_WARNING" >&2
  # Stop here — this is a caller bug in path construction (e.g. a picked-brief
  # directory missing its .md filename), not a legitimately-unknown brief; do
  # not proceed to classification with a silently-dropped resumability signal.
fi
RESUMABLE=$(echo "$RESUMABLE_JSON" | python3 -c "import sys, json; d = json.load(sys.stdin); print(str(d['resumable']).lower() if d.get('checked') else '')")
RESUMABLE_FLAG=(); [ -n "$RESUMABLE" ] && RESUMABLE_FLAG=(--resumable-state "$RESUMABLE")

worktrail-classify --request "$ARG_INTENT" --state "$STATE_JSON" "${HANDOFF_ROUTE_FLAG[@]}" "${RESUMABLE_FLAG[@]}" --repo "$REPO" --json
```

`checked: false` (the claimed brief itself couldn't be read — `--repo "$REPO"` covers the
missing-frontmatter case now) leaves `$RESUMABLE` empty and `--resumable-state` omitted —
fail-open to classify.py's prior behavior, never a reason to block dispatch. A `malformed:
true` result (exit code 2, distinct from every other `checked: false` case) is different:
the path itself was the wrong shape for a claimed brief — fail loud per the block above
instead of silently losing the whole disqualification signal. `auto` mode runs this exactly
the same way; there is no human here either, which is the whole reason a mechanical check
replaces agent judgment for this signal.

For free-text/level-2-item dispatches with no claimed brief, omit both `--handoff-route` and
`--resumable-state` (no hint to weigh, nothing to check):

```bash
worktrail-classify --request "$ARG_INTENT" --state "$STATE_JSON" --repo "$REPO" --json
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

One question — "has this already been done?" — asked three ways, by three branches that share
no state. Only two of the three are mutually exclusive with each other: a Route C/D/F/G
dispatch checks for **spec collision** (does a shipped spec already cover this request?), while
a brief-sourced dispatch on any other route checks for a **related-brief collision** (is a brief
this one names as `related:` actively claimed and in flight right now?) — those two never both
run. The third, **brief staleness** (did the work this brief describes already land while it sat
in the queue?), is not route-gated at all: it runs on every brief-sourced dispatch, on top of
whichever of the other two also applies. A free-text dispatch with no claimed brief skips all
brief-sourced branches; it may still run spec collision on Route C/D/F/G.

Every branch reads its guard's structured result the same way, and every
judgment call a guard hands to a human now arrives as the provider-neutral,
versioned `worktrail.pending-decision` envelope (`pending_decision` key;
deterministic decision id; provenance inside) with each lifecycle hop stamped
onto the run record's `pending_decisions` audit list — so the same question is
filed, presented, answered, and resumed identically across attended, adapter,
and unattended dispatch modes. Contract and resume procedure:
`references/decision-queue.md`.

**Already-implemented branch (every brief-sourced dispatch).**

Gated on the dispatch being brief-sourced (a claimed brief is in play) — there is no route
restriction. A free-text dispatch with no claimed brief skips this branch regardless of route.
Before starting Phase 6's run record, read the source and answer whether the brief's `focus`
already describes work present on the base branch. There is no script: the procedure, the
prompt shape, and the `$AUTO_MODE` fallback are
`references/subagent-prompts.md#already-implemented-check`, the same check the orchestrator
pre-launch gates run against a spec's pending tasks.

On "proceed", still run whichever of the two route-gated branches below also applies to this
dispatch — resolving this prompt does not skip them.

**Route C/D/F/G branch: spec collision.**

Gated on Phase 5's resolved route (`$ROUTE`) being `C`, `D`, `F`, or `G` — every other route
skips this branch. Runs in addition to the already-implemented branch above when the dispatch is
also brief-sourced. Before starting Phase 6's run record, check whether an existing,
already-`Implemented` spec under `docs/specs/` already covers the request: run
`check_spec_collision.py --repo "$REPO" --json` for the candidate list, judge each candidate
against the dispatch's comparison text (a claimed brief's `focus` field, or the free-text
`$ARG_INTENT`/its classify.py-derived summary when there's no claimed brief) using the same
actor + capability + primary domain rule as `references/subagent-prompts.md#overlap-check`, and
run `--verify <spec_id> --json` on any judged match.

**Auto-close applies to C/D only.** On a confirmed collision (`verify()`'s `confirmed: true`), a
brief-sourced Route C/D dispatch auto-closes the brief via `work_queue.py done ...
--implementation-complete --note "..."` and stops (no fresh dispatch); a brainstorm-sourced C/D
dispatch (no claimed brief) instead asks the user via `AskUserQuestion` before proceeding. A
confirmed Route F/G collision is **never auto-closed**, brief-sourced or not — always ask via
`AskUserQuestion` instead. Reason: C/D targets work that is new or not yet `Implemented`, so a
confirmed match is always a genuine duplicate of separate prior work. F/G targets an *existing*,
already-`Implemented` spec's own behavior by design — that spec is the controlling artifact the
fix or the change is against — so the matched candidate is frequently the very spec the F/G work
is about, not a separate collision; auto-closing on that self-match would wrongly kill a
legitimate bugfix or spec-change brief. Any non-confirmed outcome — `checked: false`, no
candidate judged a match, or `confirmed: false` — leaves Phase 6/7 unmodified and un-delayed for
every gated route. Full procedure, including both dispatch-source branches, the F/G ask-only
rule, and exact command syntax: `references/spec-collision-check.md`.

**Related-brief collision branch.**

Gated on the dispatch being brief-sourced (a claimed brief is in play), that brief's `related:`
frontmatter being non-empty, **and** Phase 5's resolved route being anything other than C, D,
F, or G — Route C/D/F/G is already covered by the branch above. Route E was also excluded while
the brief-staleness guard ran, to avoid stacking a second prompt surface on top of it; with that
guard removed, E has no other in-flight-sibling check, so it runs this branch. A free-text
dispatch has no claimed brief to read `related:` off. Runs in addition to the already-implemented
branch above when it applies. Before starting Phase 6's run record, run
`worktrail-check-related-brief-claims --brief "<claimed-brief-path>" --json` to ask whether any
brief this one names as `related:` is itself actively claimed and in flight right now. Read the
result the same way as the sibling branches: `checked: false` means the question was unanswerable
and Phase 6/7 proceed unmodified; `checked: true` with empty `active` is a definite negative and
also proceeds silently. On `checked: true` with non-empty `active`, **never auto-close the
brief** — the related work being in flight says nothing about whether this brief's own work is
done — so the operator is always asked via `AskUserQuestion`, batched across every active match,
before Phase 6/7 continues. Full procedure — command, how to read the result, the prompt shape,
and the run-record entry: `references/related-brief-collision-check.md`.

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
  --gates "$GATES" \
  --agent "$INVOCATION_CONTEXT_AGENT" \
  --dispatch-id "$INVOCATION_CONTEXT_DISPATCH_ID")
RUN=$(echo "$RUN_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['path'])")
```

Always pass `--dispatch-id "$INVOCATION_CONTEXT_DISPATCH_ID"` — it is what a later
invocation's Active-run-resume check (below) compares against to tell this dispatch's own
run apart from a different dispatch's.

Hold `$RISK_LEVEL` and `$GATES` for Phase 8's mandatory pre-PR gate
(`sdd-workflow/SKILL.md`'s `pre_pr_gate.py --run --risk --gates` call) — they are
classify.py's `risk`/`gates` fields verbatim, not re-derived later. `--gates "$GATES"`
persists the same value on the run record itself (comma-joined, possibly empty) so
`worktrail-reconcile-pr-labels`'s periodic sweep can recompute full
`automerge_eligible()` per PR and self-heal drifted `go:no-automerge` labels the same
way it already self-heals `go:risk-*` labels — not just hold it for this run's own
Phase 8.

Risk level: **low** (queue items, docs), **medium** (bug fixes, refactors), **high** (major features, spec rewrites). Hold `$RUN` for Phase 8. If the policy sets `run_record_dir`, pass it as `--dir` on every `run_record.py` call. By default run records (like the dashboard's other machine-local state) live under the operator state dir — `$WORKTRAIL_HOME` if set, else `~/.worktrail`, with the legacy `~/.go` still honored on not-yet-migrated machines.

### Phase 7 — Dispatch

Route the request to the right handler. Per-route playbooks: `references/routes.md`. Worktree and subagent procedures: `references/subagent-prompts.md`.

Dispatch policy is simple:

- Resolve the routing configuration before dispatching the route: `ROUTING_JSON=$(worktrail-policy --repo "$REPO" --resolve-routing "$ROUTE:$RISK_LEVEL" --json)`. `--resolve-routing`'s `ROUTE:RISK` argument is vestigial — `resolve_routing()` (`policy.py`) ignores it, kept only for call-site compatibility — and always returns the full resolved `{targets, tiers, roles, purposes, default_tier, drain}` configuration, not a flat `agent_cli`/`agent_model` pair and not a per-role triple. Per-spawn resolution happens downstream of this dict: `tier_for(role, task, roles, purposes, default_tier)` derives each task role's own `(tier, prefer, independent)` from it, and the adapter's `select_dispatch_cell()` resolves the front-door role's cell directly from `routing["roles"]["front-door"]`.
- Map `tier_for()`'s resolved output to the dispatch layer: `tier` names the `routing.tiers` row to select from; `prefer` (optional) names a `routing.targets` entry to move to the front of that row; `independent` (optional bool, judgment roles only) asks the selector to prefer any target on a different harness than the one that implemented the task. There is no separate per-role agent/model map and no `fallback` list to carry through — preference order lives in `routing.targets`' own file order, and the row itself is the entire fallback chain: the orchestrator's single selector (`select_cell`) walks that row in order, skipping `api`-pool targets without `api_opt_in`, skipping cells gated in `agent-capacity.json`, and resolves the concrete harness/model/effort at spawn time.
- Tiers resolve on a separate axis from `$ROUTE:$RISK_LEVEL` (per-task `tier`, or `complexity`/`purpose` mapped onto one): `tier_for()`'s precedence is an explicit per-task `tier` field > the role's own tier (for judgment roles: `review`/`resolve`/`ci-fix`/`assembly-resolve`) > a `routing.purposes` match on the task's `purpose` > `complexity` > `routing.default_tier`. `review` defaults to `{tier: default_tier, independent: true}` when `routing.roles.review` is unconfigured.
- Explicit invocation flags or caller-supplied `AGENT_CLI` always win over the derived routing values. The routing table slots into the existing precedence at the repository-policy tier: explicit invocation > repository policy (routing table here) > machine-wide env > detected host > `claude`.
- no routing table → every role resolves through `routing.default_tier` alone (a single row, its target file order the only preference), which for the starter template's two subscription targets behaves like today's flat single-agent behavior (REQ-NR005).

  Example `routing.tiers` in `~/.worktrail/routing.yaml` — target-keyed cells (`tiers.<row>.<target>
  = {model, effort?}`; a target with no cell in a row cannot serve that tier), a 3-row complexity
  ladder routing each to a progressively more capable model on the same target:

  ```yaml
  routing:
    targets:
      codex-sub: {harness: codex, pool: subscription}
    tiers:
      trivial:
        codex-sub: {model: gpt-5.6-luna}
      standard:
        codex-sub: {model: gpt-5.6-terra}
      hard:
        codex-sub: {model: gpt-5.6-sol}
  ```

  Model names above are illustrative — substitute whatever tier models the operator's provider
  actually exposes. Tasks whose format has no `complexity` field, or whose value has no matching
  row, fall through to `routing.default_tier`/`routing.roles` unaffected — `routing.tiers` is
  purely additive.

  **Routing by task purpose instead of complexity.** `routing.purposes` (renamed from
  `purpose_tiers`) maps a task's own `purpose` frontmatter value (set by
  `conductor/compile.py`'s inference pass, or by hand) to a `routing.tiers` row, so a task routes
  on *what it is* — architecture design, frontend/backend implementation, an exploratory spike,
  bulk categorization — rather than a human-assigned `complexity` label. `tier_for()` consults it
  ahead of `complexity` for implement/fix/cleanup spawns only; a task whose `purpose` has no
  matching entry (or carries no `purpose` field at all) falls through to `complexity` unaffected,
  same as an unmatched `routing.tiers` entry.

  Worked example mapping an eight-value purpose taxonomy onto the manual T1–T4
  (Deep/Build/Bulk/Trivia) tier scheme, over two subscription targets:

  ```yaml
  routing:
    default_tier: t2-build
    targets:
      claude-sub: {harness: claude, pool: subscription}
      codex-sub:  {harness: codex,  pool: subscription}
    purposes:
      architecture-design: t1-deep
      security-review: t1-deep
      frontend: t2-build
      backend: t2-build
      design: t2-build
      explore: t2-build
      categorize: t3-bulk
      bulk-mechanical: t3-bulk
      trivial: t4-trivia
    tiers:
      t1-deep:
        claude-sub: {model: opus}
        codex-sub:  {model: gpt-5.6-sol}
      t2-build:
        claude-sub: {model: sonnet}
        codex-sub:  {model: gpt-5.6-terra}
      t3-bulk:
        codex-sub:  {model: gpt-5.6-terra, effort: low}
      t4-trivia:
        codex-sub:  {model: gpt-5.6-luna}
  ```

  These purpose values are a starter set, not a fixed enum — `compile.py` reads
  `routing.purposes`' own keys as the closed vocabulary it classifies a task's `purpose`
  into, so the operator renames, adds, or removes categories by editing this table alone, no
  code change required. No `routing.purposes` table configured → `compile.py` never asks
  for `purpose` at all, identical to today's output. A row that omits a target (`t3-bulk`/
  `t4-trivia` above have no `claude-sub` cell) simply cannot be served by that target — the
  selector skips straight to the next target in file order.
- Record the resolved routing decision at dispatch time with `worktrail-run-record start ... --routing-decision "$ROUTING_JSON"` so the audit trail captures the resolved routing configuration used for dispatch.
- **Branch on `$INVOCATION_CONTEXT_DISPATCH_MODE`, never on the provider name.** The
  invocation context already applied the decision tree; do not re-derive it here, and do
  not read `agent_cli` as evidence about `Skill(...)`:

  | `dispatch_mode` | action |
  |---|---|
  | `in-session-resume` | continue the route in this session (see the Route E bullet below) |
  | `native-skill` | call `Skill("worktrail-sdd-workflow", ...)` directly, appending `run:$RUN` (Phase 6's run record path) per the Dispatch Contract below |
  | `adapter` | run `worktrail-skill-dispatch` with `$INVOCATION_CONTEXT_AGENT` (see the adapter section below) |
  | `blocked` | stop; report the resolver's `blocked_reason` verbatim |

  Never call `Skill(...)` speculatively and fall back after an exception — that is the
  failure the capability value exists to prevent. Never substitute a different provider
  than the one resolved; a provider whose CLI is missing resolves to `blocked`, which is
  an actionable stop, not a licence to switch.
- **Active-run resume (Route E) stays in-session — never spawn a nested worker, and never
  duplicate a genuinely different dispatch's own live run.** If the resolved dispatch is a
  Route E continue/resume (the dashboard/classifier resolved `action: resume`, or the route
  is E), first check whether the run is **already active**: its run record exists with no
  `final_status` AND its `worktree` path already exists on disk (this is the same run record
  `run_record.py`'s staleness logic treats as live). If not, this is a genuinely stalled run
  with no worktree — proceed via the normal Route E "reconstruct before acting" procedure.

  If the filesystem test says already-active, the evidence alone cannot tell "I am the
  process that started this run" apart from "a different, possibly still-running process
  started it" (both look identical: non-terminal + worktree exists) — the same failure shape
  as the single-session nested-worker incident (Datalena run go-20260811-132806), but at the
  cross-session level. Resolve the ambiguity with the run's own heartbeat and dispatch
  identity before acting on it:

  ```bash
  LIVENESS=$(worktrail-run-record liveness "$RUN" --dispatch-id "$INVOCATION_CONTEXT_DISPATCH_ID")
  SAME_DISPATCH=$(echo "$LIVENESS" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['same_dispatch']).lower())")
  FRESH=$(echo "$LIVENESS" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['fresh']).lower())")
  ```

  | `same_dispatch` | `fresh` | Meaning | Action |
  |---|---|---|---|
  | `true` | — | This exact `/go` invocation already claimed and started this run (the literal single-session nested-worker case the original rule covered). | Hand execution back to it by continuing the route **in this session** via the direct `Skill("worktrail-sdd-workflow", ...)` call (or, where the host blocks that, the seeded in-session path). Do **not** fall through to the Native Skill adapter or the headless subprocess spawn below and do not start a poll loop. |
  | `false` | `true` | A **different** dispatch owns this run and its heartbeat is recent — it is plausibly still actively working right now. Continuing "in this session" here would be a genuinely independent, duplicate continuation, not a hand-back — that is exactly the cross-session incident named above. | Do **not** continue in-session, do **not** spawn a nested worker. Stop and report: this run is currently owned by another active dispatch (`run_id`, `updated_at`, age from `$LIVENESS`); the user or a later retry decides next steps. |
  | `false` | `false` | A different dispatch started this run, but its heartbeat is stale — the owning process most likely crashed or was interrupted without calling `finish`. This is exactly the abandoned-run case Route E's resume path exists for. | Proceed via the full Route E "reconstruct before acting" procedure (routes.md §E) — restore state, run the drift check, determine complete/incomplete/obsolete, then re-enter at the detected stage. Do **not** take the same-dispatch fast-path shortcut; you are not the process that was working on it. |
- OpenCode parent sessions use the seeded subprocess path with `opencode` when the harness
  supplies the explicit `OPENCODE_PARENT` marker; explicit invocation, repository policy,
  and machine-wide provider overrides still win.
- Other hosts with a working headless CLI: use the seeded subprocess path from
  `references/subagent-prompts.md#subprocess-dispatch`.
- If subprocess dispatch is unavailable and `native_skill_available` is true, call
  `Skill(...)` directly and say so. If it is false, that combination is `blocked` —
  report it rather than attempting either path.

**Pinning a role to a tier/target.** `routing.roles` overrides one JUDGMENT_ROLE
(`review`/`resolve`/`ci-fix`/`assembly-resolve`) or task role (`implement`/`fix`/`cleanup`)
independently of the run's default tier — for example, to force an independent
code-reviewer model regardless of which target implemented the task. Add it under either the
repo-local policy's `routing:` block or the machine-wide `~/.worktrail/routing.yaml`
(`WORKTRAIL_ROUTING_FILE`) — same wholesale, block-level fallback as the rest of `routing`: a
non-empty repo-local `routing:` block is used in full and the machine-wide file is not
read at all, so it is not merged per-role with the machine-wide file:

```yaml
routing:
  roles:
    review:
      tier: t1-deep
      prefer: codex-sub
      independent: true
```

This resolves through `tier_for()` into `roles.review = {tier: "t1-deep", prefer:
"codex-sub", independent: true}`, which the bullet above maps onto the orchestrator
invocation's `tier`/`prefer`/`independent` triple. For `review`/`resolve`/`ci-fix`/
`assembly-resolve` this is the *only* override that can beat the run default (DEC-003) — a
per-task `tier` field or `routing.purposes` match is never consulted for those roles, by
design (independent-reviewer guarantee, 13.3). `independent: true` is a soft preference
(`exclude_harness` on the selector), never a hard requirement — a same-harness reviewer beats
no review when nothing else is healthy, and the run record names which cell actually served.
No `routing.roles` entry for a role → it resolves through `routing.default_tier` like any
other role.

**Adapter dispatch (`dispatch_mode: adapter`).** `Skill(...)` is a host capability, not a
shell command, and `native_skill_available` — not the provider name — is what says
whether this host has it. An embedded Codex session resolves `agent_cli: codex` with no
`Skill(...)` at all, which is exactly this branch. On it,
generate the seeded-dispatch prompt first so the child attaches to this front door's
existing run record instead of treating raw `handoff:<id> route:<X>` arguments as a
fresh handoff-seed invocation and opening a second run. **Before spawning, fail fast if
the one resolved agent is already capacity-gated** — `worktrail-skill-dispatch` builds a
single-provider command with no fallback of its own (`build_command(agent, ...)`), so a
gated `$INVOCATION_CONTEXT_AGENT` otherwise dies at launch, burning a doomed child and
landing the run on `blocked_external_dependency` after the fact instead of before it
(live incident: run `worktrail/go-20260825-163910`, a `claude` weekly-limit refusal
killed the dispatch instantly while `opencode` sat healthy in the capacity cache). This
checks only the one already-resolved agent — it never substitutes a different provider,
preserving the "never silently falls back" contract below:

```bash
GATE_JSON=$(worktrail-agent-capacity check-agent --agent "$INVOCATION_CONTEXT_AGENT" --routing "$ROUTING_JSON")
if [ $? -ne 0 ]; then
  TARGET=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('target') or '')")
  FAILURE_CLASS=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('failure_class') or '')")
  RETRY_AFTER=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('retry_after') or '')")
  worktrail-run-record capacity-gate "$RUN" \
    --provider "${TARGET:-$INVOCATION_CONTEXT_AGENT}" --failure-class "${FAILURE_CLASS:-unknown}" \
    ${RETRY_AFTER:+--retry-after "$RETRY_AFTER"} --note "resolved agent gated before adapter spawn"
  worktrail-run-record finish "$RUN" --status blocked_external_dependency \
    --merge-result "resolved agent capacity-gated; no child launched"
  exit 1
fi
DISPATCH_SPEC="${SPEC_ID:-none}"
if [ -n "${BRIEF_PATH:-}" ]; then
  DISPATCH_SPEC="${SPEC_ID:-handoff:$HANDOFF_ID}"
fi
SEED_ARGS=(
  --repo "$REPO" --base "$BASE" --route "$ROUTE"
  --spec "$DISPATCH_SPEC" --run "$RUN"
  --agent "$INVOCATION_CONTEXT_AGENT"
)
if [ -n "${BRIEF_PATH:-}" ]; then
  SEED_ARGS+=(--brief "$BRIEF_PATH")
fi
SEED=$(worktrail-go-seed "${SEED_ARGS[@]}") || {
  worktrail-run-record finish "$RUN" --status failed_recoverable \
    --merge-result "adapter seed generation failed; no child launched"
  exit 1
}
worktrail-skill-dispatch \
  --agent "$INVOCATION_CONTEXT_AGENT" \
  --skill worktrail-sdd-workflow \
  --args "$SEED" \
  --cwd "$REPO"
```

For authoring routes add the `--write` and provider-specific `--add-dir` flags described
below to that same dispatch command. The seed's `Run record path:` is authoritative: the
child enters seeded-dispatch mode, retains `Agent CLI:`, consumes `Brief:` without
claiming it again, and writes terminal completion to `$RUN`. The parent remains the
handoff owner identified by `by:$INVOCATION_CONTEXT_DISPATCH_ID`; do not send the raw
handoff arguments through the adapter after Phase 6 has created `$RUN`.

Run `worktrail-skill-dispatch` with the resolved `--agent`, `--skill`, seeded
`--args`, `--cwd "$REPO"`, and — for any route that will author, edit, or commit
files (D/F/G/H) — `--write` values. Without `--write`, a headless claude/opencode
child has no channel to answer the permission prompts sdd-workflow's own file
edits and commits require and stalls or fails partway through; codex needs
no approval flag beyond `-s workspace-write`, but `workspace-write` is scoped to
the child `--cwd`. For a Codex child, also pass the policy's run-record directory
and the sibling-worktree directory as repeatable `--add-dir` values, for example:
`--add-dir "$HOME/.worktrail/runs" --add-dir "${REPO}-worktrees"`. These are the
minimum additional roots required for sdd-workflow to claim its run and create
its sibling worktrees; do not grant a broad home-directory root. It executes that
same provider without shell interpolation and never silently falls back to a
different provider. A trusted Codex child inherits the parent's verified ChatGPT
subscription session by default; use `--no-inherit-codex-auth` only for an
intentionally isolated child. The default path accepts only a private file-backed
ChatGPT `auth.json` and never copies general parent configuration. **Verify the outcome from the run record, not the return
code** — same rule as `#openspec-authoring`. `worktrail-skill-dispatch` already blocks on the
child process's exit (`child.wait()`), so a return only proves the *process* ended — not that
it ever called `finish` before dying (a crash, kill, or interruption mid-CI-watch leaves `$RUN`
non-terminal with no other signal). Run the check as a hard gate, not a narrated read:

```bash
worktrail-run-record assert-terminal "$RUN"
```

A non-zero exit means the dispatch ended without a terminal `final_status` — treat this as a
failure the parent must act on (Route E's stalled-run recovery on the next `/go` invocation, or
an immediate `finish --status failed_recoverable` here), never as silent success inferred from
the child's own exit code. Skill slash-names (unlike the OpenSpec
`commands/` bundle) resolve bare — `worktrail-sdd-workflow`, not
`worktrail:worktrail-sdd-workflow` — because `_prompt` types the frontmatter
`name:` directly and Claude Code matches an installed Skill by that name with
no plugin-namespace prefix; live-verified 2026-08-09. Keep native `Skill(...)`
primary when the host exposes it, and report adapter use in the run status.
**Do not use the adapter for an active-run resume** (see the dispatch policy above):
it spawns a fresh nested worker, so re-entering a run the active parent already owns
starts a duplicate/self-polling worker instead of handing execution back. Reserve the
adapter for fresh background dispatch (routes D/F/G/H). Headless `drain`
one-shots are terminal owners: invoke the adapter as a blocking foreground
command (or use native Skill in-session) and do not return until the shared run
record has a real `final_status`; the bounded background poll is attended-only.

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
(`~/.worktrail/agent-capacity.json`, override with `WORKTRAIL_AGENT_CAPACITY_CACHE`) can be
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

**Pending-user-decision presentation and resume (go/adapter boundary).** A
dispatch blocked on a guard's decision envelope is recoverable without
guessing, in every dispatch mode. An attended host presents the exact record
with `worktrail-skill-dispatch --present-decision "<decision-id>" --run "$RUN"`
— the same versioned envelope JSON for every provider, the `[presented]` audit
hop stamped automatically, nothing spawned. Once a human has answered it,
resume through the exact id with `--resume-decision "<decision-id>"`, which
threads a verbatim `decision:<decision-id>` token into the child invocation
and launches nothing unless that exact record is answered and live (open,
superseded, or unknown → exit 2, nothing spawned). Never re-present from the
markdown by hand, and never resume through a prefix of an id — a partial id is
a different record. Lifecycle contract: `references/decision-queue.md`;
boundary mechanics and the poll-side `pending_user_decision` handoff:
`references/subagent-prompts.md`.

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
`gh pr checks --watch` (never a sleep loop, never an end-turn/resume poll —
single-turn wait discipline, and pure wait-tails stay with the dispatcher:
`references/ci-watch-loop.md` `{#ci-wait-discipline}`), then classify the settled checks — pass →
finish; transient infra → rerun; code defect → minimal patch (≤5 iterations); product
decision → `blocked_product_decision`; ceiling → `failed_recoverable`.

## Dispatch Contract (to worktrail-sdd-workflow)

```
Skill("worktrail-sdd-workflow", args="<repo-path> route:<X> [spec-folder] run:<run-path>")
Skill("worktrail-sdd-workflow", args="handoff:<id> route:<X> by:<dispatch-id> run:<run-path>")
```

sdd-workflow requires the resolved `route:X` on every dispatch, including handoff-seed
dispatches. The full `handoff:<id>` is retained alongside the route so the executor can
load the claimed brief without losing its context. On a `handoff:<id>` dispatch, always
append `by:$INVOCATION_CONTEXT_DISPATCH_ID` — sdd-workflow's own handoff-seed claim call
threads it through as `--by` so `claim()` can tell "this dispatch already owns the brief"
apart from a different, possibly concurrent, dispatch (`same_owner` in the claim response;
see the Invocation Context section above). Omit the `by:` token only for the non-handoff
form, which never calls `claim()`.

Every native-skill dispatch (both forms) MUST also append `run:$RUN` — Phase 6's
already-open run record path. Without it, sdd-workflow's own Phase 6 falls through to a
fresh `worktrail-run-record start` and permanently orphans the parent's record at
`route_selected`, since the child's record becomes the only one anything ever calls
`finish()` on (`docs/specs/research/dead-dispatch-backlog-investigation.md`, observations
5/6). This mirrors the adapter path's `--run "$RUN"` threading
(`worktrail-go-seed`'s seeded-dispatch prompt) for the native-skill dispatch surface,
which carries no such prompt of its own to thread it through otherwise. Never omit `run:`
to mean "let the child open its own" — that recreates the orphaned-record bug this token
exists to close.

On a decision-resume dispatch, the adapter threads one more token: the exact
answered decision's id, appended verbatim as `decision:<decision-id>` (via
`--resume-decision`). The executor consumes that exact record once and applies
the validated answer at the original block site; the id is never re-derived or
normalized downstream.

## Related Briefs

When a brief is claimed, surface any related briefs from its `related` frontmatter field before beginning work. Related briefs still sitting in `queue/` for the same repo are batch candidates — the Phase 2 batch-consumption flow ranks them first.

## Examples

**Defect repair**
```
/go spec fix the upload timeout
```
→ Route F from Phase 1 (`fix` is a parsed form now, not classified) → Phase 5 skipped → dispatch to sdd-workflow

**Free-text request**
```
/go the upload silently drops files over 2GB
```
→ Dashboard + category picker → classify.py picks the route → dispatch to sdd-workflow

**Bare /go (returning session)**
```
/go
```
→ Two-level picker (`category_actions` → `category_items`) → Phase 2 dispatches the chosen item

**Explicit route**
```
/go ggb spec route D 003-payments
```
→ route D detected, Phase 5 skipped → `Skill("worktrail-sdd-workflow", args="<ggb-path> route:D 003-payments")`

**Queue claim by ID (execution brief)**
```
/go 20260613-001000-feature-x
```
→ One-line dashboard summary → claim → dispatch to sdd-workflow

**Queue pickup by ID (untriaged intake brief)**
```
/go 20260613-001000-raw-handoff
```
→ One-line dashboard summary → `kind: intake` → single-brief triage gate → evaluate,
present the verdict, apply on confirmation → STOP (no claim, no sdd-workflow dispatch)

**Auto mode (spec 017)**
```
/go handoff auto
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
- Once a human has given direction — approved a fix, approved a batch of quarantine-recovery
  fixes, said to continue — carry it through to completion without re-pausing to ask
  "continue?" at each subsequent step, task, or batch. This applies to Route E quarantine
  recovery in particular: fixing one blocked task is not itself a reason to stop and check in
  before fixing the next one the same direction already covers. Confirmed live 2026-08-27: an
  agent stopped mid-recovery to ask "continue with the rest, or leave it here?" after the human
  had already approved the fix pattern for the batch — the human's reaction was "why did you
  stop? ... the orchestrator is supposed to be autonomous." Only the skill's own named
  human-gates (stale-spec-check collision, precheck DAG conflicts, a pending
  `worktrail-decision`) warrant a pause; a large remaining task count is not one of them.

## Constraints and Warnings

- Never move queue files manually; use `work_queue.py` for all queue operations (claim, claim-batch, done, release, link).
- Batch only briefs that share the same repo AND would ride the same route/worktree/PR; a batch is an execution convenience, never a scope expansion. When in doubt, leave a candidate in the queue.
- Do not invoke SDD stage skills (brainstorm, spec-to-tasks, orchestrator) directly; let sdd-workflow coordinate them.
- If sdd-workflow is not installed, decline SDD-route requests gracefully; still serve non-SDD queue items.
- Never skip the orientation dashboard — even brief-ID invocations show a one-line summary.
- When resuming a picked brief, do not call `work_queue.py claim` again.
- Auto mode (spec 017) removes the selection prompt ONLY: it never resumes in-flight briefs, never picks blocked/`no-repo`/busy-repo briefs, never retries a lost claim race more than 3 times, and never bypasses policy approval gates or risk tiers. When `auto_pick.pick` is null, report and stop — do not fall back to interactive selection or invent work.
- The run record MUST be started for every dispatched invocation and closed with an explicit completion state.
- Never guess around a pending user decision: present it
  (`worktrail-skill-dispatch --present-decision`), let a human answer, and
  resume through the exact id (`--resume-decision`) — every hop lands on the
  run record's `pending_decisions` audit trail.
- A pending user decision is a genuine fork (see above) — not "there is more work left" or
  "the next step is the same kind of fix the human already approved." Don't manufacture a
  check-in out of remaining task count or session length; see the Best Practices bullet on
  carrying through already-given direction.

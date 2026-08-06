# Investigation: does `go`'s own headless-dispatch spawn have drain.py's pre-#128 PR-label gap?

**Triggered by:** work-queue brief `20260805-173801`. Hypothesis under test:
"live.py's standalone one-off spawn path" also builds an uncovered `gh pr
create` outside `integrate.py`'s code-enforced `_refresh_pr_labels()`, the
same shape PR behindthedash/worktrail#128 fixed for `drain.py`'s headless
one-shots (`ensure_pr_risk_label()`).

## Verified Observations

**live.py itself has no uncovered `gh pr create` call site.** An AST-walk
test (`tests/router/test_pr_creation_callsite_enforcement_coverage.py`,
added in PR #89) parses every `.py` file under `src/worktrail` for a
`["gh", "pr", "create", ...]` list literal — the exact shape
`subprocess.run([...])` builds — and requires a registered, behaviorally
proven consumer for every hit. It currently finds exactly two:

- `orchestrator/integrate.py` — the group-PR path; proven to source its
  `--label` flags from `_refresh_pr_labels()` (`pre_pr_gate.py
  --labels-only`), never a hand-rolled list.
- `orchestrator/live.py`'s `full()` — a dev-only CLI subcommand
  (`worktrail-live full`) that fans out against a hardcoded sandbox repo to
  record a golden test cassette. Proven sandbox-only and never called from
  `dispatch.py`/`coordinator.py`'s production path (`inspect.signature`
  default check + AST scan of both modules for a call to `live.full`).

`test_every_callsite_is_reviewed` fails the build if a third Python-code
call site ever appears un-registered. This directly rules out the brief's
literal premise: there is no third, undocumented `gh pr create` inside
`live.py`'s production spawn code (`spawn_one`, `live_run`, `full_real`,
etc.) — none of those functions construct the `["gh", "pr", "create"]` list
at all; PR creation for every route that goes through the orchestrator
(`worktrail-live full-real`, used by routes C/D/F/G/H's `new`/`implement`/
`modify` pipelines per `subagent-prompts.md#orchestrator`) is delegated
entirely to `integrate.py`, which is enforced.

**The actual gap is one level up: `go`'s own Phase 7 headless-worker spawn,
not `live.py`.** Per `subagent-prompts.md`'s Subprocess Dispatch section and
Route F's playbook (`routes.md` §F step 5), two structurally different things
both get called "Route F/H dispatched via a one-off spawn":

1. **Spec-owned Route F/G, and Route D/C** — routed through
   `pipeline-details.md#modify-pipeline`/`#implement-pipeline`/`#new-pipeline`
   → `worktrail-live full-real` → `integrate.py` group-PR creation.
   **Covered** (case above).
2. **Unspecced-code Route F (and H, I→F)** — routes.md §F step 5: "a direct
   fix-branch worktree for unspecced code
   (`subagent-prompts.md#fix-branch-worktree-setup`)". This path never
   touches `live.py` or `integrate.py` at all. The agent session executing
   `sdd-workflow` makes the fix itself and, per SKILL.md Phase 8, runs
   `gh pr create --label <go:risk-*> ...` **as its own Bash tool call** —
   the "manual Phase 8 prose" path the original audit
   (`classify-gate-enforcement-audit.md`) already identified as Route J's
   (PR #74's) shape, "same as routes F/G/H/I and any non-grouped Route D/C
   work."

**How that agent session gets spawned matters, and `go`'s own dispatch has
no equivalent to `ensure_pr_risk_label()`.** Per `subagent-prompts.md`'s
Subprocess Dispatch section:

- Claude-hosted `go` parent → spawns `claude -p "$SEED" --permission-mode
  bypassPermissions` as a detached headless worker for background routes
  D/F/G/H (`references/subagent-prompts.md:118`).
- OpenCode-hosted `go` parent → spawns the seeded subprocess path with
  `opencode` (per `worktrail-go` SKILL.md's dispatch table).
- Codex-hosted `go` parent → stays **in-session**, no subprocess at all
  (`subagent-prompts.md:24`, `:78-79`).

In every one of these three cases, if the dispatched route lands on the
unspecced-code Route F path above, the `gh pr create` is issued directly by
that agent's own Bash tool — exactly the shape `drain.py`'s docstring
describes for its own spawns ("`claude -p / codex exec / opencode run`...
issues its own `gh pr create` -- a raw subprocess call from claude/codex/
opencode, never reachable by the Claude Code PreToolUse label-enforcement
hook... **even a headless `claude -p` session is not guaranteed to load the
interactive hook config**", `drain.py:231-240`). That last clause matters: the
gap is not Codex/OpenCode-only — a headless `claude -p` one-shot is *also*
not guaranteed hook coverage, which is exactly why `drain.py`'s correction
applies unconditionally, regardless of which agent produced the PR.

`go`'s Phase 7 completion path is `poll_run.py`
(`src/worktrail/router/poll_run.py`, 100 lines) — read in full. It does
exactly one thing: reads the shared run record, checks for a `finish` key,
and prints a completion/still-running message. It contains **zero**
label-related logic. `grep -rn "ensure_pr_risk_label" src/ skills/ docs/`
returns exactly three hits, all inside `drain.py` itself (definition,
docstring mention, one call site at `drain.py:689`) — the function is never
imported or called from `router/`, `orchestrator/`, or any skill doc outside
`drain/`.

The AST-walk test's own docstring (`test_pr_creation_callsite_enforcement_
coverage.py:41-45`) states agent-executed one-off `gh pr create` calls are
"already covered by the `worktrail-preflight` PreToolUse hook + pass-marker
system" — true for the Claude Code **interactive** case the hook targets,
but this statement doesn't carry the same headless-session caveat
`drain.py`'s own docstring makes explicit, and is silent on `go`'s own
detached/in-session spawns entirely (that test only scopes Python-code call
sites, per its own docstring line 45: "this test only covers call sites
Worktrail's own Python code constructs").

## Unknowns / Missing Evidence

- Whether a headless `claude -p` worker spawned by `go`'s Phase 7 (as
  opposed to `drain.py`'s) has ever actually produced a PR with a missing
  `go:risk-*` label in production — not reproduced live in this
  investigation; the gap is confirmed by code absence (no consumer, no
  correction step), not by an observed incident on this specific call site.
- Whether `automerge_eligibility.sh`'s fail-closed check (PR #107,
  referenced in `drain.py`'s docstring) would actually stall a PR produced
  this way the same way it did for `drain.py` before #128 — inferred from
  identical mechanics (same fail-closed script, same missing-label
  condition), not independently re-verified against this call path.

## Hypotheses

None remaining for the core question — root cause is confirmed by direct
code reading (AST-walk test results + `poll_run.py`'s full contents +
`grep` for `ensure_pr_risk_label`'s only call site), not inference.

## Confirmed Root Cause

The brief's literal premise (a third, uncovered `gh pr create` call site
*inside* `live.py`) is **false** — the AST-walk test in PR #89 already rules
this out, and `live.py`'s only call site (`full()`) is a reviewed,
proven-inert sandbox exemption.

The **adjacent, real gap** the brief was circling is confirmed: `go`'s own
Phase 7 headless-dispatch spawn (`claude -p "$SEED"` for Claude-hosted
parents, the seeded `opencode run` path for OpenCode-hosted parents, or
Codex's in-session execution) has **no equivalent to `drain.py`'s
`ensure_pr_risk_label()`** when the dispatched route lands on Route F/H's
unspecced-code, non-orchestrator, agent-executed `gh pr create` path. This
is the identical failure shape PR #128 fixed for `drain.py`'s "worktrail-go
auto" one-shots, on a **different, not-yet-patched** spawn call site: any
direct/interactive `/go` invocation dispatching routes D/F/G/H in the
background, not just the unattended queue-drain loop. `poll_run.py` (the
only code that runs after that spawn completes) does not perform any label
check, and `ensure_pr_risk_label()` is never called outside `drain.py`.

## Recommended Next Route

**Route J (fix), as a separate follow-up — not folded into this
investigation**, per the hypothesis-gated rule (this note makes no code
change) and to keep the fix scoped and reviewable on its own:

- Extract `ensure_pr_risk_label()` (and its `_current_pr_labels()` helper)
  out of `drain/drain.py` into a shared location (e.g. `router/` or
  `orchestrator/`, wherever avoids a `router` → `drain` import direction
  that doesn't already exist), and call it from `go`'s Phase 7 poll-exit
  path (`poll_run.py` or the SKILL.md step that reads its result) once a
  `finish` entry with a PR URL is observed — mirroring exactly how
  `drain.py:689` calls it today.
- Needs a design decision on where the risk level comes from for this call
  site: `drain.py` reads it from the run record's Phase-6-recorded risk
  (same source `go`'s own run record already carries per
  `worktrail-go` SKILL.md Phase 6 — `--risk "$RISK_LEVEL"` at `run_record.py
  start`), so this should be a direct reuse, not new plumbing.
- Also worth deciding in the same fix: whether to run this correction for
  the Codex in-session case too (no subprocess to poll, but the run record
  and PR URL are still available at Phase 8 completion in the same
  session) — otherwise the fix only closes the gap for Claude/OpenCode
  headless spawns and leaves Codex in-session dispatch uncovered by the
  same reasoning that makes it uncovered by the PreToolUse hook today.

Completion: `investigation_complete`.

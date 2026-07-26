# GO v2 — Route Playbooks

One module per route. **Load only the playbook for the selected route** — never
this whole file into a subagent. Shared procedures stay in
`subagent-prompts.md` (anchors cited as `#name`); rationale in `docs/design/history/go-v2-design.md`.

Conventions used by every route:

- **Evidence over confidence:** a step is complete only with evidence matched to
  risk (tests run, CI green, app walkthrough). Never claim success from code
  inspection alone.
- **Run record:** update phase/decisions via `run_record.py set|append` at each
  transition; `finish` with one of the ten completion states — no vague endings.
- **Scope:** smallest complete change. Complete every requested outcome before
  opening a PR. Only genuinely separate work may become a handoff brief
  (`/handoff new`), and its run-record scope review must identify it as
  `different purpose:` or `user approved:`; a handoff cannot replace unfinished
  acceptance work.
- **PR body:** all implementation routes use the template at the bottom of this
  file via `create-pr-from-spec` (or directly for non-spec PRs).
- **Retrieval order (token discipline):** repo instructions → controlling
  artifact → GitNexus graph (callers/blast radius; base-branch only — worktree
  diff wins) → exact files → targeted grep → tests → merged PRs → broad search
  last. Hand subagents pointers, not pasted file bodies.
- **change-spec Skill id:** invoke `developer-kit-specs:specs.change-spec`
  exactly — `developer-kit-specs:change-spec` (dropped `specs.`) does not exist
  and will fail.

---

## Route A — Idea Discovery

For unstructured ideas/problem statements. **No implementation without an
explicit decision** (`no_implementation_without_approval` gate).

1. Frame: user/business problem, who benefits, observable behavior to improve,
   smallest complete outcome, what would make it commercially unsuccessful.
2. Inspect for prior art: `overlap_check.py` + GitNexus feature ownership.
3. Dispatch brainstorm in **discovery framing** (`#brainstorm-template` with
   `constraints` noting "discovery only — no tasks"): produce problem framing,
   scope boundaries, risks/unknowns, candidate approaches, recommendation.
4. Output: a discovery note at `docs/specs/research/<slug>.md` (or seed a spec
   folder if the user converts it) + an epic/feature proposal.
5. Ask the decision: proceed to Route B/C/D, defer (handoff brief), or stop.

Completion: `investigation_complete` (note only) or
`planned_ready_for_implementation` (proposal accepted).

## Route B — Epic Planning

Multi-feature capability. Output an epic document
`docs/specs/epics/<NNN>-<slug>.md` containing: business objective, personas,
scope/non-goals, success metrics, feature decomposition (each independently
valuable + releasable), dependencies, sequencing, risks, release strategy.
Each decomposed feature lists its future spec id (lineage downward). Then route
each feature through Route C as it is picked up — do not spec all features up
front. Completion: `planned_ready_for_implementation`.

## Route C — Feature Planning

One coherent feature, spec first, optionally continuing into implementation.

Run the v1 `new` pipeline through spec-to-tasks: overlap check →
spec worktree (`#spec-worktree-setup`) → constitution (if missing) → brainstorm
→ spec-check → optional technical-plan → spec-to-tasks (all per SKILL.md `new`
pipeline). Spec header must cite owning epic (when one exists), business
objective, non-goals, security/data/UX implications, and testable acceptance
criteria.

**After spec-to-tasks (always):** push `spec/$SPEC_ID` and open a docs-only PR
(→ `$BASE`) so the spec artifact is durable across sessions.

**Inline D transition (MARKERS == 0 only):** inspect the brief's
`implementation-intent:`. `requested` continues as Route D (orchestrator →
sync) in the same session without a new `/go`; `planning-only` stops with an
explicit planning-only completion; missing/`unknown` asks once — "Proceed to
implementation now, or stop for review?". A Route-C brief must not be marked
done without an explicit `--planning-only` or `--implementation-complete`
completion mode. If implementation is requested, do not create a follow-up
handoff at this boundary.

Completion: `planned_ready_for_implementation` (stopped) or delegates inline to
Route D (`completed_pr_open` / `completed_and_merged`).

## Route D — Implementation

Approved spec exists (or the request explicitly authorizes spec + build).

1. If no spec: run Route C first in the same session (record the decision).
2. Execute the v1 `new`/`implement` pipeline: orchestrator (`#orchestrator`) →
   sync (`#sync-before-teardown`) → teardown (`#worktree-lifecycle`).
3. **Validation ladder (cheapest first):** focused unit tests → focused
   integration → typecheck → lint → targeted build → affected suites → full
   required checks → browser/E2E → security/perf when applicable. Fix before
   advancing.
4. **Application validation** for UI/full-stack changes: run the app locally,
   walk the real user flow (success, failure, loading/empty/error/denied
   states), verify persistence, check console/network. Component rendering
   alone is not evidence when routing/API/auth/persistence is involved.
5. **Auth-protected routes:** use the repo's supported mechanism (policy
   `auth_testing:`, seed scripts, Playwright storage state, dev-auth mode) —
   never invent bypasses, never commit tokens. For authz changes test:
   permitted role, forbidden role, unauthenticated, cross-org attempt, direct
   API bypass of the UI.
6. PR per the template below; merge gate per SKILL.md.

Completion: `completed_and_merged` / `completed_pr_open` /
`completed_awaiting_human_approval`.

## Route E — Continue / Resume (incl. PR & CI repair)

Existing work is the controlling artifact. **Reconstruct before acting:**

1. Restore state: dashboard (`dashboard.py`), orchestrator run journal,
   `run-<spec>.status.json` heartbeat sidecar, GO run records
   (`~/.go/runs/<repo>/`), handoff brief (if `sdd-workflow handoff[:id]` —
   `#handoff-seed`), branch/worktree status, open PRs + unresolved comments +
   CI results. A `fanout_failed` sidecar phase means the run is stuck — handle
   per `#precheck-gate`.
2. **Drift check (mandatory for handoffs):** verify referenced files, symbols,
   commits, and specs still exist; reclassify if circumstances changed. Never
   trust stale handoff assumptions.
3. Determine complete / incomplete / obsolete / incorrect; continue from actual
   repository state — never repeat earlier work.
4. **CI/PR repair sub-mode** (secondary F): reproduce the failing check
   locally, fix on the same branch, re-run affected validation, push. Quarantined
   orchestrator groups: see `#worktree-lifecycle` quarantine handling.
5. Re-enter the owning route at the detected stage (the dashboard's
   `next_action` is the entry point).

Completion: whatever the resumed route's completion is.

## Route F — Defect Repair

Behavior violates the spec or an established expectation.

1. Reproduce or establish the failure (logs, failing test, walkthrough).
2. Identify the controlling behavior: spec / AC / invariant. Record the §5
   three-way comparison (documented vs current vs requested) in the run record
   — if the *requested* behavior is the change, reroute to G.
3. Root cause per the no-guessing rule (hypothesis → validation → confirmed).
4. Failing regression test first; prove it fails for the original reason.
5. Narrowest correct fix via the `developer-kit-specs:specs.change-spec` skill
   (`--type=bugfix`; exact id per the conventions block) when a spec owns the
   behavior — run it through `pipeline-details.md#modify-pipeline`
   (single-worker orchestrate for 1-task fixes) — or a direct fix-branch
   worktree for unspecced code (setup: `subagent-prompts.md#fix-branch-worktree-setup`).
6. Validate adjacent behavior + edge cases; update the spec only if behavior
   was undocumented/ambiguous or an invariant was missing.
7. PR with root cause + evidence; sync if a spec changed. Once merged, tear
   down the worktree per `subagent-prompts.md#worktree-lifecycle` — the spec
   path's `sync` step (`#change-spec-worktree-setup` → `modify` pipeline) or,
   for unspecced code, `subagent-prompts.md#fix-branch-worktree-teardown`.

Completion: `completed_*`; if root cause cannot be proven, reroute to I instead
of shipping a guess.

## Route G — Specification Change

Intentional behavior change; **spec first, code second.**

1. Locate the current spec; record old vs new behavior + compatibility
   implications (existing users/integrations who observe a different result).
2. Update spec + acceptance criteria via the `developer-kit-specs:specs.change-spec`
   skill (`--type=delta`; exact id per the conventions block). Determine
   migration/rollout/deprecation needs; note impacted dependent specs.
3. spec-to-tasks (delta) → orchestrator → sync, per
   `pipeline-details.md#modify-pipeline`.
4. Tests prove the NEW contract; remove/update tests that pinned the old one.

Completion: `completed_*`.

## Route H — Refactor / Technical Debt

Behavior stays stable; implementation improves. Require before any edit:
defined invariants, measurable rationale, scope boundary.

1. Establish characterization coverage where existing tests are insufficient.
2. Confirm blast radius via GitNexus (callers/dependents) + worktree grep.
3. Smallest improvement series; no behavior change, no API drift unless the
   refactor explicitly owns an architectural contract (then it needs a spec —
   reroute to G for that part).
4. Prove externally observable behavior unchanged (same tests green before and
   after); performance evidence when performance is the rationale.
5. Don't rewrite tests to match the new shape unless they were improperly
   coupled to internals.

Completion: `completed_*`.

## Route I — Investigation

Cause, fix, or even the defect's existence is unknown. **No code changes**
except minimal diagnostics (logging/asserts/repro tests), per the
hypothesis-gated rule.

1. Reproduce; collect evidence (logs, traces, bisect, GitNexus relationships).
2. Structure per the no-guessing rule: Verified Observations / Unknowns /
   Hypotheses / Validation Steps / Confirmed Root Cause (only if proven).
3. Output an investigation note `docs/specs/research/<slug>.md` (or the
   relevant spec's `research/`) + **recommended next route**.
4. If root cause is confirmed and the fix is small + clearly in scope, continue
   into Route F in the same run (record the transition); otherwise stop.

Completion: `investigation_complete` (or the F completion if continued).

## Route J — Workflow Evolution

Changes to GO, skills, plugins, agent prompts, orchestration, cassettes —
**this is production code** (`routing_cassette_required` gate).

1. Work in the developer-kit repo: worktree off `main`, never the installed
   copy under `~/.claude/plugins`.
2. Routing changes MUST add/update scenarios in
   `scripts/cassettes/routing_cassette.json` and keep `test_classify.py`,
   `test_policy.py`, `test_run_record.py`, and the v1 script suites green.
3. Adverse-effect gate: a change that reduces route accuracy, drops required
   artifacts, or weakens a safety gate does not merge — fix or revert.
4. Update `docs/design/history/go-v2-design.md` when the architecture changes; run
   `make skill-lint SKILL=plugins/developer-kit-specs/skills/sdd-workflow` and
   `python3 .skills-validator-check/validators/cli.py --all` before the PR.
5. Never self-modify the active workflow mid-run based on a single run's
   evidence — capture the signal in the run record, propose separately (§21.3).
6. Route J is **not done** when the code or docs change is implemented locally.
   Commit, push, open/update the PR, and evaluate the merge gate before using a
   Route J completion state. "Implemented and validated" without a PR is an
   incomplete Route J run.

Completion: `completed_pr_open` / `completed_and_merged`.

---

## PR template (all implementation routes)

```markdown
## Outcome
## Business or User Value
## Route Selected            <!-- letter + name + one-line reason -->
## Epic / Feature / Specification   <!-- ids/paths; "none" only for Route J/unspecced fixes -->
## Behavior Before
## Behavior After
## Scope
## Non-Goals
## Implementation Summary
## Security and Authorization Impact
## Data or Migration Impact
## UI Validation             <!-- what was walked through, or "n/a" -->
## Tests and Evidence        <!-- AC → test/evidence mapping -->
## Scope Completeness         <!-- run-record scope-review items and evidence -->
## Pre-PR Test Gate          <!-- pre_pr_gate.py: command + PASS, or explicit policy skip -->
## Performance Impact
## Deferred Work and Handoffs <!-- brief ids created, or "none" -->
## Risk Assessment           <!-- low|medium|high|critical + why -->
## Rollback Plan
## Auto-Merge Eligibility    <!-- eligible/ineligible + exact reason + go:risk-*/go:no-automerge labels applied (pre_pr_gate.py --risk output; the labels, not this prose, are what auto-merge.yml actually enforces) -->
```

Sections may be one line; never omit a header — reviewers and the merge gate
key off them.

- **Auto-Merge Eligibility wording:** a reason containing "own CI automation" means the
  PR is *eligible* (subject to the live required-checks gate) and needs no further
  action — external automation will merge it; do not apply `go:no-automerge` for this
  reason. Every reason that is still ineligible (protected op, risk over policy ceiling,
  wrong target branch, or unconfigured branch protection) needs a human decision.

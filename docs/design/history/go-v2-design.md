# GO v2 — Assessment and Architecture

**Historical design record — not loaded at runtime.** Consult only for Route J
workflow-evolution changes (and update it when the architecture changes). The
normative artifact rules live in `artifact-policy.md`; operating procedure lives
in `SKILL.md` + `routes.md` + `subagent-prompts.md`. Supersedes `go-design.md`
(the v1 historical record). Some §1 evidence (line numbers, "not built" gaps)
describes v1 and is intentionally stale.

---

## 1. Assessment of GO v1 (evidence-based)

Evidence: `SKILL.md` (166 ln), `references/go-design.md` + `subagent-prompts.md`
(815 ln), `scripts/` (6 modules, ~1,400 ln logic + ~1,500 ln tests), the merged-PR
history of `briankudera/developer-kit` (#88–#129 reviewed), and the recorded
operational retrospectives (orchestrator specs 011–015).

### 1.1 Strengths worth preserving

1. **Logic in Python, prose in SKILL.md.** State detection (`dashboard.py`), repo
   resolution (`resolve_repo.py`), overlap detection, handoff seeding are
   deterministic, stdlib-only, and unit-tested. v2 extends this pattern; it does not
   replace it.
2. **Artifact authority hierarchy** (go-design §2.1): code+tests = what IS,
   spec = what SHOULD BE, KG = self-healing cache, review files = never current.
   This is the #1 drift protection and is load-bearing. Unchanged in v2.
3. **Worktree isolation** — never author or implement on the base checkout;
   per-spec worktrees; quarantine preserves evidence. Unchanged.
4. **Atomic handoff claim** — `work_queue.py` rename is the single concurrency
   arbiter. v2 maps onto it; it does not reimplement it.
5. **Stage Result contract** (`STATUS:/MARKERS:/SUMMARY:`) for subagents, and the
   context-packaging rule "hand pointers, not blobs."
6. **Sync-before-teardown ordering** and the resumable orchestrator run journal.
7. **Thin-router layering**: SKILL.md is an operating procedure; details live in
   references. v2 keeps SKILL.md thin and adds route modules as references.

### 1.2 Limitations and failure modes (verified)

| # | Limitation | Evidence |
|---|---|---|
| L1 | **Route coverage gap.** Only `new` / `implement` / `continue` / `pr` / `brainstorm` are built. Modify, bug fix, research, kg-sync are documented as "v1.1 (not built); acknowledge and stop" (`subagent-prompts.md#intent-menu`). | PR history shows defect/CI-repair and workflow-evolution PRs (#107, #110, #118, #120, #127, #116…) are among the *most common* real work — all currently unrouteable through GO. |
| L2 | **No request classification.** Menu-driven only; free-text intent inference was explicitly deferred (go-design §9). The user must know which pipeline they need. | go-design.md §9 |
| L3 | **No risk model or bounded-autonomy gates** beyond the implicit orchestrator pause. Nothing distinguishes a docs change from an auth/migration/billing change. | SKILL.md (no risk vocabulary) |
| L4 | **No run record.** The orchestrator journals its own runs, but GO itself records nothing: route chosen, why, what was inspected, final state — all unauditable after the session ends. | absence in `scripts/` |
| L5 | **No repo policy layer.** Base-branch handling, auto-merge behavior, and CI-skip patterns are hard-coded prose in `#sync-before-teardown`; consuming repos (GGB targets `dev`, datalena targets `dev`) cannot declare their own gates. | subagent-prompts.md:300–404 |
| L6 | **No explicit completion states.** Runs end in prose. Retrospectives record premature-success claims (orchestrator "completed" while orphaned; full-real timeout data loss before PR #16). | memory: orchestrator resumable full-real; GGB betty-hugging false alarm |
| L7 | **Latent defect:** `#sync-before-teardown` polls CI with `sleep 30` in a foreground Bash block for up to 30 min — exceeds the 10-min Bash timeout, and uses the exact `sleep`-poll pattern `#orchestrator` forbids three sections earlier. | subagent-prompts.md:336–392 vs :289–293 |
| L8 | **No routing tests.** The orchestrator has record/replay cassettes; GO's own routing (the highest-leverage decision) has zero test coverage. | absence of `test_*` for routing |
| L9 | **Lineage is partial.** spec→tasks→PR exists; idea→epic→feature does not (no epic layer), and PR bodies are not required to cite the controlling spec/route/risk. | templates/ + create-pr-from-spec |
| L10 | **GitNexus guidance lives outside the skill** (in `~/AGENTS.md`), so the front door is not repository-agnostic on retrieval strategy. | ~/AGENTS.md |

---

## 2. v2 architecture

The front door stays a **coordinator**. The pipeline (assignment §26):

```text
intake -> classify -> restore state -> load repository policy -> select route
  -> load required skills -> execute route module -> validate -> create PR
    -> evaluate merge gate -> record outcome
```

### 2.1 Components

| Component | Form | Responsibility |
|---|---|---|
| `SKILL.md` | prose (thin) | pipeline order, dispatch table, gates, completion states |
| `worktrail-classify` | deterministic, tested | request text + repo-state signals → route, risk, confidence, gates |
| `worktrail-policy` | deterministic, tested | repo-local `docs/specs/go-policy.yaml` merged over safe defaults |
| `worktrail-run-record` | deterministic, tested | start/update/finish a machine-readable run record |
| `references/routes.md` | prose modules | per-route playbooks A–J (load only the selected one) |
| `references/subagent-prompts.md` | prose (v1, kept) | dispatch templates, worktree lifecycle, sync procedure |
| `scripts/cassettes/routing_cassette.json` | fixture | golden routing scenarios (assignment §16.1) asserted by `test_classify.py` |
| existing v1 scripts | unchanged | dashboard, resolve_repo, overlap_check, handoff_seed |

Deterministic-where-possible: classification, policy resolution, and record-keeping
are Python. Judgment (spec content, root-cause analysis, review) stays with the
model, scoped by the selected route module.

### 2.2 Route-classification model

Routes (assignment §4) and their mapping onto the existing skill ecosystem:

| Route | Name | Executes via |
|---|---|---|
| A | idea-discovery | brainstorm (discovery framing, no tasks) → decision gate |
| B | epic-planning | brainstorm with epic decomposition → N feature proposals |
| C | feature-planning | `new` pipeline through spec-to-tasks, stop before orchestrator |
| D | implementation | full `new`/`implement` pipeline (v1 behavior) |
| E | continue/resume | dashboard + journal/handoff restore → re-enter at actual stage; includes PR/CI repair |
| F | defect-repair | `specs.change-spec --type=bugfix` → single-worker orchestrate → sync |
| G | spec-change | `specs.change-spec --type=delta` → spec-to-tasks delta → orchestrator → sync |
| H | refactor/debt | characterization tests → narrow implement → behavior-equivalence evidence |
| I | investigation | evidence collection, no code; findings note + recommended next route |
| J | workflow-evolution | this repo only: worktree + routing-cassette gate + PR |

`classify.py` is a **scored signal table**, not an LLM call: each route has weighted
text signals plus state preconditions (e.g. F requires an expectation to violate;
E requires existing work). The top score wins; if the top two are within the tie
threshold **and** the pair is materially different (F/G, A/C, D/C), the script
returns `ambiguous_between` and the conductor asks one targeted question
(assignment §1: ask only when the distinction materially affects the outcome).
Bug-fix vs spec-change ties are broken by the assignment §5 comparison
(documented intent vs current vs requested), which the conductor records in the
run record.

Risk is keyword + path classified: `critical` (billing, secrets, destructive
migration, auth weakening), `high` (authz, migrations, PII), `medium` (API/schema),
`low` (docs, tests, internal). Risk maps to gates via policy.

### 2.3 Workflow state machine

Run-level states, journaled in the run record:

```text
intake -> classified -> state_restored -> policy_loaded -> route_selected
  -> executing -> validating -> pr_open -> merge_gate -> done(final_status)
```

Every phase writes its completion to the run record before the next begins, so a
killed session resumes by reading the record (Route E consumes GO's own records the
same way it consumes orchestrator journals). Final status MUST be one of the ten
explicit completion states (assignment §22), enforced by `run_record.py finish`.

### 2.4 Artifact and lineage model

Lineage chain: idea → epic → feature → spec → AC → tasks → code → tests → PR.
Implementation in this ecosystem:

- **Epic** = `docs/specs/epics/<id>-<slug>.md` (new, Route B output) listing child
  feature/spec ids. Optional — single-feature repos skip it.
- **Feature/spec** = existing `docs/specs/<id>/spec.md`; v2 requires the header to
  cite `Epic:` (when one exists), `Status:`, business objective, and non-goals
  (already mostly present in the brainstorm template).
- **Tasks** = existing `tasks/TASK-*.md` (frontmatter `files`, `kind`,
  `success-criteria` = the AC→test mapping).
- **PR** = `create-pr-from-spec` body extended with Route, Risk, Spec id,
  AC-satisfied checklist, evidence, deferred handoffs, rollback, auto-merge
  recommendation (assignment §12.4 template, embedded in routes.md).

### 2.5 Handoff schema

The existing work-queue brief (handoff skill) already carries: id, created, focus,
repo, remote, base-branch, status, suggested-skills, blocked-by, related, Focus,
Discovery context, Suggested approach, Key artifacts, Open questions. v2 maps the
assignment §7.2 fields onto it rather than forking the format:

| §7.2 field | Brief location |
|---|---|
| handoff_id / created_at / repository / branch / status | frontmatter (existing) |
| reason_for_handoff, problem_statement, business_value | `## Focus` |
| evidence, affected_areas, files_or_symbols, related PRs/commits | `## Discovery context` + `## Key artifacts` |
| proposed_approach, alternatives | `## Suggested approach` |
| recommended_route | **new optional frontmatter `recommended-route: A–J`** (classifier reads it as a strong signal; absent = classify from focus text) |
| acceptance_criteria, suggested_tests | `## Suggested approach` checklist |
| dependencies | `blocked-by:` (existing) |

Consuming a handoff = Route E intake: verify referenced files/commits still exist
(drift check) before acting; reclassify if drifted; lifecycle stays in
`work_queue.py` (claim/done/release) exactly as v1.

### 2.6 Skill selection & subagent contract

- Load **only the selected route module** plus the anchors it cites. Never load all
  of routes.md, all templates, or specialist skills up front.
- Specialist skills (security review, ts/react code review, nextjs, render, …) are
  loaded by the route module at the phase that needs them (e.g. Route F on auth
  code loads the security-review skill at validation, not at intake).
- Subagent context contract = v1's pointer rule + Stage Result Summary, extended
  with the assignment §8.4 structured output for investigation-type dispatches.
  `subagent_type` is always `general-purpose`; the subagent invokes skills itself.
- The conductor verifies subagent findings against files before relying on them;
  subagents never merge.

### 2.7 Token-efficiency strategy

Retrieval order (assignment §9.1) embedded in SKILL.md: instructions → controlling
artifact → GitNexus → exact files → targeted grep → tests → merged PRs → broad
search last. Plus the v1 disciplines kept verbatim: `/compact` before the
orchestrator, pointers-not-blobs, model selection (sonnet when constraints are
dense, opus for sparse ideation), one dashboard scan reused as the working-state
table, batch independent commands. New: the run record doubles as the compact
working memory (§9.3) — update it instead of re-reading materials.

### 2.8 GitNexus strategy

Use the graph for: callers/dependents, blast radius, tests-for-symbol, feature
ownership, parallel-work collision checks. Use direct inspection for: string
literals, config keys, uncommitted worktree state. **The index reflects the base
branch only** — in a worktree the diff wins over the graph; never assume new
symbols exist in the graph. (This codifies `~/AGENTS.md` into the skill so the
front door is repo-agnostic.)

### 2.9 Worktree / parallel / git / PR strategy

Unchanged from v1 (worktree per task, branch forms, orchestrator collision
planning via dependency-disjoint groups) with two additions: branch-form table
extended with `workflow/<id>-<slug>` for Route J, and the §12.4 PR template
required for all routes. **Fix applied to v1 defect L7:** CI wait now uses
`gh pr checks "$PR_URL" --watch` (bounded by the Bash `timeout` parameter,
re-issued on timeout) instead of a hand-rolled `sleep` loop.

### 2.10 Authentication-protected local testing

Route modules D/F/G require, for UI/full-stack changes: discover the repo's
supported auth mechanism first (seed scripts, Playwright storage state, dev auth
mode — e.g. GGB's `.env.local` + Playwright profiles), test permitted role,
forbidden role, unauthenticated, and direct-API bypass for authz changes; never
commit tokens. Codified in routes.md §D validation, sourced from policy
(`auth_testing:` key) when the repo declares one.

### 2.11 CI cassette design

Two layers:

1. **Routing cassette (built now):** `scripts/cassettes/routing_cassette.json` —
   ~20 golden scenarios (assignment §16.1: raw idea, feature plan, approved
   implement, clear bug, behavior change, valid/stale handoff resume, CI repair,
   auth UI change, migration, refactor, debt-during-feature, parallel task,
   overlapping PR, missing spec, ambiguous request, destructive op, auto-merge
   eligible/ineligible, workflow change). `test_classify.py` replays them and
   asserts route, risk, and gate **exactly** (stable structured output → golden
   assertions); prose fields are unasserted (flexible per §16.3).
2. **Conversation cassette (existing):** the orchestrator's record/replay
   (`orchestrate.py record/check`) continues to cover Phase-2 mechanics.

Adverse-effect gate: Route J changes to GO must keep the routing cassette green;
a scenario regression fails CI (`plugin-validation.yml` runs the suite — same
mechanism as the existing orchestrator goldens).

### 2.12 Audit log design

`run_record.py` writes one YAML per run under `~/.go/runs/<repo>/<run-id>.yaml`
(outside the project repo: records are operational telemetry, not product
artifacts; the path is overridable via policy `run_record_dir`). Fields follow
assignment §20 (route, reason, risk, artifacts, skills loaded, subagents,
decisions, evidence, deferred work, merge decision, final_status…). `finish`
validates the final status against the ten allowed completion states. No secrets,
no raw conversation.

`finish` additionally code-enforces the `no_implementation_without_approval` gate
(routes.md §A, classify.py's Route-A gate string): a run whose `selected_route`
is `A` cannot `finish` on an implementation-completion state
(`completed_and_merged`/`completed_pr_open`/`completed_awaiting_human_approval`)
unless a `decisions` entry was recorded first — Route A's own completions
(`investigation_complete`/`planned_ready_for_implementation`) are unaffected.
Previously this gate was prose-only in routes.md with zero code consumer
(`docs/specs/research/classify-gate-enforcement-audit.md`).

### 2.13 Self-improvement mechanism

Improvement signals (misroutes, corrections, repeated clarifications, false
completions) are appended to the run record `decisions:`/`failure_recovery:`
fields at the moment they occur. Proposals to change GO are **Route J work**: a
separate worktree+PR that updates this design doc, adds/updates routing-cassette
scenarios, and passes the existing cassette — never an in-flight self-edit
(assignment §21.3). The user-level `tasks/lessons.md` loop continues to capture
human corrections.

### 2.14 Auto-merge policy

Decision = `policy.automerge.enabled` AND risk ≤ `policy.automerge.max_risk` AND
PR targets an allowed branch AND required checks green AND no unresolved review
threads AND no protected-operation flag (destructive migration, billing, secrets,
authz expansion, major dep upgrade — hard never-list in `classify.py` output,
non-overridable by policy). When ineligible, deliver the PR anyway and state the
exact remaining approval. Repos without a policy file default to
`enabled: false` (safe default); this developer-kit repo's existing auto-merge
workflow (#126–#129) remains the enforcement of record for Route J.

The formula above is policy-level (offline, from `go-policy.yaml`) and cannot
see whether the base branch's GitHub-side protection actually enforces
anything — a repo can set `automerge.enabled: true` while its base branch has
zero required status checks, in which case `gh pr merge --auto` (native or via
the repo's own workflow) merges instantly with no validation waited on
(continuum PR #4, brief 20260719-235920). `pre_pr_gate.py --risk` closes this:
when the policy-level formula says eligible, it additionally calls
`automerge_preflight.required_checks_gate()`, a live `gh api
repos/{owner}/{repo}/rules/branches/{branch}` query for the branch's actual
required status checks plus the repo's `allow_auto_merge` setting (false makes
`gh pr merge --auto` fall back to an immediate merge instead of waiting).
Either signal missing forces `go:no-automerge` regardless of the policy-level
verdict — checked per-PR, not cached, since protection can change mid-session.

The formula's output still had to reach the actual PR as a label, and until
this fix that step was agent-executed and unenforced for every one-off route
(F/G/H/I/J, non-grouped C/D): `pre_pr_gate.py --risk` only *printed* an
`AUTOMERGE LABELS:` line, and nothing stopped a `gh pr create` that never
copied it (`docs/specs/research/classify-gate-enforcement-audit.md`, PR #74).
`worktrail-preflight run --risk` now records the resolved labels in the same
pass marker it already writes for the test gate, and `worktrail-preflight
check` — which the `gh pr create`/`gh pr ready` PreToolUse hook already
delegates to for the test-gate decision — denies the tool call itself when a
`gh pr create` command's `--label` flags don't match. The parallel
orchestrator's own group-PR path (`integrate.py`'s `_refresh_pr_labels()`)
was already exempt from this gap since it applies labels programmatically at
`gh pr create` time; it is unaffected by this change.

---

## 3. Migration plan (v1 → v2)

1. **This PR (smallest coherent v2):** classifier + policy + run-record scripts
   with tests and routing cassette; routes.md playbooks; SKILL.md rewritten as the
   v2 pipeline; L7 sleep-loop fix. v1 scripts, templates, and subagent-prompts.md
   are kept and cited — no behavior of the `new`/`implement` pipelines changes.
   Backward compatible: bare `go`, `go <repo> <intent> <spec>`, and
   `sdd-workflow handoff[:id]` all still work (explicit intent args bypass classification,
   exactly as v1 args bypassed menus).
2. **Follow-up (deferred, handoff briefs):** epic template + Route B decomposition
   polish; policy adoption in consuming repos (GGB/datalena `go-policy.yaml`);
   conversation-level cassette for full route walkthroughs; GO run-record
   consumption inside Route E restore.
3. **Rollback:** revert the PR; v1 SKILL.md is fully self-contained in git
   history. Run records and policy files are additive and ignored by v1.

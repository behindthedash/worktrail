# Conductor + Lanes: orchestrator design for an OpenSpec-authored lifecycle

Status: accepted design, partially implemented — see the table below
Date: 2026-07-25
Applies to: `worktrail` @ v0.3.0 (`44f2fd3`)

| Phase (§6) | State |
|---|---|
| **P0** status out of task branches | shipped, v0.4.0 (#7) |
| **P1** lanes: union `plan_groups()` on shared-file edges | shipped (#25) — see the measurement note in §4.2 |
| **P2** warm lane execution | **not started** |
| **P3** conductor `compile` → cached RunPlan | shipped: prompt templating off `TaskSource` in #12, compile + cache + safe application in #24 |
| **P4** `OpenSpecTaskSource` | shipped (#11), `kind` recovery in #20 |
| **P5** corpus migration | in progress — pilot `080-test-impact-analysis` converted (datalena #1951, #1952) |

P1 is now the binding constraint: a compiled plan supplies the shared-file edges
`plan_groups()`'s own `TODO` (V7) asks for, but nothing consumes them at the group level yet, so
file overlap still only affects the concurrent-batch check (V8), not the PR partition.

Inputs: work-queue briefs `20260725-150500` (OpenSpec TaskSource), `20260725-125412` (hook
registration / devkit retirement), `20260725-131002` (verify console-script); the extraction plan
`~/.claude/plans/composed-orbiting-floyd.md`; and direct reads of this repo at `44f2fd3`.

---

## 1. Verified observations

All facts below come from reading this repo at `44f2fd3`, not from the briefs' summaries.

| # | Fact | Evidence |
|---|---|---|
| V1 | One git worktree **and** one branch per **task**. | `worktree.py:68` `worktree_path(base, spec_id, task_id)`; `worktree.py:55` `task_branch(spec_id, task_id)` |
| V2 | A dependent task gets a **fresh** worktree with its dependency branches merged in — it does not reuse the dependency's warm agent or worktree. | `live.py:933` `add_stacked_worktree(...)`, called at `live.py:1391`, `2015`, `2019`, `2908` |
| V3 | Workers are **cold**: "a spawned worker starts COLD (a new agent inherits nothing but its prompt)". | `dispatch.py` module docstring |
| V4 | Workers are **already forbidden** from writing to the spec tree. | `verify.py:134` `FORBIDDEN_WORKER_PATH_PREFIXES = (".github/workflows/", f"{DEFAULT_SPEC_ROOT}/")`, enforced against the pushed diff at `verify.py:512` |
| V5 | Nevertheless the **orchestrator itself** writes task status into the worktree and commits it **on the task branch**. | `live.py:1801` `cleanup_task_in_python()` → `set_task_status_completed()` (`live.py:1771`) then `git add` + `git commit -m "chore(TASK-xxx): mark task completed"` |
| V6 | That single write is the **sole** cause of cross-branch spec-folder conflicts, and already required a workaround. | `integrate.py:176` `_strip_spec_folder_to_base()`: *"Sibling independent groups all carry TASK-\*.md status updates, causing add/add (or modify/modify) conflicts when the first sibling merges into the real base."* |
| V7 | Groups (the PR unit) are unioned on **dependency edges only** — file overlap is a known unimplemented refinement. | `coordinator.py:200` `plan_groups()`, incl. literal `TODO: refine with shared-file edges` |
| V8 | File-disjointness is enforced only *per concurrent batch*, not per group. | `coordinator.py:94` `runnable_frontier()`; `coordinator.py:128` `disjoint_batches()` |
| V9 | Token trimming already happened at the **read-list** level, not the **process** level. | `dispatch.py:258-263`: review/fix skip spec/plan, "~60-80 KB saved per call"; implement defaults to task-file-only unless `needs-spec: true` |
| V10 | `TaskSource.mark_status()` exists in the Protocol and in `devkit/source.py:345` but **has no caller anywhere in `src/`**. | `grep -rn "mark_status" src/` → only `base.py:45`, `source.py:345` |
| V11 | OpenSpec change layout: `proposal.md`, `design.md`, `specs/` deltas, and **one `tasks.md`** holding a hierarchically-numbered checklist (`1.1`, `1.2`, `2.1`…). `/opsx:archive` moves the change to `changes/archive/[date-name]/` and merges deltas into the living specs. | `Fission-AI/OpenSpec` README, fetched 2026-07-25 |
| V12 | No repo under `~/projects/` has an `openspec/` directory yet; adoption is prospective. | `find ~/projects -maxdepth 2 -name openspec -type d` → empty |
| V13 | Corpus reality: 163 spec dirs on disk; only **12 have any open task** (datalena 10, developer-kit 2). | frontmatter scan of `docs/specs/*/tasks/TASK-*.md`, 2026-07-25 |
| V14 | **52 spec dirs have no `tasks/` at all**, in three distinct states: **47** hold only a `user-request.md` (intake captured, no spec authored — datalena 44, ggb 2, developer-kit 1); **5** have an authored spec but no tasks (datalena `085-agentic-experience-frontend`, Status: Draft; ggb 4); **1** is explicitly dead (`014-dashboard-builder`, `**Superseded-By**: 051-dashboard-builder`). | directory + marker scan, 2026-07-25 |

## 2. The reframe

Brief `20260725-150500` states file-per-task "is a structural requirement of the orchestrator, not
a stylistic choice worth relitigating." **That is half right, and the wrong half is load-bearing.**

Per V4+V5+V6, the *only* thing a task branch ever carries out of the spec tree is a status flip
plus checkbox ticks, written by the orchestrator, not by the worker. It is **bookkeeping, not
deliverable**. It is why sibling PRs collide, and it already needed `_strip_spec_folder_to_base()`
to paper over.

So the coupling to a multi-file task format is not "the orchestrator needs file-per-task." It is:

1. **Addressability** — a cold worker prompt needs one stable path to point at (`dispatch.py:266`).
2. **Per-task metadata** — `deps`, `files`, `kind`, `complexity`, `review` live in frontmatter
   (`taskformats/devkit/schema.py`); stock OpenSpec `tasks.md` has none (V11).
3. **Status writeback into git** (V5) — the only genuine merge-conflict driver.

(3) is deleted outright. (1) and (2) are satisfied by a **compiled run plan**, not by the authoring
format. Once they are, a single `tasks.md` is fine and **no OpenSpec custom schema is needed** —
which also disposes of the two unresolved external-dependency questions brief `150500` flagged
(does `generates:` glob per task? is there a `FIELD_SCHEMA` equivalent?). Both are moot if OpenSpec
is never asked to carry per-task frontmatter.

## 3. On model capability and worktrees

Stated as **inference** from the code, not capability marketing:

- Model capability does not change POSIX. Two agents writing one working tree still race, and a
  faster agent races harder. Worktrees remain the isolation mechanism for **concurrent writers**.
- What changed is the **economics of granularity**. Per V2+V3, today's cost model assumes a worker
  is cheap and disposable, so isolation granularity was set to the smallest unit (a task). With a
  1M context and a 1-hour prompt cache, a **warm agent that keeps a lane's context across several
  tasks** is the cheaper shape: the agent that just built `src/foo/service.ts` for task 1.3 already
  holds everything task 1.4 needs.

**Keep worktrees; move the granularity.** One worktree per *lane*, not per task.

## 4. Design

### 4.1 Objects

```
Change  (OpenSpec change dir — authoring artifact, never mutated by a run)
  └── RunPlan          (compiled once per run; run-scoped, NOT committed to the change)
        └── Lane[]     (unit of isolation: 1 worktree, 1 branch, 1 warm agent, 1 PR)
              └── Task[]  (unit of verification: executed serially inside its lane)
  Ledger              (run-scoped status/journal; the only mutable run state)
```

### 4.2 Lane = dependency-connected ∪ file-overlapping

Extend `coordinator.plan_groups()` to union on **dependency edges ∪ shared-file edges** — literally
its own `TODO` (V7). Consequences:

- Lanes are mutually **dependency-independent and file-disjoint** ⇒ branches merge into base without
  cross-lane conflict, by construction. Cross-lane `assembly-resolve` becomes a should-never-fire
  safety net rather than routine machinery.
- Within a lane, tasks run **serially in dependency order in one worktree with one warm agent**.
  `add_stacked_worktree()` (V2) is no longer needed for intra-lane dependencies — the dependency's
  output is already in the working tree.
- Parallelism is across lanes, which is where it was safe all along (V8 only ever guaranteed
  disjointness *within a concurrent batch*).

**Measured when P1 shipped (2026-07-26, #25), across the 81 spec dirs under `~/projects/` that
declare file scope.** Two things this section got wrong, both worth recording:

| | groups | cross-group file collisions | specs collapsed to 1 group |
|---|---|---|---|
| before | 213 | **35** | 1 |
| after | 205 | **0** | 1 (the same one) |

1. **No hub-file threshold is needed.** The obvious fear — that one shared `package.json` or
   barrel export welds a whole spec into a single lane — does not materialise. It cost 8 groups
   out of 213 and collapsed nothing new: file overlap inside a spec is sparse and mostly already
   aligned with the dependency structure. A first prototype *did* show 65 of 81 specs collapsing,
   but that was an artifact of unioning the BASE tasks too, which `plan_groups` deliberately
   excludes. Worth knowing before anyone re-derives the threshold idea from first principles.
2. **`_strip_spec_folder_to_base()` (V6) is not dead code, and P1 does not make it so.** The claim
   removed from the bullet above conflated two things. §4.3/P0 already eliminated its original
   job (sibling branches colliding on status writes) by scoping `_write_group_task_status` to each
   group's own task files. What remains is a defensive reset of *any other* spec-folder drift — and
   P1's file-disjointness cannot cover that, because the spec folder is orchestrator-written
   bookkeeping that appears in no task's `files`. It is a safety net now, not conflict machinery.
   Retiring it is a separate, independently verifiable step.

OpenSpec's numbered sections (`## 1. …` → `1.1`, `1.2`) are a **free lane hint** (V11): authors
already group tasks the way they want them shipped. Compile treats section = candidate lane, then
splits/merges by the file-overlap rule.

### 4.3 Status leaves git

Delete `cleanup_task_in_python()`'s commit (V5). Status transitions go to the **Ledger** — a
run-scoped file under the orchestrator's run dir, never inside the change dir, never on a lane
branch. Then:

- Lane branches contain **only deliverable code**. `FORBIDDEN_WORKER_PATH_PREFIXES` (V4) becomes
  enforceable against the orchestrator too, not just the worker.
- Checkboxes in `tasks.md` are ticked **once**, by the conductor, in the base checkout, at
  integration time — one commit, one place, no conflict. This matches OpenSpec's own model, where
  `/opsx:archive` is the single point that mutates durable artifacts (V11).
- `TaskSource.mark_status()` (V10, currently caller-less) is **removed** from the Protocol rather
  than implemented. The adapter becomes read-mostly.
- Knock-on for brief `20260725-125412`: the `PostToolUse` `task_lifecycle` auto-status hook exists
  to keep hand-edited task frontmatter honest. With status out of the artifact, orchestrated runs
  stop needing it — which shrinks (does not eliminate) the plugin shell that brief is about.
  Re-scope that brief after P0 lands rather than designing the shell first.

### 4.4 The Conductor

One warm, long-lived process per change. The **only** context that ever reads the full change.

```
compile   read proposal.md + design.md + specs/ + tasks.md  ── ONCE ──▶ RunPlan
          (one LLM pass: infer per-task file scope, deps, kind, complexity, review tier)
          RunPlan is content-addressed by change-dir hash → re-runs and resumes are free
plan      lane partition (deps ∪ file overlap), PR order, budget
dispatch  per lane: one warm agent, thin brief = that lane's task slice + its file scope only
observe   structured report-back per task (existing dispatch.py JSON contract)
integrate lane branch → PR; tick tasks.md checkboxes once in base checkout
archive   hand back to /opsx:archive
```

Where the token savings come from — structural, not prompt-golf:

| Source | Today | With conductor |
|---|---|---|
| Full-change comprehension | Every route step (`brainstorm` → `spec-check` → `technical-plan` → `spec-to-tasks`) re-reads the growing artifact; then each of N cold workers re-reads what it needs | **1** compile pass, cached artifact |
| Repo rediscovery | N cold starts (V3), one per task, each re-exploring the same subtree | **G** cold starts (G = lanes, G ≪ N); tasks 2..k in a lane inherit a warm context |
| Dependency carry | `add_stacked_worktree` merges dep branches into a *fresh* worktree, then a cold agent re-reads the dep's output (V2) | Intra-lane deps need no merge and no re-read |
| Per-worker read list | Already trimmed (V9) | Unchanged — that axis is done; remaining waste is process count, not read list |
| Prompt cache | Cold worker per task ⇒ little prefix reuse | Lane agent keeps a stable prefix across its tasks ⇒ cache hits within the 1h TTL |

**Non-goal:** do not build the conductor on any one harness's workflow/subagent primitives. This
package's premise (`AGENTS.md`) is runtime-agnostic, subprocess-invoked execution; binding the
conductor to one harness would undo the extraction that just shipped. Keep `spawnlib`. Borrow the
*patterns* (pipeline-over-barrier, schema at phase boundaries, resume-from-journal) — this repo
already has a run journal and resume, so that is reuse, not new build.

### 4.5 `OpenSpecTaskSource` after this reframe

Shrinks from "define a custom OpenSpec schema + port `FIELD_SCHEMA` + emit per-task files" to:

```python
def load(spec_ref) -> (change_id, [TaskDict])      # parse stock tasks.md: "- [ ] 1.2 Title"
def task_ref(task_id, spec_ref) -> (Path, anchor)  # tasks.md + "1.2" — addressability, no file per task
def spec_root(spec_ref) -> Path                    # openspec/changes/<id>/
def spec_root_prefix() -> str                      # "openspec/"
```

`deps` / `files` / `complexity` are **not** parsed from the artifact — they come from the compiled
RunPlan (§4.4). No OpenSpec custom schema. No archive-behavior coupling.

## 5. Decisions

**D1 — Migrate the existing corpus (not freeze).** Decided 2026-07-25, overriding the freeze
recommendation. Per V13+V14 the corpus is **four** cohorts, not two, and conflating them would
silently archive 52 dirs that are not history:

| Cohort | Count | Treatment |
|---|---|---|
| **Open** — ≥1 non-completed task | 12 | Real migration. `spec.md`→`proposal.md`, `technical-plan.md`→`design.md`, `tasks/TASK-*.md`→ one numbered `tasks.md`; task frontmatter (`deps`/`files`) becomes a **seed RunPlan** so compile has ground truth instead of re-inferring. These get executed, so they must round-trip. |
| **Completed** — has tasks, all done | ~98 | Mechanical, deterministic, **no LLM**: relocate to `openspec/changes/archive/<date>-<name>/`, content verbatim. Never executed again; OpenSpec's archive is their correct home (V11). |
| **Authored, untasked** — spec written, no `tasks/` | 5 | Real migration, same transform as *Open*, but lands as an **unstarted** `openspec/changes/<id>/` with a `tasks.md` the conductor's compile step generates on first run. This is live backlog (e.g. datalena `085`, ggb ×4) — archiving it would lose committed intent. |
| **Intake-only** — `user-request.md`, no spec | 47 | **Triage required — see below.** Neither archive nor change. |
| **Superseded** | 1 | Drop; the `**Superseded-By**` marker already points at the live successor (`014`→`051`). Verify the successor exists before dropping. |

**The intake-only cohort is the one judgment-heavy part of D1.** OpenSpec has no artifact for "a
request with no proposal", so there is no mechanical mapping. Worse, these cannot be classified by
inspection: datalena `001`–`045` are an earlier era's intake records, and an unknown share of them
describe capability that **shipped under a later spec number** (the one verifiable instance,
`014`→`051`, is explicit; the rest are not marked). Recommended handling, cheapest-first:

1. Mechanical pre-pass — drop anything carrying a `Superseded-By` marker.
2. Per-item triage against the code, not against the doc: *shipped* → archive as a historical
   record; *still wanted* → seed `openspec/changes/<id>/proposal.md` from the `user-request.md`
   verbatim, no LLM rewrite; *dead* → drop with the reason recorded.
3. Do **not** auto-convert all 47 into changes. That would manufacture ~44 phantom open changes in
   datalena on day one and make the OpenSpec change list useless as a work signal.

Second fork inside D1, resolved by recommendation: **do not** reconstruct `openspec/specs/` (the
living spec) from ~98 historical change docs. That is ~98 LLM passes producing a spec derived from
*change history* rather than from *code as it is now* — high cost, low fidelity. Living specs
accrue naturally as future changes archive into them. Flagged so it can be overridden deliberately
rather than by omission.

**D2 — The compile step is an LLM pass, exactly once, cached.** That is the token thesis. The
alternative (authors hand-write file scopes into `tasks.md`) reintroduces the per-task frontmatter
coupling §2 just removed.

**D3 — Lane concurrency is re-derived from `agent_capacity.py`, not carried over.** Today
`max_workers` defaults 2–3 (`live.py:1352,1475,1847`), sized for short-lived cold task workers.
Warm lane agents are longer-lived and heavier; the old constant would systematically mis-size them.

## 6. Sequencing

Each phase is independently verifiable and lands as its own PR.

| Phase | Change | Verification |
|---|---|---|
| **P0** | Ledger: status out of task branches. Delete the commit in `cleanup_task_in_python`; write to run ledger; tick checkboxes once at integrate. Remove `mark_status` from the Protocol (V10 — no callers). | Golden/cassette regression byte-identical except removed `chore(...): mark task completed` commits; `_strip_spec_folder_to_base` exercised → confirm it becomes a no-op |
| **P1** | Lanes: union `plan_groups()` on shared-file edges (V7). Still one worktree per task. | New coordinator tests: two dep-independent but file-overlapping tasks land in one group. `pr_plan` goldens re-baselined with a documented diff |
| **P2** | Warm lane execution: one worktree + one agent per lane, tasks serial inside. Delete intra-lane `add_stacked_worktree`. Re-derive concurrency per D3. | Live cassette on a real 2-lane spec; assert G worktrees not N; assert no intra-lane merge commits |
| **P3** | Conductor `compile` → cached RunPlan. Template `dispatch.py` prompt text off `TaskSource` — the extraction plan's still-outstanding "Phase 2", confirmed hardcoded at `dispatch.py:266,273,301,307,314`. | Same change compiled twice ⇒ cache hit, zero LLM calls on the second run |
| **P4** | `OpenSpecTaskSource` (§4.5) + one real dual-run against a live OpenSpec change. | Same spec driven through both adapters produces the same lane partition |
| **P5** | Corpus migration per D1, in cohort order: ~98 mechanical archive relocations → 1 superseded drop → 12 open transforms → 5 authored-untasked transforms → 47 intake-only triage (last, and the only cohort needing per-item judgment). | Archive cohort: byte-identical content, path-only diff. Open cohort: each migrated change produces a RunPlan whose lane partition matches its pre-migration `pr_plan`. Intake cohort: every one of the 47 lands in exactly one of archived / proposal-seeded / dropped, with the disposition recorded — none silently skipped |

P0 and P1 have **no OpenSpec dependency** — they pay for themselves on the existing corpus and
de-risk everything after. Do them first regardless of OpenSpec sequencing. P5 runs last so the
migration targets a settled format rather than a moving one.

## 7. Verified against OpenSpec 1.6.0 (2026-07-25)

Prototyped before the P4 adapter build, as §7 previously required. All against
`@fission-ai/openspec@1.6.0` installed locally.

- **Archive accepts externally-written checkboxes — confirmed.** Ticking `tasks.md` with plain
  `sed` (no OpenSpec involvement) and running `openspec archive <change> --yes` produced
  `Task status: ✓ Complete`, merged the delta into `openspec/specs/<cap>/spec.md`, and archived to
  `changes/archive/<date>-<change>/`. This was the design's load-bearing assumption: worktrail can
  replace `/opsx:apply` entirely and hand back to `archive`. Now covered by a live-CLI test
  (`test_archive_accepts_checkboxes_this_adapter_wrote`, skipped when the CLI is absent).
- **Incomplete tasks are a warning, not a block.** `Warning: N incomplete task(s) found. Continuing
  due to --yes flag.` Archive counts checkboxes but does not gate on them — so the orchestrator,
  not OpenSpec, owns completeness enforcement.
- **Task groups are first-class in the authored format — confirmed.** The built-in `spec-driven`
  schema's `tasks` artifact template is `## N. <Task Group Name>` + `- [ ] N.M <description>`, and
  its instruction text says "Group related tasks under ## numbered headings" and "Order tasks by
  dependency (what must be done first?)". §4.2's "free lane hint" is a property of the format, not
  an inference.
- **No custom schema needed — confirmed.** `generates: tasks.md` (plain, not a glob) is sufficient
  because status leaves the artifact during a run (§4.3). The two open questions brief `150500`
  raised — does `generates:` glob per task, is there a `FIELD_SCHEMA` equivalent — are moot rather
  than answered: no per-task frontmatter exists, so there is nothing to validate.
- `openspec schema` exists as an experimental command group (`which`/`validate`/`fork`/`init`), so
  customization is available if a later phase wants it. Not used.

Still not verified:

- `openspec status --change <id> --json` reports **artifact** completion (does each file exist),
  not task completion. If a future phase wants OpenSpec to be the progress source of truth rather
  than the run journal, that gap needs its own answer.

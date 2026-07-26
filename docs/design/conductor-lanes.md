# Conductor + Lanes: orchestrator design for an OpenSpec-authored lifecycle

Status: accepted design, not yet implemented
Date: 2026-07-25
Applies to: `worktrail` @ v0.3.0 (`44f2fd3`)

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
| V13 | Corpus reality: **163 spec dirs on disk, only 12 with any open task** (datalena 10 of 102, developer-kit 2 of 27, ggb 0 of 30, devops 0 of 4). 151 are completed history. | frontmatter scan of `docs/specs/*/tasks/TASK-*.md`, 2026-07-25 |

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
  cross-lane conflict, by construction. `_strip_spec_folder_to_base()` (V6) becomes dead code once
  §4.3 lands, and cross-lane `assembly-resolve` becomes a should-never-fire safety net rather than
  routine machinery.
- Within a lane, tasks run **serially in dependency order in one worktree with one warm agent**.
  `add_stacked_worktree()` (V2) is no longer needed for intra-lane dependencies — the dependency's
  output is already in the working tree.
- Parallelism is across lanes, which is where it was safe all along (V8 only ever guaranteed
  disjointness *within a concurrent batch*).

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
recommendation. Scoped by V13 into two jobs that must not be conflated:

| Cohort | Count | Treatment |
|---|---|---|
| **Open specs** (≥1 non-completed task) | 12 | Real migration. Transform to `openspec/changes/<id>/` — `spec.md`→`proposal.md`, `technical-plan.md`→`design.md`, `tasks/TASK-*.md`→ one numbered `tasks.md`, task frontmatter (`deps`/`files`) dropped into a seed RunPlan so compile has ground truth instead of re-inferring. These will actually be executed, so they must round-trip. |
| **Completed specs** | 151 | Mechanical, deterministic, **no LLM**: relocate to `openspec/changes/archive/<date>-<name>/` preserving content verbatim. They will never be executed again; they are knowledge, and OpenSpec's archive is the correct home for them (V11). |

Hidden fork inside D1, resolved by recommendation: **do not** attempt to reconstruct
`openspec/specs/` (the living spec) from 151 historical change docs. That would be 151 LLM passes
producing a spec derived from *change history* rather than from *code as it is now* — high cost,
low fidelity. Living specs accrue naturally as future changes archive into them. Flagged here so it
can be overridden deliberately rather than by omission.

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
| **P5** | Corpus migration per D1: 151 mechanical archive relocations, then 12 real transforms. | Archive cohort: byte-identical content, path-only diff. Open cohort: each migrated change produces a RunPlan whose lane partition matches its pre-migration `pr_plan` |

P0 and P1 have **no OpenSpec dependency** — they pay for themselves on the existing corpus and
de-risk everything after. Do them first regardless of OpenSpec sequencing. P5 runs last so the
migration targets a settled format rather than a moving one.

## 7. Unknowns / not verified

- OpenSpec's **community-schema** mechanism: the README mentions third-party bundles but the fetched
  content shows no schema-validation surface. Under this design we never need one — but if a later
  phase wants OpenSpec-side validation, that remains unverified against the actual repo (brief
  `150500` flagged the same, twice).
- Whether `/opsx:apply`'s own sequential executor conflicts with an external executor ticking
  `tasks.md`. **Assumption:** `/opsx:apply` is not used at all — worktrail replaces it — and only
  `/opsx:archive` runs afterward. Needs one real dual-run (P4) to confirm archive accepts
  externally-ticked checkboxes. This is the cheapest assumption in the design to disprove and
  should be prototyped before P4's adapter build, not during it.

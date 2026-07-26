# `go` — Spec-Driven Front Door (the Conductor)

> **Historical design record — not loaded at runtime.** Superseded by
> [`v2-design.md`](v2-design.md) (GO v2: route classification, policy, run
> records, completion states). The formerly-normative §2.1 authority hierarchy
> and §6 commit policy now live in [`artifact-policy.md`](artifact-policy.md),
> which SKILL.md references instead of this file.

A single entry point for the whole SDD lifecycle. It **routes by intent**,
**remembers the steps** (so you don't memorize commands), and **resumes
in-progress work** by detecting where each spec is in its lifecycle. The parallel
orchestrator we built is one stage of this (the "implement" step).

Status: design. To be built as a skill `developer-kit-specs:specs.go` in this fork.

---

## 1. Why

The SDD lifecycle has many commands in a specific order. Remembering which one
comes next — per spec, per intent — is the friction. `go` is the layer above:
you start at `go`, it figures out what's next.

It does **not** invent the routing — devkit already documents it in
`docs/decision-tree.md`. `go` **automates that decision tree + adds
state-awareness + chains the steps.**

---

## 2. Design principles

### 2.1 Artifact authority hierarchy (drift protection) — load-bearing

Stale artifacts that get read as current truth are the #1 failure mode. So every
artifact has a defined authority, and the conductor/agents must respect it:

| Artifact | Authority | Drift handling |
|---|---|---|
| **code + tests** | **Source of truth for what IS** | always re-read; never assume |
| **spec.md / data-model / contracts** | Source of truth for what SHOULD BE | maintained by `sync` (drift detection) |
| **knowledge-graph.json** | Derived cache of codebase understanding | **self-healing**: staleness check (`updated_at`; re-explore >30d) + `sync` updates it |
| **tasks/** | The work plan | status tracked in the orchestrator's coordinator state, not the file |
| **traceability-matrix.md** | req→task→test→code map | maintained by `sync` |
| **`reviews/TASK-XXX-review.md`** | **Point-in-time snapshot** | **NOT maintained by anything** (verified: `sync` never touches it) → never trust as current; not committed |

**Rule:** an agent never treats a derived/point-in-time artifact as the current
state of the code. The KG is allowed because it self-heals; review files are not,
so they stay out of the record.

### 2.2 Human gates vs autonomy
- **Human-gate** (judgment): `constitution`, `brainstorm`, `spec-check` — `go`
  *launches* them and hands you the interactive session; it never fabricates
  requirements.
- **Autonomous** (mechanical): `spec-to-tasks`, the orchestrator, `sync` — run
  through. Default gate: **always pause for your "yes" before the orchestrator**
  (it spawns agents); auto-run the cheap steps.

---

## 3. The canonical lifecycle (grounded in `sdd-workflow.md`)

```
Phase 0  constitution create              (first time only — architectural DNA)
Phase 1  brainstorm → spec-check → [technical-plan?] → spec-to-tasks   (the SPEC)
Phase 2  ORCHESTRATOR  (parallel fan-out replaces task-implementation → task-review)
Phase 3  (merge) → sync (full)            (KG update + drift detection — close the loop)
         → PR
```
`technical-plan` is **optional** (canonical core is brainstorm→spec-check→
spec-to-tasks); `go` prompts for it, recommending it for architecturally-
significant features.

---

## 4. Two modes

### 4.1 Resume dashboard (`go`, no args)
Scans `docs/specs/*/`, computes each spec's stage, shows what's in flight + the
next action, then the intent menu:

```
$ go
In progress:
  003-payments        tasks 0/8 done    → next: orchestrator
  004-notifications   spec unclear       → next: spec-check
  005-search          no tasks yet       → next: spec-to-tasks

What now?
  1) Resume 003-payments  (→ orchestrator)     5) bug fix
  2) new feature                               6) research (spike note)
  3) modify feature                            7) knowledge-graph / sync
  4) implement existing spec                   8) other
```

### 4.2 Intent menu
Menu-driven (chosen over keyword/free-text): you pick, `go` runs that pipeline.

### 4.3 State detection (artifact → stage)

The detector reads the spec's `Status:` header **first**. A **backfill** spec
(feature-first, documented retroactively — e.g. `Status: Backfill`) or one whose
tasks are all `completed` is **done**: no action, and it is **never flagged for
`spec-to-tasks`**. Backfill specs legitimately have *no* task DAG — the code is
the truth and the spec documents it (verified in GGB specs 005/006/007). Only
then does the artifact-based detection apply:

| Spec state | Stage → next |
|---|---|
| `Status: Backfill` **or** all tasks `completed` | **done — no action** |
| (nothing) | `brainstorm` |
| spec.md, no `## Clarifications` | `spec-check` |
| spec, no `technical-plan.md` | `technical-plan` (prompted, optional) |
| spec, no `tasks/` (and not backfill) | `spec-to-tasks` |
| `tasks/` with pending tasks | **orchestrator** |
| all tasks done, not merged | open PR |
| merged, spec not synced | `sync` |

Because `tasks/` is committed (see §6), this scan works on any clone — not just
the original author's working dir.

---

## 5. Intent → pipeline

| Intent | Pipeline |
|---|---|
| **new** | [constitution if missing] → brainstorm → spec-check → *prompt:* technical-plan → spec-to-tasks → **orchestrator** → (merge) → sync |
| **modify** | change-spec `--type=delta` (on an existing spec) → spec-to-tasks (delta) → **orchestrator** → sync |
| **fix** | change-spec `--type=bugfix` → implement (usually 1 task → single worker, no fan-out) → sync |
| **research** | spike: explore + write a findings note (`docs/specs/[id]/research/…`), **no code**; can later seed a `brainstorm` |
| **kg / sync** | `sync --kg-only` (refresh) or full `sync` (drift check) — on demand |
| **implement** | pick a spec with pending tasks → **orchestrator** |
| **other** | ask, then route (or hand to a general agent) |

Notes:
- The **orchestrator** is the Phase-2 engine for `new`/`modify` (and `fix` when
  multi-task). It fans out only when there's parallelism to exploit; a 1-task fix
  just runs a single worker.
- `sync` is the **closing step** of `new`/`modify` (run after merge, to reconcile
  spec↔code) **and** a standalone intent.
- The loader already detects tasks in either `tasks/` (brainstorm) or
  `changes/<change>/` (change-spec), so `modify`/`fix` route through the same
  orchestrator.

---

## 6. Artifact & commit policy (drift-aware) — decided: Path A

**Commit (durable SDD record — authoritative or self-healing):**
`spec.md`, `data-model.md`, `contracts/`, `tasks/`, `traceability-matrix.md`,
`knowledge-graph.json` (+ global), `architecture.md`, `ontology.md`, `changes/`.

**Gitignore (point-in-time / runtime — drift risk or scratch):**
`reviews/*-review.md` (never refreshed → would go stale and mislead),
`_ralph_loop/` (state-machine scratch), and the orchestrator's own run artifacts
(cassettes/`/tmp` instantiations — not in the project repo anyway).

Rationale: the committed set is everything `sync` keeps aligned (so it stays
truthful) plus the KG (self-heals). Review files are excluded precisely because
nothing maintains them — keeping them out prevents the "LLM reads a stale review
instead of the code" failure. Code PRs touch `src/`/`test/`; the SDD docs live
under `docs/`, separate from code review.

---

## 7. Form / implementation

A skill `developer-kit-specs:specs.go` that:
1. scans `docs/specs/*/` → builds the stage dashboard (pure file inspection),
2. uses `AskUserQuestion` for the intent menu + per-step gates,
3. invokes the existing commands in order, pausing at human gates and before the
   orchestrator,
4. for "implement", calls the parallel orchestrator (`live full` / the
   per-step pieces).

---

## 8. v1 scope

Build first (highest value): the **resume dashboard + state detection**, and the
**`new`** and **`implement`** pipelines (covers "where am I / what's next" and
"build it"). Then add `modify`, `fix`, `kg`, `research`, `other`.

## 9. Open items
- Exact `research` spike-note template + whether it optionally feeds NotebookLM.
- Whether `go` should infer intent from a free-text description in addition to the
  menu (deferred — menu first).
- Per-step confirmation defaults (which mechanical steps auto-run silently).

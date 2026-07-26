# Artifact Authority & Commit Policy

The two normative rules extracted from the design records (`docs/design/history/go-v1-design.md` §2.1 +
§6). Everything else in those files is historical — do not load them at runtime.

## Authority hierarchy (drift protection) — load-bearing

Stale artifacts read as current truth are the #1 failure mode. Every artifact
has a defined authority; the conductor and its agents must respect it:

| Artifact | Authority | Drift handling |
|---|---|---|
| **code + tests** | **Source of truth for what IS** | always re-read; never assume |
| **spec.md / data-model / contracts** | Source of truth for what SHOULD BE | maintained by `sync` (drift detection) |
| **knowledge-graph.json** | Derived cache of codebase understanding | **self-healing**: staleness check (`updated_at`; re-explore >30d) + `sync` updates it |
| **tasks/** | The work plan | status tracked in the orchestrator's coordinator state, not the file |
| **traceability-matrix.md** | req→task→test→code map | maintained by `sync` |
| **`reviews/TASK-XXX-review.md`** | **Point-in-time snapshot** | **NOT maintained by anything** → never trust as current; not committed |

**Rule:** an agent never treats a derived/point-in-time artifact as the current
state of the code. The KG is allowed because it self-heals; review files are
not, so they stay out of the record.

## Commit policy (drift-aware)

**Commit (durable SDD record — authoritative or self-healing):**
`spec.md`, `data-model.md`, `contracts/`, `tasks/`, `traceability-matrix.md`,
`knowledge-graph.json` (+ global), `architecture.md`, `ontology.md`, `changes/`.

**Gitignore (point-in-time / runtime — drift risk or scratch):**
`reviews/*-review.md` (never refreshed → would go stale and mislead),
`_ralph_loop/` (state-machine scratch), and the orchestrator's own run
artifacts.

Rationale: the committed set is everything `sync` keeps aligned plus the KG
(self-heals). Review files are excluded precisely because nothing maintains
them — keeping them out prevents the "LLM reads a stale review instead of the
code" failure.

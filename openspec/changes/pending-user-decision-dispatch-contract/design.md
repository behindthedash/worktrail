## Context

See `proposal.md` for motivation and `specs/pending-user-decision-dispatch/spec.md` for the observable contract. Today guard procedures directly describe `AskUserQuestion` and separately document AUTO_MODE fallbacks. The durable `human-decision-queue` already stores and answers decisions, but the go/skill-dispatch boundary has no common result shape that interactive and unattended hosts can both carry.

## Goals / Non-Goals

**Goals:**

- Make guard evaluation provider-independent and make the front door the sole owner of host prompting.
- Preserve one decision identity from filing through answering and guarded resume.
- Make unattended outcomes explicit, terminal for the current execution attempt, and recoverable.
- Reuse the human-decision queue rather than introducing a second durable store.

**Non-Goals:**

- Changing the existing `human-decision-queue` behavioral requirements or storage location.
- Standardizing arbitrary product questions outside guard decisions.
- Teaching the orchestrator to render provider-specific prompts.
- Defining automatic defaults for collision, staleness, or related-brief decisions.

## Decisions

### Use one serializable boundary envelope

Introduce a versioned envelope with `status: pending_user_decision`, stable `decision_id`, `guard_kind`, prompt text, typed options, source identities, and opaque continuation metadata. The result travels through native and subprocess boundaries without containing UI callbacks. A single shape prevents each provider adapter from inventing incompatible blocked states. The alternative—documenting provider-specific prose branches—cannot be validated end to end and loses identity across process boundaries.

### Keep persistence in the human-decision queue and mirror lifecycle in run records

The queue remains the durable source for open/answered decisions; the run record stores correlated lifecycle events and terminal/result state for dispatch observability. This explicitly links the new capability to `human-decision-queue` without modifying its requirements. Storing only in run records would weaken dashboard discovery; storing only in the queue would hide dispatch ownership and resume history.

### Split guard evaluation from host interaction

Guard code produces either a continue result or the envelope. The worktrail-go front door presents an envelope when attended; adapters and drain propagate it unchanged when headless. Orchestrator launch requires an explicit resolved decision input and cannot call a prompt API. This keeps provider detection at the existing invocation-context boundary.

### Resume by decision ID plus evidence revalidation

Continuation data identifies the guard and source but is not treated as authority. Resume resolves the recorded answer by decision ID, verifies provenance, then re-runs the guard's read-only evidence collection before applying it. Changed evidence supersedes the decision with lineage. This avoids replaying an answer against a different collision or stale brief.

### Treat pending decision as a first-class completion/result state

The run-record, adapter, polling, and drain surfaces recognize `pending_user_decision` rather than encoding it as a generic external dependency failure. The current attempt yields without closing the brief as done; the claim remains associated with a discoverable decision and an attended resume path. This gives automation a stable stop condition and prevents retry loops.

## Risks / Trade-offs

- [Adding a result state can break exhaustive status handling] → centralize parsing and add matrix tests for run-record, adapter, poll, and drain consumers.
- [Opaque continuation data can become stale across code revisions] → version the envelope, keep only identities/hints in it, and always revalidate evidence.
- [Mirrored queue/run events can diverge] → make queue transition the authoritative mutation and append the run event from the same command path with idempotency tests.
- [Interactive host capabilities differ] → test semantic equivalence at the boundary and keep provider rendering thin; unsupported attended prompting falls back to the same structured pending result.

## Migration Plan

1. Add the envelope and lifecycle helpers compatibly alongside existing decision records.
2. Convert the three guard families and front-door resume path.
3. Teach adapter, polling, and drain consumers the new result before emitting it by default.
4. Update skill procedures and provider/dispatch-mode tests.
5. Roll back by stopping new envelope emission; existing queue decisions remain answerable because persistence is unchanged.

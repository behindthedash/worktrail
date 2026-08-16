# Investigation: do other `worktrail-skill-dispatch` call sites share openspec-propose's collision-prone-retry-on-kill risk (PR #455)?

**Triggered by:** work-queue brief `20260816-133219`.

**Question:** PR #455 fixed a risk in `#openspec-propose` (`subagent-prompts.md:369`): a
killed/crashed/disconnected headless `/opsx:propose` spawn leaves partial artifacts at
`openspec/changes/<change-id>/`, and a naive retry re-invokes the same command, which hits
OpenSpec's own change-name-collision guardrail (`openspec new change` refuses an existing
name) instead of continuing the work. `check_openspec_propose_resume.py` closed the gap with
a filesystem pre-check that routes a retry to `/opsx:update` instead of `/opsx:propose` when
partial artifacts already exist. Do any other `worktrail-skill-dispatch` call sites documented
in `subagent-prompts.md`/`routes.md` share the same shape — "child writes to a path with its
own guardrail against re-creation," no resumability pre-check?

## Verified Observations

- `grep -rn "worktrail-skill-dispatch\|skill_dispatch" skills/` returns 7 lines across 3
  files: `worktrail-go/SKILL.md` (546, 603), `worktrail-go/references/subagent-prompts.md`
  (32, 126, 397, 407), `worktrail-sdd-workflow/SKILL.md` (57). `routes.md` contains zero
  direct invocations of `worktrail-skill-dispatch`.
- Of those, exactly two are actual invocation call sites (the rest are descriptive text about
  the adapter, not a distinct target-path spawn):
  1. `subagent-prompts.md:397` — `#openspec-propose`, the spawn PR #455 fixed.
  2. `worktrail-go/SKILL.md:603` — the "adapter dispatch" branch of go's Phase 7, which
     launches an entire headless `worktrail-sdd-workflow` route session (routes D/F/G/H)
     against `$REPO` (or an existing worktree), not a single self-guardrailed artifact path.
- `#openspec-propose`'s vulnerable shape: the child authors `openspec/changes/<change-id>/`,
  a path whose *own* tool (`openspec new change`, verified against `@fission-ai/openspec`'s
  `validateChangeName`) refuses to recreate if it already exists. A killed spawn leaves that
  directory partially populated; a blind retry of the identical command collides with that
  guardrail instead of resuming. This is exactly what `check_openspec_propose_resume.py` now
  pre-checks.
- `worktrail-go/SKILL.md:603`'s adapter dispatch does **not** share this shape, and is already
  covered by two independent, pre-existing mechanisms distinct from a filesystem pre-check:
  1. `#active-conflicts-scan` (`subagent-prompts.md:856`) — every worktree-creating route step
     atomically claims `repo+specification` on the run record before touching any file. A
     second run targeting the same spec while the first run's worktree still exists on disk is
     hard-blocked (`blocked_external_dependency`), not silently colliding with a raw git/tool
     error the way pre-PR-455 `openspec new change` did.
  2. go's own dispatch policy ("Active-run resume (Route E) stays in-session," `subagent-prompts.md:133`)
     — a stalled dashboard brief (claimed with no `final_status` and its worktree still present)
     routes through Route E in-session resume rather than a fresh top-level adapter dispatch.
     The natural "retry" path for this call site therefore never blindly re-issues the same
     top-level dispatch against work already in flight; it hands execution back to what's
     already there.
- Checked the orchestrator's own per-task worker spawning, `worktrail-live full-real`
  (`subagent-prompts.md:614` area — a distinct mechanism from `worktrail-skill-dispatch`,
  Python `subprocess.Popen` from inside an already-backgrounded orchestrator process, not the
  CLI adapter). It is explicitly documented as already resumable: "a killed run is recovered
  by re-issuing the same command; the orchestrator reads its run journal and continues from
  where it left off" (`subagent-prompts.md:660`). No gap.
- Checked `#stage-result-handling`'s generic retry path ("On retry, increment counter and
  re-dispatch with same inputs," `subagent-prompts.md:528`). This governs retries of the same
  propose spawn on a `STATUS: failure` result. `#openspec-propose`'s resumability pre-check is
  worded "mandatory, before every dispatch below," so a stage-result-triggered retry re-runs
  the pre-check too — not a second, uncovered path to the same spawn.
- Checked `#openspec-sync` (`/opsx:sync <change-id>`, `subagent-prompts.md:508`) — merges a
  change's delta specs into `openspec/specs/`. It is not a create-a-new-uniquely-named-resource
  operation the way `openspec new change` is, so it does not exhibit the change-name-collision
  guardrail pattern this audit is scoped to.

## Unknowns / Missing Evidence

None — this is a bounded, fully grep-enumerable audit of every `worktrail-skill-dispatch` call
site named in `subagent-prompts.md`/`routes.md`, plus the two adjacent mechanisms (orchestrator
spawn, stage-result retry) close enough in shape to warrant a direct check.

## Hypotheses

None remaining.

## Confirmed Root Cause

Not applicable — no additional defect found. The exact vulnerable shape PR #455 fixed (a
headless spawn against a target path guarded by the target's own must-not-already-exist check,
with no resumability pre-check before a retry) exists at exactly one call site
(`#openspec-propose`), already fixed. The repo's other headless-spawn/dispatch paths — the
adapter-dispatch call site in `worktrail-go/SKILL.md`, and the structurally separate
`worktrail-live full-real` orchestrator spawn — each already carry their own, differently-shaped
resumability protection (run-record claim + Route E resume; run-journal resumption,
respectively), appropriate to what they actually write to.

## Recommended Next Route

None — no code or doc change is warranted from this audit's findings.

Completion: `investigation_complete`.

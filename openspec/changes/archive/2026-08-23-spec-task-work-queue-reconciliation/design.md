## Context

See proposal.md - Why. Three existing mechanisms already do adjacent comparisons, none at
task granularity:

- `overlap_check.py`'s `scan()` enumerates whole-spec (devkit) or whole-change/whole-capability
  (OpenSpec) candidates — `{spec_id, stage, title, feature_summary, user_request_excerpt}` — for
  the brainstorm `#overlap-check` step, before a new spec is created.
- `check_spec_collision.py` reuses that same `scan()` for Phase 5.5's dispatch-time guard
  (`/go` Routes C/D/F/G only), then `verify()`s a single agent-judged candidate by checking its
  `**Status**:` header reads `Implemented` and its task `files:` are git-tracked on the base
  branch.
- `cluster_detect.py` (via `dashboard.py`) clusters queued briefs against **each other** —
  slug/target-spec/related-link/focus-overlap signals — surfaced as an advisory on the dashboard,
  regardless of route or dispatch state.

None of the three ever reads an individual task row inside an active spec's `tasks.md`. The
discovery incident (two briefs independently targeting datalena's `onboarding-tool-dispatch-
wiring` tasks 2.3/2.4/4.1/5.1, filed a day apart) was caught by a human reading both brief bodies
during a queue-cleanup triage pass — neither brief had been claimed/dispatched yet, so Phase
5.5 never ran for either, and the brainstorm overlap-check never runs for a brief (only for new
spec creation). The mechanism that *did* eventually catch it — a human scanning the queue/
dashboard — maps directly onto `duplicate-brief-detection`'s existing advisory surface, which
today can't express "this brief matches an existing task" because its candidate set is
brief-only.

Separately, `openspec-stale-bookkeeping-detection` already has a working, model-call-free pattern
for reading a pending task's real-world state: load a cached `RunPlan` (`conductor.runplan.
load_cached`/`fingerprint`), merge it onto the parsed task list, and check the merged files
against git. This change reuses that pattern's *shape* (cached lookup, fail-open on a cache miss)
for a different question — not "is this file shipped" but "is this specific task's checkbox
still unticked" — rather than adding a second `tasks.md` parser.

## Goals / Non-Goals

**Goals:**
- Let a brief/request that already resolves to a known target spec be compared against that
  spec's individual open tasks, not just its whole-spec status.
- Reach the actual failure mode from the discovery incident: two undispatched queued briefs
  targeting the same task, visible only via the dashboard/triage pass.
- Catch closure-time drift between a brief marked done and its target task's checkbox state,
  without silently auto-editing a shared spec artifact on unverified say-so.

**Non-Goals:**
- Fleet-wide task-level overlap scanning when no target spec is known (would mean comparing
  every new brief against every open task in every active spec/change — expensive, and not what
  the discovery incident needed: both briefs already carried enough context to resolve a target).
- Auto-ticking a spec task's checkbox from a brief's closure note. Warn-only in this change.
- A new capture-time check inside `worktrail-handoff` itself, or a new standalone periodic sweep
  script. Both were considered and rejected — see Decisions 1 and 3 below.
- Task-level matching for devkit-shaped roots (`docs/specs/**/tasks/TASK-*.md`). Devkit
  represents each task as its own frontmatter'd file, not rows in one checklist; extending
  task-level matching there needs separate extraction logic from the OpenSpec `tasks.md`
  checklist case. Scoped out of this change — OpenSpec-shaped roots only, matching both where
  the discovery incident occurred and where this repo's own specs live going forward.
- Reconciling the pre-existing `docs/specs/**/TASK-*.md` `status: completed`-vs-unticked-checkbox
  fleet drift (`[[project_datalena_task_checkbox_fleet_drift]]`). That is a different repo's
  bulk-backfill problem being worked through its own handoff queue; this change's closure-time
  check only prevents *new* drift going forward, scoped to briefs that carry `target-task:`.

## Decisions

### Decision 1 — Task-level matching only runs when a target spec is already resolvable

Resolves open question (a). Task-level candidate enumeration in `overlap_check.py` requires a
caller-supplied target spec/change id; it is never invoked as a fleet-wide scan across every
active spec. This mirrors how a target is already available at every site this change wires it
into:

- Phase 5.5 (Routes C/D/F/G): Route F/G dispatch already starts by locating the controlling spec
  the fix/change is against (`routes.md` §F/§G); Route C/D frequently has `target-spec:` on the
  claimed brief.
- Dashboard advisory clustering: a brief without `target-spec:` already can't form a
  `same-target-spec` cluster edge today (`duplicate-brief-detection`'s existing repo-scoped
  matching rule) — extending clustering to tasks only for briefs that *do* carry `target-spec:`
  is a narrower version of the same existing rule, not a new one.

A brief with no `target-spec:` gets exactly today's behavior: whole-spec/whole-change candidates
only, from focus-text/slug signals. This keeps the check cheap (bounded to one spec's `tasks.md`,
not a fleet scan) and consistent with `check_spec_collision.py`'s existing "best-effort, never a
hard dependency" contract — a missing target degrades to "no additional signal," not "scan
everything to compensate."

**Alternative considered:** always attempt task-level matching by running focus-overlap against
every open task in every active spec, regardless of target. Rejected — this is `duplicate-brief-
detection`'s own job at the wrong granularity (it already exists to cluster on weak signals like
focus-overlap; duplicating that machinery for tasks specifically would be two systems doing the
same probabilistic matching) and would materially raise the cost of both Phase 5.5 and every
dashboard render.

### Decision 2 — Closure-time check warns; it does not auto-tick

Resolves part of open question (b) for *this* mechanism specifically (see Decision 3 for the
broader fleet-drift question). `work_queue.py done` already has two guards
(`_reverification_claim_missing_evidence`, `_consolidation_closure_missing_evidence`) that
refuse to accept a closure note's self-reported claim without literal re-run evidence in the
note. Auto-ticking a task's checkbox in a *shared* spec artifact from that same kind of
self-reported claim would be a strictly bigger blast radius than what those guards already
refuse to trust. So: `done()` surfaces `checkbox_out_of_sync: true` in its JSON result and
appends the fact to the closure note when the referenced task is still unticked; it never writes
to the target spec's `tasks.md`.

**Alternative considered:** auto-tick in the same action, matching the request's "either tick it
... or surface a warning" framing. Rejected for this change on the evidence-required precedent
above; revisit only if warn-only output shows the drift is common enough, and closure notes
consistently carry re-run evidence, to justify trusting an automatic tick (a future change, not
this one — auto-ticking is a strictly separable, additive follow-on to the warn-only contract
this change establishes, not a prerequisite for it).

### Decision 3 — No new periodic sweep; reuse the existing dashboard scan cadence

Resolves the rest of open question (b) and the capture-time/dispatch-time/closure-time/periodic
part of open question (3). `[[project_datalena_task_checkbox_fleet_drift]]` shows checkbox drift
is real and not rare fleet-wide (~46 specs affected in one repo alone) — but that memory documents
a *different* drift shape: `status: completed` frontmatter vs. an unticked body checkbox, found by
a now-archived bulk audit script, unrelated to any work-queue brief. This change's closure-time
check is narrower and cheaper: it only fires when a brief with `target-task:` closes, so it
can't detect drift that has no corresponding brief at all (most of the fleet-drift instances).
Building a new standalone periodic sweep to cover *that* broader case would duplicate
`openspec-stale-bookkeeping-detection`'s dashboard-scan-time git-evidence detector, which already
runs on the same cadence and already answers a closely related question ("is this pending task's
file scope actually shipped"). If broader periodic reconciliation is warranted, it belongs as a
follow-on extension of that existing detector, not a new script — out of scope here.

### Decision 4 — `target-task:` is a new optional brief frontmatter field, not inferred text

A task-level match needs an unambiguous way for a brief to say which task it tracks, for both
direction 1 (point a new brief at the existing task) and direction 2 (closure-time lookup) to
consume. Free-text inference from a brief's `focus` field (matching a task id embedded in prose)
would be fragile and silently wrong on a false match. `target-task:` (e.g. `2.3`) is optional,
pairs with the existing optional `target-spec:`, and follows the same "producer-side validation,
no reinterpretation of existing malformed data" pattern `work-queue-dependency-reference-
contract` already established for `blocked-by`: validated on write (non-empty, matches the
target spec's actual task-id shape), never rewritten for briefs that predate the field.

**Alternative considered:** store the reference as a link inside the brief body (e.g. a
"Tracks:" line in Markdown) instead of frontmatter. Rejected — frontmatter is what every existing
consumer (`cluster_detect.py`, `classify_handoff.py`, `work_queue.py`) already reads
programmatically; a body-text convention would need its own parser and wouldn't compose with the
existing `target-spec:` field it's paired with.

## Risks / Trade-offs

- [Risk] A brief's `target-spec:` is stale or wrong (spec archived/renamed since capture) →
  Mitigation: task-candidate lookup degrades to "no signal" on a missing spec dir or unreadable
  `tasks.md`, exactly like `check_spec_collision.py`'s existing best-effort contract; never
  blocks dispatch or closure.
- [Risk] Dashboard advisory clustering adds a per-render cost (reading `tasks.md` for every
  active spec a queued brief targets) → Mitigation: bounded by "only for briefs with
  `target-spec:` set" (Decision 1); no fleet-wide task scan.
- [Risk] `checkbox_out_of_sync: true` becomes noise if closure notes rarely carry a clean
  `target-task:` → Mitigation: warn-only (Decision 2) means false positives cost a note, not a
  corrupted spec artifact; the field is optional so adoption can be gradual.
- [Trade-off] Warn-only closure check means this change does not fully solve the "queue says
  done, spec still shows unchecked" drift on its own — it only prevents new instances where the
  brief already names its task. Full backfill of existing drift stays a separate, evidence-driven
  handoff effort (see Non-Goals).

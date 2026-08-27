# Design: should `recommended-route:` stay brief frontmatter?

**Triggered by:** work-queue brief `20260827-090557`, incident decision `20260827-030725`. A
claimed brief carried a stale `recommended-route: B` (author a new epic); the target epic already
existed with the brief's exact scope already decomposed, citing a spec already in Draft with
pending tasks. The brief itself raised a broader design question alongside the concrete bug: given
a route recommendation baked into a brief at filing time can go stale (repo state moves on) or
become wrong outright if the route taxonomy changes later, should `recommended-route:` remain
brief frontmatter at all, or should briefs instead carry router-agnostic signals (what already
exists vs. what's still needed) that the router re-derives a route from fresh each dispatch? This
note records that evaluation; the mechanical half of the same brief (the epic-collision guard,
`epic-collision-check.md`) is the enforcement half of the answer below.

## Where `recommended-route:` is actually load-bearing today

`worktrail-handoff-seed seed` (`handoff_seed.py`) surfaces it; `worktrail-classify --handoff-route`
(`classify.py`) treats it as a low/medium-confidence override signal (`route_source:
handoff-recommended-override` when the organic text-classification score is weaker); the dashboard
(`dashboard.py`) reads it for backlog display; `backfill_recommended_route.py` exists specifically
to retrofit it onto pre-existing briefs; and every capture path (`worktrail-handoff`,
`create_handoff.py`, `seed_backlog.py`'s epic-gap-seeded briefs) writes it at filing time. It is
also the field a **human filing a brief by hand** uses to hand the router a head start — the exact
mechanism this incident showed can go stale, but also the field that makes a hand-filed brief
legible without a human re-deriving the route themselves.

## Options considered

### Option 1: remove `recommended-route:` entirely, replace with router-agnostic signals

Briefs would carry only what-exists/what's-needed prose (or a structured equivalent) and the
router would derive a route fresh, every dispatch, from current repo state plus that prose. This
directly eliminates the staleness class the incident hit — there is no point-in-time label to go
stale.

**Rejected as the primary fix.** Three reasons:

1. **Blast radius.** `recommended-route:` is read by five call sites and written by four capture
   paths (above), several with their own test suites pinning the field's presence/shape
   (`test_backfill_recommended_route.py`, `test_create_handoff.py`, `test_handoff_seed.py`,
   `test_classify_handoff.py`). Removing it is a schema migration across the whole brief corpus in
   `$WORK_QUEUE_DIR`, not a scoped fix for one escalation-threshold bug — exactly what
   `routes.md`'s own Route F/G distinction (fix the defect vs. change the spec/contract) says to
   keep as a separate, deliberately-scoped change if it's still wanted after the narrower fix
   lands.
2. **It solves the wrong half of the problem.** The incident's actual failure was not "the
   recommendation was present and wrong" — every recommendation from a human or a classifier can be
   wrong, on any route, at any point; the taxonomy has no way to guarantee accuracy at filing time
   regardless of format. The failure was **there was no mechanical re-verification step before
   Route B started acting on it**, so a wrong recommendation went unquestioned until an agent
   improvised a decision mid-execution. Removing the field doesn't add that re-verification step —
   it just removes a (still generally useful) hint while leaving the actual gap (no pre-dispatch
   collision check for Route B) unaddressed on its own.
3. **It is still a genuinely useful low-cost signal.** For the common case — a fresh brief, filed
   moments before pickup, describing work with no existing epic/spec — the recommendation is
   accurate and saves the router a classification pass. Removing it trades a rare staleness failure
   mode (now mechanically caught, see below) for a permanent loss of signal in the common case.

### Option 2 (chosen): keep the field, but never let an artifact-creating route trust it un-verified

`recommended-route:` remains brief frontmatter and remains a valid override signal for
`classify.py`'s low/medium-confidence path. What changes: a route whose entire job is to **create
a new durable artifact that might already exist** (Route B: a new epic; Route C: a new spec) may
never begin authoring on the strength of the recommendation alone — it must first run a
pre-dispatch collision check against current repo state, exactly like Route C/D/F/G already do via
`check_spec_collision.py` (`spec-collision-check.md`). Route B previously had no such check; this
brief's own fix is exactly that gap, closed via `check_epic_collision.py`
(`epic-collision-check.md`).

This treats staleness as a **verification problem at the moment an artifact-creating route is
about to act**, not a **provenance problem in how the recommendation was captured**. It is the
same shape `routes.md`'s own Route F step 2 already uses for a different kind of staleness
("Identify the controlling behavior... if the *requested* behavior is the change, reroute to G") —
the route a request arrives with is always provisional until the route's own playbook confirms it
against current ground truth; Route B/C artifact-creation now has a documented ground-truth check
where before it had none.

**Escalation threshold, addressed the same way.** The recommendation staying frontmatter never
implied every mismatch should escalate to a human — that conflation was itself the incident's
actual bug (see the brief's own framing: "reserved for genuine product-scope calls... not for
reconciling a stale route label against verified repo state the router already gathered").
`epic-collision-check.md` makes that distinction the check's own contract: an unambiguous
collision (exactly one citing spec covers the request) redirects the route silently and records
the correction on the run record; only a genuinely ambiguous match (multiple candidate citing
specs, or unclear scope coverage) escalates. This is a general principle, not specific to epics —
any future route-mismatch check built on this pattern should draw the same line.

## What this does not solve

- **Route taxonomy changes** (the brief's other stated staleness mode — "become wrong outright if
  the router's route taxonomy changes later") are not addressed by this design at all. A route
  letter is never validated against a live enum anywhere in the pipeline today; if `routes.md` ever
  drops or renumbers a route, every brief citing the old letter silently carries a now-invalid
  recommendation until read. This is a real but separate gap — worth a future
  `worktrail-check-route-taxonomy` sanity pass (e.g. at classify-time, refuse/ignore a
  `recommended-route:` value outside the current route enum) if a taxonomy change is ever actually
  proposed. Out of scope here: there is no live taxonomy-change work motivating it today, and
  building the guard speculatively ahead of one would be exactly the "no speculative flexibility"
  this codebase's own conventions reject.
- **Routes other than B/C** can still receive a stale recommendation and act on it without a
  pre-dispatch collision check of their own (D already has `check_spec_collision.py`; F/G too; E
  has `check_resumable_state.py`; A/H/I/J author no pre-existing-artifact-shaped output the way
  B/C's epic/spec documents do, so the same collision shape does not apply to them the same way).
  If a future incident surfaces an analogous staleness failure on one of those routes, the fix is
  the same pattern applied there, not a reason to revisit this decision.

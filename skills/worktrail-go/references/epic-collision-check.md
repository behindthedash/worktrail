# Pre-Dispatch Epic-Collision Guard (Route B) {#epic-collision-check}

Route C/D/F/G already refuse to author or fix blind against `docs/specs/` (`spec-collision-check.md`),
and a brief-sourced dispatch already checks whether its own requested *work* is already
implemented (`subagent-prompts.md#already-implemented-check`). Route B had no equivalent: it could
be dispatched to author a brand-new `docs/specs/epics/<id>.md` decomposition document even when an
epic covering the same scope already exists with its own feature decomposition and a citing spec
already in flight.

Incident (2026-08-27, decision `20260827-030725`): a claimed brief carried a stale
`recommended-route: B`. Epic `004-james-agentic-vertical-slice` already existed with the brief's
exact scope decomposed as "Feature 2 — tracked under spec `053`/`088`", both citing specs Draft
with pending tasks. Nothing before Route B's playbook began surfaced that — the executing agent
discovered it mid-run (by reading the epic directly) and, with no documented mechanical
resolution path, improvised a blocking human decision over a fact the router already had the
tooling to check: `dashboard.detect_epic_stage()`/`scan_epics()` already extract exactly this
(`citing_specs` per epic, built for `seed_backlog.py`'s backlog-brief seeding). This guard is the
missing pre-dispatch consumer of that existing extraction.

**Gate: Route B only**, run once at the very start of Route B's playbook (before authoring
`docs/specs/epics/<id>.md`), for both brief-sourced and brainstorm-sourced dispatches.

## Running it

```bash
EPIC_JSON=$(worktrail-check-epic-collision --repo "$REPO" --json 2>/dev/null)
CHECKED=$(echo "$EPIC_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('checked', False))")
```

`check()` is pure extraction, no semantic judgment — the calling agent applies the same actor +
capability + primary domain rule `subagent-prompts.md#overlap-check` already documents to decide
whether any `candidates` entry's `title`/`feature_summary` matches `${BRIEF_FOCUS:-$ARG_INTENT}`.
`checked: false` (no `docs/specs/epics/` directory, or an internal failure) is "no signal" —
proceed with Route B as originally routed, never treat it as "no collision confirmed."

## Reading a matched candidate

Each candidate already carries `stage`/`features`/`citing_specs` from `detect_epic_stage()`:

```json
{"epic_id": "004-james-agentic-vertical-slice",
 "title": "Epic: James Agentic Vertical Slice",
 "status": "Proposed",
 "feature_summary": "Prove that Ask Lena can drive the James workflow end to end...",
 "stage": "epic-gap",
 "features": 6,
 "citing_specs": ["088-output-first-workflow-vertical-slice"]}
```

Once a candidate is judged a semantic match, branch on `citing_specs`:

- **Empty `citing_specs`** — the epic exists but nothing has been speced against it yet. This is
  not the mismatch this guard exists for (Route B's own job — decompose into features — is still
  live scope for at least part of the epic). Proceed with Route B unmodified; there is nothing
  mechanically resolvable here beyond what the agent can already see by reading the epic file.
- **Non-empty `citing_specs`, exactly one entry whose own scope clearly covers the request** — this
  is the incident's shape: mechanically resolvable evidence that Route B is the wrong pipeline
  stage for *this* scope. **Redirect, do not ask.** Read that citing spec's own current status
  (`**Status:**` header) and pending-task state the same way the already-implemented check reads
  source (`rg`, or the spec's own `tasks/`/`tasks.md`), and re-derive the route it implies —
  typically D when the spec is on base with pending tasks, C when the spec itself still needs a
  fresh planning pass. Record the correction on the run record exactly like any other in-run route
  correction: `worktrail-run-record append "$RUN" decisions "Route corrected B -> <X>: epic
  <epic_id> already decomposes this scope citing <spec_id> (<status>, <N> pending tasks)."`, then
  continue Phase 6/7 under the corrected route — no `AskUserQuestion`, no decision filed. This
  mirrors `spec-collision-check.md`'s "task-level matches: redirect, never auto-close" contract:
  a citing spec at Draft/pending-tasks status is open, unshipped work, not a confirmed-shipped
  duplicate — never grounds to auto-close a brief on its own.
- **Non-empty `citing_specs` but genuinely ambiguous** — multiple citing specs and it is unclear
  which one (or several) cover the request, or the single citing spec's own status/scope does not
  clearly resolve to one route — this is the same shape as the "inspector projection contract"
  question the incident's own brief flagged as a genuine open product-scope call. **Ask, do not
  guess** (below).

## The operator prompt (ambiguous match only)

```
AskUserQuestion(
  questions=[{
    question: "Epic `{epic_id}` (\"{title}\") already decomposes this scope, citing "
              "{citing_specs}. Which pipeline stage should this dispatch enter?",
    header: "Epic already exists",
    options: [
      {label: "Redirect to {citing_spec}", description: "Continue against the existing citing spec instead of authoring a new epic"},
      {label: "Author the epic anyway", description: "The match is superficial or the epic's own decomposition doesn't actually cover this request; proceed with Route B as originally routed"},
    ],
  }]
)
```

On "Redirect", re-derive and record the route correction exactly as the unambiguous case above,
using whichever citing spec the operator picked. On "Author the epic anyway", proceed with Route
B unmodified, recording the operator's judgment on the run record.

**`$AUTO_MODE=true`: no ask.** `AskUserQuestion` is not a callable tool inside the headless
one-shot `worktrail-go drain` spawns. Phase 6 has not run yet for this dispatch, so open a minimal
run record now purely to record the block, then file the ambiguous match as a decision record per
`decision-queue.md#file-a-decision` (question: which citing spec, if any, should this dispatch
redirect onto?; context: the matched epic id, its citing specs, and their status) and release the
brief, then finish `blocked_product_decision` quoting the matched epic and its citing specs. The
unambiguous redirect case above has no `$AUTO_MODE` branch of its own — it never asks in the
first place, in either dispatch mode, which is the entire point of this guard.

`check_epic_collision.py` additionally exposes `build_pending_decision()` (CLI: `--decision-for
<epic_id>`), mirroring `check_spec_collision`/`check_related_brief_claims`'s own provider-neutral
`pending_decision` envelope for identity/dedup purposes — structural, additive, and not required
by the filing procedure above.

## Relationship to the sibling Phase-5.5-style checks

This guard is route-specific (Route B only) where `spec-collision-check.md` is gated to
C/D/F/G and the already-implemented check runs on every brief-sourced dispatch regardless of
route — the three do not overlap. Unlike its siblings, a confirmed unambiguous match here never
produces an ask or a filed decision at all: the whole point of this guard is that Route B's most
common mismatch (a stale `recommended-route: B` against an epic the router can already see) is
mechanically resolvable, so it resolves it. Escalation is reserved for the genuinely ambiguous
remainder.

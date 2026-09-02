# Internal drain procedure

This procedure is reached only through `worktrail-go drain`; it is not a separate
user-facing skill.

## Arguments

```text
worktrail-go drain
worktrail-go drain <max-items>
worktrail-go drain <max-items> <repo>
worktrail-go drain dry-run
worktrail-go drain --intake-triage
worktrail-go drain --seed-backlog
worktrail-go drain --intake-triage --seed-backlog
```

Resolve the agent CLI once using the same invocation-context precedence as the
main Go flow. Then derive the fallback chain from the resolved policy's
`routing.fallback` (repo-local `routing:` block, else the machine-wide
`~/.worktrail/routing.yaml`, else the flat `fallback_agent_cli` key), minus the
primary agent — drain never had `$POLICY` in scope (it branches out of Phase 1,
before Phase 6's policy load), so fetch it fresh here rather than assume it is
set. Then run the installed console script in the background:

```bash
DRAIN_POLICY=$(worktrail-policy --repo "$PWD" --json 2>/dev/null || echo '{}')
DRAIN_FALLBACK_ARGS=()
for agent in $(echo "$DRAIN_POLICY" | AGENT_CLI="$INVOCATION_CONTEXT_AGENT" python3 -c "
import json, os, sys
policy = json.load(sys.stdin)
primary = os.environ.get('AGENT_CLI', '')
names = [e.get('agent_cli') for e in ((policy.get('routing') or {}).get('fallback') or [])
         if isinstance(e, dict) and e.get('agent_cli')]
if not names and policy.get('fallback_agent_cli'):
    names = [policy['fallback_agent_cli']]
seen = set()
for n in names:
    if n and n != primary and n not in seen:
        seen.add(n); print(n)
"); do
  DRAIN_FALLBACK_ARGS+=(--fallback-agent "$agent")
done
worktrail-drain \
  --agent "$INVOCATION_CONTEXT_AGENT" \
  "${DRAIN_FALLBACK_ARGS[@]}" \
  ${MAX_ITEMS:+--max-items "$MAX_ITEMS"} \
  ${ARG_REPO:+--go-repo "$ARG_REPO"} \
  ${INTAKE_TRIAGE:+--intake-triage} \
  ${SEED_BACKLOG:+--seed-backlog}
```

## Pre-pass flags

**`--intake-triage`** (default off) — Before the main drain loop, evaluate and triage every
intake brief in the queue: rank candidates against active changes in each brief's repo, score
the brief, propose a verdict (fold-into-change, propose-change, work-directly, needs-decision,
or keep), and apply approved verdicts without interactive confirmation. Folds and proposes open
PRs, work-directly converts an intake brief into a seeded execution brief, and needs-decision
files a decision record and releases the brief. Results are captured into the drain summary
(`briefs_evaluated`, `verdict_counts`, `pull_requests_opened`, `briefs_held_by_cap`). An
intake-triage failure is logged but does not stop the drain; the main loop runs afterward
regardless.

**`--seed-backlog`** (default on) — Before the main drain loop, mechanically convert planning
backlog into queue briefs: every spec in the `needs-tasks` stage becomes a planning brief to
generate its task DAG, and every epic under `docs/specs/epics/` with more unused feature
headings than citing specs becomes a brief to spec the next feature. Seeding is deterministic,
capped per sweep, and deduplicated via `seeded-from:` frontmatter — a fruitless completed
brief never loops. Results are captured into the drain summary (`seeds_captured`). A
seed-backlog failure is logged but does not stop the drain; the main loop runs afterward
regardless.

## Loop operation

A drain iteration that exhausts every configured agent's capacity stops
cleanly (`capacity_gated`) rather than misclassifying the exhaustion as a
generic failure — relay that stop reason verbatim if it fires.

Only pass `--permission-arg --dangerously-skip-permissions` when the user has
explicitly opted into unattended permission bypasses. Otherwise state that
one-shots use default permission prompts and may stall on unapproved tools.

`dry-run` previews the first decision without launching a worker. Relay each
iteration outcome and the final `drain stop: ...` reason. A brief that is blocked
or fails remains in `picked/`; do not release another session's claim.

The console script launches one fresh-context `worktrail-go auto` process per
item. Do not implement draining by looping `worktrail-go auto` in the current
conversation.

## WIP cap (max_active_changes)

Per-repo policy can declare `max_active_changes: N` to cap the number of simultaneously active
OpenSpec changes. When a `propose-change` verdict applies and the target repo already has N or
more active changes, the verdict is downgraded to `keep` with a `## Triage <date>` note naming
the cap, the current count, and the top fold-candidate recommendations. The cap does not affect
`fold-into-change`, `work-directly`, or `needs-decision` verdicts. Set `max_active_changes: 0`
(or omit it) to disable the cap entirely; every repo without an explicit cap defaults to 0 (no
throttling).

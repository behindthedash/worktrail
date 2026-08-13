# worktrail

Spec-format-agnostic task orchestration: parallel git-worktree fan-out execution, a deterministic
route classifier, and a work-queue handoff system — extracted from the `developer-kit` Claude
Code plugin marketplace so they can be consumed by any harness and paired with any spec/task
format via the `TaskSource` adapter interface.

See [AGENTS.md](AGENTS.md) for architecture, origin, and development workflow.

Worktrail understands DevKit task files, OpenSpec changes, and GitHub Spec Kit feature tasks
(`.specify/specs/<feature>/tasks.md`) through the `TaskSource` adapter interface.

The Claude plugin also includes a once-per-session Stop hook, with OpenCode `session.idle`
parity, that asks for an exceptional next-step idea after substantive work and captures it
through `worktrail-handoff` when appropriate.

Run `worktrail-check-provider-commands` to validate every generated Claude,
Codex, and OpenCode headless command against the installed CLI parsers without
authenticating or launching model work.

## Autonomous operation

`worktrail-drain` runs the work queue unattended: each iteration spawns one fresh-context
headless one-shot (Claude, Codex, or OpenCode) that claims a queued brief, does the work, and
opens a PR. Around that loop sit three subsystems that keep it fed, unblocked, and honest:

**Backlog seeding** (`worktrail-seed-backlog`, run automatically pre-loop by every drain pass).
Planning backlog that no brief points at is converted into queue briefs mechanically: a spec in
the `needs-tasks` dashboard stage becomes a planning-only brief to generate its task DAG, and an
epic under `docs/specs/epics/` with more `### Feature` headings than specs citing its id becomes
a brief to spec the next feature. Seeding is capped per sweep, deterministic, and deduplicated
via a `seeded-from:` frontmatter key — a fruitless completed brief never loops, while real
progress (a new citing spec) re-arms the epic's next feature.

**The human decision queue** (`worktrail-decision`). When an unattended run hits a genuine
product decision — the one thing it must never guess — it files a structured record (question,
plain-English background, why it is a product call, what was attempted, at least two options in
priority order with optional per-option cost labels, and a recommendation — conditioned on
product priority, e.g. quick-to-production vs long-term architecture, when it genuinely
depends) under
`$WORK_QUEUE_DIR/decisions/open/`, releases its brief back to the queue blocked on that record,
and terminates. The human answers on their own time (`worktrail-decision answer <id>
--answer "..."`, or by editing the record's `## Answer` section and moving the file to
`decisions/answered/` — the directory is the arbiter). The brief unblocks the moment the answer
lands; the next drain pass claims it, treats the answer as binding, continues from the blocked
point, and archives the record to `decisions/resolved/`. Guardrails keep this from becoming a
laziness escape hatch: `ask` refuses unstructured records, one open decision per brief, and the
drain's circuit breaker still counts any `blocked_product_decision` one-shot that did *not*
file a decision.

**Operator config** (`~/.worktrail/config.json`). Machine-wide preferences for the drain —
currently the default provider and fallback chain (`drain.agent`,
`drain.fallback_agents`) — resolved as CLI flags > config file > built-in default, so
explicit automation is never affected while config-less manual drains honor the operator's
stated provider.

The drain also sweeps for resumable states before and after each pass (budget-exhausted
quarantines, verify-pending and sync-pending specs, stale bookkeeping), so interrupted pipeline
work restarts itself instead of waiting to be noticed.

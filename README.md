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

## PullHook handoff ingress

Datalena publishes accepted-work events as `datalena.worktrail-handoff.v1`
envelopes to a PullHook channel. A scheduled WorkTrail consumer pulls those
events, validates the supported schema, and writes each event through the
canonical handoff writer. WorkTrail stores an event marker to make redelivery
idempotent; when queue git sync is enabled or required, it commits and pushes
the new brief and marker before acknowledging the PullHook delivery. The
consumer makes outbound requests only, so the WorkTrail host needs no inbound
network path. Normal WorkTrail triage or drain handles the resulting brief.

Install WorkTrail where `$WORK_QUEUE_DIR` is available and set
`PULLHOOK_CONSUME_CREDENTIAL` in a protected environment file readable only by the
service account (for example, mode `0600`). A systemd oneshot service can load
that file without putting the token in its command line:

```ini
# /etc/systemd/system/worktrail-pullhook-ingress.service
[Unit]
Description=Pull Datalena handoffs from PullHook into WorkTrail

[Service]
Type=oneshot
User=worktrail
EnvironmentFile=/etc/worktrail/pullhook.env
ExecStart=/usr/local/bin/worktrail-pullhook-ingress --base-url https://pullhook.io --channel datalena-worktrail --max-items 20
```

```ini
# /etc/systemd/system/worktrail-pullhook-ingress.timer
[Unit]
Description=Check PullHook for Datalena handoffs every minute

[Timer]
OnBootSec=2m
OnUnitActiveSec=1m
Persistent=true

[Install]
WantedBy=timers.target
```

Enable the timer with `systemctl enable --now worktrail-pullhook-ingress.timer`.
Use `--once` to process at most one event, `--max-items` for a batch of up to
100 events, and `--dry-run` to peek at and validate one event without creating
a brief or acknowledging it. Results are emitted as JSON for scheduler logs.

The data path is Datalena → PullHook → `worktrail-pullhook-ingress` → the local
work-queue. PullHook remains the durable transport; WorkTrail owns materialized
briefs and their deduplication records.

### Gracefully Giving Back feedback

GGB feedback follows the same pull-only path:

```text
Gracefully Giving Back → PullHook → worktrail-pullhook-ingress → git-backed work queue
```

Configure the consumer beside WorkTrail with `PULLHOOK_BASE_URL` (for example,
`https://pullhook.io`), `PULLHOOK_CONSUME_CREDENTIAL`, and `PULLHOOK_CHANNEL` set to
the channel carrying GGB feedback. The credential is read only from the environment. Keep it in a protected
environment file readable by the service account; do not put it in command-line
arguments. The GGB adapter accepts only source `{id: "gracefully-giving-back", kind:
"website-feedback"}` and routes those events to the trusted `gracefully-giving-back`
repository slug. Event fields cannot choose a repository, remote, branch, or queue
directory. `sourceFileHint` is retained as descriptive provenance and is never read
as a path.

For a systemd timer, use a service such as:

```ini
[Service]
Type=oneshot
User=worktrail
EnvironmentFile=/etc/worktrail/pullhook.env
ExecStart=/usr/local/bin/worktrail-pullhook-ingress --base-url ${PULLHOOK_BASE_URL} --channel ${PULLHOOK_CHANNEL} --max-items 20
```

The timer can run the service every minute. `--once` limits a run to one delivery;
`--max-items` is bounded to 100; `--dry-run` peeks at and validates one item without
creating a handoff or acknowledging it. Results are JSON for scheduler logs. Invalid
or unsupported envelopes remain unacknowledged for PullHook's lease/retry policy.
When queue git sync is required, WorkTrail acknowledges an event only after its
brief and provenance marker have been pushed. A push failure leaves the event
available for retry, and the durable event marker makes that retry reuse the same
brief. PullHook accepting a feedback submission means it is durably in the relay;
it does not mean WorkTrail has materialized it in the local queue yet.

## Autonomous operation

`worktrail-drain` runs the work queue unattended: each iteration spawns one fresh-context
headless one-shot (Claude, Codex, or OpenCode) that claims a queued brief, does the work, and
opens a PR. Around that loop sit several subsystems that keep it fed, unblocked, and honest:

**Intake triage pre-pass** (controlled by `--intake-triage` flag, default off). Before the main
drain loop, when enabled, intake briefs are evaluated and triaged: each unexamined brief is
scored against active changes in its target repo (candidate ranking), the evaluator proposes a
verdict (fold-into-change, propose-change, work-directly, needs-decision, or keep), and
approved verdicts are applied — opening PRs for folds and proposes, converting work-directly
briefs into seeded execution briefs, or filing decision records. This closes the intake loop
before drain's own claim loop runs, converting the highest-confidence intake work into
execution briefs ready for the main loop. The pre-pass captures results into the drain summary
(`briefs_evaluated`, verdict counts, `pull_requests_opened`, `briefs_held_by_cap`).

**Backlog seeding** (controlled by `--seed-backlog` flag; default on). Planning backlog that no
brief points at is converted into queue briefs mechanically: a spec in the `needs-tasks`
dashboard stage becomes a planning-only brief to generate its task DAG, and an epic under
`docs/specs/epics/` with more `### Feature` headings than specs citing its id becomes a brief
to spec the next feature. Seeding is capped per sweep, deterministic, and deduplicated via a
`seeded-from:` frontmatter key — a fruitless completed brief never loops, while real progress
(a new citing spec) re-arms the epic's next feature. Seed results are captured into the drain
summary (`seeds_captured`).

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

**Operator config** (`~/.worktrail/routing.yaml`, the same machine-wide file that governs
agent/model routing — see `worktrail-routing --init`/`--show`). Its `targets:` (ordered
harness + account-pool declarations) and `tiers:` (per-target model/effort rows) sections
are what a drain iteration actually selects from, via the same single selector every other
spawn path uses — provider/pool preference is target file order, not a separate drain-only
agent or fallback chain. An explicit CLI flag still wins entirely over the resolved
selection, so explicit automation is never affected while config-less manual drains honor
the operator's declared target order. The `drain:` block itself now holds only
machine-wide non-selection defaults (`max_workers`). Per-repo policy can set `max_active_changes`
to cap the number of simultaneously active OpenSpec changes in that repo; when a repo hits this
cap, triage verdicts of `propose-change` are downgraded to `keep` with a note listing the cap,
the current active count, and top fold-candidate recommendations.

The drain also sweeps for resumable states before and after each pass (budget-exhausted
quarantines, verify-pending and sync-pending specs, stale bookkeeping), so interrupted pipeline
work restarts itself instead of waiting to be noticed.

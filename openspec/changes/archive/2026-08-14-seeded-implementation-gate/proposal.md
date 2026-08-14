## Why

`backlog-seeding` (PR #361) seeds planning-only Route C briefs for `needs-tasks` specs and
under-specced epics, but it deliberately stops at planning. A spec whose task DAG is already
complete — the dashboard's `ready-to-implement` stage — still sits invisible to `worktrail-go
auto` until a human notices it and captures a brief by hand. That gap was explicit in PR #361's
own scope: seeding implementation work crosses the `no_implementation_without_approval` doctrine
(routes.md §A) that Route A's discovery gate exists to enforce, so it needed a deliberate,
separately-reviewed decision rather than riding along with the lower-stakes planning seeders.

## What Changes

- New `find_ready_specs` finder in `seed_backlog.py`: every (repo, spec) pair whose dashboard
  stage is `ready-to-implement` (task DAG complete with real pending impl work, not stale
  bookkeeping or an already-stuck orchestrator run) becomes a Route D implementation brief —
  `recommended-route: D`, `implementation-intent: requested`, `target-spec` naming the spec id.
  Reuses the same dashboard scan (`resolve_spec_rel`, devkit and OpenSpec formats alike) the
  existing `needs-tasks` finder already calls.
- **Opt-in per repo, off by default:** a new `allow_seeded_implementation` boolean key in
  `docs/specs/go-policy.yaml` (default `false`, validated like the existing `automerge.enabled`
  boolean). `find_ready_specs` runs — and is even attempted — only for a repo whose policy sets
  it `true`. A repo that never sets the key sees zero behavior change: no new brief kind, no new
  dashboard read for this purpose. This is the deliberate operator decision PR #361 deferred:
  unattended implementation is a materially different risk than unattended planning, so it does
  not inherit planning's default-on posture.
- Seed keys follow the existing dedup pattern with a new shape: `<repo>:impl:<spec-id>`, checked
  against `existing_seed_keys()` (whole queue/ + picked/, any status) exactly like the
  `needs-tasks` and epic keys. Unlike the epic key, the impl key carries no progress-dependent
  suffix — the underlying scenario (task DAG complete for spec X) either did or did not just
  happen once, and a claimed-but-unfinished brief must not be re-seeded next sweep, same rationale
  as the stable `<repo>:spec:<id>` key.
- Same seeding mechanics as the existing finders: deterministic ordering, the shared per-sweep
  cap with logged deferral, one `create_handoff` call per candidate with per-candidate error
  isolation, `dry-run` support, and the `--repo` restriction flag.
- `worktrail-drain`'s existing `seeded_backlog` summary key (already populated from
  `seed_backlog()`'s return value) surfaces the new finder's candidates automatically — no drain
  code changes needed — since `seed_backlog()` continues to return one merged `seeded` list.

## Capabilities

### Modified Capabilities
- `backlog-seeding`: adds a policy-gated finder that seeds Route D implementation briefs for
  specs whose task DAG is complete, alongside the existing planning-only finders.

## Impact

- `src/worktrail/workqueue/seed_backlog.py` (new `find_ready_specs` finder, brief-kwargs builder,
  wiring into `seed_backlog()`), `src/worktrail/router/policy.py` (`allow_seeded_implementation`
  default + validation), `tests/workqueue/test_seed_backlog.py` (new finder's scenarios),
  `tests/router/test_policy.py` (new key's validation).

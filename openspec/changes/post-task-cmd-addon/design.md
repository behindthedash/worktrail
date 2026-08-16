## Context

See proposal.md - Why. Grounding facts from the current codebase:

- `router/policy.py` (`load_policy`, `policy.py:627`) already returns a plain
  `Dict[str, Any]` with a forward-compatible unknown-key passthrough
  (`policy.py:~638-641`) — adding a new top-level `add_ons` key to `DEFAULTS`
  is a one-line addition, no schema migration.
- The existing `pre_pr_cmd`/`integrate_smoke_cmd` gate
  (`router/pre_pr_gate.py:454-479`, `orchestrator/integrate.py:1088-1094`
  via `_run_integration_smoke`, `integrate.py:642-665`) is a pure
  pass/fail exit-code check. It cannot be reused for this: nothing in it
  stages or commits file output.
- The group-PR path (`integrate.py:853-1231`, `integrate_one`) already has
  exactly this add→diff-check→commit shape at `_write_group_task_status`
  (`integrate.py:274-323`) and `_strip_spec_folder_to_base`
  (`integrate.py:259-277`), run in the group's integration worktree (`iw`)
  after each task's branch is merged into it.
- The one-off/single-task path has **no orchestrator-side commit/push/PR
  code at all** — it is agent/skill-driven: the agent commits its own work,
  runs `worktrail-preflight run` (which executes `pre_pr_gate.py` in-process
  and records a pass marker), then calls `gh pr create` itself, enforced by
  a PreToolUse hook that checks preflight's recorded marker
  (`skills/worktrail-sdd-workflow/SKILL.md:165-218`, `router/preflight.py`).
  So the one-off path's hook point is inside `worktrail-preflight run`, not
  inside `live.py` — `live.py`'s `_default_smoke_cmd` (`live.py:105-134`)
  only feeds the group-PR path via `integrate.py`, confirmed by tracing its
  only call site (`live.py:5365-5370` → `full_real` → `integrate_one`).
- `taskformats/base.py` already establishes the house pattern for a pluggable
  seam: a `typing.Protocol` (no entry_points/registry) plus a plain
  if/elif dispatch function (`taskformats/resolve.py:73-82`,
  `task_source_for`). The add-on interface follows the same shape.
- `subprocess.run` house style (`integrate.py:651-658`, `live.py:2365`):
  `shell=True` for a policy-configured shell string, explicit `cwd`,
  `capture_output=True, text=True`, a named timeout constant, and
  `(bool, detail_str)` return with `TimeoutExpired`/`OSError` caught.

## Goals / Non-Goals

**Goals:**
- A single shared implementation of "run an add-on, stage its output, commit
  it" usable from both `integrate.py` and `router/preflight.py`.
- Zero behavior change for any repo that does not configure `add_ons:`.
- An add-on interface generic enough that a second, unrelated add-on needs
  no core-code change beyond registering its name.

**Non-Goals:**
- No dynamic plugin discovery (entry_points, third-party packages) — one
  in-repo dispatch function, matching `taskformats/resolve.py`, is
  sufficient for the one known add-on today and can be upgraded later
  without changing the `AddOn` interface.
- No new scheduling/cron primitive for keeping the aspens CLI updated
  independent of task runs (see Decisions).
- Not attempting automatic detection/repair of the two repos' existing
  vestigial `.aspens.json` — that re-init is a one-time rollout action
  (Migration Plan), not runtime add-on logic.

## Decisions

**D1 — `AddOn` as a `Protocol` with `install`/`configure`/`run`, dispatched
by name via a plain function.** Mirrors `TaskSource`/`task_source_for`
exactly: `src/worktrail/addons/base.py` defines the `Protocol`
(`name: str`, `install(ctx) -> None`, `configure(ctx) -> None`,
`run(ctx) -> AddOnResult` where `AddOnResult` is `(changed: bool, detail:
str)`), and `src/worktrail/addons/resolve.py` is an if/elif over configured
names returning the concrete implementation. Alternative considered:
`importlib.metadata.entry_points` for third-party add-on discovery — rejected
as premature; this repo has no existing entry_points-based extension point
anywhere, and the brief's ask is "an add-on interface a future add-on can
reuse," which the Protocol + factory function already satisfies without the
added complexity of a discovery mechanism nothing yet needs.

**D2 — One shared runner function, called from both hook sites with the
target worktree path.** `src/worktrail/addons/runner.py` exposes
`run_addons(worktree: Path, repo: Path, policy: dict) -> list[AddOnRunLog]`.
`integrate.py` calls it on `iw` after the per-task merge loop, alongside
`_write_group_task_status` (~`integrate.py:1076`), before `_run_drift_gate`
(~1085). `router/preflight.py`'s `run` command calls it on the agent's
current worktree, after the agent's own commit exists, before invoking
`pre_pr_gate.py`'s pass/fail check. Both call sites resolve `add_ons` from
the same `load_policy(repo)` used today for `pre_pr_cmd`/`integrate_smoke_cmd`
— no second config-loading path.

**D3 — `add_ons` policy shape: `Dict[str, Dict[str, Any]]`, default `{}`.**
```yaml
add_ons:
  aspens:
    enabled: true
    target: <backend-specific config, add-on-defined>
    required: false   # default; see D4
```
Matches the existing generic-dict style already used for unknown keys in
`policy.py` — no new strict schema class. `enabled: false` or the key
absent for a given add-on name is equivalent to not configuring it.

**D4 — Add-on failures are non-fatal by default; `required: true` opts a
repo into fail-closed.** Aspens sync is maintenance, not correctness — a
transient CLI/network failure should not block a task's real deliverable
PR. The `required` flag (per add-on, per repo) exists for a future add-on
where that trade-off is wrong. Alternative considered: always fail-closed,
matching the drift/smoke gates — rejected because it would make a flaky
third-party CLI (aspens) a hard blocker on unrelated engineering work, which
is a worse failure mode than a occasionally-stale skill doc.

**D5 — Unknown add-on names fail closed at config-resolution time.** A typo
in `go-policy.yaml` (e.g. `add_ons: { aspen: ... }`) must not silently
no-op forever — `resolve.py` raises with the unresolved name, surfaced by
both `integrate_one` (quarantine-style failure, same class of error as an
unresolvable `TaskSource` format) and `worktrail-preflight run` (non-zero
exit, same as an unresolvable `pre_pr_cmd`).

**D6 — One commit per add-on per hook invocation**, message
`chore(<addon-name>): <summary>` mirroring `_write_group_task_status`'s
`chore({group_name}): ...` convention, so a revert of one add-on's output
never entangles with another's or with the task's own commit.

**D7 — aspens CLI staleness is folded into the existing per-run Install
step, gated by a machine-local timestamp cache, not a separate scheduled
job.** `addons/aspens.py`'s `install()` checks a marker file (e.g.
`~/.cache/worktrail/addons/aspens/last-check`) and only re-checks
`npm view aspens version` / re-installs when more than a configurable
interval (default 24h) has elapsed since the last check — otherwise it's a
no-op. This answers the brief's open "worth a design call" question:
alternatives considered were (a) a dedicated cron/schedule for update
checks — rejected, adds a new recurring-automation primitive for a
low-stakes concern this repo doesn't otherwise have infrastructure for; and
(b) checking on every single task run with no cache — rejected, adds a
network round trip to every task's pre-PR flow for no benefit over a daily
check. The cache lives at the machine level (not per-repo/per-worktree),
consistent with this repo's existing machine-local compile-cache pattern
(AGENTS.md, `48afddf`/#446).

## Risks / Trade-offs

- [Add-on run adds latency to every opted-in task's pre-PR flow] →
  bounded by a named timeout constant (mirroring `SMOKE_TIMEOUT_DEFAULT`);
  a timeout is treated as a non-fatal failure per D4 unless `required`.
- [aspens `doc sync` could touch files outside the intended skill-doc
  target if misconfigured] → the add-on requires an explicit `target` in
  its config; the framework never infers or guesses one, and only stages
  files the add-on's own run step reports changing.
- [Two call sites (`integrate.py`, `preflight.py`) could drift if hook logic
  is duplicated] → both call the single `addons/runner.py:run_addons`
  function; no per-call-site reimplementation.
- [Group-PR path runs the hook once per group, after multiple tasks' branches
  are merged, so the sync commit reflects the group's combined diff rather
  than each task individually] → intentional and consistent with
  `_write_group_task_status` already being a once-per-group commit at the
  same point in the same function.
- [A repo enabling `required: true` for aspens turns a third-party CLI
  outage into a blocked PR] → opt-in per repo per add-on; default is
  non-fatal (D4), so this risk only applies to a repo that explicitly
  chose it.

## Migration Plan

1. Land the framework (`add_ons` policy key defaulting to `{}`, `AddOn`
   protocol + resolver, shared `run_addons` stage-and-commit runner, wiring
   into `integrate_one` and `worktrail-preflight run`) with no repo
   configured yet — verified as a no-op change via existing test suites.
2. Land the `aspens` add-on implementation and its tests.
3. Enable `add_ons: { aspens: ... }` in worktrail's own `go-policy.yaml`
   first (dogfood on this repo, which has never run aspens — first-time
   `aspens doc init`), verify a real task's sync lands in its own PR.
4. (Follow-up, separate repo/PR — out of scope for this change; this repo's
   orchestrator cannot write another repo's checkout) Enable for `datalena`
   and `gracefully-giving-back`, first deleting/replacing their vestigial
   `.aspens.json` with a genuine `aspens doc init` run — not layering the
   hook on top of stale config.
5. (Follow-up, separate repo/PR each — same reason as step 4) Enable for
   `mailbox-service`, `kudera-consulting`, and `pullhook`, each getting its
   first-time `aspens doc init` as part of enabling.

Rollback is per-repo and code-free: removing/disabling a repo's `add_ons:`
block makes the hook inert immediately (framework guarantees zero behavior
for unconfigured repos per the framework spec), with no orchestrator code
change required to roll back a single repo's adoption.

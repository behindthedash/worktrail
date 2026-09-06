## Context

Two code paths, traced against the current worktree:

- `agent_capacity.check()` reads the entry for `<target>:<model>` and raises
  `ProviderUnavailable` when `retry_after`/`reset_at` is in the future. It is a lock-free
  reader by design (see `write_lock`'s docstring). `gate_for_agent()` (front-door fail-fast),
  `runtime/selection.select_cell` (via `skill_dispatch._capacity` and `spawnlib._select`) all
  funnel through it, so a change here reaches every dispatch surface without touching them.
- `spawnlib.spawn_agent` is the only writer that observes a real provider outcome for a cell:
  success records `available` (line 1219), an exhausted infra-failure budget records
  `unavailable` with `retry_time(failure_class)` (line 1238). The session-limit path (line 1137)
  already clamps a provider-stated reset to `SESSION_LIMIT_REPROBE_MAX_S` (PR #1006) -- that is
  the precedent for "re-probe on a cadence rather than trusting a long window."
- `land_pr.land_pr()`'s `budget_exhausted` branch (line ~1160) finishes and returns before the
  merge-state guard / review-thread gate / `MERGED` completion that the settled path runs.

## Goals / Non-Goals

**Goals:**
- A gate whose window was guessed self-heals within one probe cadence of the provider
  recovering, with at most one spawn per cadence spent finding out.
- A gate whose window came from the provider's own notice is left alone.
- A watch-budget ceiling on a PR that has already merged finishes as merged, not failed.

**Non-Goals:**
- A dedicated "ping the provider" subprocess. Every harness's cheapest real request is a
  headless run, which is what `spawn_agent` already does and already records.
- Touching `drain.py`'s bare-agent gates or `gate_snapshot()`.
- Rerunning checks or re-arming auto-merge on a ceiling exit; only reading state.

## Decisions

### D1. The spawn is the probe (probe-through), claimed single-flight under the write lock

`check()` is extended rather than a new function added, so no caller changes. When the entry
is gated and probe-eligible (D2) and `now - max(probe_at, checked_at) >= PROBE_INTERVAL`,
`check()` takes `write_lock`, re-loads, re-verifies the same condition (another worker may have
claimed it between the lock-free read and the lock), stamps `probe_at = now` on the entry, saves,
and returns `None` -- the caller proceeds as if ungated. Any other caller inside the cadence still
sees `ProviderUnavailable`. The stamp is the only write `check()` ever makes, and it happens at
most once per cadence per key, so the "readers are lock-free" property holds for the common path.

`spawn_agent` then does the real work: a successful run records `available` (existing code),
which removes the gate entirely; a failure re-records `unavailable` with a fresh window and, via
`record()` rewriting the entry, a fresh `checked_at` -- so the next probe is a full cadence away.
A front-door dispatch that goes through `gate_for_agent`/`select_cell` but never calls
`record()` simply consumes the probe slot; the gate remains until the next cadence. That is the
correct degradation: the operator's dispatch ran, and nothing was falsely cleared.

Alternative rejected: return a "probing" marker from `check()` so `spawn_agent` could use a
single attempt. `check()` returns `None` today and `selection._capacity` treats a `None` result
as available; changing the return shape touches the pure selection module for a marginal saving
(one retry cycle per cadence on a genuinely-down provider).

### D2. Probe eligibility: `reset_source != "provider"` and `failure_class != "model_unavailable"`

`record()` gains `reset_source: str = "cooldown"` stored on the entry. Only a caller that
obtained `retry_after` from the provider's own text passes `"provider"`. A pre-existing entry
with no field is treated as `"cooldown"` -- every entry the incident class produced was a guess.
`model_unavailable` is excluded by class: a retired model is a configuration fact, and a probe
would spend a failing spawn every cadence for a day. `auth` is *included*: the 24h window exists
because auth cannot heal without an operator, but once the operator re-authenticates and forgets
to `clear`, the gate is exactly the stale-gate pattern this change fixes -- and `spawn_agent`'s
auth path already gates on the first attempt with no backoff, so a failed auth probe is cheap.

`PROBE_INTERVAL` is read from `GO_AGENT_GATE_PROBE_INTERVAL` (seconds), default 900, the same
cadence as `ORCH_SESSION_LIMIT_REPROBE_MAX_S`, so the two re-probe knobs agree by default.

### D3. `spawnlib` passes the provider's stated reset when it has one

In the exhausted-budget record path, call `agent_capacity.parse_explicit_reset(last_raw +
stderr)`; when it returns a timestamp, record it with `reset_source="provider"`, otherwise fall
back to `retry_time(failure_class)` with the default `"cooldown"`. This mirrors what `drain.py`
already does and is the "provider-reported quota-reset time" half of the brief's ask. The
session-limit `rate_limit` record (line 1137) is left on the default: it is deliberately clamped
to the re-probe cadence, so treating it as probeable is consistent with its own intent.

### D4. Ceiling re-check reuses the settled path rather than duplicating completion logic

Add `_pr_is_merged(repo, pr_number, runner) -> bool` (one `gh pr view --json state`; any
failure or non-`MERGED` state returns `False`). In the `budget_exhausted` branch, when
`pr_number` is set and `_pr_is_merged()` is true, replace `watch` with the all-pass shape and
fall through instead of returning. The existing code then runs `_merge_state_guard` (which
returns the merged PR's payload without rerunning anything, since `mergeStateStatus` is not
`BLOCKED`), the review-thread gate (which D7 requires before *any* completion, merged included),
and the `state == "MERGED"` branch that finishes `completed_and_merged` / "merged externally".
No second copy of the completion sequence, and the review-thread invariant holds by
construction. `_merge_state_guard` is not reused for the probe itself because it reruns
CANCELLED/SUCCESS pairs on a BLOCKED PR -- a side effect a ceiling exit must not have.

`_pr_is_merged` is a module-level function so `LandPrOrchestrationTests._patched` can add it to
its seam list with a default of `False`, keeping every existing orchestration test unchanged.

## Risks / Trade-offs

- A genuinely-down provider costs one `spawn_agent` retry cycle per cadence per key. Bounded by
  the cadence; the previous behaviour (hard block for the whole guessed window) cost operator
  time instead. Operators who want the old behaviour can set the interval very high.
- `check()` now writes on the probe path. It uses the same `write_lock`/`save` sequence as every
  other writer, so a crash mid-write leaves the old file intact.
- `_pr_is_merged` adds one `gh` call on the ceiling path only; it never runs on the settled path.

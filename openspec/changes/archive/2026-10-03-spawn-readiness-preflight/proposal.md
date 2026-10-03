## Why

On 2026-10-02 an unattended drain's intake-triage spawn of the `claude-deepseek` target
failed. A defect in `resolve_routing()` had dropped the `env_profiles` key from the table a
spawn consumes, so every profile-backed cell resolved no credentials — the worker made no API
call and still exited 0 (PR #1380 / `6c3d1d10`, whose message records the live confirmation on
that spawn).

Nothing caught it before the spawn, and each of the three available guards was blind in a
different way:

- `worktrail-routing --check` **ran**, and reported `ok`. It validates the **raw** routing
  mapping through `_validate_routing()` and never calls `resolve_routing()`, `select_cell()` or
  `build_child_env()` — the three functions that actually serve a spawn. A key the resolver
  drops is therefore `ok` to `--check` while being fatal to every spawn that reads it.
- The one thing `--check` does resolve against reality — a target's `auth.profile` — it resolves
  against that same raw mapping, so a profile-bearing target is reported `ok` on the strength
  of a dict no spawn ever reads.
- `api_opt_in` is not checked at all. An `api`-pool target that declares tier cells but no
  `api_opt_in: true` is silently skipped by selection in every row
  (`spawnlib.py:1048`, `_preflight_primary_target`; `select_cell()` drops it the same way) and
  can never serve, yet `--check` reports those cells `ok`.
- Even had `--check` failed, the drain discarded the verdict: `drain.py:2522` calls it as
  `check_routing_liveness(...)` best-effort, under the comment "a liveness-check error never
  blocks the drain itself". The run proceeds and every spawn fails.

The gap is one of altitude. The check re-derives what the configuration *says* instead of
asking whether the configuration can *launch anything*, and the one caller that could act on
its answer throws that answer away.

## What Changes

- Add a spawn-readiness probe that asks the question directly: for every tier cell the selector
  could serve, it builds that cell's launcher command and child environment from the **resolved**
  routing table (`load_policy()` → `resolve_routing()`, the same values `select_cell()` and
  `build_child_env()` consume) against the checking process's own environment. A cell is ready
  only if its spawn construction succeeds.
- Make `worktrail-routing --check` run it, so `ok` means "this cell can be launched" rather than
  "the file parses": an unresolvable environment profile, a claude `api` cell with no usable auth
  source or an unset `auth.env` variable, a codex `api` home that is undeclared or unprovisioned,
  an `api`-pool target with no `api_opt_in`, and a harness outside the supported set all become
  `FAIL` cells that flip the exit code. As with the existing env-profile `FAIL`, none of these
  records an `agent_capacity` gate.
- Make a drain refuse to start when the probe reports unreadiness — before any pre-pass that
  spawns an agent (the intake-triage pass is one) and before the first iteration — exiting 2 and
  naming the offending cell(s) and `routing.yaml`. A cell that is merely capacity-gated (a retired
  opencode model) keeps its current behavior: the gate is recorded and selection walks past it.
- Extract the codex `api` home validation out of `_prepare_child_env` into a named helper that the
  probe and the spawn path both call, so the readiness claim covers every auth lane from one
  source of truth instead of the claude lanes alone.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `model-tier-routing`: `worktrail-routing --check` decides a cell's status from the resolved
  routing table rather than the raw file, constructs each cell's spawn before reporting `ok`, and
  reports an `api`-pool target that can never be selected as `FAIL`.
- `drain-operator-config`: a spawn-readiness failure stops a drain before any spawning pre-pass
  or first iteration, instead of being logged and ignored.

## Impact

- `src/worktrail/router/spawn_readiness.py` (new): the probe.
- `src/worktrail/router/routing_cli.py`: `--check` runs the probe and reports its failures.
- `src/worktrail/drain/drain.py`: the fail-closed readiness preflight, ordered ahead of the
  intake-triage pre-pass.
- `src/worktrail/orchestrator/spawnlib.py`: the codex `api` home check becomes a named helper
  shared by the spawn path and the probe.

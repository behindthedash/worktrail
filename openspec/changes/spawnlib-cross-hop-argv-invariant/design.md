## Context

`spawnlib.spawn_agent` builds subprocess argvs at exactly two `build_cmd` call sites:
the pre-launch capacity-gate selection (spawnlib.py:804) and the session-limit fallback
hop rebuild (spawnlib.py:924). Both now correctly withhold caller-supplied `extra_args`
when the serving agent is not the requested primary (PR #701 fixed the first; the second
always passed `extra_args=None`). Every production worker spawn funnels through this one
function (`live.py` task/review/tail workers, `verify.py` post-PR workers), so an argv
invariant asserted at this chokepoint covers the whole fleet. Existing coverage is
scenario-shaped, not invariant-shaped: `test_preflight_fallback_drops_primary_agent_extra_args`
(tests/orchestrator/test_agent_capacity.py:189) checks the gate hop for two pairs with one
hard-coded arg list; `SessionLimitFallback.test_switches_to_configured_fallback_without_sleeping`
(tests/orchestrator/test_spawnlib.py:1070) checks one token on claude→opencode only.

## Goals / Non-Goals

**Goals:**
- One reusable invariant helper + a sweep of scenarios that fails if ANY claude-only flag
  token reaches ANY codex/opencode argv built inside `spawn_agent`, on either mechanism,
  any hop depth, any fallback pair.
- Ground the forbidden-token list in tokens this repo really derives for claude workers
  (live.py `_LEAN_WORKER_FLAGS`, dispatch reviewer `--append-system-prompt`) so the sweep
  tracks actual leak vectors, not an abstract ideal.
- Keep a positive control: the ungated claude primary still receives its extra_args.

**Non-Goals:**
- No production code changes; if the sweep passes today (it must), spawnlib stays untouched.
- No redesign of `extra_args` into a per-agent mapping (left open by PR #701's research doc).
- Direct non-spawnlib `build_cmd` users are out of scope: `check_agent_contract.py` is a
  diagnostics tool passing no extra_args; `router/cluster_detect.py` has its own unrelated
  same-named helper.
- No new spec capability (test-only; `skip_specs: true`).

## Decisions

- **Home: new `CrossHopArgvInvariant` unittest class in tests/orchestrator/test_spawnlib.py**,
  not test_agent_capacity.py. The invariant spans both build_cmd call sites in spawnlib and
  reuses that file's established harness idioms (scripted `subprocess.run`, session-limit
  fixtures, FallbackChain's hermetic `GO_AGENT_CAPACITY_CACHE` tmp-dir setup).
  Alternative considered — extending #701's test in place — rejected: its scenario-loop shape
  does not generalize to multi-hop chains, and mixing a property sweep into a scenario test
  weakens both failure messages.
- **Invariant formulation**: capture every argv handed to `spawnlib.subprocess.run` across a
  whole `spawn_agent` call, then assert for each captured argv whose executable is codex or
  opencode that no forbidden token appears as an exact argv element. Exact-element membership
  (not substring) keeps opencode/codex prompt arguments and their legitimate shared flags
  (`--model`) out of the false-positive surface.
- **Forbidden-token set = module constant `CLAUDE_ONLY_ARGV_TOKENS`**: the `_LEAN_WORKER_FLAGS`
  flags (`--strict-mcp-config`, `--tools`, `--setting-sources`) plus their sentinel values
  (`project,local`), the reviewer/dispatch extras (`--append-system-prompt`), claude transport
  flags (`--permission-mode`, `bypassPermissions`, `--output-format`, `stream-json`,
  `--verbose`), and claude lifecycle flags that must never survive a hop (`--effort`,
  `--resume`, `--fork-session`). `--effort` belongs because build_cmd translates effort to
  `--variant`/`-c model_reasoning_effort=` per agent — a literal `--effort` in a non-claude
  argv is always a leak.
- **Scenario matrix**: mechanism ∈ {capacity-gate first hop, session-limit hop} × fallback ∈
  {codex, opencode}, plus one multi-hop chain (claude→codex→opencode hitting a limit twice)
  to sweep second hops, plus the positive control. Extra_args used by scenarios carry the full
  token set, so every scenario exercises every token rather than one token per test.
- **Hermeticity**: point `GO_AGENT_CAPACITY_CACHE` at a tmp file (the FallbackChain.setUp
  pattern) instead of SessionLimitFallback's shared-cache reset, so the new class never
  touches the operator's real capacity cache; restore `subprocess.run` and any
  `default_model_for_agent` override in tearDown.

## Risks / Trade-offs

- [Token list drifts from claude CLI surface] → The list documents its provenance (_LEAN_WORKER_FLAGS
  et al.); when live.py grows a new lean flag, the sweep still catches cross-hop leaks of it
  only if added here. Acceptable: the catastrophic regression class (old tokens returning)
  stays covered; new-flag omissions surface via the existing per-hop scenario tests.
- [Sweep could false-positive on future legit codex/opencode flags] → Only exact-element
  matches against claude-shaped tokens fail; codex/opencode use disjoint flag names today
  (`-c`, `--variant`, `--session`, `--fork`, `--format`). If a collision ever appears, the
  test failure message names the offending element for a deliberate list update.
- [unittest global-mutation style] → Mitigated by setUp/tearDown save-restore exactly like
  the neighboring classes; no pytest-fixture rewrite of existing tests.

## Migration Plan

Additive test commit only; no rollout or rollback concerns. Run
`PYTHONPATH=src pytest tests/orchestrator/test_spawnlib.py -q` plus the full suite before PR.

## Open Questions

None.

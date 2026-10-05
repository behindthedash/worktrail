## Context

See proposal.md — Why, for motivation. This section records the verified current state the
approach has to work against (every statement below was reproduced in this worktree, not
inferred from reading alone).

**The no-op shape is real and unclassified.** With a synthesized stream-json `result` event
matching the measured shape (`duration_api_ms: 0`, `num_turns: 0`, `input_tokens: 0`,
`output_tokens: 0`, cache counts 0, `subtype: "success"`, `stop_reason: "stop_sequence"`,
`is_error: false`):

- `spawnlib.is_infra_failure(0, <event>)` → `False` (so `spawn_agent` records `available`
  and returns `finish(last_raw)` — a successful-but-empty run).
- `spawnlib._parse_stream_json(<event>)[1]` returns the usage dict the code builds today —
  and it does **not** contain `duration_api_ms` at all.
- The operator's `~/.worktrail/routing.yaml` (2026-10-02) documents the incident this shape
  caused on this machine ("a worker with no endpoint makes ZERO API calls while still
  exiting 0") and its current workaround (`env_profiles` + `expect`, which is a pre-launch
  guard for one cause of the shape — this change is the post-hoc detection for the class).

**Fixture provenance, stated plainly: no recording of a real no-op run exists on this
machine.** The regression fixtures are synthesized stream-json result events built to the
documented measured shape. Tests that use them say so in their docstrings; the design does
not claim live verification it does not have.

**The two `live.py` call sites are 100% dead code.** Driving each directly against a routing
file that declares `claude-sub`:

- `run_research_session(...)` → `TypeError: spawn_agent() got an unexpected keyword argument
  'agent'`
- `smoke("claude", "sonnet")` → `TypeError: spawn_agent() got an unexpected keyword argument
  'agent'`

A signature-bind scan over every `spawn_agent`/`spawn_claude_p` call site in `src/worktrail/`
found **exactly these two** invalid; all ten others bind cleanly. Both sites' tests patch the
callee with a MagicMock / `side_effect` lambda, which accepts any kwargs — that is why the
suite is green while the code cannot run.

**`worktrail-live precheck` dies before its DAG check.** With a routing file declaring only a
codex target and the host-detected agent resolving to `claude`,
`live.main(["precheck", "--repo", ..., "docs/specs/001-x"])` raises
`OperatorConfigError: no default model configured for agent 'claude'` at `main()`'s shared
tail (`live.py:7768-7770`) — before `precheck()` (which spawns nothing) is ever called. The
same tail calls `_effective_role_models(...)`, which resolves a codex default model whenever
the host agent is codex — the same crash with a codex host.

**Constraints carried into the design:**

- `agent_capacity.DEFAULT_COOLDOWNS` values are fixed: `auth` and `model_unavailable` are
  24 h; `auth` additionally gates WITHOUT retry (`_is_auth_failure` at `spawnlib.py:1550`).
- No route-classification change and no routing-cassette change.
- `tests/orchestrator/test_spawn_exhausted_callers.py` keys its EXEMPT table by
  `<module>:<qualname>` — the two repaired calls must stay lexically inside
  `run_research_session` and `smoke` (or its EXEMPT entries go stale and fail the build).
- The repo's pre-PR gate is `pytest` plus the golden record/replay check, lint through
  `scripts/ci/ruff_pinned.py`.

## Goals / Non-Goals

**Goals:**

- A worker that made zero API calls is never counted as a completed run: it is retried,
  hopped, and — if the whole row no-ops — fails closed as exhausted.
- The detection is specific: no false positives on real completed turns (including
  empty-but-real ones) and no reach into other harnesses' stream vocabularies.
- The exhausted gate for this shape is a short-cooldown class, so one mis-detection can
  never sideline a healthy routing rung for a day.
- Both `live.py` call sites pass kwargs the real signature accepts, express their intent
  through `tier`/`prefer`, and are covered by tests that would have caught the breakage.
- Every non-spawning subcommand of `worktrail-live` runs without a worker model resolvable
  on the invocation host; every spawning subcommand still fails loud when its routing target
  is missing.

**Non-Goals:**

- Diagnosing *why* the provider environment is absent. The operator's `env_profiles.expect`
  guard (spawn-readiness-preflight) remains the pre-launch mechanism; this change only makes
  the resulting run fail correctly instead of silently succeeding.
- Adding a new failure class or touching any `DEFAULT_COOLDOWNS` value.
- Reordering `_is_auth_failure` relative to the new detector (see Risks).
- Re-specifying `--model`/`--effort` semantics for these two surfaces: they are already
  accepted-for-compatibility-only on the worker path (`LiveSpawn.__init__` documents that
  `__call__` "never reads self.model"), and this change keeps that contract.
- A repo-wide static audit of spawn-call-site kwargs (considered under D5; rejected).

## Decisions

### D1: The detector extends `is_infra_failure`, keyed on the parsed usage dict

`is_infra_failure` is the single predicate that answers "did this spawn fail without the
worker ever doing work?" — the existing clauses are non-zero exit, empty output, and the
opencode top-level error event. A zero-API-call clean exit is the same class of event: no
work happened, no task verdict. Extending that predicate means the *existing* retry-then-hop
control flow runs with no new branches in the attempt loop — which is exactly constraint (a)
of the change ("let the existing retry/hop path run first"). It is also the call the change's
regression test pins.

The predicate, a new sibling of `_opencode_error_event`:

- `_zero_api_call_result(usage)` is True only when **all** of:
  - `"duration_api_ms" in usage` — the marker that this usage dict came from a claude
    `result` event. `_parse_stream_json` sets the key only in its claude-result branch, so
    the opencode-synthesized dict (which carries `num_turns: 0` and no
    `duration_api_ms`) and the raw-text fallback (empty dict) can never match.
  - `usage["duration_api_ms"] == 0`
  - `usage.get("num_turns", 0) == 0`
  - every token count (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`) is 0.
- `_parse_stream_json` retains `duration_api_ms` from the result event as a diagnostic field
  alongside `subtype`/`is_error`/`stop_reason`/`num_turns`.
- `is_infra_failure` gains the third clause: `return _zero_api_call_result(usage)` after the
  existing opencode check.

Verified specificity (all reproduced against the proposed predicate): the no-op fixture →
True; a real turn (`duration_api_ms: 5178`, `num_turns: 2`, non-zero tokens) → False; a
zero-token turn with `num_turns: 1` → False (constraint (b): `num_turns >= 1` vetoes even
when tokens are 0); an opencode permission-denial stream → False; plain text and empty
output → unchanged (empty output was already an infra failure via its own clause).

**Alternatives considered:**

- *Raw-scan for the result event* (the shape `_opencode_error_event` uses). Rejected: it
  duplicates the JSONL scan, and the change's contract is "keyed on the usage dict the
  claude JSON result event already carries" — one source of truth (the parser) instead of
  two.
- *Requiring `total_cost_usd == 0` too.* Rejected as unnecessary: cost reporting is not
  proof of API activity (a subscription lane can report 0), while zero `duration_api_ms` +
  zero turns + zero tokens **is** the definition of zero API work.
- *Detecting on `num_turns == 0` alone.* Rejected: too broad; the conjunction is what keeps
  the detector specific to the measured all-zero shape.
- *Impact on the two other `is_infra_failure` callers* (analyzed, no code change needed):
  `check_agent_contract.py` — a claude no-op today fails as "parser fell back to raw output"
  (an empty `result` string falls back to the raw transcript); with this change it fails as
  "infra failure (exit 0)". Same verdict, truer message. `codex_probe.py` — its nested spawn
  emits codex's own event vocabulary (`item.completed`/`agent_message`), which never parses
  into a claude result-event usage dict, so the new clause cannot fire there.

### D2: The exhausted no-op maps to `startup` (60 s), explicitly

When the shape survives the cell's whole retry budget, `spawn_agent`'s exhausted-budget class
resolution maps it to the existing `startup` class before falling through to
`classify_failure`'s text matching:

- *Why not the fallthrough*: `classify_failure(0, <no-op stream>, "")` returns `transport`
  (verified — a 30 s cooldown). Short, but a mislabel: no transport was involved; the process
  ran and never reached the provider.
- *Why `startup`*: it is the codebase's existing label for exactly "never got off the
  ground" (codex_probe's `StageOutcome.STARTUP` for a nested spawn that never started), and
  its default cooldown is 60 s — short.
- *Why not `auth` / `model_unavailable`* (constraint (a), load-bearing): both carry 24-hour
  cooldowns, `auth` gates without retrying, and `model_unavailable` is never probe-eligible.
  A mis-tuned or future-drifting detector must not be able to sideline a healthy routing rung
  for a day off one shape misread.
- *Why explicit rather than letting text classification handle it*: an explicit branch makes
  the mapping a testable contract (`failure_class == "startup"` on the exhausted result), so
  a future `classify_failure` wording change cannot silently reclassify no-ops.

Verified mechanics (extension simulated in-process, two-claude-target row, `retries=1`):
attempt 1 no-op → "spawn infra failure (attempt 1/2); retrying in 5s" (retried, not returned
as success); attempt 2 no-op → gate recorded + "hopping to <cell 2>"; cell 2 healthy → the
call completes on it and records `available`. With the fallthrough the gate read `transport`;
with D2's branch it will read `startup`. When the row has no servable cell left, the existing
give-up path returns `finish(last_raw, exhausted=True, failure_class="startup")`, which
`raise_if_exhausted` callers already fail closed on.

### D3: The two `live.py` call sites resolve through `tier`/`prefer`

Both sites get the same repair shape, with one new shared lookup each:

- `_first_target_for_harness(routing, harness)` (module level in `live.py`): the first
  declared `routing.targets` entry whose harness matches — the *target-name* form
  `select_cell()`'s `prefer` expects. `LiveSpawn.__call__`'s local `_target_for_harness`
  (same loop, closing over `self._routing`) is replaced by a call to it, so the lookup
  exists once. `LiveSpawn` keeps resolving its routing once per instance — the helper takes
  the routing mapping as a parameter.
- `_default_tier_and_prefer(agent)`: resolves the machine-wide routing fresh per call
  (matching `_default_model_for_agent()`'s documented contract) and returns
  `(routing["default_tier"], _first_target_for_harness(routing, agent))` — the spec's own
  designated row for a spawn with no more specific tier ("front-door sessions"), plus the
  requested harness expressed as a preference.
- `run_research_session`: spawn becomes
  `spawn_agent(prompt, spec_folder.parent.parent, tier=tier, prefer=prefer, timeout=timeout,
  extra_args=extra_args, log=print)`.
- `smoke`: spawn becomes
  `spawn_agent("Reply with exactly: PONG", Path.cwd(), tier=tier, prefer=prefer, timeout=120,
  retries=0)`.
- The `model`/`effort` parameters stay in both signatures (callers pass them) and remain
  eagerly resolved exactly as today (`model = model or _default_model_for_agent(agent)`),
  but are no longer fed to the spawn — the same compatibility-only contract `LiveSpawn.__init__`
  already documents for its own `model` ("accepted for CLI/caller backward compatibility
  only"). The eager line stays for its fail-fast value: an agent with no declared
  default-tier cell still raises `OperatorConfigError` with the existing remedy message
  before any spec I/O.

**Why `tier`/`prefer` and not `explicit_cell_override`** (both are cited working patterns —
`live.py:3086` is the explicit-override branch, `live.py:3104` is the tier/prefer branch):
these two spawns have no task/role context, so the `default_tier`-row walk is the least
machinery that still expresses the requested agent (as a real target preference, so e.g.
`smoke --agent codex` cannot silently smoke claude) and keeps spawn_agent's own same-row
hop. The explicit-override path would additionally pin one cell, require a temp routing file
per call, and duplicate `LiveSpawn`'s explicit branch at two more sites — for a `model`
parameter that is compatibility-only everywhere else post-refactor. **Consequence, stated
plainly:** a run-level `--model`/`--effort` handed to these two surfaces is not consulted for
dispatch (it already is not for task workers); explicit per-cell overrides continue to flow
through `--model-map` into `LiveSpawn`'s explicit branch, which this change does not touch.
On a routing table with no `default_tier`, the CLI path fails earlier in `main()`'s kept
`_default_model_for_agent` resolution with its remedy message; a direct caller passing an
explicit model gets `select_cell`'s `NoExecutionTarget` ("attempted: none"). Both are loud.

Verified end-to-end (hermetic: real `spawn_agent`, patched `subprocess.run` +
`resolve_routing`): the repaired call shape resolved `claude-sub:sonnet` from a
`tier="t2-build" / prefer="claude-sub"` pair, parsed the scripted stream's session id, and
recorded `available` — i.e. the test pattern below exercises real behavior, not a stub.

### D4: `main()` resolves the worker model only for subcommands that consume it

A module-level constant next to the parser lists the subcommands whose tail-resolved values
are consumed downstream: `{"smoke", "live-run", "full", "live-run-real", "full-real"}`.
`main()` guards both lines — `args.model = _default_model_for_agent(...)` and
`role_models = _effective_role_models(...)` — behind `args.cmd in <that set>`. Non-spawning
subcommands (`precheck`, `status`, `usage`, `skip`, `clear-task`, `instantiate`) then run
their own work without requiring a routing table that serves the invocation host.

- *Why guard both lines*: `_effective_role_models(agent, ...)` resolves a codex default
  model when the host agent is codex — the identical crash for a codex-hosted session, even
  though `role_models` is consumed only by `full-real`.
- *Why an allow-list of consumers, not a deny-list of non-spawners*: a subcommand added
  later defaults to the safe side (no premature model requirement). A future spawning
  subcommand that is not added to the allow-list still resolves lazily inside its spawn
  function — `live_run`, `live_run_real`, `full`, `full_real`, `smoke` and
  `run_research_session` each already do `model or _default_model_for_agent(agent)` — so the
  failure mode is a *later* loud error, never a silent one.
- *`spawn-one`*: it does spawn, but consumes neither tail value directly (`LiveSpawn.__init__`
  resolves its own default eagerly at construction). Outside the allow-list, its
  `OperatorConfigError` for an unroutable agent still fires — one frame later, from the same
  helper.
- *Rejected alternative* (offered in the change request): resolving the agent from the
  routing table rather than the invocation host. That changes *which* agent a spawning
  command defaults to on any host/routing mismatch — a behavior change for spawning
  commands, which the request says to keep intact — while the allow-list changes only the
  subcommands that genuinely need no model.

### D5: Tests exercise the real signature and pin the real predicate

- **Item 1** (`tests/orchestrator/test_spawnlib.py`, using the existing `_routing()` /
  `_patch_routing` / `FakeRun` / capacity-cache isolation fixtures):
  - Predicate tests through `is_infra_failure`: the synthesized no-op event → True; a real
    completed turn → False; a `num_turns: 1`, zero-token turn → False.
  - Spawn-level tests: a no-op stream is retried (outcome `available` never recorded on a
    non-final attempt); exhausted on cell A with cell B healthy → completes on B and gates A
    with `failure_class: "startup"`; only-cell no-op → exhausted result with
    `failure_class: "startup"`, not a successful empty run.
  - The no-op fixture's docstring states it is **synthesized** to the documented measured
    shape (no live recording exists on this machine).
- **Item 2** (`tests/orchestrator/test_live_extras.py`): each repaired call site is driven
  through the **real** `spawn_agent` — only `spawnlib.subprocess.run` and
  `spawnlib.resolve_routing` are patched (the same hermetic pattern `test_spawnlib.py` uses).
  Against current code both raise `TypeError` before any stub could return, which is exactly
  the property a MagicMock patch cannot test. Assertions: `run_research_session` returns the
  scripted stream's session id; `smoke` returns True on a PONG stream (and the spawn resolved
  the `default_tier` row's cell for the requested harness).
- **Item 3** (`tests/orchestrator/test_precheck.py`, reusing `_make_spec_dir`): with a temp
  codex-only routing file (`WORKTRAIL_ROUTING_FILE`) and `live.DEFAULT_AGENT` patched to
  `"claude"`, `live.main(["precheck", ...])` returns its normal exit code (current code
  raises `OperatorConfigError`); contrast: `live.main(["smoke", ...])` on the same table
  still raises — pinning "resolution intact for every subcommand that does spawn".
- **Rejected: a repo-wide AST audit binding every spawn call site's literal kwargs against
  the real signature.** Considered as the structural guard for this defect class; a static
  check cannot see through `**kwargs` sites (several exist) and would create coverage that
  looks broader than it is. The two end-to-end tests cover the two real sites, and
  `test_spawn_exhausted_callers.py` already forces an explicit decision (handled or EXEMPT)
  for every new spawn call site.

## Risks / Trade-offs

- **[A zero-API-call run whose stderr carries auth-looking wording still takes the auth
  short-circuit]** → Not observed in the measured shape (a run that makes no API call cannot
  receive an API auth verdict, so any such text would be CLI noise), and reordering
  `_is_auth_failure` against spec'd behavior ("An auth failure gates its cell without retry")
  is out of scope. If it occurs, the auth gate is probe-eligible on the ~15-minute cadence,
  so it self-heals rather than sticking for the full 24 h. Documented, not "fixed".
- **[`is_infra_failure` parses the stream once more for every clean-exit spawn]** → The
  attempt loop already parses `last_raw` for the session-limit check and scans it for the
  opencode error event; one more in-memory JSONL pass is not measurable against
  minutes-to-hours spawns. Correctness over the pass.
- **[A false positive would reject a legitimate empty-but-real spawn]** → Constraint (b) is
  enforced conjunctively (`num_turns >= 1` or any non-zero token count vetoes) and covered by
  the contrast test.
- **[The D4 allow-list could skip resolution for a future spawning subcommand]** → Spawn
  functions resolve lazily, so the same `OperatorConfigError` surfaces at spawn time; the
  contrast test pins that spawning commands still fail loud on an unroutable host agent.
- **[The repaired call sites no longer consult a run-level `--model`]** → Compatibility-only
  by the post-refactor contract these surfaces are converging on (LiveSpawn documents the
  same); explicit per-cell overrides keep working through `--model-map` /
  `explicit_cell_override`, untouched by this change.
- **[Fixture-only verification of the no-op shape]** → Mitigated by grounding the fixture in
  the measured event fields and by the detector's conservative conjunction; if a live no-op
  ever recurs, `WORKTRAIL_KEEP_TRANSCRIPTS` captures the raw stream for comparison, and a
  mismatch would surface as the spawn still being treated as completed (the pre-change
  behavior) rather than as a false positive.

## Migration Plan

No deployment steps and no data migration: the change is a patch to the spawn layer's
outcome classification plus two call-site repairs. Rollback is a straight revert of the PR;
capacity gates written by the new class (`startup`, 60 s) expire on their own within a
minute, so no cleanup is needed either way. No version bump ships in this PR (repo policy:
release-metadata bumps are standalone `chore:` commits).

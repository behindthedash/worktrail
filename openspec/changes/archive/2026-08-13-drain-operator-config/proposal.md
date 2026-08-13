## Why

`worktrail-drain`'s provider selection was hardcoded: `--agent` defaulted to `claude` in the
argparse layer, and nothing read an operator preference. A config-less manual drain therefore
always spent paid Claude capacity even when the operator's stated preference is the free-tier
OpenCode default model — observed live 2026-08-13: a trivial-repo drain launched from OpenCode
"passed claude as the agent, so it burned tokens rather than using my preferred free option."
The operator asked for exactly this: the defaults "really shouldn't be hardcoded... a
.worktrail directory with a config."

## What Changes

- New `shared/operator_config.py`: one JSON file of machine-wide operator preferences at
  `worktrail_home()/config.json`, currently a `drain` section (`agent`, `fallback_agents`).
  Missing file = empty config; malformed file fails loud (`OperatorConfigError`) — a broken
  preference file must never silently invert into built-in defaults.
- `worktrail-drain` resolves its agents as CLI > operator config > built-in `claude`, and
  validates config-sourced agents against `SUPPORTED_AGENTS` with an error naming the config
  file. Explicit automation (the nightly drain script passes `--agent`/`--fallback-agent`
  itself) is unaffected by design.

## Capabilities

- `drain-operator-config` (new)

## Impact

- `src/worktrail/shared/operator_config.py` (new), `src/worktrail/drain/drain.py` (CLI
  resolution only), `tests/shared/test_operator_config.py` (new), `tests/drain/test_drain.py`.

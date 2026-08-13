## 1. Operator config module

- [x] 1.1 Add `src/worktrail/shared/operator_config.py`: `config_path()` under
      `worktrail_home()`, `load_operator_config()` (missing → `{}`, malformed →
      `OperatorConfigError`), and `drain_config()` shape-checking the `drain` section.
      Implements "A malformed operator config fails loud".

## 2. Drain CLI resolution

- [x] 2.1 In `src/worktrail/drain/drain.py`, change `--agent` to default None and resolve
      agent + fallback chain as CLI > operator config > built-in `claude`, validating
      config-sourced names against `SUPPORTED_AGENTS` with an exit-2 refusal naming the
      config file.
      Implements "Drain agent selection resolves CLI over config over built-in".

## 3. Tests

- [x] 3.1 `tests/shared/test_operator_config.py`: missing file, round trip, malformed JSON,
      non-object top level, bad `drain` shapes.
- [x] 3.2 `tests/drain/test_drain.py`: built-in default without config, config honored, CLI
      overrides config, unsupported config agent refused, malformed config fails loud.

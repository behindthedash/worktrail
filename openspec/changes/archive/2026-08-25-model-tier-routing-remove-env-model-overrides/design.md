## Context

`spawnlib.default_model_for_agent()` (src/worktrail/orchestrator/spawnlib.py:415) is the
single resolution point for fallback default models across every spawn path (`live.py`,
`check_agent_contract.py`, and spawnlib's own builders). For codex/opencode it consults
three layers in order:

1. `os.environ.get("ORCH_CODEX_MODEL")` / `os.environ.get("ORCH_OPENCODE_MODEL")`
2. `_load_model_defaults()` → `worktrail_home()/model-defaults.yaml` (path overridable via
   `$WORKTRAIL_MODEL_DEFAULTS_FILE`, legacy `$GO_MODEL_DEFAULTS_FILE`)
3. Hardcoded `DEFAULT_CODEX_MODEL` / `DEFAULT_OPENCODE_MODEL`

Claude already resolves 2 > 3 only. Layer 1 predates the model-defaults file and bypasses
the repo's own `env_setting()` convention (`WORKTRAIL_*` with legacy `GO_*` synonyms) — it
is a raw `os.environ.get()` under an unrelated `ORCH_` prefix, undocumented in skills/docs,
and the source of the ambient-env hermeticity guards that
`tests/orchestrator/test_resilience_helpers.py` and `tests/orchestrator/test_spawnlib.py`
must maintain today.

Config-driven routing (`routing.tiers`/`roles`/`fallback` via `dispatch.agent_for()` /
`resolve_routing()`) sits above these defaults and is not touched by this change.

## Goals / Non-Goals

**Goals:**

- One operator-facing override channel for fallback default models:
  `model-defaults.yaml` > hardcoded constant.
- Delete the two raw env lookups and their precedence documentation in the comment block
  at spawnlib.py:370-375.
- Make existing tests strictly simpler: the ORCH_* ambient-env scaffolding exists only to
  neutralize this layer and goes away with it.
- Add positive coverage that ambient `ORCH_*` vars are ignored.

**Non-Goals:**

- No change to `routing.tiers`/`routing.roles`/`routing.fallback` resolution or precedence.
- No deprecation-warning shim for the removed variables (see Decisions).
- No rename/rework of `MODEL_DEFAULTS_FILE_ENV` / `_load_model_defaults()`.
- No change to claude's resolution path (already config-file driven).

## Decisions

**D1 — Hard removal, no deprecation shim.**
Alternatives considered: (a) keep reading the vars but log a warning; (b) accept them via
`env_setting()` as `WORKTRAIL_*` names. Rejected both: this is single-operator tooling
where the operator is the author; the config file covers the identical need without
ambient leakage into child processes and CI; any retained read keeps the two-channel
ambiguity and the test hermeticity burden this change exists to delete. A silent ignore
matches how the codebase treats other retired knobs (no compat layer for internal env
knobs).

**D2 — Resolution order inside `default_model_for_agent()` becomes uniform for all three
agents**: `defaults.get(agent) or DEFAULT_*_MODEL`. The codex/opencode special-casing
collapses to the same shape claude already has; the function becomes a one-liner over the
defaults dict.

**D3 — Comment block rewrite, not deletion.** The rationale comment at spawnlib.py:364-375
(model drift history, why constants are last-resort) stays valuable; only its precedence
sentence drops the env-var layer.

**D4 — Test strategy.**
- Delete `test_opencode_model_override_remains_supported`
  (tests/orchestrator/test_resilience_helpers.py:207) and
  `test_explicit_env_var_wins_over_file` (:257) — they assert removed behavior.
- Replace with negations: patch `ORCH_OPENCODE_MODEL`/`ORCH_CODEX_MODEL` into the env and
  assert the constant (and separately, the file value) still wins.
- Simplify `ModelDefaultsFileTest.setUp`'s explicit pop-and-restore of the two vars —
  after removal they cannot influence results.
- Update the stale hermeticity comments (test_spawnlib.py:1404, corpus fixture reference
  in classifier_corpus.json:826 may stay as historical incident text — verify the router
  accuracy check still passes rather than editing fixture data).

## Risks / Trade-offs

- [Operator relied on `ORCH_*_MODEL`] → Mitigation: one-line migration to
  `model-defaults.yaml`; the variables are undocumented in skills/docs, so exposure is
  limited to someone reading source.
- [A forgotten CI/cron environment sets the var expecting effect] → Mitigation: silent
  ignore is deterministic and the config file produces the intended model; no partial
  behavior.
- [Hidden call sites reading the vars directly] → Mitigation: repo-wide grep shows the
  only readers are the two lines in `default_model_for_agent()` plus tests; grep again in
  tasks as a verification step.

## Migration Plan

Single PR, no data migration. Operator-facing migration (if needed): move the desired
model into `~/.worktrail/model-defaults.yaml` as `codex:`/`opencode:` keys. Rollback =
revert the PR; no state to unwind.

## Open Questions

None.

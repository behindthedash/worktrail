## ADDED Requirements

### Requirement: Every policy key is type-checked against its declared default
`load_policy()` SHALL check each flat policy key's resolved value against an expected type
before returning, where the expected type is derived from that key's entry in `DEFAULTS` (or,
for a key whose default is `None`, from an explicit key-to-type table). A value whose type does
not match SHALL be replaced by that key's default value.

#### Scenario: a string where a list is expected falls back to the default
- **WHEN** a policy file sets `protected_paths: docs/**`, which parses as a string
- **THEN** the resolved policy's `protected_paths` is the default `[]`, not the string

#### Scenario: a non-string where a command is expected falls back to the default
- **WHEN** a policy file sets `pre_pr_cmd: true`, which parses as a boolean
- **THEN** the resolved policy's `pre_pr_cmd` is `None`

#### Scenario: a bare-string require_human_routes keeps gating
- **WHEN** a policy file sets `require_human_routes: B`, which parses as a string
- **THEN** the value is left unchanged and no type warning is emitted, because
  `automerge_eligible()` tests `route in require_human_routes` and a bare single-route string
  is a working gate that replacing it with `[]` would silently open

#### Scenario: a correctly-typed policy file is unchanged
- **WHEN** a policy file sets every key with a value of its declared type
- **THEN** the resolved policy is identical to the policy resolved before the sweep existed,
  and no type warning is emitted

### Requirement: A rejected value is reported in the policy warnings
Each value the type sweep rejects SHALL add one entry to `_meta["warnings"]` naming the key, the
expected type, and the offending value, so an operator sees the fallback rather than silently
running on defaults.

#### Scenario: the warning names key, type and value
- **WHEN** `protected_paths` is set to the string `docs/**`
- **THEN** `_meta["warnings"]` contains an entry mentioning `protected_paths`, that a list was
  expected, and the rejected value

#### Scenario: a valid file warns about nothing
- **WHEN** every key in a policy file is correctly typed
- **THEN** no warning mentioning a type mismatch is present

### Requirement: The sweep preserves the existing per-key validation
The type sweep SHALL run before the existing per-key validation in `load_policy()`, and SHALL
NOT change the resolved value, warning text, or clamping behavior for any key that validation
already covers -- `automerge.max_risk`, `automerge.enabled`, `allow_seeded_implementation`,
`agent_cli`, `fallback_agent_cli`, `agent_model`, the integer keys, `pre_commit_cmd`,
`max_active_changes`, `triage_keep_limit`, `triage_max_queue_age_days`,
`merge_method_by_base` and `promotion_pairs`.

#### Scenario: an out-of-vocabulary risk keeps its own warning
- **WHEN** `automerge.max_risk` is set to `critical`
- **THEN** the resolved value is clamped to `low` with the existing `max_risk ... clamped`
  warning, and no duplicate type warning is added

#### Scenario: an out-of-range integer keeps its own warning
- **WHEN** `max_workers` is set to `0`
- **THEN** the existing "must be an integer >= 1" warning is emitted and the value falls back to
  its default, unchanged from today

### Requirement: automerge.target_branches is swept like a flat key
The nested `automerge.target_branches` value SHALL be subject to the same type check as a flat
list-valued key, since a mistyped value there disables auto-merge targeting silently.

#### Scenario: a mistyped target_branches falls back to empty
- **WHEN** `automerge.target_branches` resolves to something that is neither a list nor a
  string, such as an integer
- **THEN** the resolved value is `[]` and a warning naming `automerge.target_branches` is
  emitted

#### Scenario: a bare-string target_branches is left to auto-merge eligibility
- **WHEN** `automerge.target_branches` is a single bare string such as `dev`
- **THEN** the value is left unchanged and no type warning is emitted, because
  `automerge_eligible()` already normalizes a bare string to a single-branch list and
  replacing it with `[]` would silently drop that restriction

### Requirement: Every DEFAULTS key has a declared expected type
The expected-type table SHALL cover every key in `DEFAULTS`, and the test suite SHALL fail if a
key is present in `DEFAULTS` with no declared expected type, so a policy key added later cannot
reintroduce an unswept key. `routing` and `add_ons` MAY be declared as explicitly exempt,
because both are re-parsed with real YAML and validated by their own validators.

#### Scenario: adding an undeclared key fails the build
- **WHEN** a new key is added to `DEFAULTS` without an entry in the expected-type table
- **THEN** the coverage test fails

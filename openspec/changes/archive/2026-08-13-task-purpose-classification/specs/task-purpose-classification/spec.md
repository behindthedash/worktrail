## ADDED Requirements

### Requirement: Task schema supports an optional purpose field
A task definition (devkit `TASK-*.md` frontmatter, validated by
`FIELD_SCHEMA`; the in-memory `TaskDict`) SHALL accept an optional `purpose`
string field, alongside the existing optional `complexity`/`domain` fields.
A task with no `purpose` key SHALL behave identically to today.

#### Scenario: Task with purpose validates
- **WHEN** a devkit task's frontmatter includes `purpose: architecture-design`
- **THEN** the task loads successfully and `TaskDict["purpose"]` is
  `"architecture-design"`

#### Scenario: Task without purpose is unaffected
- **WHEN** a devkit task's frontmatter has no `purpose` key
- **THEN** the task loads successfully and `TaskDict.get("purpose")` is
  `None`, identical to this field's absence today

### Requirement: compile.py infers purpose from the repo's configured vocabulary
`conductor/compile.py`'s authoring-time inference pass SHALL request a
`purpose` value for each task, chosen only from the target repo's
`routing.purpose_tiers` keys (resolved via `policy.py`), when that repo has
a non-empty `routing.purpose_tiers` table configured. When a repo has no
`routing.purpose_tiers` table (or an empty one), the inference pass SHALL
NOT request a `purpose` value, and no task in that repo's compiled plan
SHALL carry a `purpose`.

#### Scenario: Repo with configured purpose_tiers gets purpose inference
- **WHEN** `compile.py` runs for a repo whose `routing.purpose_tiers` is
  `{architecture-design: t1-deep, scaffolding: t2-build}`
- **THEN** the compilation prompt asks the authoring agent to choose each
  task's `purpose` from `[architecture-design, scaffolding]` (or omit it),
  and a returned `purpose` value outside that set is dropped with a warning,
  matching `_validate()`'s existing handling of malformed fields

#### Scenario: Repo with no purpose_tiers configured skips purpose inference
- **WHEN** `compile.py` runs for a repo with no `routing.purpose_tiers` table
- **THEN** the compilation prompt does not request `purpose`, and every
  task in the resulting `RunPlan` has `purpose: None`

#### Scenario: runplan.apply_to_tasks merges purpose like complexity and review
- **WHEN** a `RunPlan`'s `TaskPlan` for task `T1` has `purpose:
  "security-review"` and the underlying task dict has no `purpose` set
- **THEN** `apply_to_tasks()` sets `T1`'s `purpose` to `"security-review"`,
  the same merge-only-if-absent rule already applied to `complexity`/
  `review`

### Requirement: routing.purpose_tiers maps a purpose value to a tier name
`policy.py` SHALL accept an optional `routing.purpose_tiers` table:
`{<purpose>: <tier-name>}`, a plain string-to-string mapping validated and
resolved by `resolve_routing()` alongside the existing `fallback`/`roles`/
`tiers` keys. An unconfigured or empty `routing.purpose_tiers` SHALL resolve
to an empty mapping, changing no existing behavior.

#### Scenario: purpose_tiers resolves and validates
- **WHEN** policy configures `routing.purpose_tiers: {architecture-design:
  t1-deep}`
- **THEN** `resolve_routing()`'s returned dict includes `purpose_tiers:
  {"architecture-design": "t1-deep"}`

#### Scenario: Unconfigured purpose_tiers is an empty mapping
- **WHEN** policy configures no `routing.purpose_tiers` key
- **THEN** `resolve_routing()`'s returned `purpose_tiers` is `{}`, and
  dispatch resolution is unaffected

### Requirement: dispatch.agent_for() resolves a task's tier via purpose before complexity
For `implement`/`fix`/`cleanup` roles, `dispatch.agent_for()` SHALL
determine a task's effective tier name as: (1) `purpose_tier_map.get
(task.get("purpose"))` when the task has a `purpose` that resolves via the
run's `routing.purpose_tiers`; else (2) `task.get("complexity")`, matching
today's behavior. `JUDGMENT_ROLES` (`review`/`resolve`/`ci-fix`/
`assembly-resolve`) SHALL remain entirely unaffected — `purpose` and
`purpose_tier_map` are never consulted for those roles, per the existing
DEC-003 precedence.

#### Scenario: purpose-derived tier takes precedence over complexity
- **WHEN** a task has `purpose: "architecture-design"`, `complexity:
  "trivial"`, and `routing.purpose_tiers` maps `architecture-design` to
  `t1-deep`
- **THEN** `agent_for()` resolves the task's tier as `t1-deep`, not the
  `trivial`-complexity tier

#### Scenario: Falls back to complexity when purpose does not resolve
- **WHEN** a task has `purpose: "unmapped-value"` (not a key in the run's
  `routing.purpose_tiers`) and `complexity: "hard"`
- **THEN** `agent_for()` resolves the task's tier via `complexity` (`hard`),
  identical to a task with no `purpose` at all

#### Scenario: Judgment roles never consult purpose or purpose_tier_map
- **WHEN** the `review` role is resolved for a task with `purpose:
  "architecture-design"` and a matching `routing.purpose_tiers` entry
- **THEN** `agent_for()`'s resolution is unchanged from today — the
  independent-reviewer precedence (role_agent_map, else the run's reviewer
  default) is used, `purpose`/`purpose_tier_map` are not read

### Requirement: dispatch.agent_for() tries an agent-aware tier key before the plain tier key
Once a task's effective tier name is resolved (previous requirement),
`agent_for()` SHALL first look up `f"{tier}-{agent}"` (where `agent` is the
call's `default_agent`, or `"claude"` if unset) as the tier_map's
complexity-slot key, alongside the task's `domain`. If no match is found,
it SHALL fall back to looking up the plain `tier` name, matching today's
behavior exactly. This lookup order applies whether the tier came from
`purpose` or from `complexity`.

#### Scenario: Agent-aware key is preferred when present
- **WHEN** `tier_map` contains both `("t1-deep-codex", None)` and
  `("t1-deep", None)` entries, a task resolves to tier `t1-deep`, and
  `default_agent` is `"codex"`
- **THEN** `agent_for()` returns the `t1-deep-codex` entry, not the plain
  `t1-deep` entry

#### Scenario: Falls back to the plain tier key when no agent-specific entry exists
- **WHEN** `tier_map` contains only `("t1-deep", None)`, a task resolves to
  tier `t1-deep`, and `default_agent` is `"codex"`
- **THEN** `agent_for()` returns the plain `t1-deep` entry, identical to
  `model-tier-routing`'s existing behavior

### Requirement: Configuring nothing new preserves current behavior exactly
A repo or task that sets no `purpose` frontmatter and configures no
`routing.purpose_tiers` table SHALL dispatch identically to how it did
before this change — no schema addition or lookup-order change in this
change SHALL alter behavior unless explicitly configured.

#### Scenario: Untouched repo/task is unaffected
- **WHEN** a repo has no `routing.purpose_tiers` entries, and its tasks
  carry no `purpose` frontmatter beyond what already existed
- **THEN** `compile.py`'s output, `resolve_routing()`'s output, and
  `agent_for()`'s resolution are all byte-identical to this change's
  predecessor state

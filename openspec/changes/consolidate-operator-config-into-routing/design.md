# Design: consolidate operator config into routing.yaml

## Context

Five machine-wide surfaces express one concept (which provider/model runs which work) with
no single owner, and they have measurably diverged -- see proposal.md's Why for the six
verified drift instances. This design records the decisions that collapse them to two.

The organizing principle is the one `runtime/catalog.py`'s own docstring already stated but
never enforced: **"An operator's desired order is configuration; a provider's current answer
is evidence."** Two files, one per side of that line.

| Concern | File | Written by |
|---|---|---|
| Intent -- what should run, in what order | `~/.worktrail/routing.yaml` | operator, by hand |
| Evidence -- what actually answered | `~/.worktrail/agent-capacity.json` | worktrail, at runtime |

Anything that is neither (`max_workers`) belongs with intent, because it is a stated operator
preference and not an observation.

## Decisions

### D1: Two files; `routing.yaml` keeps its name

`routing.yaml` absorbs `config.json` and `model-defaults.yaml`. It is **not** renamed to
something broader like `operator.yaml`, even though it will hold `drain.max_workers`.

Rationale: the name is already load-bearing in `WORKTRAIL_ROUTING_FILE`, `policy.py`'s
`default_routing_file()`, the repo-local `routing:` block name, the plugin docs, and operator
memory. A rename buys a marginally better noun and costs a migration on every machine plus a
legacy-fallback branch to carry indefinitely (the `~/.go` -> `~/.worktrail` migration in
`homedir.py` is the precedent for how long those linger). The file's subject -- how work is
routed to providers -- fairly covers drain concurrency.

**Rejected:** a third file for non-routing operator knobs. That is the exact failure mode this
change exists to undo.

### D2: No hardcoded model constants and no model-bearing env vars

`DEFAULT_CLAUDE_MODEL`, `DEFAULT_CODEX_MODEL`, `DEFAULT_OPENCODE_MODEL`, and
`MODEL_DEFAULTS_FILE_ENV` are removed. `default_model_for_agent(agent)` resolves exclusively
from `routing.agents.<agent>.default_model`.

This extends the merged `model-tier-routing-remove-env-model-overrides` decision (model
selection is config-file driven, never env-derived) to its endpoint: the *fallback* is also
config, so there is no code-resident model string left to go stale. `spawnlib.py:366-369`'s
own comment records that `DEFAULT_CODEX_MODEL` had already drifted to a
"discontinued-looking" value once.

**Scope of "no env var":** this removes env vars that carry a *model value*. Env vars that
point at *where config lives* -- `WORKTRAIL_HOME`, `WORKTRAIL_ROUTING_FILE`,
`WORKTRAIL_AGENT_CAPACITY_CACHE` -- are retained. They carry no model intent, and the test
suite relies on them for isolation. `WORKTRAIL_MODEL_DEFAULTS_FILE` and
`WORKTRAIL_PROVIDER_MODEL_CATALOG_FILE` go away with their files.

### D3: Missing routing config fails loud (the notable consequence of D2)

With no constants to fall back on, **a machine with no `routing.yaml` cannot resolve a default
model, and any spawn that does not carry an explicit model must fail with a message naming the
file and the missing key.**

This is deliberate and consistent with the two nearest precedents in the codebase:
`operator_config.py`'s docstring ("a malformed file is stated operator intent that silently
falling back to built-in defaults would invert -- the exact failure mode that motivated this
module: a config-less manual drain silently defaulting to a paid provider") and
`catalog.load_catalog`'s "a normal runtime load must fail closed when the operator catalog is
absent." Silently spawning a hardcoded paid model on an unconfigured machine is the outcome
both of those were written to prevent.

**Mitigation is mandatory, not optional** (tasks 5.1-5.3): a `worktrail-routing --init`
writes a starter file, `worktrail-repo-init` invokes it, and the raised error names that exact
command. A fail-closed default without a one-command fix is a worse trade than the drift it
replaces.

### D4: Delete `catalog.py`, keep `selection.py`, adapt routing to it

`runtime/catalog.py` and `runtime/selection.py` landed together today (PRs #730/#731, ~1,040
LOC, no OpenSpec change behind either). They are **not** coupled, and only one of them is
wired:

- `selection.py` **is** wired -- `drain.py:125` imports `select_execution_target` -- and is
  written provider-agnostically. `_catalog_items()` accepts a plain `{provider: [models]}`
  mapping or any list of `{provider, model}` dicts; `_policy_values()` already reads
  `purpose_tiers`, `tiers`, `defaults`, `fallbacks`, and `fallback_chain` -- which is
  routing.yaml's own vocabulary. It needs no change to consume routing.
- `catalog.py` is **not** wired -- nothing outside `runtime/` and its tests calls
  `default_catalog()`. Its distinctive machinery (`reconcile_discovery`, `discover_catalog`,
  cost/capability metadata, `observed:` blocks) has no consumer, and its `observed:` state
  duplicates the side of the line `agent-capacity.json` already owns.

So the catalog is not being discarded for a rewrite -- it is the unwired half of a two-part
effort whose wired half already accepts the format we are consolidating on. The adapter is a
`routing_candidates(routing)` function yielding `{provider, model, tiers, purposes}` from
`routing.agents` and `routing.tiers`.

**Rejected: finish wiring `catalog.py` instead.** It would make the catalog authoritative for
the registry and routing authoritative for selection -- two files that must agree on every
model name, which is the drift this change exists to remove. The metadata it uniquely carries
(`cost`, `capabilities`) has no reader; the pricing commentary that actually drives operator
decisions lives in routing.yaml's comments today and is retained there.

**Consequence to accept:** if per-model `cost`/`capabilities` metadata is wanted later, it
returns as fields on `routing.agents.<agent>.models[]`, not as a second file.

### D5: `configured_providers` becomes derived, never stored

`agent_capacity.configure()` writes a `configured_providers` array into the evidence file; on
this machine it holds the *catalog's* stale trio while `providers` holds 17 records, so
`gate_snapshot()`'s `all_gated` is computed over a set dispatch does not use.

Storing intent inside the evidence file is the category error. `gate_snapshot()` takes the
provider set as an argument, resolved from routing at the call site. The evidence file then
contains only observations, and this drift class cannot recur.

### D6: Nested tier table, with the flat form accepted for one release

`tiers.<tier>.<agent>` replaces the twelve flat `<tier>-<agent>` keys (four tiers x three
agents), which encode a compound key as a string and made the 2026-08-25 opencode model swap a
three-place edit with a triplicated comment.

```yaml
agents:
  opencode:
    default_model: opencode/x-preview-f-free   # declared once
tiers:
  t2-build:
    claude:   {model: sonnet, effort: medium}
    opencode: {model: opencode/x-preview-f-free}
```

`_validate_routing_tiers` accepts both shapes, warning on the flat one via the existing
`meta["warnings"]` channel. Zero repos carry a repo-local `routing:` block today, so the
compatibility window is a courtesy rather than a real migration burden -- but the flat form is
what `dispatch.agent_for()`'s `<tier>-<agent>` lookup was built for, so removing it in the same
change would couple a config reshape to a dispatch change for no benefit.

### D7: Per-repo `routing:` override is retained

Used by 0 of 15 repos. Retained anyway: `_resolve_routing`'s precedence chain (repo block ->
machine file -> flat keys) is already written and tested, removing it saves no meaningful
complexity, and a repo with genuinely unusual model needs is a plausible near-term case.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| D3 makes an unconfigured machine unable to spawn | `worktrail-routing --init` + `repo-init` wiring + an error that names the command (tasks 5.1-5.3). Verified by an acceptance test that a fresh `WORKTRAIL_HOME` produces the actionable error, not a traceback. |
| Deleting today's merged `catalog.py` discards recent work | It is the unwired half (D4); `selection.py`, the wired half, is kept intact. If the metadata is wanted later it returns as routing fields, not a second file. |
| Changing the drain to claude-first alters unattended cost profile | Explicit operator decision (2026-08-26). Recorded here because the previous opencode-first behavior was cheaper by accident, not by intent; if cost is the goal, it should be stated as `drain.agent: opencode` in routing under the operator's own eye, not diverge silently from the routing chain. |
| Sequencing collision with the in-flight compile-routing change | Land after it; task 6.1 updates its `model-defaults.yaml` spec reference. |

## Open Questions

1. Should `routing.agents.<agent>.models[]` be introduced now as an explicit enumerated
   registry (enabling "unknown model in a tier fails validation"), or only
   `default_model` now, with the enumeration deferred until something needs it? Deferring
   keeps this change smaller; introducing it now is the one piece of the catalog's design
   worth preserving. **Recommendation: defer** -- validation against an enumerated set is a
   separate purpose from consolidation, and D4 already records where it would live.

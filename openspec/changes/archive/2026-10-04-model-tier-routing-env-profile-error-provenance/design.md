# Design: env-profile error provenance

## Context

See `proposal.md` — Why, for the incident and motivation. The design-relevant
current state:

- `spawnlib._apply_env_profile(cell, env, env_profiles)` (spawnlib.py:755) is the single
  raise site for "the cell names `auth.profile` but the table cannot supply it". Its two
  inputs are separately observable: the **cell** (from `select_cell`, i.e. the resolved
  `targets` table) and the **`env_profiles` table** (the resolved routing dict's key,
  threaded by the caller). An empty/absent `env_profiles` and a populated table missing the
  name currently produce the same raise — the empty case's message even names the operator's
  routing file and instructs adding an entry there.
- `resolve_routing()` now carries `env_profiles` (fixed in #1380, the resolver drop behind
  the 2026-10-02 misfiled brief), so the remaining defect is purely the message's
  attribution.
- `build_child_env(cell, base_env, *, env_profiles=None)` is called from two places:
  `spawn_agent`'s `_prepare_child_env` (spawnlib.py:1382, threading the resolved table from
  the same `resolve_routing(load_policy(worktrail_home()))` the cell was selected from) and
  `router/spawn_readiness.readiness_problems` (spawn_readiness.py:121, the `--check` /
  drain preflight). The preflight's design (and the archived 2026-10-03
  spawn-readiness-preflight change's requirement, "The routing check proves spawn readiness
  against the resolved table", which this change does **not** modify) pins it to judging the
  resolved table and nothing else — it takes no path parameter and opens no file of its own;
  its FAIL text is the raised message.
- The `env_profiles` requirement's current text was authored by the unarchived sibling
  change `openspec/changes/add-env-profiles`; this delta adopts it and differs only by the
  resolved-table-omitted distinction (see proposal.md — What Changes).

## Goals / Non-Goals

**Goals:**

- At the raise site, make the two failure structures distinguishable: populated-table-
  missing-name keeps today's operator-config message verbatim; empty/absent resolved table
  gets an error attributed to the resolved table (resolver/caller provenance), naming the
  target, the profile and the resolved routing source, with no file-edit instruction.
- When the loader (`load_policy`) sees the profile declared, say so in the empty-table
  error, so the operator learns the config is innocent.
- Record the sibling-raise audit explicitly (brief's requirement), whether or not each
  sibling is changed.
- No behavioral change anywhere else: resolution still uses the resolved table the cell was
  selected from, with no re-read of policy; the preflight's input contract is untouched.

**Non-Goals:**

- Partial resolver-drop detection. The distinction keys on resolved-table **emptiness**, per
  the adopted contract: a populated table missing one name still raises the operator-config
  message even if a hypothetical name-filtering resolver bug dropped that single entry.
  Today's `resolve_routing()` passes the validated table through wholesale, so a per-name
  loss would be a validator bug, and the `--check` requirement above exists to catch
  resolver drops end-to-end.
- Re-deriving or weakening any `add-env-profiles` decision (its message formats for
  profile-file failures, the `expect`/`keys` independence, the mutual-exclusion rule, the
  explicit-override reproduction rule).
- Changing any sibling raise's behavior (audit outcomes below).
- Adding a warning channel: the empty-resolved-table raise is the loud event; the loader's
  existing `_warn_undeclared_env_profiles` warning and `worktrail-routing --check` remain
  the file-level truth channels.

## Decisions

### D1. Branch on the resolved table's emptiness at the raise site

`_apply_env_profile` inspects `env_profiles` (a `Mapping`, normalized by the caller) before
falling through to the existing raise:

- `not env_profiles` → the new resolved-table-attribution error (D3).
- Otherwise, `env_profiles.get(name)` not a `Mapping` → today's raise, byte-identical.

**Alternative considered:** have the raise call `load_policy()` itself when the table is
empty, to decide blame. Rejected: it adds a second policy read at raise time; the
requirement pins profile resolution to the resolved table ("without re-reading policy"); and
it would make the preflight's messages depend on a file its design deliberately never opens.

**Alternative considered:** distinguish at `_prepare_child_env` (pre-raise) where the policy
is in hand. Rejected: it splits one message across two sites and duplicates the name lookup;
the raise site is the single owner of this error (the preflight relies on that).

### D2. Thread the loader's declared table as a diagnostic-only parameter

The config-is-innocent clause (D3) needs the loader's view, which only the spawn path has.
Thread it as an optional keyword used for membership only — never for resolution:

- `_apply_env_profile(cell, env, env_profiles, declared_env_profiles=None)` — consults
  `declared_env_profiles` solely to test whether the named profile is declared; a `Mapping`
  or `None`.
- `build_child_env(cell, base_env, *, env_profiles=None, declared_env_profiles=None)` —
  forwards it, keeping its `env_profiles or {}` normalization (None and `{}` are the same
  empty-table case).
- `spawn_agent` (spawnlib.py:1296) hoists its existing single load and computes both inputs
  from it — no new policy read:

  ```python
  policy = load_policy(worktrail_home())
  routing = resolve_routing(policy)
  declared_env_profiles = (policy.get("routing") or {}).get("env_profiles")
  ```

  and `_prepare_child_env` passes `declared_env_profiles=declared_env_profiles` to
  `build_child_env`.

- `router/spawn_readiness.py` is deliberately unchanged: it passes only `env_profiles=`,
  continuing to judge the resolved table alone; its FAIL text is the D3 base message
  (no clause), which still names the target and the profile — matching the archived
  preflight requirement's scenario "A key the resolver drops is caught even though the file
  declares it".
- Under `explicit_cell_override`, `load_policy` reads the temporary routing file, so
  `declared_env_profiles` is "what the loader sees for this spawn" by construction — the
  correct semantics, no special-casing.

Note on semantics: `policy["routing"]["env_profiles"]` is the loader's **validated** view of
the winning routing source (repo-local `routing:` block or machine-wide file). That is
exactly the brief's "when the raw policy (load_policy) does declare the profile" — a
malformed entry the loader dropped is not "declared" for this purpose.

### D3. Message contract

Case A — populated table, name missing (unchanged, byte-identical):

```
routing target 'X' declares auth.profile 'Y', which is not declared in
routing.env_profiles in <resolved routing file> -- add an `env_profiles: {Y: {from: <file>,
keys: [...]}}}` entry there
```

Case B — empty/absent resolved table (new):

```
routing target 'X' declares auth.profile 'Y', but the resolved routing table (resolved from
<resolved routing file>) carries no env_profiles table, so no profile can resolve from it --
this is a resolver/caller fault (the table was dropped before spawn or never threaded into
the spawn)
```

Case B, with the clause appended when `load_policy` sees `'Y'` declared:

```
; the routing file does declare 'Y', which the resolved table dropped
```

Pinned markers for the regression tests:
- Case B contains the target, the profile, `resolved from`, and `resolver/caller`; it does
  **not** contain `not declared in routing.env_profiles` nor `-- add an`; it contains
  `does declare` iff the declared table was supplied and declared the name.
- Case A contains `not declared in routing.env_profiles` and the `-- add an` instruction.

Both cases still raise `OperatorConfigError` before any process launches, and neither opens
the profile's source file (the failure precedes `resolve_env_profile`), so the "A secret
outside expect is never printed" scenario is unaffected — the new branch reads no value at
all.

### D4. Sibling-raise audit (required; outcomes of record)

Each listed sibling was examined against the defect's shape — a raise that (a) collapses
inputs it can distinguish, or (b) asserts a routing-*file* fact it cannot verify. Findings,
with the line numbers as of this change:

| Raise | Why it does NOT share the defect |
|---|---|
| `build_child_env` claude-api "no auth.env or auth.profile configured" (~spawnlib.py:839) | Single-input: reads only `cell.auth`; there is no separate table at the site whose emptiness could distinguish "the operator declared nothing" from "the cell lost its auth mapping upstream". Distinguishing would mean threading the raw `targets` table purely to phrase an unobserved caller-bug hypothesis — a new mechanism, not this fix — and the remedy it names is already pinned by this capability's scenario "API lane with neither auth source fails loud" ("naming the target and both remedies"). |
| `codex_api_home` "no auth.codex_home configured" (~:879) | Same single-input shape as above. Additionally its text is shared with `--check` (the preflight reuses this raise), and the remedy is pinned by "Codex api lane without a declared home fails loud" ("naming the target and the `auth: {codex_home: <path>}` remedy"). |
| `codex_api_home` "has no auth.json" (~:887) | Reports a verified observation: it checks existence of the exact path the cell declared, and only fires when a home **is** declared. Nothing is hypothesized (declared-but-unprovisioned vs. provisioned-elsewhere are both truthfully covered by the message), and the remedy matches "Codex api lane with an unprovisioned home fails loud". |
| `_prepare_child_env` (~:1336) | Not a raise. It is the threading point: its `routing.get("env_profiles") or {}` normalization is precisely the input the defect's case 1 describes, and D1/D2 give that input correct attribution; it additionally gains the `declared_env_profiles` thread from the policy `spawn_agent` already loads. No defect of its own. |

Also checked (not in the brief's list, same function family): the both-auth-sources raise in
`_apply_env_profile` (~:777) fires only when the cell itself carries both `env` and
`profile` — an upstream drop removes a key and cannot produce it — so its condition is
directly observed, not hypothesized; and `resolve_env_profile`'s `_config_error` messages
already name the verified offending path/key with `declared_in` used purely as provenance.
Neither is changed.

## Risks / Trade-offs

- [Case B attributes resolver/caller provenance even when the routing file also declares
  nothing (a genuine omission)] → That sub-case is already surfaced loudly at load time by
  `_warn_undeclared_env_profiles` (on every `load_policy`) and by `worktrail-routing
  --check`; the spawn raise deliberately stops asserting a file-level fact it cannot verify
  at this site. Accepted per the adopted contract.
- [A hypothetical partial resolver drop (populated table, one name lost) still reads as an
  operator-config error] → Accepted, documented as a Non-Goal; `resolve_routing()` is a
  wholesale passthrough today and the `--check` requirement covers resolver-drop detection.
- [Message growth / drift between the two branches] → The wording is pinned verbatim here
  and by the regression tests' markers; a shared helper is deliberately not extracted for
  two one-shot strings.
- [Callers passing `declared_env_profiles` for resolution semantics] → Documented as
  membership-only in both docstrings; resolution continues to read only `env_profiles`.

## Migration Plan

None. No config surface, schema, or persisted state changes; the raise is process-local.
Rollback is reverting the change commit (no data or flag to unwind).

## Open Questions

None.

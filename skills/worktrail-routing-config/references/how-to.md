# Routing config how-tos

All edits below are to `~/.worktrail/routing.yaml` (or the repo-local `routing:` block, which
uses the same schema one level deeper under a `routing:` key). Run `worktrail-routing --check`
after every edit.

## Add a new harness/adapter

Add an entry under `targets:` naming a supported harness (`claude`, `codex`, or `opencode`) and
a pool (`subscription`, `free`, or `api`; `api` also needs `api_opt_in: true` on the same
target to actually be selected — see gotchas.md). Then give it at least one cell in whichever
tier(s) it should serve:

```yaml
targets:
  opencode-free:
    harness: opencode
    pool: free
tiers:
  t2-build:
    opencode-free:
      model: opencode/some-model
```

A target with no cell in a tier's row simply can't serve that tier — no error, it's skipped.

## Disable a harness/adapter without deleting its config

Two options, depending on whether you want it gone or just parked:

- **Remove its cells from every tier row it appears in**, but leave the `targets:` entry. It
  stays declared (so re-enabling is a one-line uncomment) but is never selected, since
  `select_cell` skips any target with no cell in the row it's walking.
- **Delete the `targets:` entry entirely** if you also want `routing.roles`/`prefer` references
  to it to warn/fail loudly instead of silently resolving elsewhere.

Don't try to "disable" a harness by leaving its login/auth broken — that produces confusing
capacity-gate churn in `agent-capacity.json` instead of a clean absence.

## Add a new model to an existing harness (e.g. add a DeepSeek model to `opencode`)

`opencode`'s models are just model strings — there's no separate catalog file. Point an
existing `opencode-*` target's cell (or a new tier) at the new model string:

```yaml
tiers:
  t2-build:
    opencode-free:
      model: opencode/deepseek-v4-flash
```

If you want the DeepSeek model available at a different capability tier than whatever
`opencode-free` currently serves, either change that cell's model, or add a second
`opencode-*` target (see "Add a new harness" above) so the two models coexist as an
intra-harness ladder — same pattern the starter config uses for `claude-fable`/`claude-sub`.

## Add or remove a tier

Add a new top-level key under `tiers:` with a cell per target that should serve it, then
reference it from `default_tier`, a `roles.<role>.tier`, or a `purposes` entry. Removing a
tier that's still referenced elsewhere leaves those references pointing at a nonexistent row —
`worktrail-routing --check` catches this.

## Route a purpose to a tier

```yaml
purposes:
  security-review: t1-deep
  bulk-mechanical: t3-bulk
```

Only consulted for `implement`/`fix`/`cleanup` roles, and only for a task that carries that
`purpose` in its frontmatter — judgment roles (`review`/`resolve`/`ci-fix`/`assembly-resolve`)
never consult `purposes` at all (see gotchas.md).

## Pin, change, or unpin the code reviewer

```yaml
roles:
  review:
    tier: t1-deep
    prefer: claude-sub     # optional: which target to try first within that tier's row
    independent: true      # optional: exclude whichever harness implemented the task (default when unconfigured)
```

- To force review onto a specific target regardless of who implemented, set `independent:
  false` — this is also the fix if you only use one harness to implement and don't want
  `independent`'s exclusion silently pushing review onto a different harness (see gotchas.md).
- To remove the override entirely, delete the `roles.review` block — review then defaults to
  `{tier: t1-deep (or default_tier if t1-deep isn't declared), independent: true}`.
- `resolve`/`ci-fix`/`assembly-resolve` take the identical `{tier, prefer?, independent?}` shape
  under their own `roles.<name>` key.

## Change `default_tier`

```yaml
default_tier: t2-build
```

This is the tier used for any role/task that doesn't resolve through a more specific path
(explicit per-task `tier`, judgment-role `roles.<role>.tier`, `purposes` match, or matching
`complexity` row). Must name a tier that's actually declared under `tiers:`, or it's ignored
with a warning and effectively unset.

## Validate a change before trusting it

```bash
worktrail-routing --check                                        # syntax + legacy-key + cell sanity
worktrail-policy --repo <repo-path> --resolve-routing "x:x" --json  # print the fully resolved table
```

`--resolve-routing`'s `"x:x"` argument is vestigial (ignored) — it always returns the complete
`{targets, tiers, roles, purposes, default_tier, drain}` table, which is what to read to confirm
an edit resolved the way you expect before it affects a live spawn.

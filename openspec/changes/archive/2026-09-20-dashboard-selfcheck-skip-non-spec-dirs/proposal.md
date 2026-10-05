## Why

`dashboard_selfcheck.check_repo()` scans *every* directory under a repo's `docs/specs/`:

```
for spec_dir in sorted(p for p in specs_root.iterdir() if p.is_dir()):
```

(`src/worktrail/router/dashboard_selfcheck.py:40`). The dashboard it is a detector *for* does
not: `dashboard.py` keeps a `_NON_SPEC_DIRS` denylist (`dashboard.py:1206`) behind
`_is_spec_folder()` (`dashboard.py:1220`) and applies it at both of its own scan sites
(`dashboard.py:1596`, `:2792`), because siblings like `addenda/`, `research/`, `archived/`,
`epics/`, `templates/`, `reviews/`, `contracts/` hold loose `.md` files that are not spec docs.
`dashboard_selfcheck` imports only `_is_spec_doc` and `find_spec_file` from that module
(line 26) and inherits none of that scope.

The consequence is a false positive that is indistinguishable from a real one. Two or more
untagged `.md` files sitting in `docs/specs/addenda/` make `find_spec_file()` return `None` --
correctly, since there is nothing there to pick -- and the selfcheck reports
`ambiguous-spec-doc` for a directory the dashboard never renders and never could drop. `main()`
exits `1` on any finding, so a sweep over a repos root turns those into a non-zero exit and a
triage item for a spec that does not exist. A passive detector whose findings are not actionable
trains its reader to ignore it, which costs the real refusals it exists to surface.

The fix has one trap worth naming: `_is_spec_folder()` is the wrong filter to reuse wholesale.
It returns `True` only when the folder carries a spec doc, a `tasks/`, a `changes/`, or a
`user-request.md` -- and a folder with *ambiguous* spec docs has no resolvable spec doc, which is
precisely the case this detector exists to flag. Reusing `_is_spec_folder()` would suppress
every real finding. The name-based `_NON_SPEC_DIRS` half is the part that transfers.

## What Changes

- `dashboard_selfcheck.check_repo()` skips a `docs/specs/` child whose directory name (lowercased)
  is in `dashboard._NON_SPEC_DIRS`, importing that constant rather than restating the list, so the
  two modules cannot drift.
- No other selfcheck behavior changes: a skipped directory contributes no finding and no error,
  and every folder that is not on the denylist is still checked exactly as before -- including one
  with no spec doc at all.
- The module docstring records that the scope is name-filtered and why `_is_spec_folder()` is
  deliberately not used.

## Capabilities

### New Capabilities
- `dashboard-selfcheck-spec-folder-scope`: the selfcheck scans the same set of directories the
  dashboard treats as spec folders.

### Modified Capabilities

## Impact

- `src/worktrail/router/dashboard_selfcheck.py` (name-based scope filter in `check_repo`).
- `tests/router/test_dashboard_selfcheck.py`.
- Strictly fewer findings; no finding that was actionable before is lost.

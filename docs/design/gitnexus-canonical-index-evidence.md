# GitNexus canonical-index evidence & refresh workflow

Status: shipped
Date: 2026-08-30
Applies to: `worktrail`'s own canonical checkout (`~/projects/worktrail`)

## What this answers

PR #313 (`e327354`) added `router/gitnexus_preflight.check()`, a bounded, read-only probe
the orchestrator uses internally (`orchestrator/dispatch.py`, `orchestrator/live.py`) to
decide whether GitNexus context is safe to lean on for a given run. Before this change
there was no operator-facing way to run that same check by hand or from CI, and no
documented remediation for each of its degraded states — an operator finding
`canonical-base-index-missing` had no path from "what does this mean" to "how do I fix it."

`worktrail-gitnexus-preflight` closes that gap: the same `check()` logic, exposed as a
console script, always advisory (exit 0 unless `--strict` is passed), and it never runs
`gitnexus analyze`/`index` — it only reads the registry and probes the already-running MCP
server, exactly like the internal callers.

## States and remediation

| `status` | `reason` | Meaning | Fix |
|---|---|---|---|
| `available` | `mcp-context-readable` | Canonical checkout is registered, its `lastCommit` matches `HEAD`, and the MCP server answered a live `resources/read` probe. | Nothing — this is the healthy state. |
| `unavailable` | `canonical-repo-unavailable` | `--repo` isn't a git checkout, or its `--git-common-dir` couldn't be resolved. | Point `--repo` at a real git checkout (canonical or a linked worktree of one). |
| `unavailable` | `registry-missing` | No file at `$GITNEXUS_REGISTRY` (default `~/.gitnexus/registry.json`). | GitNexus has never indexed anything on this machine. See "Registering the canonical checkout" below. |
| `unavailable` | `registry-unavailable` / `registry-invalid` | The registry file exists but isn't readable JSON, or isn't the expected list/`repositories` shape. | Inspect the file by hand; a corrupt registry usually means re-running `gitnexus analyze` for the affected repos, or restoring from a backup. |
| `unavailable` | `canonical-base-index-missing` | The registry has no entry whose `path` resolves to this checkout's canonical root (or the entry's `storagePath` no longer exists on disk). | Register the checkout — see below. |
| `unavailable` | `registry-stale` | The registry's `lastCommit` for this checkout doesn't match `HEAD`. | Refresh the index (`gitnexus analyze` for that repo, or wait for the scheduled refresh cron per `~/.gitnexus/refresh-all.sh`). |
| `unavailable` | `mcp-timeout` | The registry entry looks current, but the MCP server didn't answer within `--timeout` seconds (default 5.0). | Check whether the `gitnexus mcp` process/server is up; retry with a larger `--timeout` if the machine is under load. |
| `unavailable` | `mcp-unavailable` | The MCP server responded but not with a usable `gitnexus` server identity and non-empty resource contents (or the process itself failed to start). | Check `GITNEXUS_MCP_COMMAND` / that `gitnexus` is on `PATH`; inspect server logs. |

## Registering the canonical checkout

This probe deliberately never indexes anything itself — indexing is a separate, explicit
operator action, consistent with the "never index a task worktree" invariant `check()`
exists to protect. To move a `canonical-base-index-missing` result to `available`:

```bash
gitnexus analyze ~/projects/worktrail --name worktrail
```

Then re-run the evidence check to confirm:

```bash
worktrail-gitnexus-preflight --repo ~/projects/worktrail --json
```

## Running the check

```bash
worktrail-gitnexus-preflight --repo <checkout> [--registry <path>] [--timeout <seconds>] [--json] [--strict]
```

- Default output is the same one-line prompt note the orchestrator itself uses
  (`prompt_note()`); `--json` prints the full `check()` result for scripting.
- Exit code is always `0` unless `--strict` is passed, in which case it's `1` on any
  non-`available` result. Use `--strict` only where a caller has decided a missing/stale
  index should actually fail a step (this repo's own CI does not — GitNexus registration is
  a per-machine, per-operator concern, not something a hosted GitHub Actions runner can have).

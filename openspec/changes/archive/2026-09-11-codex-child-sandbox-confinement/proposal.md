## Why

`build_command()` (`src/worktrail/router/skill_dispatch.py:450-511`) launches every codex
child with `codex exec --json -s danger-full-access`, which disables Codex's own filesystem
sandbox entirely (confirmed by `tests/router/test_skill_dispatch.py:379-383`, which asserts
this is unconditional — `write=True` and the default both produce it). That means the `-C`
working root and `--add-dir` roots this function also builds are advisory only: nothing stops
a codex child's `apply_patch` from writing to an absolute path outside them. Grepping
`src/worktrail/` confirms worktrail has zero in-process logic today that detects a canonical
(non-worktree) checkout or otherwise restricts where a dispatched codex child may write — the
only existing enforcement point for this is `codex-write-guard.cjs`, a PATH-level wrapper that
lives in the separate `devops` repo as a per-machine setup step, not something this package
ships or depends on. Its own docstring says as much: "Best-effort, not a sandbox... a codex
session already launched with an unflagged working root could still reach a flagged checkout
via an absolute-path `apply_patch` target." On any environment without that external wrapper on
`PATH` (a fresh machine, CI, a contributor's box), worktrail's own codex dispatch has no
confinement at all.

## What Changes

- Add an in-repo guard inside `build_command()`'s codex branch that resolves the `-C` working
  root and every `--add-dir` value before returning the argv, and refuses to build the command
  (raising, not spawning) when any of them resolves to the canonical (non-worktree) checkout
  root of a git repository rather than a linked worktree — the same failure class
  `codex-write-guard.cjs` was written to close, ported in-process so worktrail no longer
  depends on an external, machine-specific PATH wrapper for it.
- This closes the "no external wrapper installed" gap, not the full theoretical
  absolute-path-escape surface. `codex-write-guard.cjs`'s own docstring already scopes itself
  the same way ("not every theoretically reachable path") — replicating that same scope
  in-process is a mechanical, verifiable improvement over the status quo of zero in-repo
  enforcement, not a claim of a fully sandboxed child process. Dropping
  `-s danger-full-access` in favor of Codex's `workspace-write` mode plus a narrower
  loopback-socket allowance was considered and rejected for this change: neither this repo nor
  `devops` references a granular network-access config for Codex's `workspace-write` sandbox,
  and verifying that surface is out of scope for a mechanical guard addition.

## Capabilities

### New Capabilities
- `codex-canonical-checkout-guard`: `build_command()` validates a codex child's `-C`/`--add-dir`
  targets before spawn and refuses to build the command when one resolves to a canonical
  (non-worktree) checkout root, so worktrail's own codex dispatch has in-process confinement
  independent of external PATH-level tooling.

### Modified Capabilities
(none — `worker-dispatch-identity` and the `managed-codex-*` capabilities are unaffected;
this adds a new pre-spawn check, it does not change their existing requirements)

## Impact

- `src/worktrail/router/skill_dispatch.py` — `build_command()` gains the guard; `main()`'s
  existing `--cwd`/`--add-dir` argument handling is the caller surface it must cover.
- `tests/router/test_skill_dispatch.py` — needs new coverage for the refusal path alongside
  the existing structural argv assertions (`test_codex_preserves_codex_binary`,
  `test_codex_receives_explicit_additional_writable_dirs`,
  `test_codex_worker_always_uses_socket_enabled_sandbox`).
- No change to `-s danger-full-access` itself, to the `write`/`add_dirs` public signature of
  `build_command()`, or to any other agent branch (`claude`, `opencode`) — those already gate
  write access behind `write` and don't expose a working-root flag in the same way.

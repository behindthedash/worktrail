## Why

Every headless Codex child worktrail launches runs with `-s danger-full-access`, which turns
Codex's own filesystem sandbox off entirely. The `-C`/`--add-dir` roots each launch site
passes are therefore advisory: a child's `apply_patch` or shell command with an absolute path
can write anywhere the user can, including a flagged canonical checkout
(`~/projects/<repo>`) that the task's worktree model says it must never touch. The devops
`codex-write-guard.cjs` PATH wrapper closes only the observed incident (a launch whose own
`-C`/`--add-dir` names a canonical checkout); it explicitly documents that it is "not a
sandbox" because of this flag.

Confirmed 2026-09-11 (work-queue brief `20260911-145324-worktrail-skill-dispatch-py-launches`),
the flag is hardcoded at three independent sites, so the scope is wider than the brief's
`skill_dispatch.py`:

| Site | Child |
|---|---|
| `src/worktrail/router/skill_dispatch.py:503` | route-executor / propose / triage skill children |
| `src/worktrail/orchestrator/spawnlib.py:592` | orchestrator task workers in a linked worktree |
| `src/worktrail/drain/drain.py:170,241` | unattended drain one-shots |

The `skill_dispatch.py` docstring justifies the flag by "local integration tests can bind
loopback sockets". That is a network concern, and Codex's `workspace-write` sandbox has a
separate knob for it (`sandbox_workspace_write.network_access=true`), so dropping the flag does
not have to give up loopback binding. Probed live on this WSL2 host 2026-09-11 with
`codex sandbox` (codex-cli 0.154.0): under `workspace-write` a write outside the workspace fails
with `Read-only file system`, a loopback `bind()` fails with `EPERM` until the network knob is
set, and a `git commit` inside a linked worktree fails on `<common>/.git/worktrees/<name>/index.lock`
until the git common dir is added as a writable root. Both failures are loud, not silent.

## What Changes

- New `src/worktrail/shared/codex_sandbox.py`: one helper that returns the sandbox argv for a
  headless Codex child -- `-s workspace-write`, the network-access config override, and one
  `--add-dir` per computed writable root. Roots are the child's cwd, its git common dir (so
  commits inside a linked worktree still work), the operator state dir (`worktrail_home()`,
  which also contains the managed child `CODEX_HOME`), the work-queue root, the target repo's
  sibling `<repo>-worktrees/` directory, and any caller-supplied extras. The helper never
  grants a home directory or a canonical checkout's working tree.
- `skill_dispatch.build_command`, `spawnlib.build_cmd`, and `drain.build_command` /
  `BASE_CMDS` all obtain their Codex sandbox flags from that helper instead of hardcoding
  `-s danger-full-access`. Caller-supplied `--add-dir` values (`worktrail-skill-dispatch
  --add-dir`) are merged, not replaced.
- Drain, whose one-shot may work on any repo under `--repos-root`, grants `<repo>/.git` and
  `<repo>-worktrees/` for each git checkout directly under that root -- never the checkout's
  working tree itself.
- Two operator escape hatches, both environment variables read by the helper:
  `WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS` (path-list of additional roots, for tool caches a
  worker turns out to need) and `WORKTRAIL_CODEX_SANDBOX_MODE=danger-full-access` (restores the
  old behavior, with a one-line stderr notice on every launch so it cannot stay on unnoticed).
- The lifecycle fake agent (`tests/orchestrator/lifecycle/fake_propose_agent.py`) gates Codex
  writes on `-s workspace-write` instead of `danger-full-access`, so the propose-spawn lifecycle
  test keeps proving the child had write permission.
- `skills/worktrail-go/SKILL.md` and `references/subagent-prompts.md` already describe the
  child as `workspace-write` scoped and instruct passing `--add-dir` roots; the `skill_dispatch`
  docstring is corrected to match rather than contradict them.

## Non-goals

- Re-implementing the devops flagged-checkout rule inside `build_command()` (the brief's
  second option). That rule lives in `behindthedash/devops` and is enforced by the PATH wrapper
  at every `codex` launch regardless of caller; with the sandbox on, a canonical checkout can
  only be reached by naming it as a root, which is exactly what the wrapper already refuses.
- Sandboxing `claude` or `opencode` children. `opencode` already gets an equivalent
  `external_directory` permission config from `spawnlib`; `claude` has no filesystem sandbox
  flag to set.

## Capabilities

- `codex-sandbox-confinement` (new)

## Impact

- `src/worktrail/shared/codex_sandbox.py` (new), `src/worktrail/router/skill_dispatch.py`,
  `src/worktrail/orchestrator/spawnlib.py`, `src/worktrail/drain/drain.py`.
- `tests/shared/test_codex_sandbox.py` (new), `tests/router/test_skill_dispatch.py`,
  `tests/router/test_internal_dispatch_lifecycle.py`,
  `tests/orchestrator/lifecycle/fake_propose_agent.py`, `tests/orchestrator/test_spawnlib.py`,
  `tests/drain/test_drain.py`.
- Operational: a Codex worker that writes somewhere not in the root set now fails loudly with
  `Read-only file system` instead of succeeding silently. Expected first candidates are
  tool caches under `~/.cache` and `~/.npm`; the extra-roots escape hatch covers them without a
  code change while the default root set is tuned.

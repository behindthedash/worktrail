## Context

Codex's sandbox applies to model-issued shell commands and `apply_patch`, not to the `codex`
process itself, so the child's own session logs and auth under `CODEX_HOME` are unaffected by
the mode. Everything worktrail's children *do* -- edit files, run pytest, `git commit`, spawn
nested `worktrail-*` commands and, under drain, nested `codex exec` grandchildren -- runs under
it, and a grandchild inherits the parent's Landlock ruleset, so a nested spawn can only narrow
the root set, never widen it.

Live probe on this host (codex-cli 0.154.0, WSL2 kernel 6.18, 2026-09-11):

| Probe under `workspace-write` | Result |
|---|---|
| `touch` inside cwd | ok |
| `touch ~/x` | `Read-only file system` |
| loopback `socket.bind` | `PermissionError: EPERM` |
| same with `sandbox_workspace_write.network_access=true` | bound |
| `git commit` in linked worktree, common dir not a root | `Unable to create <common>/.git/worktrees/<n>/index.lock: Read-only file system` |
| same with common dir as writable root | commit ok |

`codex exec` runs with `approval: never`, so a denied command fails the child's step rather
than stalling on a prompt it has no channel to answer.

## Goals / Non-Goals

Goals: every Codex child worktrail launches is filesystem-confined to an explicit, auditable
root set; the three launch sites share one definition of that set; loopback/network keeps
working; the change is loud when it breaks something.

Non-goals: see proposal. Additionally, not sandboxing the `codex sandbox`-less code path
`cluster_detect.py` uses (already `read-only`).

## Decisions

**D1: `workspace-write` plus the network knob, not a narrower approach.** The docstring's
stated reason for `danger-full-access` was loopback binding. `workspace-write` with
`sandbox_workspace_write.network_access=true` keeps full network (needed anyway for `git
push`, `gh`, package installs in workers) while restoring filesystem confinement, which is the
property the brief cares about. Network confinement is out of scope.

**D2: one shared helper, `shared/codex_sandbox.py`.** Three call sites hardcode the flag today;
a fourth divergence is how this drifted. The helper takes `cwd`, `repo` (optional, the target
checkout whose `<repo>-worktrees/` sibling should be writable), and `extra_roots`, and returns
the argv fragment. It resolves roots through `worktrail_home()` and the work-queue root
resolver so test isolation (`tests/conftest.py` redirects both into `tmp_path`) applies
automatically. Git common dir discovery reuses `spawnlib._git_common_dir`, which is moved into
the helper module (spawnlib keeps importing it) so opencode's `external_directory` config and
Codex's `--add-dir` set derive from the same function.

**D3: root set.** cwd; cwd's git common dir; `worktrail_home()`; work-queue root;
`<repo>-worktrees/` when `repo` is given; caller extras; `WORKTRAIL_CODEX_EXTRA_WRITABLE_ROOTS`
entries. `/tmp` and `$TMPDIR` are writable by Codex default and are not repeated. Roots are
de-duplicated and emitted in a stable order so argv is reproducible in dry-run output and
tests. Nonexistent roots are still emitted (Codex tolerates them; `worktrail_home()` is
lazily created at first write).

**D4: drain enumerates per-repo roots.** A drain one-shot runs from a scratch dir under
`worktrail_home()` and may pick a brief for any repo under `--repos-root`. Granting
`--repos-root` wholesale would make every canonical working tree writable, defeating the
change. Instead drain adds, for each immediate child of `--repos-root` that contains a `.git`
directory, `<child>/.git` and `<child>-worktrees`. The working tree stays read-only, so a
one-shot can create/commit in worktrees and read the checkout but cannot edit it -- the same
rule the devops guard enforces at launch. A `--repo` filter narrows enumeration to that repo.

**D5: escape hatches are environment variables, not policy fields.** They exist for incident
response (a worker needing a cache dir nobody anticipated), not for routine configuration.
`WORKTRAIL_CODEX_SANDBOX_MODE=danger-full-access` prints
`codex sandbox: danger-full-access (WORKTRAIL_CODEX_SANDBOX_MODE override)` to stderr on every
launch so the override cannot linger silently. Any other value than the two modes raises
`ValueError` naming the variable.

**D6: fake agent gate flips to `workspace-write`.** `fake_propose_agent.py` mirrors "the flag
that actually grants write access" per CLI; for Codex that becomes `-s workspace-write`. The
lifecycle test also asserts the `--add-dir` set contains the child cwd's common dir and the
run-record dir, since a missing root is the new silent-failure shape this harness exists to
catch.

## Risks / Trade-offs

- Unknown write surfaces in workers (tool caches, `gh` config writes, `pip`/`npm` caches).
  Mitigation: loud `Read-only file system` failures, the extra-roots variable, and tuning the
  default set from observed failures. This is why the mode override exists for the first
  drain runs after landing.
- Landlock availability. The probe confirms it on this host; on a kernel without it Codex
  refuses to run `workspace-write` rather than silently degrading, which is acceptable.

## Open Questions

- Whether `~/.cache/worktrail` (aspens addon cache) belongs in the default set. Left out until
  a worker actually hits it; the addon runs in the orchestrator, which under drain is inside
  the sandbox.

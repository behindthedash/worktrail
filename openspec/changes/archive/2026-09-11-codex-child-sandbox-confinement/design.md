## Context

See `proposal.md` - Why. `build_command()`'s codex branch (`src/worktrail/router/skill_dispatch.py:502-510`)
always passes `-s danger-full-access` (confirmed unconditional by
`tests/router/test_skill_dispatch.py:379-383`), so the `-C` and `--add-dir` values it also
builds are not enforced by Codex itself. The only existing enforcement of "codex must not
write to a canonical checkout" is `codex-write-guard.cjs` in the separate `devops` repo — a
PATH-level wrapper around the real `codex` binary, installed per-machine, not shipped or
depended on by worktrail. `build_command()` only returns an argv list; the actual subprocess
spawn happens later via `subprocess.Popen` with `command[0] == "codex"`
(`src/worktrail/router/skill_dispatch.py:803`, called from `main()`), resolved via `PATH` —
so an external wrapper on `PATH` can already intercept it today, but only if one is installed.
Real callers currently pass `cwd` as a task worktree path (`src/worktrail/drain/drain.py:947-965`)
and, per the documented agent invocation pattern (`skills/worktrail-go/SKILL.md:826-833`,
`skills/worktrail-go/references/subagent-prompts.md:55-58`), `--add-dir` for
`~/.worktrail/runs` and a `<repo>-worktrees` container directory — neither of which is itself a
bare repo checkout.

`devops`'s `flagged-checkout.cjs` already defines the core "is this a canonical checkout, not a
linked worktree" signal via `git rev-parse --show-toplevel --git-common-dir`: a path is inside a
linked worktree exactly when the two differ; a plain (non-worktree) checkout reports the same
path for both. That signal is pure git plumbing, portable, and has no dependency on the
`~/projects` layout.

## Goals / Non-Goals

**Goals:**
- Give worktrail's own `build_command()` an in-process refusal for the specific, already-named
  failure class: a codex child's working root or `--add-dir` target resolving to a bare
  (canonical) git checkout rather than a worktree — independent of whether an external PATH
  wrapper happens to be installed on the host machine.
- Keep the check generic to any git repository the orchestrator might dispatch work against
  (worktrail coordinates work across multiple target repos, not only itself).

**Non-Goals:**
- Not a full sandbox. An `apply_patch` call using an absolute path elsewhere on disk (outside
  any canonical checkout this check can see) is still not blocked — this closes the same,
  narrower gap `codex-write-guard.cjs`'s own docstring already scopes itself to, not the full
  theoretical escape surface. See proposal.md - What Changes.
- Not replacing or removing `-s danger-full-access`. Proposal.md already rules out switching to
  `workspace-write` plus a narrower loopback allowance for this change, since neither repo
  documents a granular network-access config for that Codex sandbox mode.
- Not reusing or depending on `devops`'s `flagged-checkout.cjs` directly (different repo,
  different runtime — Node vs. Python) or its `~/projects`-specific gating
  (`isWorktreesOrIntegrateContainer`, the `-worktrees` sibling existence check). Those exist in
  `devops` to decide when to *nudge* a user toward a convention; worktrail's guard only needs
  the portable "is this a bare checkout, not a worktree" signal itself.
- Not adding this check to the `claude` or `opencode` branches of `build_command()`: neither
  exposes a working-root flag the way codex's `-C`/`--add-dir` does (see the existing docstring
  at `skill_dispatch.py:463-465`), so there is nothing to validate there.

## Decisions

**Detection method: shell out to `git rev-parse --show-toplevel --git-common-dir` per target,
mirroring `flagged-checkout.cjs`'s `getWorktreeIdentity`.** This is the same signal already
proven for this exact purpose in `devops`, reimplemented in Python rather than imported, since
the two repos don't share a runtime. Alternative considered: parsing `.git` file contents
directly (a linked worktree's `.git` is a file pointing at `<canonical>/.git/worktrees/<name>`,
not a directory) — rejected because `git rev-parse` already handles worktrees, submodules, and
edge cases correctly and is one subprocess call per target, not meaningfully more expensive.

**Scope the check to the git signal only — no `~/projects` layout assumption, no
`-worktrees`-sibling-must-exist gate.** `devops`'s extra gates exist to avoid nudging a user
toward a convention their repo doesn't use; worktrail's guard is a hard refusal, not a nudge, so
it should fire whenever the target is provably a bare checkout, regardless of where that
checkout lives on disk.

**Escape hatch: a new, worktrail-scoped environment variable, not reuse of
`WORKTREE_GUARD_ALLOW`/`WORKTREE_GUARD_EXEMPT_REPOS`.** Those are `devops`'s names for a
different enforcement point; coupling worktrail's own override to them would make worktrail's
behavior depend on `devops`-specific env var conventions even when its wrapper isn't installed.
Exact variable name is an implementation detail for tasks.md, not a spec-level concern.

**Failure mode: raise before returning a command, not print-and-continue.** `build_command()`
already has no established pattern for returning a soft-fail value (it either returns a valid
argv list or raises `ValueError` for an unsupported agent, per
`src/worktrail/router/skill_dispatch.py:483-484`); raising keeps callers from accidentally
spawning a refused command by ignoring a return value.

## Risks / Trade-offs

- [Extra `git rev-parse` subprocess per `-C`/`--add-dir` target on every codex dispatch] →
  Small, bounded number of targets per call (one working root plus the caller-supplied
  `add_dirs`, currently at most a handful per the documented invocation pattern); each call is
  a single local git plumbing command with no network I/O.
- [False refusal if a legitimate `--add-dir` target is itself a bare checkout root a caller
  genuinely needs to write to] → Covered by the explicit escape hatch (see Decisions); no
  observed real caller currently needs this (see Context - real `add_dirs` values).
- [Divergence from `devops`'s `flagged-checkout.cjs` over time, since the two implementations
  are now independent] → Both scope themselves to the same narrow signal (bare checkout vs.
  worktree via `git rev-parse`); the `~/projects`-specific gating that could drift is
  deliberately not replicated here (see Decisions), which shrinks the surface that could
  diverge.

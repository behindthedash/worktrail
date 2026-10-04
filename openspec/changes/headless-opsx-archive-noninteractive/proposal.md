## Why

The bundled OpenSpec archive procedure this repo ships stops to confirm with the user at
three points — an incomplete artifact set (`skills/openspec-archive-change/SKILL.md:41-42`),
an unchecked `tasks.md` (lines 52-53), and the delta-spec sync choice (lines 67-68) — and
every one of them is a call to `AskUserQuestion`. `router/skill_dispatch.py:66` routes
`opsx:archive` to that skill. Its twin, `commands/opsx/archive.md`, ships the same procedure
as the `/opsx:archive` slash command, which is what a Claude Code host actually resolves.

None of those prompts has an answer in the runs that reach archive most reliably:

- `worktrail-go drain` spawns fresh-context headless one-shots, and `worktrail-go auto`
  holds `$AUTO_MODE=true` for the dispatch. `AskUserQuestion` is not a registered tool in
  those processes at all — verified 2026-08-10 by a direct `claude -p` probe, which found
  the tool absent rather than merely unanswered (`references/auto-mode.md`, Phase 5.5).
- The go close-out runs `/opsx:archive <change-id>` **inline** once every task is checked
  (`skills/worktrail-go/references/subagent-prompts.md`, "sync-before-teardown" step 3) —
  i.e. the one path that most reliably reaches archive is exactly the one with no channel to
  answer a prompt.

The result is the failure class `router/skill_dispatch.py`'s `build_command` docstring
already records for claude/opencode: they "are otherwise unable to write without an
interactive approval that a headless run has no channel to answer, **which strands the spawn
instead of failing it**" (line 629). A stranded spawn burns the iteration and returns no
signal; a documented default costs one line of prose.

This repo already applies the fix everywhere else. Phase 5.5's three asks, the route-execution
asks, Route A/C closeout, the implement-pipeline spec pick, and the compile-gate recovery all
carry a documented `$AUTO_MODE=true` branch, and
`test_route_execution_ask_sites_carry_auto_mode_fallbacks` structurally enforces it over the
go/sdd-workflow surface. That test's `surface` dict names three files and does not include
either shipped copy of the archive procedure, and no other test extends the invariant to
`skills/openspec-archive-change/` or to `commands/opsx/`. The doctrine is applied everywhere
except the one bundled procedure a headless run actually executes.

## What Changes

- **Both shipped copies of the archive procedure get one unattended contract, defined once
  and cited at every confirming step.** `skills/openspec-archive-change/SKILL.md` and
  `commands/opsx/archive.md` each gain a short block stating what makes a run unattended —
  `$AUTO_MODE` is `true`, or the `AskUserQuestion` tool is unavailable to the session — and
  every step that would otherwise confirm takes a documented default instead, under one
  greppable marker (`**Unattended: no ask.**`) so the fallback can be enforced structurally
  rather than trusted:
  - **Step 1 (no change name given)** → fail closed. An unattended run is never given an
    implicit change: stop with an error naming the required `<change-id>`, archive nothing,
    and never auto-select. Picking a change on the user's behalf is the one place where
    guessing archives someone else's in-flight work.
  - **Step 2 (incomplete artifacts)** → list them in the summary's warnings and proceed.
  - **Step 3 (incomplete tasks)** → report the count and proceed.
  - **Step 4 (delta-spec sync)** → take the recommended option ("Sync now" when changes are
    needed, "Archive now" when already synced) and archive regardless, as the step already
    does after a choice.
  - **Step 6 (summary)** → name every confirmation that was skipped and the default taken,
    so the run's own record shows what was decided unattended.
  - **Guardrails** → the "always prompt for change selection if not provided" bullet carries
    the carve-out; it is the one guardrail that contradicts the fallback head-on.
- **`tests/test_plugin_surface.py` gains a structural test over both files.** It asserts each
  file defines the unattended condition, and that every prompt-carrying numbered step — plus
  that change-selection guardrail — carries the marker with it, so a later edit cannot
  reintroduce an unconditional ask without failing the build. This is the same section-level
  invariant `test_route_execution_ask_sites_carry_auto_mode_fallbacks` already enforces over
  the go surface, extended to the two files it omitted.

The interactive path is unchanged: a session that has `AskUserQuestion` and is not in auto
mode prompts exactly as it does today.

## Capabilities

### New Capabilities

- `openspec-archive-headless-safety`: the bundled archive procedure degrades to documented
  defaults instead of blocking when no human can answer a confirmation, and fails closed
  rather than guessing which change to archive.

### Modified Capabilities

_None._

## Impact

- `skills/openspec-archive-change/SKILL.md` and `commands/opsx/archive.md` — the unattended
  contract plus the per-step defaults.
- `tests/test_plugin_surface.py` — the structural fallback pin.
- No `src/` change. Drain's own unattended archive path (`drain/drain.py`'s
  `_run_openspec_archive`, which runs `openspec archive -y`) is already non-interactive and
  is untouched; this change closes the agent-driven procedure's equivalent gap, not the CLI
  sweep's.

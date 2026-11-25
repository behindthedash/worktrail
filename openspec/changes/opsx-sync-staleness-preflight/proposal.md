## Why

The bundled `/opsx:sync` instructions currently treat the sync worktree's canonical specs as
authoritative. A worktree cut from an old base can therefore apply a stale delta over stale
content and remove requirement text that has already converged on `origin/main`. A verified
near-miss in `hearsay-interview-copilot` was reverted only after an operator compared the result
with `origin/main`; repeated dispatches make that manual safeguard insufficient.

## What Changes

- Add a mandatory remote-base preflight to the bundled OpenSpec sync instructions before any
  canonical spec is edited.
- Fetch `origin`, resolve the target base ref, and use `origin/<base>` rather than the local
  worktree's canonical specs as the baseline for assessing and applying every delta.
- Recognize a delta already represented by the remote canonical specs, report an explicit
  already-synced no-op, and make no edits.
- Keep the Claude command and the installed sync skill aligned, with plugin-surface assertions
  that prevent either instruction surface from losing the guardrail.

## Capabilities

### New Capabilities

- `openspec-sync-staleness-preflight`: protects agent-driven OpenSpec delta synchronization from
  stale worktrees and redundant re-application.

### Modified Capabilities

_None._

## Impact

- `skills/openspec-sync-specs/SKILL.md` and `commands/opsx/sync.md`: synchronized agent
  procedures.
- `tests/test_plugin_surface.py`: regression coverage for the required preflight and parity.
- No drain lifecycle, OpenSpec CLI behavior, public Python API, or automatic mutation of a local
  base branch changes.

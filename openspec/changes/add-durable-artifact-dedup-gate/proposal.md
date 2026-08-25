## Why

End-of-session auto-capture can put one follow-up on two lists. On 2026-08-25, after Route C
merged spec 003-tailwind-v4-migration (PR #86), the Stop-hook-driven convention auto-captured a
handoff brief to implement work the merged spec already tracked — one item on both the spec tree
and the work queue. The duplicate was closed with a done-with-dedup-note (20260825-093426) and the
pattern recorded in `~/tasks/lessons.md` (2026-08-25 entry). Nothing mechanical prevents a
recurrence: the convention text instructs unconditional capture, the Stop hook has no visibility
into session-created durable artifacts, and handoff capture writes without checking what already
exists.

## What Changes

- Reword the End-of-Session Next-Step Suggestion convention in `~/AGENTS.md` (agent-doctrine) so
  auto-capture happens **only when no durable artifact tracks the follow-up**; otherwise emit a
  suggestion-only line naming the resume command. Durable artifacts: a spec/openspec change created
  or merged this session, an epic doc, an existing queue brief, an open PR. Wording stays
  provider-portable (Claude/Codex/OpenCode all read this file).
- Extend Worktrail's Stop hook (`hooks/suggest_next_step.py`) with a mechanical dedup check that
  runs before any capture instruction is issued: detect session-touched `docs/specs/**` or
  `openspec/changes/**`, run records finishing `planned_ready_for_implementation`, and merged
  docs-only spec PRs; on a hit, block the capture and downgrade to a suggestion-only line unless
  the brief text carries explicit justification.
- Add a capture-time overlap warning inside Worktrail's handoff capture (`work_queue` capture path,
  `create_handoff.py`): scan `docs/specs` slugs and open PRs against the `--focus` text and warn
  before writing, mirroring the dashboard's consolidatable-briefs detection but at write time.

## Capabilities

### New Capabilities

- `durable-artifact-dedup-gate`: A mechanical dedup gate on Worktrail's follow-up capture path.
  Covers the Stop hook's pre-capture durable-artifact detection and downgrade-to-suggestion
  behavior, and the handoff capture CLI's write-time overlap warning.

### Modified Capabilities

(none — no existing requirement changes)

## Impact

- `hooks/suggest_next_step.py` (+ its co-located test `hooks/test_suggest_next_step.py`)
- New fail-open checker module under `src/worktrail/router/` plus a console-script entry point in
  `pyproject.toml`, mirroring `worktrail-check-deferred-work-handoff`
- `src/worktrail/workqueue/create_handoff.py` (+ `tests/workqueue/test_create_handoff.py`)
- Reuses `cluster_detect`'s focus-token overlap machinery for write-time warnings
- `~/AGENTS.md` → resolves to `~/projects/devops/agent-doctrine/AGENTS.md`, a separate private
  repo with its own commit flow (coordination task, not a worktree-scoped edit)
- Version bump or `go:no-version-bump` label required (`CI: Version Bump Check` fires on any
  `src/worktrail/**` change)

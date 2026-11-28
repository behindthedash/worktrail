## Why

Drain's `sync-pending` remediation spawns the bundled `/opsx:sync` skill into a short-lived
worktree and lands whatever it wrote — commit, push, `gh pr create` — without inspecting the
resulting draft. The bundled `openspec-sync-specs` skill tells that agent to stamp a top-level
title line (`# <capability> Specification`) onto a main spec, on the stated premise that
"`openspec validate` requires this line" (`skills/openspec-sync-specs/SKILL.md`, step 4d). That
premise is false: a canonical spec with no title line validates clean (`openspec validate
cap-a --strict` exits `0` against a `## Purpose`-first spec on openspec 1.8.0). Applied to an
**existing** canonical spec that already has no title line — the normal shape in a consumer repo
whose specs were authored by hand — the dispatch prepends a stray H1 that the delta never asked
for.

Confirmed live in `/home/briank/projects/hearsay-interview-copilot`: every one of its 12
canonical `openspec/specs/*/spec.md` files begins with `## Purpose` and carries no title line,
yet commit `dde2866` — whose message is exactly drain's `chore(<spec-id>): sync specs from
change` (`drain.py:1133`) — prepends `# teleprompter-content Specification` to the existing
`openspec/specs/teleprompter-content/spec.md` above a legitimate 16-line scenario addition. The
landing sweep had no way to reject it, so the nonconformant draft became a PR a human had to
supersede by hand. That repo still holds 13 unarchived changes, so the same dispatch runs again
every night.

## What Changes

- The bundled sync skill converges on the repository it is syncing: a newly created main spec
  takes the heading shape the repo's existing canonical specs already use, and an existing main
  spec is never restyled — no added, reworded, or repositioned title line.
- Drain's `sync-pending` land step rejects a draft that adds a top-level `# ... Specification`
  title line to a canonical spec that already existed at base, before commit, push, or PR — so a
  nonconformant draft fails the finding loudly instead of reaching a PR.

## Capabilities

### New Capabilities

- `openspec-sync-main-spec-shape`: the bundled sync operation converges a new main spec on the
  target repo's existing canonical-spec shape and never alters an existing spec's title line.

### Modified Capabilities

- `drain-stage-remediation-table`: the `sync-pending` land step gains a draft-shape rejection for
  a title line added to an existing canonical spec.

## Impact

- `skills/openspec-sync-specs/SKILL.md`: the create-new-spec step and the guardrails, to drop the
  false `openspec validate` justification and state the convergence rule.
- `tests/test_plugin_surface.py`: assertions that the bundled skill carries the convergence and
  no-restyle rules.
- `src/worktrail/drain/drain.py`: `_run_sync_pending`, between the sync exiting zero and the
  commit, to reject a title-line addition to a pre-existing canonical spec.
- `tests/drain/test_drain.py`: real-git coverage for the rejected and accepted draft shapes.
- No CLI flag, result-dict shape, summary key, or PR-landing-pipeline change.

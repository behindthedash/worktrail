## Context

The OpenSpec overlap scanner intentionally classifies every change directory
with a readable `proposal.md` as active. Queue triage reuses that scan result
for both fold-candidate discovery and `max_active_changes` counting, although
those consumers have different lifecycle needs. The scanner already owns the
parser for top-level OpenSpec task checklist entries.

## Goals / Non-Goals

**Goals:**

- Derive one consistent started-work signal from the existing OpenSpec task
  parsing rules.
- Use that signal only for WIP-cap counting while retaining full proposal
  visibility for overlap and fold discovery.

**Non-Goals:**

- Changing the `max_active_changes` policy format, cap threshold semantics, or
  which verdict types the cap holds.
- Reclassifying dashboard/overlap `stage` values or hiding roadmap proposals.
- Inferring work start from proposal, design, or spec artifact presence.

## Decisions

**Use a checked top-level task as the start boundary.** A checked task is an
observable implementation-progress signal already represented in `tasks.md`.
The shared OpenSpec task parser recognizes the task grammar used for task
candidate extraction, so the WIP calculation will reuse that grammar rather
than add a second regex in queue triage. A missing or unreadable task list is
unstarted. Alternatives rejected: proposal creation, because it is the current
false-positive boundary; task-list creation, because planning commonly creates
one before implementation; and an external run record, because planned work
does not necessarily have one.

**Separate started-work lookup from overlap scan entries.** The overlap scan's
five-key result shape and `stage: active` behavior remain unchanged. A focused
helper determines whether each proposed OpenSpec change has started work, and
the cap combines proposal enumeration with that helper. This avoids changing
the meaning or API shape of scan output consumed by duplicate detection.

## Risks / Trade-offs

- [A task can be started but not yet checked] → The cap deliberately follows
  recorded progress, the same repository-visible signal available after a
  process ends; a change remains unstarted until an author records completion.
- [Malformed task lines are not recognized] → Reuse the existing parser and add
  cases for accepted checked task syntax, so WIP counting and task candidates
  cannot drift.

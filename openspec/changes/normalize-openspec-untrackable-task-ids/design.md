## Context

`parse_tasks_md` is tolerant by design: a malformed checkbox becomes a warning, never a
parse error, because OpenSpec itself silently ignores lines that are not `- [ ] N.M ...`.
That tolerance is correct for one bad line among many, but it turns an entire id-less
`tasks.md` into an empty task list. The dashboard sees the same `tasks == []` it sees for a
change with no `tasks.md`, and the backlog seeder acts on that stage. The artifact and the
pipeline therefore disagree about whether work exists: the author wrote tasks, worktrail
believes none exist, and the only trace is a parser warning that dies with the empty task list
(warnings ride on task dicts, so zero tasks means zero warnings reach the dashboard).

## Goals / Non-Goals

- Goals: a `tasks.md` with checkboxes but no `N.M` ids is detected as repairable; the repair
  is deterministic and idempotent; the dashboard and seeder route to repair instead of to task
  creation; a change with a genuinely missing or empty task list keeps seeding as before.
- Non-Goals: auto-rewriting another repo's artifact during a dashboard scan or seed sweep;
  renumbering existing tasks; guessing ids for checkboxes that have no enclosing group; making
  the parser reject malformed checkboxes; changing the devkit format.

## Decisions

- **Normalize the artifact, not the in-memory parse.** Assigning ids only at parse time would
  let the orchestrator run tasks that OpenSpec's own tracker still cannot see and that
  `set_task_checked` could never mark in a way `openspec archive` understands. The fix has to
  land in the file.
- **Detection is structural and survives a zero-task parse.** `ParsedTasks` gains an
  `untrackable` list of `(line_no, text)` entries. The existing warning stays, but callers no
  longer depend on warnings attached to task dicts. That is what makes the zero-task case
  visible to the dashboard at all.
- **Only top-level checkboxes count.** A `- [ ]` at column 0 is a task line in the OpenSpec
  format; an indented checkbox is a sub-item of the task above it. Normalizing sub-items would
  invent sibling tasks, so indented checkboxes are left alone (and remain tolerated). This
  narrows the false-positive surface to stray top-level checklists, which in `tasks.md` are
  intended tasks by convention.
- **Assign after the group's highest existing id.** Existing ids may be referenced by
  `depends:` lines, run journals, and human review. Renumbering is destructive; appending
  `max + 1` is stable and reversible by eye. File order is preserved, so execution order does
  not change — the source adapter derives sequential deps from file order, not from numeric id
  order.
- **Report ungrouped checkboxes, do not guess.** A checkbox before any `## N.` heading has no
  group number to borrow. Inventing one or inserting a heading would rewrite structure the
  author did not write; the normalizer reports the line and leaves it for a human.
- **Keep the dashboard stage, change the next action and add a count.** Introducing a new stage
  would ripple through the active/backlog sets, category order, pick-list priority, and drain
  remediation. The `needs-tasks` stage remains accurate (the change is not runnable yet); the
  next action and an `untrackable_task_checkboxes` count carry the distinction, and the seeder
  pre-checks that count rather than the stage alone.
- **Do not mutate during scan or seed.** The dashboard is pure file inspection, and the seeder
  is a queue producer that runs unattended against arbitrary repos. Silently rewriting another
  repo's `tasks.md` there would race with active sessions and bypass review. Instead the CLI
  performs the rewrite on demand, and the seeder seeds a brief that points at it.
- **Distinct stable seed key.** `<repo>:normalize:<id>` is separate from `<repo>:spec:<id>`, so
  an already-seeded (wrong) "create tasks" brief neither suppresses the normalization brief nor
  loops it. Like the ordinary spec key, it is stable: a fruitless normalization brief does not
  re-arm on every sweep.

## Risks / Trade-offs

- A change with a stray top-level checklist item (for example a prose checklist that is not
  intended as tasks) now blocks `ready-to-implement`/`complete` until the id is added or the
  item is demoted to an indented sub-item. That is deliberate: worktrail cannot tell a stray
  top-level checkbox from an authored task, and silently dropping it is the bug being fixed.
- The normalizer appends ids after the highest existing suffix, so a normalized task may carry
  an id larger than a task that follows it in the file. Execution order still follows file
  order; the ids are identifiers, not sort keys.
- Normalization writes to the artifact, so the caller must land it through the normal
  branch/PR flow. The CLI is intentionally explicit rather than a side effect.

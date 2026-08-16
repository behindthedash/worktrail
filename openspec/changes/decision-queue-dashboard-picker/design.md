## Context

See proposal.md - Why/What Changes for motivation. Relevant existing shape:

- `dashboard.py` is deliberately **decoupled** from the work-queue package: it never imports
  `worktrail.workqueue.*`. Queue data arrives as a pre-fetched JSON blob (`--queue-json`, from a
  separate `worktrail-work-queue list --json` call the calling skill makes) and in-flight briefs
  arrive as a raw directory (`--picked-dir`) that `dashboard.py` globs itself. This module's own
  docstring frames it as "Pure file inspection... No git, no network, no agents" for the spec
  scan, and the queue/decisions inputs follow the same "caller fetches, dashboard.py only
  shapes" division of labor.
- `build_category_actions`/`build_category_items` (dashboard.py:1916-2144) already implement the
  ≤4-category, ≤4-item-per-category picker contract `AskUserQuestion` requires, with `new-work`
  always appended last and `categories[:4]` as the truncation point. `_CATEGORY_DESC` holds the
  Level-1 descriptions.
- `worktrail.workqueue.decisions.list_decisions(status="open")` already returns exactly the shape
  needed: `{"decisions": [{"id", "status", "path", "repo", "brief", "created", "answered_at",
  "question"}, ...]}` — the same command the human-facing CLI pointer (PR #453) already tells
  users to run (`worktrail-decision list`).
- `worktrail-go/SKILL.md` Phase 1 already fetches `QUEUE_JSON` before calling `worktrail-dashboard`
  and threads it through both the single-repo and multi-repo branches; Phase 2 is a flat
  `action` → dispatch table, several of whose rows point at a `references/*.md` file for the
  full procedure (`consolidate-cluster` → none inline, `cleanup-worktrees` →
  `references/worktree-cleanup.md`, batch claim → `references/batch-consumption.md`).

## Goals / Non-Goals

**Goals:**
- Make open decisions discoverable from `/go` without a human needing to already know
  `worktrail-decision` exists.
- Let a human answer a decision entirely inside the picker flow — no manual CLI invocation.
- Never make an existing category (`ready`/`needs-tasks`/`workqueue`) disappear because decisions
  now compete for the same ≤4 Level-1 slots.

**Non-Goals:**
- Changing `worktrail-decision`'s CLI (`ask`/`list`/`show`/`answer`/`resolve`) or its record
  format. The interactive flow reads the record the same way a human reading `show`'s output
  would; it does not add a `--json` shape to `show`.
- Changing the agent-side filing/resuming procedure in `decision-queue.md` — that stays exactly
  as documented (an unattended run still files with `ask --brief --release`; a resuming agent
  still reads the answer and calls `resolve`).
- Adding a summary line to the printed `rendered` dashboard text. The proposal's ask is
  specifically the picker (`category_actions`/`category_items`); `rendered` already has its own
  established flag-line pattern (`policy_flags`/`automerge_flags`/`quarantine_flags`/...) and
  extending it is a separate, later decision if the picker alone proves insufficient signal.
- Filtering open decisions by the resolved repo in single-repo (`/go <repo>`) mode. The sibling
  `workqueue` category does not filter `category_items` by repo either (only the printed
  `rendered` text does, via `render_dashboard`'s own `queue_repo` argument) — decisions follow
  the same, already-established precedent rather than introducing a new inconsistency between
  two adjacent categories in the same picker.
- A `resolve` step from the picker. Resolving requires the *consuming* agent to have actually
  read the answer and resumed the brief; a human clicking "answer" has not done that.

## Decisions

**Decision fetch stays a sibling JSON flag (`--decisions-json`), not a new `dashboard.py` import.**
Mirrors `--queue-json` exactly: the calling skill runs `worktrail-decision list --status open
--json` and passes the result in. Alternative considered: have `dashboard.py` import
`worktrail.workqueue.decisions` directly and call `list_decisions()` itself (like `--picked-dir`'s
glob, which dashboard.py does do in-process). Rejected because `--queue-json` is the established
precedent for *queue-shaped* data specifically (as opposed to `--picked-dir`, which is a directory
of files dashboard.py already knows how to parse via `_parse_fm` for its own in-flight scan) —
decisions are queue-adjacent data owned by another package's CLI, not a directory format
dashboard.py already parses, so the JSON-flag precedent is the closer match and keeps
`dashboard.py` free of a new cross-package import.

**`decisions` ranks ahead of `ready` in `category_actions`, and `new-work` is the one that yields
its slot under the ≤4 cap.** An open decision blocks a brief from making any unattended progress
at all until a human answers it — that is strictly higher-leverage for a human's limited /go time
than surfacing another ready-to-implement spec, which can be picked up any time. Concretely:
`categories` is built by appending, in order, `decisions?`, `ready?`, `needs-tasks?`,
`workqueue?`, then always `new-work`, and slicing to `[:4]` — identical mechanism to today, one
more conditional entry inserted first. Before this change the three conditional categories could
never exceed 3, so `new-work` was mathematically guaranteed a slot; today a fourth conditional
category means the all-four-populated case is possible for the first time, and slicing drops
`new-work`. This is accepted (see Non-Goals) because `new-work`'s two actions (`brainstorm`,
`see-backlog`) both remain reachable through the picker's own "Other" free-text fallback — nothing
becomes unreachable, only one redundant button disappears in an already-crowded, rare case.

**No `see-more` overflow for the `decisions` category.** `workqueue`'s overflow item exists
because the work queue is routinely large. Open decisions are, by design, a rare/bounded
occurrence (the queue guards against pile-up via the "second open decision for the same brief"
refusal and the drain's decision-filed circuit-breaker exemption); capping silently at 4 and
relying on "Other" for the 5th+ matches how `ready`/`needs-tasks`/`new-work` already behave with
no dedicated overflow item.

**The interactive answer flow parses the decision record as markdown text, not JSON.** `show`
already prints the full record; the record's `## Options` section is a numbered list with
optional `- Cost:` sub-lines specifically written (per `decisions.py`'s own docstring) to be
readable by "a product owner ... from their phone without opening the repo." An agent reading
that text to build an `AskUserQuestion` call needs no new machine-readable shape. Alternative
considered: add a `--json` output to `show` (it currently accepts but ignores `--json` — the
`show` branch in `decisions.py main()` prints the raw file unconditionally and returns before
checking `args.as_json`). Rejected as out of scope: the proposal explicitly keeps
`worktrail-decision` unchanged, and fixing that pre-existing dead flag is a separate,
unrelated cleanup.

## Risks / Trade-offs

- [A human picks "Open decisions" then "Other" and types free text that doesn't match any listed
  option] → Accepted by design: `ask`'s own header already tells the human they may "write your
  own direction"; the flow passes typed text through to `--answer` verbatim, same as the CLI
  already allows today.
- [`new-work` disappearing from Level 1 in the rare 4-populated case surprises a returning user]
  → Mitigated: "Other" at Level 1 still reaches free-text/brainstorm (Phase 1b already documents
  this fallback for any omitted category), and the case requires decisions, ready work, tasking
  work, and a live queue all simultaneously — an already-busy workspace where new-feature framing
  is the least urgent of the four.
- [A decision's linked brief was deleted or already resumed by an unattended pass between the
  picker rendering and the human answering] → Not a new risk: `answer` on decisions.py side is
  unconditional (it only requires the record not already be `resolved`), and if the brief is gone
  or already progressed, `human-decision-queue`'s existing "the decision resolves to no record"
  and "already-resolved" handling (both pre-existing, unchanged) apply exactly as they do for a
  CLI-driven answer today.

## Migration Plan

No data migration. Purely additive: existing callers of `worktrail-dashboard` that never pass
`--decisions-json` see byte-identical `category_actions`/`category_items` output (the new
parameter defaults to no open decisions). Roll out as one PR; no flag, no staged rollout needed.

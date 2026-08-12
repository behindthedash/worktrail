# Dashboard Rendering — Field Contract

`dashboard.py --json` owns rendering and the picker option arrays. The conductor
prints one field and feeds the others to `AskUserQuestion`; it does **not** build the
dashboard text or the option lists itself. This is deliberate: hand-rendering in the
model is what made two `/go` windows show different lists.

This file is the **data contract** — what each field *contains*. The **procedure** for
consuming these fields (the two-level picker flow, verbatim option mapping, the
single-option guard, "Other"/free-text handling) lives in `SKILL.md` Phase 1/1b and is
**not** restated here — one fact, one home. Three fields drive Phase 1/1b:

| Field | Contains |
|---|---|
| `rendered` | The compact dashboard **text**. Print it verbatim. |
| `category_actions` | Level-1 options: one entry per populated work category. |
| `category_items` | Level-2 options: `category → [items]`, each with full dispatch data. |

## `rendered`

A ready-to-print block. Active specs are grouped by work-category in priority order
(Unreadable spec folder → Needs stuck-run recovery → Ready to implement → Needs
verify/merge → Needs E2E/cleanup tail → Needs sync → Needs tasking → Needs
clarification), the shared next action hoisted into each category header; the
unspec'd backlog collapses to a count + the first two ids; in-flight briefs and
queued handoffs show the top three (claimable first, blocked ones flagged
`[blocked]`); stale worktrees get a one-line `→ review/prune` nudge. Empty
sections are omitted. When nothing is active it prints a single "start with
brainstorm" line.

Print it as-is. Do not regroup, reorder, re-summarize, or re-emoji it.

## `category_actions` (Level 1)

The ≤4 work-category buttons for the first `AskUserQuestion` call. Each entry is
`{n, label, description, category}`, present in priority order and only when the
category has actionable items. `category` is one of `ready`, `needs-tasks`,
`workqueue`, `new-work`; `new-work` is always the final entry. The label carries the
count (e.g. `Ready / in-progress (3)`).

## `category_items` (Level 2)

A map keyed by `category` string; each value is the ≤4 items for that category's
second `AskUserQuestion` call. Each item carries its display fields (`label`,
`description`), a per-category `n`, a `type` (`inflight`/`spec`/`queue`/`fixed`/
`cluster`/`see-more`), an `action`, and the dispatch fields that action needs
(`id`, `repo`, `path`, `spec_id`, `next_action`, `overflow_ids`, `members`,
`signals`). `action` is one of: `resume`, `implement`, `close-stale`, `claim`,
`consolidate-cluster`, `brainstorm`, `see-backlog`, `see-more`.
Blocked queue briefs are excluded from both the Level-1 count and the Level-2
items (they are not claimable). Items beyond the 4 shown are reachable via the
tool's automatic "Other". Dispatch directly from the chosen item — do not
re-derive it.

A `type: "cluster"` item (`action: "consolidate-cluster"`) surfaces a detected
brief cluster (`docs/specs/018-handoff-queue-cluster-detection`) as a
consolidation candidate, carrying `members` (the cluster's brief ids) and
`signals` (why they clustered — `related-link`, `same-target-spec`, etc.).
Within the `workqueue` category's ≤4-item cap, cluster items are ranked ahead
of individual `type: "queue"` items — one cluster action collapses N briefs
into a single higher-leverage choice. A brief that is a member of a shown
cluster item is excluded from also appearing as a separate `type: "queue"`
item in the same list (no double-listing); it stays reachable via "Other"/
`see-more` like any other overflow item.

The stale-worktree entry point is **not** a picker item; it surfaces only as the
`rendered` worktree nudge and is reached via "Other"/free-text (`cleanup-worktrees`).

## JSON shapes by mode

- **`--root` (in-repo)**: `{constitution, specs, active_specs, handoff_queue, inflight, worktrees, category_actions, category_items, rendered}`
- **`--repos` (multi-repo)**: `{repos, active_specs, handoff_queue, inflight, category_actions, category_items, rendered}`
  (each repo row carries `active_specs`, `backlog`/`backlog_ids`, and `worktrees`).
- **brief-ID invocations**: skip the picker; print a one-line summary from `specs`/`inflight` counts.

`inflight` contains only **stalled** picked briefs: a brief claimed less than
`--inflight-stale-hours` ago (default 48) is presumed actively owned by the
session that claimed it and is omitted entirely — from the rendered section,
the picker, and the Level-1 count — so a live session's work never eats a
picker slot or invites a colliding resume. Briefs past the threshold (or with
a missing/unparseable `claimed-at`, where ownership can't be verified) surface
as `🛠️ Stalled in-flight` with `hours_since_claim` set. Batch companions
(`batch-primary` pointing at a still-picked primary) fold into the primary's
entry as `batched: N` rather than appearing as separate rows.

The top-level `active_specs` / `handoff_queue` ints exist for `classify.py --state`
(its Route-D demotion and Route-E boost read exactly those keys) — extract just
those two into a small object (`$STATE_JSON` in SKILL.md's Phase 5) rather than
passing the full `$DASHBOARD_JSON` blob as `--state`; classify.py never reads any
of the blob's other fields, and passing it whole can overflow the argv size limit
in a large workspace. A spec folder that cannot be read gets `stage: "error"` instead of
crashing the scan; it renders under "Unreadable spec folder" and is never a
picker item.

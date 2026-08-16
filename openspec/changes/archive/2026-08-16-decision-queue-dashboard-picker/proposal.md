## Why

`human-decision-queue` gives an unattended run a way to hand a genuine product decision to a
human and release the blocked brief instead of stranding it. Today the only way a human
discovers that decision exists is by running `worktrail-decision list`/`show`/`answer` directly
— nothing in the interactive `/go` dashboard or its `AskUserQuestion` picker points at it. An
interactive session that opens `/go` while briefs sit blocked on open decisions sees no signal at
all: `category_actions`/`category_items` (the picker's own data source) carry no notion of
decisions today, so a human has to already know the CLI exists and remember to run it on their
own initiative. That defeats the asynchronous design the queue was built for — the answer is
supposed to be one `/go` session away, not a separate command a human has to remember.

## What Changes

- `dashboard.py` gains a fifth optional input, open decision records (`worktrail-decision list
  --status open --json`), threaded into `build_category_actions`/`build_category_items` exactly
  like the existing `queue_briefs` input. When at least one decision is open, a new `decisions`
  category ("Open decisions (N)") appears in the Level-1 picker, ranked ahead of `ready` so it is
  never crowded out by active spec work; each open decision appears as a `type: "decision"` item
  in Level 2, carrying the decision `id`, its question (for the label), and its linked `repo`/
  `brief`. The always-present `new-work` category is the one that yields its slot in the rare
  case all four conditional categories are populated at once (the existing `categories[:4]`
  truncation already governs this; `new-work` is redundant with the picker's own "Other" free-text
  fallback, so nothing becomes unreachable).
- `worktrail-go`'s Phase 1 fetches `worktrail-decision list --status open --json` alongside the
  existing queue fetch and passes it to `worktrail-dashboard --decisions-json` in both the
  single-repo and multi-repo branches.
- A new Phase 2 dispatch entry, `answer-decision`, drives an interactive answer: `worktrail-decision
  show <id>` surfaces the record's question, background, and priority-ordered options (with
  per-option costs when present) as an `AskUserQuestion` call; picking an option or typing free
  text via "Other" is recorded with `worktrail-decision answer <id> --answer "..."`, which — per
  the existing `human-decision-queue` behavior, unchanged — unblocks the linked brief
  automatically. No `worktrail-decision` command runs by hand.
- `worktrail-go/SKILL.md`'s dispatch table and `dashboard-render.md`'s field contract document
  the new category/action/item shape; `worktrail-help/SKILL.md` notes the dashboard now surfaces
  open decisions directly, keeping the PR #453 CLI pointer as the non-interactive alternative.

## Capabilities

### Modified Capabilities
- `human-decision-queue`: adds an interactive dashboard/picker consumption path — an "Open
  decisions" category in `/go`'s two-level picker that lets a human answer a filed decision
  in-chat, additive to the existing CLI (`list`/`show`/`answer`) and the unchanged agent-side
  filing/resuming procedure in `decision-queue.md`.

## Impact

- `src/worktrail/router/dashboard.py` (`build_category_actions`, `build_category_items`, `main()`
  CLI: new `--decisions-json` flag)
- `tests/router/test_dashboard.py` (new category/item coverage)
- `skills/worktrail-go/SKILL.md` (Phase 1 decisions fetch, Phase 2 dispatch table)
- `skills/worktrail-go/references/dashboard-render.md` (field contract)
- `skills/worktrail-go/references/answer-decision.md` (new — the interactive answer procedure)
- `skills/worktrail-help/SKILL.md` (mention the dashboard path)
- No changes to `src/worktrail/workqueue/decisions.py`, the `worktrail-decision` CLI, or
  `skills/worktrail-go/references/decision-queue.md` (agent-side filing/resuming stays as-is).

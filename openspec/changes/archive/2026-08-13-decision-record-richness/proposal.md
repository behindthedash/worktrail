## Why

A decision record is the entire interface the answering human gets, and the first-generation
format under-served them: options carried no cost/effort axis, the recommendation had no way to
express "it depends on your product priority," and nothing forced a plain-English problem
statement — so a product owner could still be forced to open the repo to understand what was
being asked. Operator feedback (2026-08-13): options should be listed in priority order, each
labeled with a cost ("better architectural long term solution but higher cost"), the
recommendation conditioned on quick-to-production vs full-architecture when that is the real
axis, and the record must carry "enough plain english — this is the problem, this is why, this
is the background."

## What Changes

- `worktrail-decision ask` gains a required `--background` field: the plain-English story (what
  the problem is, why it exists, how the run got here) rendered as its own `## Background`
  section, written for a reader with no context.
- `ask` gains repeatable `--option-cost`, index-matched to `--option` (count must agree when
  used), rendered as a `- Cost: ...` line under each option so quick-to-production vs
  long-term-architecture tradeoffs are visible at a glance.
- The `## Options` section now states that options are listed in the agent's priority order and
  that the human may answer with a number or write their own direction.
- `--recommendation` guidance (CLI help + `decision-queue.md`): condition the recommendation on
  product priority when it genuinely depends ("quick to production: option 1; long-term
  architecture: option 2"), and say so plainly when one option is simply right.
- `decision-queue.md#file-a-decision` updated with the richer template and a
  "answerable from a phone, zero prior context" writing standard.

## Capabilities

- `human-decision-queue` (modified)

## Impact

- `src/worktrail/workqueue/decisions.py`, `skills/worktrail-go/references/decision-queue.md`,
  `tests/workqueue/test_decisions.py`, README's autonomous-operation summary line.

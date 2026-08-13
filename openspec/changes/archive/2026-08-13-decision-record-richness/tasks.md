## 1. Richer decision records

- [x] 1.1 In `src/worktrail/workqueue/decisions.py`, add required `background` (rendered as
      `## Background`), repeatable index-matched `option_costs` (rendered as `- Cost:` lines,
      count-mismatch refused), the priority-order note in `## Options`, and the
      conditional-recommendation CLI help.
      Implements "Decision records are structured and directory-arbitrated".
- [x] 1.2 Update `skills/worktrail-go/references/decision-queue.md#file-a-decision` with the
      richer template (background, per-option costs, conditional recommendation) and the
      answerable-with-zero-context writing standard; update the README summary line.

## 2. Tests

- [x] 2.1 `tests/workqueue/test_decisions.py`: background required + rendered, priority-order
      note rendered, per-option costs rendered in order, cost-count mismatch refused, CLI
      round-trip with background and costs.

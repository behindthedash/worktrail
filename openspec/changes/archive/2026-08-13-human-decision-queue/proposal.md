## Why

An auto-mode one-shot that hits `blocked_product_decision` has only one documented move today:
finish the run record and leave the brief stranded in `picked/` for the "stalled-in-flight
resume path" — which requires a human to open an interactive session, notice the dashboard's
resume line, and re-drive the work by hand. Nothing surfaces the actual question to the human,
nothing carries the answer back to the next unattended pass, and the drain counts every such
block toward its circuit breaker. The operator asked for exactly the missing loop: "a way for
the autonomous agent to queue up decisions that it needs a human to make... and a way to follow
up to see if a decision has been made so it can continue its autonomous work" — with an explicit
guardrail that this must not become "a scapegoat for the agent... because it got lazy."

## What Changes

- New `workqueue/decisions.py` (`worktrail-decision` console script): `ask` / `list` / `show` /
  `answer` / `resolve` over `$WORK_QUEUE_DIR/decisions/{open,answered,resolved}/`. The directory
  a record lives in — not its status field — is the arbiter, so a human can answer either via
  the CLI or by editing the `## Answer` section and moving the file.
- `ask` enforces structure (question, why-this-is-a-product-call, what-was-attempted context,
  ≥ 2 concrete options) and refuses a second open decision for the same brief; with
  `--brief --release` it stamps `awaiting-decision:` on the source brief and releases it back to
  the queue.
- `work_queue.list` reports a brief awaiting a still-open decision as `blocked` (new
  `awaiting_decision`/`decision_status` fields), so every existing ready-count and auto-pick
  consumer excludes it until the human answers; a deleted decision record never wedges its brief;
  explicit claims of an awaiting brief warn.
- `worktrail-drain` treats a `blocked_product_decision` iteration that filed a decision as a
  cleanly handled outcome (no circuit-breaker pressure, `decisions_filed` on the iteration
  record); a decision-less block still counts — the incentive that keeps filing honest. The run
  summary and end-of-run log report open decisions awaiting a human.
- Skill text: new `worktrail-go/references/decision-queue.md` (filing guardrails, auto-mode
  filing procedure, resume-from-answer procedure); the auto-mode block sites (staleness guard,
  spec collision, related-brief collision, route-execution ask fallbacks, CI-watch case 4,
  auto-mode.md Phases 2/5.5/7) now file-and-release instead of stranding the brief, with
  leave-in-picked demoted to the fallback when filing itself fails; `worktrail-handoff` documents
  the human answering surface.

## Capabilities

- `human-decision-queue` (new)

## Impact

- `src/worktrail/workqueue/decisions.py` (new), `src/worktrail/workqueue/work_queue.py`,
  `src/worktrail/drain/drain.py`, `pyproject.toml` (`worktrail-decision` entry point),
  `skills/worktrail-go/references/{decision-queue.md (new), auto-mode.md, subagent-prompts.md,
  brief-staleness-check.md, spec-collision-check.md, related-brief-collision-check.md,
  ci-watch-loop.md}`, `skills/worktrail-handoff/SKILL.md`,
  `tests/workqueue/test_decisions.py` (new), `tests/drain/test_drain.py`.

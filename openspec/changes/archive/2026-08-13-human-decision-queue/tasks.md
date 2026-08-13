## 1. Decision store and CLI

- [x] 1.1 Add `src/worktrail/workqueue/decisions.py`: `ask`/`answer`/`resolve_decision`/
      `list_decisions`/`find_decision`/`open_decision_ids`/`decision_status` over
      `decisions/{open,answered,resolved}/`, with structured-field validation, the
      one-open-decision-per-brief guard, directory-as-arbiter status resolution, and a
      `main()` CLI (`ask`, `list`, `show`, `answer`, `resolve`), plus best-effort git backup
      via the queue's existing `_git_backup`.
      Implements "Decision records are structured and directory-arbitrated".
      Implements "Answer and resolve close the loop".
- [x] 1.2 Register the `worktrail-decision` console script in `pyproject.toml`.

## 2. Queue blocking integration

- [x] 2.1 In `src/worktrail/workqueue/work_queue.py`, add `_awaiting_decision_info` and fold an
      open linked decision into `list`'s `blocked` flag (new `awaiting_decision` and
      `decision_status` fields), with lenient handling of missing records, and add a claim
      warning for briefs whose decision is still open.
      Implements "Filing a decision blocks and releases the source brief".

## 3. Drain integration

- [x] 3.1 In `src/worktrail/drain/drain.py`, snapshot open decision ids around each iteration;
      a `blocked_product_decision` outcome with newly filed decisions skips the
      consecutive-failure increment and logs the ids; iteration records gain
      `decisions_filed`; the summary gains `decisions_open` and an end-of-run reviewer hint.
      Implements "The drain rewards filed decisions and punishes decision-less blocks".

## 4. Skill procedure

- [x] 4.1 Add `skills/worktrail-go/references/decision-queue.md` (filing guardrails, auto-mode
      filing procedure, resume-from-answer procedure, human surface) and rewire the auto-mode
      block sites (`auto-mode.md` Phases 2/5.5/7, `subagent-prompts.md#auto-mode-ask-fallbacks`,
      `brief-staleness-check.md`, `spec-collision-check.md`,
      `related-brief-collision-check.md`, `ci-watch-loop.md` case 4) to file-and-release with
      leave-in-picked demoted to the filing-failure fallback; document the human answering
      surface in `skills/worktrail-handoff/SKILL.md`.

## 5. Tests

- [x] 5.1 `tests/workqueue/test_decisions.py`: ask structure/refusals, brief stamping +
      release, list blocking + unblock-on-answer + deleted-record leniency, claim warning,
      answer/resolve lifecycle incl. hand-moved files and still-open refusal, list/open ids,
      CLI round-trip and error paths.
- [x] 5.2 `tests/drain/test_drain.py`: decision-filed blocks skip the circuit breaker and
      surface `decisions_filed`/`decisions_open`; decision-less blocks still trip the breaker.

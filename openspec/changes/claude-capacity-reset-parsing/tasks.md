## 1. Parse Claude's cap-notice reset wording

- [ ] 1.1 In `src/worktrail/orchestrator/agent_capacity.py`: add a lenient module-level pattern
      beside `_EXPLICIT_RESET_RE` matching `resets`/`resets at` followed by a clock time with an
      optional `:MM` and an optional parenthesised timezone (case-insensitive), plus a
      notice-size bound constant equal to `spawnlib`'s 600 chars (design.md D2). Extend
      `parse_explicit_reset` to try the existing dated pattern first, unchanged and unbounded,
      then -- only when `text.strip()` is within the bound -- the lenient one: resolve the
      parenthesised zone with `zoneinfo.ZoneInfo`, falling back to local wall-clock on
      `ZoneInfoNotFoundError` or a missing/non-IANA label, roll the clock time forward to its
      next occurrence judged in that zone, and return it converted to UTC-aware (design.md D1,
      D3, D4). Update the module docstring/comment at `:369` so the quoted Claude wording is
      described as parsed rather than merely classified.
      In `tests/orchestrator/test_agent_capacity.py`, add: `resets 2pm (America/Los_Angeles)`
      returns the next 2pm Pacific as a UTC-aware value; the same notice parsed at 9am Pacific
      resolves to today and at 4pm Pacific to tomorrow; `resets at 3:00pm` and `resets 3:00 PM`
      both parse; an unresolvable zone label and a zone-less notice both still parse against
      local time; the lenient wording embedded in text longer than the notice bound returns
      `None`; the existing Codex dated cases still parse, including inside text longer than that
      bound, and win when both wordings are present; and a spawn recording a gate from a Claude
      weekly-limit notice writes `reset_source: "provider"` with the parsed `retry_after`
      instead of the `billing` cooldown
      (Requirements: A Claude cap notice yields an explicit reset timestamp; A date-less reset
      resolves to its next occurrence in the stated zone; The lenient wording is only matched in
      notice-sized text; A parsed Claude reset produces a provider-derived gate).
      files: src/worktrail/orchestrator/agent_capacity.py, tests/orchestrator/test_agent_capacity.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 1.1. Verification-only, no file changes expected.

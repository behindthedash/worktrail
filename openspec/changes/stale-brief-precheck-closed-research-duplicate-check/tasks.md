## 1. Backward-looking research-note search

- [ ] 1.1 In `check_brief_staleness.py`, add module-level constants `RESEARCH_NOTES_GLOB`
      (`"docs/specs/research/*.md"`), `RESEARCH_LOOKBACK_DAYS` (default `30`),
      `RESEARCH_NOTE_CAP` (default `20`), `RESEARCH_MATCH_CAP` (default `20`), and
      `RESEARCH_PHASE_BUDGET_SECONDS` (default `20`), each documented with the same
      rationale-in-comment style as the existing `PATH_PROBE_CAP`/`GH_PHASE_BUDGET_SECONDS`/
      `RACE_GRACE_SECONDS` constants (design.md - "Decisions").
- [ ] 1.2 Add `_offset_since(since_str, seconds)`: parses `since_str` via `_to_utc_datetime()`
      and returns an ISO string shifted by `seconds` (positive = later, negative = earlier),
      falling back to `since_str` unchanged when it does not parse — same fail-open posture as
      `_widen_since()`. Leave `_widen_since()` itself untouched.
- [ ] 1.3 Add `_list_recent_research_notes(repo, base_ref, window_since, window_until, timeout)`:
      one `_run_git()` call — `git log <base_ref> --since=<window_since> --until=<window_until>
      --name-only --format= -- '<RESEARCH_NOTES_GLOB>'` — returning the touched note paths
      deduplicated in first-seen (most-recently-touched-first) order, or `None` on failure.
- [ ] 1.4 Add `_note_last_touch(repo, base_ref, path, timeout)` (one `git log -1 --format=%h
      %x1f%ad --date=short <base_ref> -- <path>` call, returns `(sha, date)` or `None`) and
      `_read_note_content(repo, base_ref, path, timeout)` (one `git show <base_ref>:<path>`
      call, returns content or `None`).
- [ ] 1.5 Add `_search_research_notes(repo, base_ref, probes, since_str, timeout)`: computes the
      window via `_offset_since()` (`since_str` minus `RESEARCH_LOOKBACK_DAYS` days, `since_str`
      plus `RACE_GRACE_SECONDS`), lists candidates via `_list_recent_research_notes()`, caps them
      at `RESEARCH_NOTE_CAP` (reporting the drop count), and — bounded by a
      `RESEARCH_PHASE_BUDGET_SECONDS` deadline, skipping and counting any candidate not reached —
      fetches each candidate's content and last-touch info, checks each of `probes["paths"]` and
      `probes["symbols"]` as a literal substring, and returns matches (each
      `{"sha", "date", "path", "probe", "kind"}`) capped at `RESEARCH_MATCH_CAP` (reporting the
      drop count), plus a combined warning string or `None`. Never raises.
      (Requirement: Backward-Looking Research-Note Search Complements The History Search)
- [ ] 1.6 Wire `_search_research_notes()` into `check()`: call it whenever `probes["paths"]` or
      `probes["symbols"]` is non-empty (the same gating condition already used for the
      forward-looking history search), add its result to `result["research_notes"]`, and merge
      its warning into `result["warning"]` the same way the `gh` phase's warning is merged today
      — without changing `result["checked"]`, `result["matches"]`, or
      `result["pull_requests"]` on its failure, and without letting a history-search failure
      suppress it. Update the `checked: false` early-return result dicts (non-git path, missing
      `since`, no probes) to also include `"research_notes": []`, so the JSON shape is uniform
      across every return path.
      (Requirement: Backward-Looking Research-Note Search Complements The History Search)

## 2. Formatting and CLI surface

- [ ] 2.1 Add `_cite_research_note(note)` (parallel to `_cite_match`:
      `f"{note['path']} ({note['kind']} probe: {note['probe']})"`). Extend
      `format_verified_absent_evidence()` and `format_verified_present_closure_note()` with an
      optional `research_notes: Optional[List[Dict[str, Any]]] = None` parameter, appending its
      citations alongside the existing commit/PR citations and including its count in the
      evidence-count sentence, when non-empty.
- [ ] 2.2 Update `_format_human()` to report `research_notes` matches (count and per-item lines,
      mirroring the existing `matches`/`pull_requests` rendering) and to treat a non-empty
      `research_notes` the same as non-empty `matches`/`pull_requests` for the "no evidence"
      vs. "EVIDENCE" branch.
- [ ] 2.3 [e2e] Confirm `main()`'s CLI JSON output (`--json`) and its non-JSON fallback both reflect
      the new `research_notes` field with no CLI flag changes required (the field flows through
      `check()`'s existing return dict, read by the already-generic `json.dumps(res)` /
      `_format_human(res)` calls).

## 3. Skill doc

- [ ] 3.1 In `skills/worktrail-go/references/brief-staleness-check.md`, update the JSON example
      under "Reading the result" to include a `research_notes` entry, and update that section's
      table so the non-empty-evidence row reads "`matches`, `pull_requests`, or `research_notes`
      non-empty" triggering File-state verification — the same step and the same operator
      prompt, not a second ask site. Update the operator-prompt evidence-display wording to
      mention research-note citations alongside commit/PR ones.
      (Requirement: Research-Note Evidence Reaches The Same Operator Prompt)
- [ ] 3.2 Update the "Cost and bounds" section to document `RESEARCH_LOOKBACK_DAYS`,
      `RESEARCH_NOTE_CAP`, `RESEARCH_MATCH_CAP`, and `RESEARCH_PHASE_BUDGET_SECONDS` alongside
      the existing bounds it already documents, in the same "module-level constants,
      deliberately not policy knobs" framing.

## 4. Tests

- [ ] 4.1 In `tests/router/test_check_brief_staleness.py`, add coverage for
      `_search_research_notes()`/the window boundary: a note touched inside the lookback window
      before capture is a match; a note touched before the window start is not; a note touched
      moments after capture (within `RACE_GRACE_SECONDS`) is still a match; a pull-request-only
      probe set yields no research-note matches.
      (Requirement: Backward-Looking Research-Note Search Complements The History Search)
- [ ] 4.2 Add coverage for independent degradation: research-note search failure (simulate a
      failing/timeout `git` call) leaves `matches`/`pull_requests`/`checked` untouched and adds a
      warning; a forward-looking history-search failure does not suppress `research_notes`.
- [ ] 4.3 Add coverage for `RESEARCH_NOTE_CAP`/`RESEARCH_MATCH_CAP` drop-and-count behavior, and
      for `_format_human()` / the CLI `--json` output including `research_notes`, and for
      `format_verified_absent_evidence()`/`format_verified_present_closure_note()` with a
      non-empty `research_notes` argument.
- [ ] 4.4 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_check_brief_staleness.py`, then
      the full suite (`PYTHONPATH=src pytest -q`) and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`.

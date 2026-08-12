## 1. Core capability

- [x] 1.1 In `src/worktrail/router/check_brief_staleness.py`, add a module-level
  `RACE_GRACE_SECONDS = 300` constant (documented rationale: same-session races, observed 56s
  gap, advisory-only trade-off). Add a helper that computes an effective search-boundary ISO
  string as `created_dt - RACE_GRACE_SECONDS` when the `created` timestamp parses, else falls
  back to the original string unchanged. Use the effective boundary (not the raw `created`
  string) in every `_search_probe()` call inside `check()` (`files:` type=path, `-S` and
  `--grep` searches) and when calling `_lookup_pull_requests()`.

- [x] 1.2 In the same file, add CLI-flag-shaped token recognition: a regex matching GNU
  long-form flags (`--` + letter + letters/digits/hyphens). Wire it into both the backtick-quoted
  token loop and the unquoted token loop in `extract_probes()`, folding a matching token into
  the existing `symbols` list (no new top-level probe kind, no schema change).

- [x] 1.3 Update the module docstring and `check()`'s own docstring to describe the grace
  window and the CLI-flag admission rule, mirroring the existing docstring's level of detail for
  the rules it already documents.

## 2. Tests

- [x] 2.1 Extend `tests/router/test_check_brief_staleness.py` extraction coverage: a
  backtick-quoted CLI flag (e.g. `` `--tier-map` ``) is extracted as a symbol probe; an unquoted
  CLI flag in prose (e.g. "add the --json flag") is extracted as a symbol probe; ordinary
  hyphenated prose words (e.g. "file-scope") are still not extracted.

- [x] 2.2 Extend the history-search fixture coverage: a commit landing a few seconds *before*
  the brief's `created:` timestamp (inside `RACE_GRACE_SECONDS`) is reported as a match; a
  commit landing well before `created:` minus `RACE_GRACE_SECONDS` is not.

- [x] 2.3 Add PR-lookup coverage (mock or fixture-driven, following the existing `gh`-lookup
  test pattern in the file): a resolved pull request merged a few seconds before `created:` is
  kept in the result; one merged well before the grace-widened boundary is excluded exactly as
  today.

## 3. Validation

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and
  `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` from the worktree root;
  both must pass.

# Tasks

## 1. Detector

- [ ] 1.1 Add the smoke-flake detector module, complete with its CLI.
      - `check_repo(repo, *, window_days=30, now=None)` globs `<repo>-worktrees/run-*.json`,
        filters by journal mtime against the recency window, reads each journal's
        `smoke_flakes` map, and aggregates by suite name into entries carrying `suite`,
        `count` (distinct runs), `runs` (spec ids, most recent first), `recurrence`
        (`recurring` when count >= 2, else `single`), and `detail` (the most recent run's
        first-attempt detail), ordered by `count` descending then `suite` ascending.
        Returns `{"entries": [...]}`.
      - Skip — never raise on — journals that are unreadable, do not parse, are not a JSON
        object, or whose `smoke_flakes` value is not a mapping of string to string.
      - Do not consult the run lock: a live run's journal is included, unlike
        `journal_selfcheck.py`.
      - `main()` accepts `--repo` (required), `--window-days`, and `--json`, printing one line
        per entry in human mode and the `check_repo()` result in JSON mode, exiting 0 with no
        entries and 1 with at least one — matching `journal_selfcheck.py` /
        `dashboard_selfcheck.py`.
      - Tests cover: multi-run aggregation and ordering, the empty/no-journals case, the
        window excluding a stale journal and a widened window including it, both recurrence
        classifications, a malformed journal among valid ones, a live-lock-held journal being
        counted, and both CLI exit paths.
      (Requirement: Aggregate recorded smoke flakes per repository)
      (Requirement: Bound aggregation to a recency window)
      (Requirement: Classify recurring flakes distinctly from single flakes)
      (Requirement: Detector never breaks its caller)
      (Requirement: Detector is runnable standalone)
  files: src/worktrail/router/smoke_flake_selfcheck.py tests/router/test_smoke_flake_selfcheck.py

- [ ] 1.2 Register the `worktrail-smoke-flake-selfcheck` console script, alphabetically placed
      among the sibling `worktrail-*-selfcheck` entries.
  files: pyproject.toml
  review: skip

## 2. Dashboard integration

- [ ] 2.1 Thread the aggregate through `dashboard.py` and render it.
      - Build a cross-repo snapshot by calling the detector once per in-scope repo (both
        single-repo and multi-repo mode), tagging each entry with its repo name and merging
        into one `{"entries": [...]}` aggregate, wrapped in the same
        never-break-the-dashboard `except Exception` the capacity snapshot uses so a failure
        yields an empty aggregate.
      - Pass it to `render_dashboard()` as a new `smoke_flakes` keyword argument (defaulting
        to `None`) and include it under a `smoke_flakes` key in both JSON payloads — present
        and empty when there is nothing to report.
      - Render the section alongside the sibling detector sections: nothing when the aggregate
        is empty; otherwise one line naming each suite with its run count (recurring first,
        per the detector's ordering), the `… +N` overflow suffix past the display cap, and a
        `→` next-action pointer telling the operator to fix the flaky suite. Update the
        `render_dashboard()` docstring to describe the new section, as it does for every other
        one.
      (Requirement: Surface smoke flakes on the orientation dashboard)
      (Requirement: Expose smoke flakes on the dashboard JSON payload)
  files: src/worktrail/router/dashboard.py tests/router/test_dashboard.py

- [ ] 2.2 Document the new `smoke_flakes` key in the dashboard JSON field contract, describing
      the entry shape and that the key is always present.
  files: skills/worktrail-go/references/dashboard-render.md
  review: skip

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q`, `ruff check .`, `ruff format --check .`, and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and confirm all
      pass. Then run the new CLI against this repository and confirm it exits 0 with no
      findings (no smoke flakes have been recorded on disk yet) and that
      `worktrail-dashboard --root <repo>/docs/specs --json` still renders and carries the
      `smoke_flakes` key.

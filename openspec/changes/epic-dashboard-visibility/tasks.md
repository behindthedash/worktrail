## 1. Shared epic-stage detector (dashboard.py)

- [ ] 1.1 In `src/worktrail/router/dashboard.py`, add the epic-id filename pattern, terminal-status
      set, and a per-epic-file stage detector that reads `**Status:**`, counts `### Feature`
      headings, matches citation patterns against `docs/specs/` and `openspec/{specs,changes}`,
      and returns one of `epic-gap` / `epic-unparseable` / `epic-complete` plus a `next_action`;
      wrap it so an unreadable/unparseable individual file degrades to an `error`-stage row
      instead of raising (Requirement: Epic files are classified into a dashboard stage)
      (Requirement: A malformed epic file degrades to a per-row error, not a crashed scan)
- [ ] 1.2 In `src/worktrail/router/dashboard.py`, add a sibling scan entry point that lists
      `docs/specs/epics/*.md` files matching the epic-id pattern for a given repo, ignores
      non-matching files (e.g. `README.md`), and returns one row per epic via the 1.1 detector,
      returning an empty list (no error) when the repo has no `docs/specs/epics/` directory
      (Requirement: A repo's epics are scanned into one row per epic file)

## 2. Wire epic rows into dashboard output (dashboard.py)

- [ ] 2.1 In `src/worktrail/router/dashboard.py`'s `main()`, call the 1.2 scan for both the
      single-repo path and the multi-repo (`--repos`/`scan_repos()`) path, and include the epic
      rows in the JSON payload distinctly from the existing `specs`/`repos` rows in each mode
      (Requirement: Epic rows appear in dashboard JSON output)
- [ ] 2.2 Extend `render_dashboard()` in `src/worktrail/router/dashboard.py` to accept the epic
      rows and render a section for outstanding (`epic-gap` and `epic-unparseable`) epics,
      omitted entirely when a repo has none, alongside the existing spec/backlog/worktree
      sections (Requirement: Epic state renders in the human-readable dashboard)

## 3. Deduplicate backlog-seeding's epic parsing

- [ ] 3.1 Refactor `find_epic_gaps()` in `src/worktrail/workqueue/seed_backlog.py` to call the
      dashboard's shared epic-stage detector/scan from section 1 instead of its own
      `_epic_status`/`_count_features`/`_epic_citation_patterns`/`_citing_spec_ids`
      implementations, removing the now-unused duplicated helpers while preserving
      `find_epic_gaps()`'s existing return shape (`seed_key`, `citing_specs`, `unparseable`
      reporting, terminal-status skip) unchanged

## 4. Tests

- [ ] 4.1 In `tests/router/test_dashboard.py`, add coverage for the epic-gap, epic-unparseable,
      epic-complete-via-terminal-status, and epic-complete-via-full-citation classifications, and
      for a non-epic-named file under `docs/specs/epics/` being ignored (Requirement: Epic files
      are classified into a dashboard stage)
- [ ] 4.2 In `tests/router/test_dashboard.py`, add coverage for the epic scan's row shape, its
      empty-list result when a repo has no `docs/specs/epics/` directory, and an unreadable epic
      file degrading to an `error`-stage row while sibling epic files still scan successfully
      (Requirement: A repo's epics are scanned into one row per epic file) (Requirement: A
      malformed epic file degrades to a per-row error, not a crashed scan)
- [ ] 4.3 In `tests/router/test_dashboard.py`, add coverage that both single-repo and `--repos`
      JSON output include the repo's epic rows (Requirement: Epic rows appear in dashboard JSON
      output)
- [ ] 4.4 In `tests/router/test_dashboard.py`, add coverage that `render_dashboard()` shows an
      epics section when a repo has an `epic-gap` epic, and shows no epics section when a repo has
      no epic files or only `epic-complete` ones (Requirement: Epic state renders in the
      human-readable dashboard)

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest tests/workqueue/test_seed_backlog.py -q` and confirm
      every existing epic-gap seeding test still passes unchanged against the section-3 refactor.
- [ ] 5.2 [e2e] Run `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both succeed.

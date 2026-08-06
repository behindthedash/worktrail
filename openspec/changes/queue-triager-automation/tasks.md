## 1. Inventory and grouping

- [ ] 1.1 Create `src/worktrail/workqueue/queue_triage.py`; add `group_queue_by_repo()` that
      reads every brief in `queue_dir()` via `read_frontmatter()`, groups by `repo:` value
      (missing/`null` collapses into one `"__none__"` group key), and returns
      `{repo_or_none: [brief_path, ...]}`.
- [ ] 1.2 Add `is_recently_triaged(path, within_days) -> bool` that scans the brief body for
      the most recent `## Triage <ISO date>` section and compares against `within_days`.
- [ ] 1.3 Add `inventory(within_days) -> (groups, skipped)` composing 1.1 + 1.2: briefs failing
      the dedup check are excluded from `groups` and returned in `skipped` for report visibility.

## 2. Evaluator prompt and spawn

- [ ] 2.1 Add the module-level evaluator prompt template (mirrors `drain.py`'s `PROMPT`
      constant pattern): repo-fetch-first instruction, per-brief `{id, focus, created}` list
      injected into the prompt, ~3-4 tool-call budget per brief, fail-open-to-`keep`
      instruction, `gh repo view --json isArchived` archival check instruction, and the
      memory-file-check-before-alarm instruction — all per design.md's Decisions section.
- [ ] 2.2 Add `evaluate_group(repo, briefs, *, agent, model, cwd) -> List[dict]` that builds
      the prompt for one group and calls `spawnlib.spawn_agent()`, returning the raw text for
      2.3 to parse. `cwd` is the group's target repo checkout when `repo` is non-null (so the
      evaluator can run `git`/`gh` there), else the worktrail repo itself.
- [ ] 2.3 Add `parse_verdicts(raw_text, expected_brief_ids) -> List[Verdict]` implementing the
      spec's evidence-required / malformed-falls-back-to-keep / never-dropped rules. Define a
      `Verdict` dataclass: `brief_id, verdict, duplicate_of, evidence, confidence`.
- [ ] 2.4 Add the archived-repo short-circuit: before 2.2's spawn, run
      `gh repo view --json isArchived,name -- <repo>`; on a confirmed `isArchived: true`,
      synthesize `stale-close` verdicts for the whole group without spawning an evaluator; on
      any check failure, proceed to 2.2 unchanged.

## 3. Aggregation and output

- [ ] 3.1 Add `write_verdict_file(verdicts, out_dir) -> Path` (JSON) and
      `write_report(verdicts, skipped, out_dir) -> Path` (Markdown: counts by verdict type,
      skipped-via-dedup list, full per-brief table with evidence).
- [ ] 3.2 Add the `evaluate` CLI subcommand: `worktrail-queue-triage evaluate
      [--skip-if-triaged-within-days N] [--agent claude|codex|opencode] [--out-dir DIR]
      [--queue-dir DIR] [--json]`. Wires 1.3 → 2.x per group → 3.1. Prints the report path and
      a one-line summary (groups evaluated, briefs skipped, verdict counts).

## 4. Apply step

- [ ] 4.1 Add `resolve_duplicate_targets(verdicts) -> List[Verdict]` implementing the
      dangling-duplicate-of rule: a `duplicate-of` verdict whose target isn't `keep`/absent in
      the same batch is downgraded to a no-op with a logged warning (spec's "Duplicate-of
      verdicts resolve safely" requirement).
- [ ] 4.2 Add `apply_verdicts(verdicts, *, confirm) -> List[dict]` (action log): for each
      non-`keep` verdict post-4.1, when `confirm` is true, execute via `work_queue.claim()` +
      `work_queue.done(..., note=evidence)` for `stale-close`/`duplicate-of`, or an in-place
      `## Triage <run-date>` body append for `needs-update`; when `confirm` is false, only log
      the planned action. `keep` verdicts are always a no-op.
- [ ] 4.3 Add the `apply` CLI subcommand: `worktrail-queue-triage apply --verdict-file PATH
      [--confirm] [--json]`. Loads the verdict file, runs 4.1 → 4.2, prints the action log.

## 5. Packaging

- [ ] 5.1 Add `worktrail-queue-triage = "worktrail.workqueue.queue_triage:main"` to
      `pyproject.toml`'s `[project.scripts]`, alphabetically placed among the other
      `worktrail-*` entries.
- [ ] 5.2 Add the recommended-cadence note (monthly / pre-drain-weekly, not nightly — ~1M
      tokens per full run) to the module docstring, matching `drain.py`'s docstring
      precedent.

## 6. Tests

- [ ] 6.1 `tests/workqueue/test_queue_triage.py`: grouping (repo grouping, null-repo
      collapsing), dedup-marker detection (recent vs. stale `## Triage` section).
- [ ] 6.2 Verdict parsing: well-formed verdict, malformed output falls back to `keep` with raw
      text retained, missing-evidence falls back to `keep`.
- [ ] 6.3 Archived-repo short-circuit: confirmed archival synthesizes group-wide
      `stale-close` without spawning; a `gh` failure falls through to normal per-brief
      evaluation (fake the `gh` call, don't hit the network).
- [ ] 6.4 Apply step: `--confirm` false is a pure dry run (assert no filesystem mutation);
      `--confirm` true executes `stale-close` (claim+done with note) and `needs-update`
      (in-place append) against a temp `$WORK_QUEUE_DIR` fixture.
- [ ] 6.5 Dangling `duplicate-of` resolution: target verdicted non-`keep` in the same batch
      downgrades the referencing verdict to a no-op with a logged warning.
- [ ] 6.6 Report/verdict-file output: counts in the Markdown report match the JSON file
      exactly for a synthetic multi-group run.

## 7. Verification

- [ ] 7.1 [cleanup] `PYTHONPATH=src pytest -q` green.
- [ ] 7.2 [cleanup] `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` green.
- [ ] 7.3 [cleanup] `pytest tests/test_plugin_surface.py` green (no skill/plugin surface
      touched by this change, but the new console script must resolve cleanly for any doc that
      might reference it later).

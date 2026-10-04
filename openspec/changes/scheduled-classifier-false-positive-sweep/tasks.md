## 1. The baseline and the reportable-cluster rule

- [ ] 1.1 Add `src/worktrail/router/classifier_false_positive_sweep_delta.py`: `load_baseline(path)`
      and `save_baseline(path, snapshot)` for a JSON map from a cluster key
      (`<origin>:<expected>-><predicted>`) to the count and ISO time the cluster was surfaced, and
      a pure `reportable_clusters(clusters, baseline)` returning the clusters whose key is absent
      from the snapshot, ordered by `count` descending then by the route pair so a run's filing
      order is deterministic. A missing snapshot is the empty baseline, never an error. Cover it in
      `tests/router/test_classifier_false_positive_sweep_delta.py`: a missing baseline reports
      every cluster, a recorded key is excluded, ordering is stable for equal counts, and a
      snapshot round-trips through save then load. Do not put the queue lookup, the brief write, or
      any replay in this module -- it is the pure rule the engine consumes.
      (Requirements: Sweep files one deduplicated brief per newly surfaced cluster)
      files: src/worktrail/router/classifier_false_positive_sweep_delta.py, tests/router/test_classifier_false_positive_sweep_delta.py

## 2. One brief per surfaced cluster

- [ ] 2.1 Add `src/worktrail/router/classifier_false_positive_sweep_brief.py` with
      `file_cluster_brief(...)` writing exactly one brief into `<queue_base>/queue/` for one
      cluster: frontmatter rendered through `shared.brief_frontmatter.serialize_frontmatter()`
      with `id`, `created`, `focus`, `repo`, `remote: null`, `status: queued`,
      `drift-source: classifier-false-positive-sweep`, and
      `classifier-cluster: <origin>:<expected>-><predicted>`; and a body carrying the counted
      old-vs-new delta, the no-signal and mis-weighted counts, the high-confidence count, the
      audit's `actionable` flag, the pinned replay settings (`state`, `resumable_state`, and the
      withheld-hint condition), and up to a small fixed number of sample focus texts, truncated.
      Validate the written file with `validate_brief(required=("id", "status", "focus"))` and raise
      on failure, so a bad write fails the run instead of landing in the queue. Write no
      `recommended-route` field. Cover it in
      `tests/router/test_classifier_false_positive_sweep_brief.py`: one file per call, every marker
      and evidence field present, the frontmatter canonical (`is_canonical_style()` true, and
      `workqueue.check_corpus_style.scan_corpus()` reports no finding for the written brief), and a
      validation failure raising rather than leaving a partial file.
      (Requirements: Sweep files one deduplicated brief per newly surfaced cluster)
      files: src/worktrail/router/classifier_false_positive_sweep_brief.py, tests/router/test_classifier_false_positive_sweep_brief.py

## 3. The unresolved-brief lookup

- [ ] 3.1 Add `src/worktrail/router/classifier_false_positive_sweep_dedup.py` with
      `find_unresolved_cluster_brief(cluster_key, queue_base)`, returning the path of the first
      brief whose `drift-source` is `classifier-false-positive-sweep` and whose
      `classifier-cluster` equals `cluster_key`, or None. Any matching brief in `queue/` counts as
      unresolved; a matching brief in `picked/` counts unless its `status` is `done` or
      `superseded`. Read frontmatter through `shared.brief_frontmatter.read_frontmatter()` rather
      than re-parsing YAML. Cover it in
      `tests/router/test_classifier_false_positive_sweep_dedup.py`: a match in `queue/`, a match in
      `picked/` with `status: picked`, a `picked/` brief with `status: done` that does not block, a
      brief discussing the same route pair but carrying neither marker (no match), a brief with the
      marker but a different sweep's `drift-source` (no match), and a missing queue root returning
      None.
      (Requirements: Sweep files one deduplicated brief per newly surfaced cluster)
      files: src/worktrail/router/classifier_false_positive_sweep_dedup.py, tests/router/test_classifier_false_positive_sweep_dedup.py

## 4. The sweep engine, its CLI, and the console script

- [ ] 4.1 Add `src/worktrail/router/classifier_false_positive_sweep.py`, the composer and its CLI.
      (a) A `replay` step that always runs `classifier_coverage.audit_coverage()` over the live
      queue (`--queue-dir`, default `$WORK_QUEUE_DIR` or `~/work-queue`) with the runs root
      (`--runs-dir`, default `worktrail_home()/runs`), and, when `--corpus` names a
      `classifier_corpus.json`-shaped fixture, materializes its `items` into a scratch queue of
      brief files and replays that too -- tagging each source's clusters with `origin` `live` or
      `corpus` and tagging every cluster key with that origin. A missing or unreadable `--corpus`
      is recorded as skipped, never raised. Pass the audit its pinned inputs (`state` defaulting to
      the audit's replay state, `resumable_state` default `unknown`, and no handoff hint or PR
      states); make no network, `gh`, or model call.
      (b) `run_sweep(...)` composing the building blocks: take a non-blocking exclusive `flock` on
      `--lock-file` first and, when it is held, return immediately reporting `skipped_overlap` with
      nothing done; otherwise load the baseline (`--baseline`, default
      `worktrail_home()/classifier-false-positive-sweep/baseline.json`), compute the reportable
      clusters (`_delta`), and for at most `--max-briefs` (default 5) of the largest: skip and
      record when `_dedup` finds an unresolved brief, else file one brief (`_brief`) through
      `--repo` (default the installed checkout's own root, resolved file-relative as
      `queue_triage` does) and record it in the baseline; leave every unfiled overflow cluster out
      of the baseline so it stays reportable. With no baseline file present, record the current
      clusters and file nothing. `--dry-run` files nothing and does not write the baseline. Write
      the baseline only on a completed, non-dry run.
      (c) `main(argv)`: the flags above plus `--json`, printing the JSON record or a short human
      summary of corpora scanned, clusters reportable, briefs filed, clusters skipped, and overlap
      state; exit 0 for a completed run (including one that filed nothing or was skipped as an
      overlap) and non-zero only when a queue write or the baseline write fails.
      (d) Register `worktrail-classifier-false-positive-sweep = "worktrail.router.classifier_false_positive_sweep:main"`
      in `pyproject.toml`'s `[project.scripts]`. Give the module the standard executable shebang
      and file mode its sibling router modules carry.
      In `tests/router/test_classifier_false_positive_sweep.py`, exercise `run_sweep()` and the CLI
      end-to-end on a hermetic queue under a temp root: a live corpus with a new cluster files one
      brief and records it, a second run files nothing, an unresolved brief suppresses a second
      filing, the per-run cap bounds filing while the overflow stays reportable next run, a
      `--corpus` fixture files a `corpus`-tagged cluster distinct from a `live` one, a
      missing `--corpus` and a missing `--queue-dir` are handled, a held lock yields
      `skipped_overlap` with no writes, `--dry-run` writes neither brief nor baseline, and the exit
      codes are 0 on completion and non-zero on a queue write failure.
      (Requirements: Sweep replays the live queue and each supplied corpus through the shared
      audit; Sweep files one deduplicated brief per newly surfaced cluster; A sweep run is
      single-flight, non-mutating under dry-run, and machine-readable; The sweep is offline and
      deterministic over the audit's pinned replay inputs)
      depends: 1.1, 2.1, 3.1
      files: src/worktrail/router/classifier_false_positive_sweep.py, tests/router/test_classifier_false_positive_sweep.py, pyproject.toml

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/router/test_classifier_false_positive_sweep.py
      tests/router/test_classifier_false_positive_sweep_delta.py
      tests/router/test_classifier_false_positive_sweep_brief.py
      tests/router/test_classifier_false_positive_sweep_dedup.py`, then the full
      `PYTHONPATH=src python3.14 -m pytest -q`, `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`, `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .` and `python3.14
      scripts/ci/check_shebang_exec_bits.py`. Confirm the installed console script resolves
      (`worktrail-classifier-false-positive-sweep --help`) and, against a scratch
      `WORK_QUEUE_DIR` seeded with briefs that disagree with their recorded routes, that a real run
      files one deduplicated brief per new cluster carrying the counted delta and a sample text,
      that an immediate second run files nothing, and that an overlapping run reports
      `skipped_overlap` without writing. Finally run `openspec validate
      scheduled-classifier-false-positive-sweep --strict` and `worktrail-compile
      openspec/changes/scheduled-classifier-false-positive-sweep`.
      (Requirements: Sweep replays the live queue and each supplied corpus through the shared
      audit; Sweep files one deduplicated brief per newly surfaced cluster; A sweep run is
      single-flight, non-mutating under dry-run, and machine-readable; The sweep is offline and
      deterministic over the audit's pinned replay inputs)
      depends: 4.1

## Why

Every root-relative `contracts/*.md` citation in this repo's sources points at a document
that has never existed here. The paths came in with the extraction from `developer-kit`
(`git log -S 'contracts/'` on a citing file bottoms out at the Phase 1a extraction commit,
and `git log --all --diff-filter=D -- 'contracts/*'` is empty -- nothing was ever deleted
because nothing was ever added), and `find` across the checkout finds none of the cited
basenames. A dangling pointer in a comment or docstring is worse than no pointer: a reader
-- human or agent -- spends a lookup on a nonexistent file, and a worker told to read
`contracts/x.md` cannot.

The defect class has already regressed once: the `run_record.py` citation was dropped by
the `active-conflicts-staleness-reconciliation` change ("update the docstring's JSON-shape
description in place rather than authoring that file"), then re-added verbatim four
commits later. A cleanup without a guard does not hold.

Twelve sites cite the phantom tree -- eleven in `src/` (`coordinator.py`, `dispatch.py`,
`live.py` x4, `spec_sync_sweep_brief.py`, `spec_sync_sweep_checkbox_brief.py`,
`run_record.py`, `taskformats/devkit/source.py`, `workqueue/dist_tag_watch.py`) and one in
`tests/orchestrator/test_precheck.py`; two of them wrap the filename across a comment
continuation. The same sweep also finds legitimate `contracts/` uses that must be left
alone: the devkit spec-format convention (`spec.md + data-model.md + contracts/` in a
worker-prompt string, the `contracts/*.md` glob in `classify_handoff.py`) and the real
fixture path in `drain/summary_contract.py`.

## What Changes

- Resolve every dangling citation in place: drop the dead path where the surrounding prose
  already states the behavior, and state the referenced contract where the pointer carried
  it. No repo-root `contracts/` tree is authored -- this repo's contract homes are OpenSpec
  specs, `docs/`, and the module docstrings themselves, and the earlier archived change set
  the precedent. The `run_record.py` entry additionally gets corrected: it claims the
  command "prints a JSON array" while `cmd_active_conflicts` prints the
  `{"live": [...], "stale": [...]}` partition.
- Add `tests/test_no_dangling_contracts_doc_refs.py`, a structural guard in the
  `tests/test_no_bare_head_ctx_default.py` shape: it scans Python sources under `src/` and
  `tests/` for root-relative `contracts/<name>.md` citations -- including a filename split
  across source lines -- and fails naming file and line when the cited path does not exist
  at the repository root. Prefixed paths (`docs/specs/<id>/contracts/x.md`,
  `../contracts/x.md`), globs (`contracts/*.md`), bare directory mentions, and non-Markdown
  targets (`.fixtures/contracts/*.json`) are not citations of this class and stay unflagged.
- Leave the three legitimate `contracts/` uses untouched.

## Capabilities

### New Capabilities
- `source-contracts-citation-integrity`: source comments and docstrings may only cite a
  root-relative `contracts/` document that exists, enforced by a structural guard; the
  run-record `active-conflicts` description matches the output its command prints.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/{coordinator,dispatch,live}.py`,
  `src/worktrail/router/{run_record,spec_sync_sweep_brief,spec_sync_sweep_checkbox_brief}.py`,
  `src/worktrail/taskformats/devkit/source.py`, `src/worktrail/workqueue/dist_tag_watch.py`
  -- comment and docstring text only, no behavior change.
- `tests/orchestrator/test_precheck.py` (one class docstring);
  `tests/test_no_dangling_contracts_doc_refs.py` (new).
- Out of scope: the same defect class for other doc paths (a separate handful of
  `docs/specs/research/*.md` citations also name files this repo lacks); this change covers
  the `contracts/` citation class the brief evidence defined.
- No CLI arguments, external dependencies, persisted data, or version bump.

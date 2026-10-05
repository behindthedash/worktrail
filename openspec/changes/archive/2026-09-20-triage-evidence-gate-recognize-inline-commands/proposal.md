## Why

Intake triage downgraded a `work-directly` verdict that carried a reproduced command. Brief
`20260920-162359-triage-evidence-gate-false-negative` records the run: the evaluator's evidence
quoted a concrete inline reproduction --

```
python3 -c "from worktrail.router.dashboard import _resolve_repo_dir; _resolve_repo_dir('x'*300, None)" -> OSError Errno 36
```

-- plus `file:line` locations, and the apply gate still recorded
`evidence does not cite a test, check, or command, and no premise-check entry was confirmed --
downgraded to keep`.

Confirmed by inspection in this checkout 2026-09-20:

- `_work_directly_downgrade_note` (`src/worktrail/workqueue/queue_triage.py:2526`) is the exact
  text quoted above, reached only when `_work_directly_accepted`
  (`src/worktrail/workqueue/queue_triage.py:631`) fails *both* halves.
- `_REPRODUCTION_EVIDENCE_RE` (`src/worktrail/workqueue/queue_triage.py:194`) has branches for
  `pytest`, `make`, `npm`/`yarn`, `go test`, `cargo test`, `gh`, `git`, `grep -`, and the
  "reproduces/confirmed via" phrasings -- and no branch for a Python interpreter invocation. An
  inline `python3 -c ...` with its output is therefore not recognised as citing a command. That
  is the false negative.
- The premise check could not rescue the verdict either: the brief cited the bare basenames
  `queue_triage.py` and `dashboard.py`, and `_check_path`
  (`src/worktrail/workqueue/premise_check.py:225`) resolves a candidate only as
  `repo_path / candidate` -- relative to the repo root -- so a real file living at
  `src/worktrail/workqueue/queue_triage.py` is reported `path does not exist`.

Both halves of the accept rule failed on a brief whose defect was in fact reproduced, so the
gate that exists to stop unverified work instead threw away verified work.

The brief also reports prose tokens (`OSError/ValueError`, `A/B` pairs) being misread as paths.
That is already in flight as the active change `premise-check-path-needle-shape-filter`, whose
proposal names those cases directly; this change deliberately does not touch it.

## What Changes

- `_REPRODUCTION_EVIDENCE_RE` gains a branch for an inline interpreter reproduction: `python`,
  `python3`, or `py` followed by a `-c`/`-m` flag (`python3 -c "..."`, `python -m pytest` is
  already covered by the `pytest` branch but stays matched). The branch still requires the flag,
  so prose such as "the python code path" does not qualify -- the same discipline the existing
  `grep -` and `git <subcommand>` branches use.
- `_check_path` resolves a *bare basename* needle (no `/` in it) that does not exist at the repo
  root by looking it up anywhere in the checkout with `git ls-files`. A unique hit confirms and
  reports the resolved path; several hits confirm and report the count with one example; no hit
  keeps today's `path does not exist` outcome. A needle that already contains a `/` is unchanged.
- Nothing else about the accept rule moves: a verdict with neither inline-command evidence nor a
  confirmed premise entry is still downgraded to `keep`, and the downgrade note is unchanged.

## Capabilities

### New Capabilities
- `triage-evidence-inline-commands`: an inline reproduced command counts as reproduction
  evidence, and a bare-basename path needle resolves anywhere in the checkout.

### Modified Capabilities

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`_REPRODUCTION_EVIDENCE_RE`).
- `src/worktrail/workqueue/premise_check.py` (`_check_path` basename fallback).
- `tests/workqueue/test_queue_triage.py`, `tests/workqueue/test_premise_check.py`.
- Fewer false downgrades of `work-directly`; no verdict that is accepted today becomes rejected.
- Touches `_check_path` alongside the in-flight `premise-check-path-needle-shape-filter`; the two
  are disjoint (shape gate on non-existent prose tokens vs. basename resolution of a real file),
  but whichever lands second rebases on the other.

## Why

The front door claims batches; the executor consumes one brief.

Batch claiming is real, shipped, and used. `claim_batch()`
(`src/worktrail/workqueue/work_queue.py:1412`) claims a primary plus each companion through
the same single-brief rename arbiter, stamps every claimed companion
`batch-primary: <primary-stem>` (`:1444`), and records the claimed companion stems on the
primary's own frontmatter (`batch:`, `:1455`). The front door drives it from three entry
points -- `references/batch-consumption.md` (the `claim` action), `references/auto-mode.md`
steps 2-3, and `SKILL.md`'s best-practices list -- and the dashboard renders companions folded
under their primary (`router/dashboard.py:2512-2567`).

The consumption the front door promises does not exist. `batch-consumption.md:62-65` tells
whoever runs the claimed brief: "Read every claimed brief, then treat their union as ONE
request for the rest of the flow: one classification (Phase 5), one run record (list every
brief id in `handoffs_consumed`), one worktree/PR -- unless a companion genuinely classifies
to a different route or repo, in which case release it"; `:69-70` requires each brief to be
marked done (or released) individually; `auto-mode.md:54` repeats the contract. But the
executor is handed exactly one brief, and consumes exactly that one:

- The dispatch contract carries `handoff:<id>` -- a single id
  (`skills/worktrail-go/SKILL.md:992`); no batch token, and the executor's argument table has
  no batch form.
- `worktrail-sdd-workflow` has no batch path at all: `grep -rni batch
  skills/worktrail-sdd-workflow/` hits only the unrelated "batch independent commands in one
  message" (`SKILL.md:34`).
- The handoff-seed sub-flow (`#handoff-seed`, `references/subagent-prompts.md:1609`) is
  single-brief end to end: Step 3 claims one brief, Step 4 maps one brief through
  `worktrail-handoff-seed seed "<path>"`, Step 7 closes one brief.
- The seed mapper's docstring maps "the path to a single handoff brief", its documented output
  shape has no batch key, and it never reads the `batch:` list the claim wrote (`grep -rn batch
  src/worktrail/router/handoff_seed.py` -- no match).
- `handoffs_consumed` -- the field the promise names -- has no writer in code at all. It is a
  run-record default (`router/run_record.py:527`) read as "actual" consumption evidence by
  `router/classifier_coverage.py:191,223`, and no document under `skills/worktrail-sdd-workflow/`
  names it, so it is written only when an agent happens to follow the front door's prose.

Live consequence, from the brief this change implements
(`20261005-083823-batch-companions-never-consumed`): an auto-mode run auto-folded two
`related-link` companions via `claim-batch`; both were claimed and stamped, and their scope
reached the run only through the run record's `request_summary` plus `handoffs_consumed` --
a channel the brief calls undocumented, because nothing in the executor requires reading
either. The ordinary outcome when an agent does not happen to read them is companions sitting
in `picked/` with no work done and no signal until someone notices.

No fix has landed and no other active change covers it: `git log --all --since=2026-10-05 -i
--grep=batch` is empty, and before this proposal no non-archive
`openspec/changes/*/proposal.md` mentioned batch consumption or companions.

## What Changes

- **The seed mapper carries the claimed batch.** `worktrail-handoff-seed seed PATH` reads the
  primary brief's `batch:` frontmatter list -- the companion stems the claim recorded -- and
  emits a `batch` list on the seed output: one member per stem, in declared order, each
  carrying the same per-brief fields the seed already maps (`id`, `path`, `focus`, `repo`,
  `feature_idea`, `constraints`) plus an `error`. A stem that no longer resolves to a brief
  file beside the primary (a released companion, a hand-move) becomes an error member instead
  of aborting the seed. A brief without a `batch:` list emits `"batch": []` and an otherwise
  identical seed, so the single-brief path is unchanged. The mapper stays read-only: it
  follows the primary's own declared links and still never lists, moves, or stamps the queue.
- **The handoff-seed flow consumes the union as one request.** `#handoff-seed` Step 4 loads
  every member together with the primary and folds them into the run's single request -- one
  classification (fed the primary's route evidence), one run record, one worktree/PR -- with
  each consumed companion's focus/suggested-approach/constraints labeled by its brief id. A
  member that cannot ride the run (an error member, a different `repo`, or route evidence
  naming a different route) is excluded and released back to the queue with `--by` rather than
  forced in, and the exclusion is reported. Step 7 closes each consumed brief individually, and
  releases -- never marks done -- a companion whose scope did not actually land.
- **The run record names every brief the run consumed.** Step 7 records the consumed ids
  (primary plus folded companions) in the run record's `handoffs_consumed` as a YAML list via
  `worktrail-run-record set-list` -- never `set`, which stores a JSON string as a scalar (the
  20261003-204455 incident documented at `run_record.py:558`). A brief excluded from the union
  is not consumed and does not appear, so the field keeps meaning "executed under this run"
  for `classifier_coverage.py`'s actual-route join.
- **The front-door references name the consumption path.** `batch-consumption.md` step 4 and
  `auto-mode.md` step 5 point at where the claimed batch is read back (the primary's `batch:`
  frontmatter, via `worktrail-handoff-seed` and the executor's `#handoff-seed`) and where
  `handoffs_consumed` is written, so the promise's reader and its implementation stay linked.
- Non-goals: no change to `claim-batch`'s claim, atomicity, or stamps; no dispatch-contract
  token (the batch is already on disk in the primary's own frontmatter, and a parallel channel
  could drift from it between claim and dispatch); no change to `handoffs_consumed`'s
  consumers or the classifier-coverage ratchet; no new command, script, or config key.

## Capabilities

### New Capabilities

- `handoff-batch-consumption`: the handoff-seed flow consumes the batch the claim folded in --
  the seed's `batch` members, the single union request, the run record's `handoffs_consumed`
  set, and per-brief closure.

### Modified Capabilities

<!-- None. The claim side (`claim-batch`, its stamps, the dashboard grouping) keeps its
     contract; the fix is entirely on the consumption side. -->

## Impact

- `src/worktrail/router/handoff_seed.py` -- the batch pass, the `batch` key on the seed
  output, and the docstring's canonical field table.
- `tests/router/test_handoff_seed.py` (unit) and `tests/router/test_handoff_seed_e2e.py`
  (claim-batch through the real CLI).
- `skills/worktrail-go/references/subagent-prompts.md` -- `#handoff-seed` Steps 4 and 7.
- `skills/worktrail-go/references/batch-consumption.md` and `.../auto-mode.md` -- the
  consumption path and the consumed-set wording.
- `tests/test_plugin_surface.py` -- a lockstep guard tying the promise, the procedure, and the
  seed mapper to the same `batch` seam.
- No change to `work_queue.py`, the dispatch contract, the run-record schema, any console
  script's interface, or any policy key. (Work-queue brief
  `20261005-083823-batch-companions-never-consumed`.)

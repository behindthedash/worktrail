## Context

The stale-bookkeeping probes in `router/dashboard.py` operate on the row shape
`_load_tasks` builds from each `TASK-*.md`'s frontmatter. Only `files` carries file
scope. The fleet has two authoring schemas for output paths: the flat `files: [...]`
list and the richer `provides:` block of `{file, symbols, type}` maps; a task written
with the latter alone loads with `files: []` and is invisible to every probe.

## Decisions

### Normalise at parse time, in `_load_tasks`, not in each probe

Adding a `provides` branch to `_pending_impl_stale` and `_pending_tail_stale` would
duplicate the fallback in two places and still leave `_count_tasks`' empty-cleanup
rule reading the old shape. Filling the row's `files` once in `_load_tasks` means
every consumer (both probes, `_count_tasks`, the weekly sweep's reuse of
`detect_stage`) sees the same scope with no probe-side changes, and the row shape
stays exactly what it is today.

### `files:` wins; `provides:` is a fallback only when `files` is empty

When a task declares both, `files:` is the authored scope and stays authoritative.
Unioning the two would let a `provides:` entry that was never created (a
speculative output) hold a task out of stale even though every `files:` entry
shipped, which is the opposite of the failure being fixed. Fallback-only keeps the
121 `files:` tasks' behaviour bit-for-bit unchanged.

### Read `provides` with `yaml.safe_load` on the frontmatter block, not the devkit parser

`taskformats/devkit/source.py:parse_frontmatter` deliberately does not support nested
maps (its own comment says so) and collapses a `provides:` block of maps into
`['file: src/a.py']`. Extending that parser touches the orchestrator's task source
and every other caller for a dashboard-only need. Instead, `_load_tasks` calls a
small helper that slices the same `---` block, runs `yaml.safe_load` on it (pyyaml is
already the package's one runtime dependency), and returns
`[entry["file"] for entry in provides if isinstance(entry, dict) and entry.get("file")]`
as stripped strings in declared order, de-duplicated. Any `yaml.YAMLError`, a
non-list `provides`, or an entry without a string `file` yields `[]` for that
block, so a malformed task degrades to today's behaviour rather than crashing the
dashboard scan (which the weekly sweep runs unattended across repos).

## Risks

- A `provides:`-only task whose output files have all shipped will now flip a spec
  to `stale-bookkeeping` where it previously showed `ready-to-implement` or
  `tail-pending`. That is the intended correction; `confirm & close` remains a
  human step, and the existing `stale-sweep: exempt` opt-out still applies.

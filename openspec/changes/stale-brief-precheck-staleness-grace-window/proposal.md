## Why

`check_brief_staleness.py` answers one question -- "did the work this brief describes already
land, since it was captured?" -- by extracting evidence probes from a brief's focus text and
searching the base branch's history and merged pull requests for anything matching them at or
after the brief's `created:` timestamp.

That exact-timestamp boundary produces a false negative in a tight race: a delivering PR can
merge moments before a duplicate brief is captured in the same session. Observed directly:
`behindthedash/worktrail` PR #325 merged at `2026-08-12T13:31:37-07:00`; a duplicate brief
(`20260812-133233`) was captured 56 seconds later, at `13:32:33-07:00`, describing the exact
scope PR #325 had just shipped. `worktrail-check-brief-staleness` reported `checked: true,
matches: [], pull_requests: []` -- a definite "clean" result -- because the merged-PR exclusion
(`merged_dt >= since_dt`) and the git history search's `--since=<created>` window both treat
"merged/committed one second before capture" identically to "merged a year before capture." A
human doing the same check by hand caught it immediately; the automated guard did not, and the
gap is structural, not incidental -- any same-session race this tight will reproduce it.

A second, independent weakness compounded the miss on that same brief: probe extraction pulled
exactly one path probe (`wheel/sdist`, itself only path-shaped by accident) and zero symbol
probes from the brief's prose, even though the brief was later confirmed to closely paraphrase
the delivering PR's own commit subject ("installed drain provider smoke"). Investigation traced
this to a real, separate gap: any backtick-quoted or unquoted CLI-flag-shaped token (`--json`,
`--tier-map`) is silently dropped by extraction today -- it satisfies neither the path-token
rule (no `/`, no recognized extension) nor the symbol-token rule (`_SYMBOL_RE` requires a
leading letter/underscore, rejecting the leading `-`). Flags are exactly the kind of
distinctively-shaped, low-false-positive token the existing probe-extraction design already
admits for snake_case symbols; they were never wired up.

## What Changes

- Widen the search boundary used by both the base-branch history search and the merged-PR
  exclusion filter by a fixed, documented grace window (`RACE_GRACE_SECONDS`) applied *before*
  the brief's `created:` timestamp, so a same-session race that merges/commits moments before
  capture still surfaces as evidence. The check remains fully advisory (see the existing
  "Evidence Is Surfaced To The Operator, Never Auto-Applied" requirement, unchanged) -- widening
  the window trades a small chance of surfacing an unrelated older commit for closing a
  demonstrated false-negative gap; a human still judges every match.
- Extend `extract_probes()` to recognize CLI-flag-shaped tokens (`--flag-name`, GNU long-form)
  as a new admitted symbol-probe shape, both backtick-quoted and unquoted, following the same
  narrowly-admitted pattern already used for snake_case identifiers. Flag probes are searched
  exactly like existing symbol probes (`git log -S` and `git log --grep`, since a new flag
  typically appears as a literal string in an `argparse` call and often in a commit's subject).

Deliberately out of scope: fuzzy multi-word technical-phrase extraction from a brief's ordinary
prose. The existing spec's own design draws a hard line -- "ordinary unquoted prose is not a
symbol probe" -- specifically because unbounded phrase matching against `git log --grep` has no
deterministic extraction rule to specify or test, and trades a bounded, human-judged check for
an unbounded, noisy one. See `design.md` Decisions for the full reasoning; this is a considered
exclusion, not a deferral of named work.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: the history-search and merged-PR-lookup requirements gain a grace
  window before the brief's capture time; the probe-extraction requirement gains CLI-flag-shaped
  tokens as a third admitted symbol-probe source.

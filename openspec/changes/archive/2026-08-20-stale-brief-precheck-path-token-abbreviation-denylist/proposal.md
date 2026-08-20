## Why

`_is_path_token()` in `check_brief_staleness.py` accepts any token ending in a `.` followed by
1-10 alphanumeric characters as a path-probe file extension, with no denylist. Common prose
abbreviations -- `e.g`, `i.e`, `etc.`, `vs.`, `a.k.a.` -- match that shape (`e.g` looks like a
bare filename with extension `.g`) and get misclassified as path probes. Those bogus probes are
then searched via `gh pr list --search`, and any unrelated merged PR that happens to match
returns as false "evidence" that the brief's work is already delivered. This false-fires the
`/go` Phase 5.5 brief-staleness guard on any brief whose focus prose contains a common
abbreviation -- including completely fresh briefs -- and in `AUTO_MODE` (no `AskUserQuestion`
available) silently files a `blocked_product_decision` and a human decision record. Observed
live 2026-08-18 on brief `20260814-021836-epic-001-dependabot-consolidation-automation`.

## What Changes

- Add a denylist of common non-path prose abbreviations (`e.g`, `i.e`, `etc`, `vs`, `a.k.a`, and
  similar) to `_is_path_token()`, checked case-insensitively against the token with any trailing
  punctuation already stripped, so these tokens are never extracted as path probes regardless of
  which extension-length rule would otherwise match them.
- Update the `stale-brief-precheck` spec's "Evidence Probe Extraction From Brief Text"
  requirement to document the new denylist rule, and add a scenario covering `e.g.` (and similar
  abbreviations) not being extracted as a path probe, while legitimate short extensions (`.py`,
  `.md`, `.sh`) still qualify.
- Keep the existing "Bare filename with an extension is a path probe" scenario passing --
  legitimate bare filenames with short extensions are unaffected.

## Capabilities

### Modified Capabilities
- `stale-brief-precheck`: the "Evidence Probe Extraction From Brief Text" requirement gains a
  denylist of common non-path prose abbreviations that `_is_path_token()` must reject, so
  abbreviation-shaped tokens no longer qualify as path probes.

## Impact

- `src/worktrail/router/check_brief_staleness.py` (`_is_path_token()`): add the denylist check.
- `tests/router/test_check_brief_staleness.py`: add coverage for abbreviations being rejected
  and legitimate short extensions still being accepted.
- `openspec/specs/stale-brief-precheck/spec.md`: delta spec updates the "Evidence Probe
  Extraction From Brief Text" requirement.
- No caller-visible interface changes -- `extract_probes()`'s signature and return shape are
  unchanged; only which tokens qualify as path probes changes.

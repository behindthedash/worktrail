## Context

`check_brief_staleness.py` (spec: `stale-brief-precheck`) is a fail-open, best-effort guard run
during `/go` Phase 5.5 for every brief-sourced dispatch. It answers "did the work this brief
describes already land?" by extracting bounded evidence probes from the brief's focus text and
searching the base branch's history and merged PRs for anything matching them since the brief's
`created:` timestamp.

Two independent gaps were found live against `behindthedash/worktrail` brief `20260812-141212`,
itself filed after a near-miss where a duplicate brief (`20260812-133233`) was claimed and
started dispatch before a human noticed, mid-worktree-setup, that PR #325 had shipped the exact
scope 56 seconds before the brief was captured.

## Goals / Non-Goals

**Goals:**
- Close the specific false-negative class demonstrated live: a delivering commit or PR landing
  in the same narrow window as (moments before) the brief's capture must surface as evidence,
  not silently pass as "nothing found."
- Extend probe extraction to a token shape (CLI flags) that is genuinely distinctive and
  low-false-positive, following the exact admission pattern the existing spec already uses for
  snake_case symbols, rather than inventing a new, broader mechanism.
- Keep the check's existing contract unchanged: fail-open, advisory only, never auto-closes a
  brief, bounded per-kind probe caps, bounded subprocess timeouts.

**Non-Goals:**
- Fuzzy multi-word phrase extraction from ordinary prose. See Decisions below.
- Changing the check's caller contract (`checked`/`matches`/`pull_requests`/`warning` shape) or
  its route/dispatch gating -- both are owned by the "Staleness Check Covers Every Brief-Sourced
  Dispatch" requirement, unchanged by this proposal.
- Retroactively re-evaluating brief `20260812-133233` -- it is already closed as
  already-delivered by a human decision; this change hardens the detector for future briefs, not
  that one.

## Decisions

**A single grace-window constant applied symmetrically to both search boundaries.** The false
negative in the motivating incident had two contributing filters -- the git history search's
`--since=<created>` and the merged-PR lookup's `merged_dt >= since_dt` -- and both needed
widening for the fix to be complete: fixing only the PR filter would still miss a same-session
delivering *commit* that the git history search excludes on the same boundary. Rather than two
independently-tuned windows, one `RACE_GRACE_SECONDS` constant computes a single effective
search-start time (`created - grace`) used by both, so the two searches stay in sync by
construction instead of by convention.

**300 seconds (5 minutes), not a smaller or larger value.** The observed race was 56 seconds.
5 minutes comfortably covers same-session races (a human or an agent capturing a duplicate brief
within moments of a sibling run's PR merging) without being so wide that it routinely surfaces
unrelated older commits that merely happen to touch a probed path or symbol shortly before an
unrelated brief was filed. The check remains advisory regardless -- a wider window trades a
larger set of candidates for a human to judge against a smaller chance of a true miss, and the
existing "Evidence Is Surfaced To The Operator, Never Auto-Applied" requirement is what makes
that trade safe to make generously rather than needing to be exact.

**CLI-flag tokens are folded into the existing "symbols" probe kind, not a new fourth kind.**
A flag probe is searched exactly like a symbol probe -- `git log -S` (a new flag typically
appears as a literal string in an `argparse.add_argument()` call, so its occurrence count
changes when it's added) and `git log --grep` (flags are commonly named in commit subjects,
e.g. "add --tier-map flag"). Introducing a fourth top-level probe kind would touch the JSON
result shape, every caller of `check()`/`extract_probes()`, the CLI's `--json` output, and every
document that names the current three-kind shape (`brief-staleness-check.md`,
`subagent-prompts.md#handoff-seed`), for no behavioral gain over reusing the existing "symbols"
list and its existing two search strategies.

**Flag admission requires no backticks, mirroring the snake_case symbol fallback's own
rationale.** The existing spec already reversed an original require-backticks decision for
snake_case symbols after finding a real captured brief with zero backticks and four real
identifiers in plain prose. The same reasoning applies here: a brief describing "the --tier-map
flag" in prose is exactly as common as one backticking it, and requiring backticks would make
flag extraction dead on arrival for the same reason it would have for snake_case.

**Multi-word technical-phrase extraction is excluded, not deferred.** This is a considered
design decision, not a scope cut for later: the existing spec's own "Ordinary unquoted prose is
not a symbol probe" scenario establishes that admitted probes must be *distinctively* shaped --
narrow enough that admitting them cannot make the check noisy. A CLI flag (`--[a-z][a-z0-9-]*`)
and a snake_case identifier are both narrow, low-false-positive regexes with a clear yes/no
answer per token. "Multi-word technical phrase" has no comparable rule: any bounded, testable
definition (a fixed word-count window, a stoplist, a capitalization heuristic) either
under-extracts prose exactly like the motivating brief's ("installed drain provider smoke" is
lowercase, ordinary English) or over-extracts to the point of matching `git log --grep` against
arbitrary word pairs, reintroducing the false-positive risk the existing negative-extraction
rules (task ids, absolute paths, parenthesised call-site lists) were added specifically to keep
out. Implementing it partially, without a rule that can be specified and tested with the same
rigor as the rest of this module, would violate the surgical-edit and no-speculative-flexibility
standard the rest of the codebase holds itself to. If a future brief demonstrates a concrete,
boundable phrase-extraction rule, it is a new proposal against this same spec.

## Risks / Trade-offs

- **A wider search window can surface an unrelated older commit as a false-positive candidate.**
  Accepted: the check has never auto-applied evidence, and the existing "Evidence Is Surfaced To
  The Operator, Never Auto-Applied" requirement is unchanged -- a human judges every match either
  way, and a few extra seconds-old candidates are cheaper than a silent miss.
- **CLI-flag admission could match a flag name that recurs across unrelated commits** (e.g. a
  very common flag like `--json` appearing in many commits). Mitigated by the existing per-kind
  probe cap and by the check remaining a signal source, not a gate -- the same mitigation that
  already applies to a common snake_case identifier today.

## Migration Plan

None. Both changes are internal to `check_brief_staleness.py`'s implementation; the CLI, the
`/go` Phase 5.5 dispatch contract, and every caller-facing shape are unchanged.

## Open Questions

None.

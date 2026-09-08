## Why

Twice, an orchestrator git merge was invoked with a biased merge strategy
option (`-X ours`) and silently discarded real content instead of failing
loud:

- `integrate_one`'s dependency-branch-gone fallback reconciled a stale
  reconstructed start point against the live base with an unconditioned
  `-X ours` merge, reverting a *different* group's already-landed `tasks.md`
  checkboxes and reintroducing duplicate code (PR #414; root-caused in
  `docs/specs/research/integrate-one-dep-branch-gone-fallback-root-cause.md`).
- The same risk class in `live.py`'s squash-merged-dependency carry, where an
  `-X` strategy auto-resolves *every* content-level conflict in the merge, not
  only ones touching the dependency's own files
  (`docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`).

Both were caught by a human diffing after the fact, not by CI. Both are now
fixed: `grep -rn "merge.*-X (ours|theirs)" src/ tests/ .github/` finds zero
live invocations under `src/worktrail/orchestrator/` — `integrate.py:682`,
`integrate.py:1409` and `live.py:2273` now carry comments explaining why
`-X ours` is deliberately NOT used, and archived changes
`2026-08-09-stacked-worktree-conflict-resolution-squash-merged-dependency-carry`
and `2026-08-25-resume-repair-and-checklist-carry` record the fixes.

Nothing enforces that. The prohibition lives entirely in prose comments, which
a future edit can delete or a new merge call site can simply not have read.
The baseline is clean right now, which makes a regression guardrail cheap to
add: it goes in green and stays green.

## What Changes

- A new structural guard test, `tests/test_no_biased_merge_strategy.py`, scans
  `src/worktrail/` for the biased-merge-strategy option shape (`-X`,
  `-Xours`/`-Xtheirs`, `--strategy-option`, and the `ours`/`theirs` merge
  strategies) appearing as a **string literal in executable code** — i.e. as an
  argument actually handed to git.
- The scan reads Python source through `ast`, so the existing explanatory
  comments and docstrings at `integrate.py:682`, `integrate.py:1409` and
  `live.py:2273` — which must keep naming `-X ours` to explain why it is not
  used — do not trip it.
- The guard runs in the already-required `pytest` step of
  `CI: Lint, Test & Build`; no new workflow or required check is added.

## Capabilities

### New Capabilities
- `biased-merge-strategy-guardrail`: a structural CI guard that fails the build
  if a biased git merge strategy option is reintroduced into `src/worktrail/`,
  while leaving the prose that documents the prohibition intact.

### Modified Capabilities
(none — no existing spec constrains how orchestrator merges are invoked)

## Impact

- `tests/test_no_biased_merge_strategy.py` (new)
- No `src/` change: the guarded baseline is already clean, and this change is
  only the enforcement that keeps it clean.
- Out of scope: guarding non-Python surfaces (shell scripts under `scripts/`,
  workflow YAML, skill markdown). No git merge is invoked from those today, and
  a literal-scan over prose-bearing files would fight the same
  documentation-mentions-the-forbidden-flag problem the AST scan solves for
  Python. Also out of scope: any allowlist/opt-out mechanism — see design.md.

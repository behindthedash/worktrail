## Why

The mechanical premise check that runs before every brief-triage evaluation can hang the
evaluator indefinitely. `premise_check.py`'s `_git_grep_whole_string` and
`_git_grep_fragments` (`src/worktrail/workqueue/premise_check.py:174`, `:201`) spawn
`git grep -nIF -e <needle>` with **no subprocess timeout**, and the quoted needles it feeds
them can contain blank lines: `_QUOTED_RE` pairs apostrophes across prose, so a needle can
span paragraphs. Observed 2026-10-05 triaging brief
`20261005-105710-out-of-band-work-falsely`: quoted needle #0 was 1124 characters with two
embedded newlines and a blank middle line (apostrophe-paired from "Task 1.1's own branch
...", not a deliberate quote — re-verified from the brief's focus in this worktree), and its
grep ran >10:21 CPU before being killed; the next needle wedged the same way.

The degeneration is specific to needles containing an empty line, and it is two-sided
(verified in this worktree, 2026-10-05):

- **Pathological cost.** `git grep -nIF -e $'short\n\nshort2'` in this checkout was still
  running when killed at 10s (the defect record measured >60s CPU with stdout discarded);
  `git grep -nIF -e $'zzqxx\n\nwwvvu'` in a scratch repo is not the counterexample it looks
  like — it "finished" only because it printed **every line in the repo** and exited 0.
- **Spurious confirmation.** `-F` with an embedded blank line behaves as an alternation
  containing an empty alternative, which matches every line. `_git_grep_whole_string` reads
  rc=0 plus non-empty stdout as a hit, so were the search to finish it would confirm the
  needle against arbitrary content. A needle only thirteen characters long
  (`short\n\nshort2`) triggers the same class, so a needle-length bound cannot fix it.
  Non-triggers are all fast (~0.1-0.2s): the same needle minus the blank line, a
  single-line 895-character needle, a multi-line needle with no blank line, and each line
  of the needle searched separately.

Every other search in the check is already bounded — command needles run under
`run_premise_check`'s `timeout_s` and are recorded unconfirmed on `TimeoutExpired`
(`premise_check.py:386`). The quoted-string search is the one pattern-driven path with no
bound, and the one brief text can drive into the degenerate class.

## What Changes

- **The quoted-string search runs under the same bounded timeout as the command needles.**
  `_git_grep_whole_string` and `_git_grep_fragments` pass the existing `timeout_s` into
  their `subprocess.run` calls; a quoted search that exceeds it is terminated and its
  needle recorded `confirmed: false` with a timeout detail, mirroring the command-needle
  timeout behavior, and the evaluation proceeds.
- **A quoted needle whose text contains an empty line is not searched at all.** It is
  recorded `confirmed: false` with a detail stating it was not searched because its text
  contains an empty line. An empty-line-containing fixed pattern degenerates in `git grep`
  (the empty alternative matches every line), producing both the pathological cost and the
  spurious confirmation above. The guard is needle-level and runs before any grep, so an
  empty-line needle reaches neither the whole-string nor the fragment search.
- No new CLI knob, policy key, or console script: the bound reuses `run_premise_check`'s
  existing `timeout_s`. `_fragments` splitting semantics (ellipses and `: ` separators),
  the `_basename_matches`/`git ls-files` basename path, and the path and command needle
  paths are unchanged.

## Capabilities

### New Capabilities

<!-- None. This change bounds and guards one existing requirement's quoted-string search. -->

### Modified Capabilities

- `intake-triage`: `Mechanical premise check precedes evaluation` — the quoted-string search
  gains the command needles' bounded timeout (a search exceeding it is terminated, recorded
  `confirmed: false` with a timeout detail, and the evaluation proceeds) and a quoted needle
  whose text contains an empty line is never searched, recorded unconfirmed with a detail
  stating why.

## Impact

- `src/worktrail/workqueue/premise_check.py` — `_git_grep_whole_string()`,
  `_git_grep_fragments()`, `_check_quoted()`, and the `run_premise_check()` quoted branch
  that passes `timeout_s` through.
- `tests/workqueue/test_premise_check.py` — regression coverage: the empty-line needle is
  not searched and not confirmed even when the repo contains its text; a `TimeoutExpired`
  during the quoted search yields `confirmed: false` with a timeout detail; every
  `git grep` subprocess call carries the bounded timeout.
- No behavior change for any needle without an empty line that completes within the
  timeout; no new dependency, console script, policy key, or journal schema.

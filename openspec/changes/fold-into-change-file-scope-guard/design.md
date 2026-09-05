## Context

`_has_valid_target()` (`queue_triage.py:1392-1433`) is the sole mechanical gate a
`fold-into-change` verdict passes through before `parse_verdicts()` accepts it as-is. It checks
`target_change` against the brief's presented candidates but nothing about `evidence` -- the one
field `_apply_fold_into_change()` (`queue_triage.py:2664-2739`) later appends verbatim as the new
task's checklist text. `worktrail-compile` (the gate `_worktree_pr_close()` runs before any
commit) needs a file reference somewhere in that task's text to assign it file scope; evidence
with none reaches the gate, fails it, and the whole apply aborts with the brief left untouched.

## Goals / Non-Goals

**Goals:**
- Reject a `fold-into-change` verdict whose `evidence` cannot possibly satisfy the compile gate,
  before it is ever applied -- not after a wasted worktree/compile attempt.
- Reuse the path-probe extraction this codebase already trusts (`premise_check.extract_needles()`
  / `router/brief_probes.extract_probes()`) rather than adding a second file-path heuristic.

**Non-Goals:**
- Changing `_apply_fold_into_change()`, `_worktree_pr_close()`, or `worktrail-compile` itself.
  Those already fail closed correctly for a fold that reaches them; the gap is entirely upstream,
  in what `_has_valid_target()` lets through.
- Guaranteeing the evaluator's cited path is *correct* (that the file actually needs touching) --
  only that *some* file reference exists for `worktrail-compile` to key scope off. Evidence
  citing a wrong-but-real-looking path is a separate, pre-existing evaluator-accuracy concern,
  not the reliability gap this change closes.
- Retrying `worktrail-compile --force` with additional context on failure. That still requires a
  worktree, a compile attempt, and a failure to occur first; rejecting evidence with no file
  reference at parse time is strictly earlier and cannot itself fail non-deterministically the
  way a second model-driven compile attempt could.

## Decisions

### Reuse `premise_check.extract_needles()`'s path-needle kind, not a new regex

`extract_needles()` already wraps `router/brief_probes.extract_probes()`'s path detection (a `/`
separator or a recognized extension, with denylisting for task-id/version false positives like
`1.1`) and is already imported into `queue_triage.py` as `premise_check`. Filtering its output to
`kind == "path"` gives exactly the file-path-shaped-token check this change needs with no new
regex to validate independently. `needs-update`'s `refuted_span`/`corrected_span` similarly reuse
`_MIN_REFUTED_SPAN_LEN` rather than inventing a second length floor -- this follows the same
reuse-over-reinvent precedent in the same file.

### Downgrade to `keep`, not a new verdict field

Considered adding a dedicated `file_scope`/`touched_files` field (mirroring `target_quote`'s
addition for the "insufficiently grounded fold target" gap) that the evaluator would populate
explicitly. Rejected as unnecessary complexity here: unlike `target_quote` (which must be
re-verified against live content at apply time, since a quote can go stale), a file-path
reference's only job is to give `worktrail-compile` something to key scope off, and it is
consumed as-is, verbatim, exactly like the rest of `evidence` already is -- there's nothing to
re-verify at apply time that a structural check on `evidence` itself at parse time doesn't
already cover. Extending the existing `evidence`-content check inside `_has_valid_target()`'s
`fold-into-change` branch is a smaller, in-place change than adding and threading a new field
through `Verdict`, `parse_verdicts()`, and the evaluator prompt's JSON output shape.

## Migration

None -- this only tightens what a future `fold-into-change` verdict must carry. A verdict file
already produced by a not-yet-updated evaluator that lacks a file reference in `evidence` is
simply downgraded to `keep`, the same fail-closed behavior an invalid `target_change` already
gets today.

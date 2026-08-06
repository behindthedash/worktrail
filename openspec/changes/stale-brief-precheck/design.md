## Context

`/go` Phase 5.5 today is a single guard: `check_spec_collision.py`, gated to routes C/D,
answering "does a shipped `docs/specs/` spec already cover this request?" It is structured as
two separate steps — `check()` does pure extraction and hands candidates to the calling agent
for semantic judgment; `verify()` does artifact verification on one already-judged candidate.
Both are best-effort and never raise. `check_repo_freshness.py` follows the same shape for a
different question ("is this checkout behind its remote?"), returning `checked`/`warning` and
degrading rather than blocking.

This change adds a third guard in that same family. The question is different — "did the work
this brief describes already land?" — but the shape, the failure posture, and the caller
contract are deliberately the same, so the three read as one system rather than three
one-offs.

The distinguishing input is that a *brief* carries two things free-text does not: a `created:`
timestamp that bounds the search window, and prose that names the symbols and files it is about.
That is why the check is brief-sourced only. It is also why it is cheap: a bounded set of
`git log` invocations over a bounded time window.

## Goals / Non-Goals

**Goals:**
- Catch the specific failure the motivating brief describes: a brief whose work landed between
  capture and claim, discovered only after a worktree and run record already exist.
- Cost close to zero on the overwhelmingly common no-evidence path, so it can run on every
  eligible dispatch without anyone weighing whether to skip it.
- Fail open, unconditionally. A guard that can wrongly block a dispatch is worse than no guard;
  the cost of a missed detection is one wasted run, the cost of a false block is a stalled queue.
- Keep the operator in the loop. Evidence that a fix landed is not proof the brief is satisfied —
  partial deliveries, similarly-named symbols, and adjacent refactors all produce matches.
- Reuse the existing guard family's shape (`checked`/`warning`, pure functions, CLI with
  `--json`) so the third guard needs no new mental model.

**Non-Goals:**
- Batch triage of the whole queue. That is brief `20260731-210136`'s scheduled ~1M-token sweep.
  This check looks at exactly one brief, at claim time, for milliseconds.
- Semantic judgment of whether a matched commit truly satisfies the brief. The check produces
  evidence; the operator judges it. This mirrors `check_spec_collision.check()`, which
  deliberately performs no semantic matching of its own.
- Auto-closing briefs. The motivating brief is explicit: "surface the candidate to the operator
  (`AskUserQuestion`, never auto-close)".
- Extending the check to routes beyond E/F. The route gate is a named constant, so widening it
  later is a one-line change plus a spec delta — but widening it now would be speculative.
- Cross-repo search. The check searches the dispatch's resolved `$REPO`. A brief whose work
  landed in a *different* repo is out of reach here and remains the triager's job.

## Decisions

**A separate module, not an extension of `check_spec_collision.py`.** The two guards share a
family resemblance but no logic: one scans `docs/specs/` and verifies git-tracking of declared
artifact paths; the other extracts probes from prose and searches commit history over a time
window. Folding the second into the first would produce a module with two unrelated entry points
and one misleading name. `check_brief_staleness.py` sits beside it, mirrors its docstring
conventions, and imports nothing from it.

**Probe extraction prefers backticks but does not require them.** Briefs are written by agents
following the handoff skill, which reliably backticks code references — but not universally. The
extractor therefore treats backtick-quoted tokens as the high-confidence source and falls back to
unquoted path-shaped tokens. Symbol probes require backticks: an unquoted snake_case word in
prose is far more likely to be a phrase than an identifier, and a bad symbol probe is expensive
(`git log -S` over a wide window) as well as noisy.

**A bare filename with an extension is a valid path probe.** `check_spec_collision.py`'s
traceability-matrix extractor requires a `/` because it reads a table of repo-relative paths.
Here the motivating example is `prevent-destructive-commands.py` — named without a directory, as
briefs habitually do. Requiring `/` would have missed the exact case this change exists to catch.
`git log -- <bare-name>` does not match by basename, so path probes without a separator are
searched with a `**/` prefix pathspec.

**The search window opens at the brief's `created:` timestamp, with no look-back margin.** Work
that landed *before* capture cannot be what the brief was filed against — the brief exists
because the author observed the gap. A look-back margin would only add false positives.

**The base branch is resolved preferring the remote-tracking ref.** The motivating failure mode
is work that landed upstream while the brief sat in the queue. A local checkout may well not have
it. Searching `origin/<base>` when it exists, falling back to `<base>`, then to `HEAD`, means a
stale local checkout does not blind the check — and pairs with the Phase 3 freshness guard, which
warns about that staleness but does not fix it.

**Caps and timeouts are constants in the module, not policy knobs.** The check must be cheap
enough that nobody reasons about whether to run it. Making the caps configurable invites tuning
and creates a way to accidentally make the check expensive. If a cap proves wrong, changing the
constant is a one-line PR with a test.

**`checked: false` and `checked: true, matches: []` are different answers.** The first means the
question could not be asked; the second means it was asked and the answer was clean. Collapsing
them would let a git failure read as "nothing landed" — exactly the silent-absence failure mode
`check_repo_freshness.py`'s docstring was written to warn about.

**The operator prompt offers two outcomes, and closing routes through `work_queue.py`.** Nothing
in this capability mutates the queue. When the operator judges the brief delivered, `/go` calls
the existing `work_queue.py done ... --implementation-complete` path, the same owner every other
queue mutation goes through. This keeps the "single shared owner" invariant `handoff_seed.py`'s
docstring states.

## Risks / Trade-offs

**False positives are the expected common failure.** A symbol probe like `check` or a path probe
naming a file that many PRs touch will match commits that have nothing to do with the brief. This
is accepted deliberately: the output is evidence for a human, not a verdict, and the operator
prompt's "proceed anyway" path is a first-class outcome rather than an escape hatch. The cap
prefers longer, more distinctive probes specifically to bias against this.

**False negatives are silent.** A brief that names nothing code-shaped, or whose delivering PR
renamed the files it named, produces no evidence and the dispatch proceeds — which is exactly
today's behavior, so the check can only improve on the status quo, never regress it. The batch
triager remains the backstop for briefs this misses.

**Every eligible dispatch pays a small latency cost.** Bounded by the probe cap times the
per-invocation timeout in the worst case, and by a handful of fast local `git log` calls in
practice. The `gh` lookup is the one network-dependent step and is both last and independently
degradable, so an offline session pays a timeout on that step alone and still gets the git
evidence.

**Adding a second branch makes Phase 5.5 a two-headed step.** The mitigation is that the two
branches are mutually exclusive by route and share no state, and the skill documents them as one
question asked two ways ("has this already been done?") rather than as two unrelated checks that
happen to sit at the same phase.

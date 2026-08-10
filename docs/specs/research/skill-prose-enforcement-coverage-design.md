# Design: extending the enforcement-coverage pattern to SKILL.md prose

**Triggered by:** work-queue brief `20260810-104428`. `test_gate_enforcement_coverage.py`
(classify.py `gates.append(...)` strings) and
`test_pr_creation_callsite_enforcement_coverage.py` (`gh pr create` call sites) both close a
recurring failure shape — a policy decision computed correctly in code with no registered proof
that anything actually consumes it — but both only extract from **Python source via `ast.walk`**.
Neither has a mechanism for the shape the `go:risk-*`/`go:no-automerge` PR-label bug actually
took for most of its six recurrences (#74/#80/#82/#128/#137/#281, finally code-enforced inside
`run_record.py`'s own `finish()` in #283): a **SKILL.md paragraph narrating a corrective action as
mandatory, with nothing in code enforcing it.** This note records the investigation into a
detection mechanism for that shape and the reasoning for the one chosen, before
`tests/router/test_skill_prose_enforcement_coverage.py` was built.

## The three candidate mechanisms

The brief posed three options: (a) a machine-checkable authoring marker/convention, (b) a scoped
keyword/AST hybrid over the prose itself, (c) limiting coverage to the specific known-repeat-offender
instruction shape.

### Option (a): a new authoring marker — rejected on its own

An inline marker (e.g. `<!-- enforcement-claim: id -->`) that a SKILL.md author adds next to any
paragraph describing a mandatory corrective action, cross-checked against a registry, was the
first idea. It was set aside **as a sole mechanism**: it reproduces the exact failure mode this
investigation exists to close. The original bug was "the calling agent must remember to run the
correction every time a PR is created"; a bare marker convention is "the *authoring* agent must
remember to tag the paragraph every time one is written" — still a human-must-remember step, just
moved earlier. A marker only becomes safe once something unconditional (not authored per-instance)
checks for its *absence* too, which is what option (b)/(c) below actually have to solve. It remains
useful as a **future extension point** (see "What this does not catch" below), not the primary
mechanism.

### Option (b): a generic keyword/AST hybrid over prose — tried, rejected on evidence

Prototyped directly against the live `skills/**/*.md` corpus: extract every markdown paragraph
containing both an emphatic mandate cue (`mandatory`, `MUST`, `immediately run`, `never skip`) and
a backtick-quoted named script/callable, on the theory that this mirrors `gates.append(...)`'s
AST-walk — a single deterministic shape, no hand-maintained list.

Two results killed this as the primary mechanism:

1. **Recall was near zero at safe precision.** A same-paragraph pairing needed proximity windowing
   to avoid pairing unrelated mandates and actions from the same long paragraph (see next point);
   tightening to a ~120-character window around each mandate cue returned **zero** matches anywhere
   in the corpus — real pairings in this codebase's prose style span farther than any window narrow
   enough to stay precise.
2. **Loosening to paragraph-level produced a confirmed false positive.** `skills/worktrail-go/SKILL.md`
   has one paragraph that says "Hold `$RISK_LEVEL` and `$GATES` for Phase 8's **mandatory** pre-PR
   gate (`pre_pr_gate.py --run --risk --gates` call) ... so `worktrail-reconcile-pr-labels`'s
   periodic sweep can recompute..." — a paragraph-level extractor pairs the word "mandatory" (which
   is about `pre_pr_gate.py`) with `worktrail-reconcile-pr-labels` (an unrelated sentence two
   clauses later), a coincidental co-occurrence, not the same instruction. Sentence-splitting to
   fix this was tried and immediately broke recall to zero (naive regex sentence boundaries do not
   hold up against this codebase's heavy use of inline code spans, parenthetical asides, and
   em-dashes).

Unlike Python source, markdown prose has no AST — "the same instruction" is a natural-language
judgment a regex cannot make reliably at the precision this test needs (a spurious extraction is a
CI failure that blocks every future PR touching that file, not a warning). This rules out a fully
generic scan as the *primary*, hard-gating mechanism.

### Option (c): a closed vocabulary scoped to the known failure family — chosen

Instead of trying to detect "any mandatory-sounding corrective instruction" in general, scope
extraction to the literal vocabulary this specific, six-times-recurring failure family is made of:
`go:risk-`, `go:no-automerge`, `ensure_pr_risk_label`, `ensure_pr_no_automerge_label`. A file-level
substring scan (no proximity ambiguity — the vocabulary itself only appears in a file when that
file is discussing this exact mechanism) found these files in the current corpus:

- `skills/worktrail-go/SKILL.md` — describes `worktrail-reconcile-pr-labels`'s self-healing sweep.
- `skills/worktrail-go/references/routes.md` — the PR template's Auto-Merge Eligibility section.
- `skills/worktrail-sdd-workflow/SKILL.md` — Phase 8, the exact passage `finish()`'s #283 fix
  rewrote to read "code-enforced inside `finish` itself ... no longer a separate step to
  remember."
- `skills/worktrail-go/references/ci-watch-loop.md` — "the tool itself also stamps
  `go:no-automerge` on the PR the moment `blocking` goes true."

This mirrors `CALLSITE_CONSUMERS`' file-level granularity exactly (not paragraph/sentence), and
gives a **non-vacuous, deterministic, zero-false-positive today** starting point: four real files,
each claiming a specific code path performs this correction unconditionally, each checked against
a registered proof that the claim is actually true (`co_names`/AST inspection of the named
function, or a behavioral patch-and-call test where the function is cheaply callable without
network I/O). Extending coverage to a *new* recurring failure family later is the same one-line
change `GATE_CONSUMERS` already requires when classify.py emits a new gate string: add the new
family's literal markers to the vocabulary tuple.

## What this does not catch

Same bounded-scope posture the sibling tests already state explicitly for their own shape
(`test_pr_creation_callsite_enforcement_coverage.py`'s docstring scopes itself away from
Bash-tool-issued `gh pr create` calls, for example). This test:

- **Does not catch a brand-new corrective-action family** described in SKILL.md prose for the
  first time, before anyone has added its vocabulary to the closed list — exactly the same
  boundary `GATE_CONSUMERS` has for a `gates.append("brand_new_gate")` nobody registered yet
  (caught the moment it ships, not before). The recommended escape hatch when a *new* failure
  family is suspected, until it has recurred enough to justify a fourth vocabulary entry here: use
  option (a)'s marker convention manually as documentation next to the paragraph, and register it
  as a normal code-review point — not a blind spot this test claims to close.
- **Does not verify the prose accurately describes the code** beyond what each proof function
  checks — a proof that only checks `co_names` confirms the named function is *referenced*, not
  that every branch of it runs unconditionally the way the prose claims. Each proof docstring below
  states exactly what it does and does not establish, same discipline as
  `test_gate_enforcement_coverage.py`'s `_proves_*` functions.

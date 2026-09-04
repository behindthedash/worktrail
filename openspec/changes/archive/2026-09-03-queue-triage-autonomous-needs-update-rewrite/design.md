## Context

`src/worktrail/workqueue/queue_triage.py` is the single-brief-and-batch triage
pipeline: `evaluate` spawns an agent per repo group against
`EVALUATOR_PROMPT_TEMPLATE`, `parse_verdicts()` turns its output into
`Verdict` objects, and `apply_verdicts()` executes (or, without `--confirm`,
only previews) each verdict's action via a dedicated `_apply_*` helper —
`_apply_close` for `stale-close`/`duplicate-of`, `_apply_work_directly`,
`_apply_needs_decision`, `_apply_fold_into_change`, `_apply_propose_change`,
`_apply_keep`, and `_apply_needs_update`. `router/skill_dispatch.py`'s
`evaluate_single_brief()`/`apply_single_brief_verdict()` expose the identical
pipeline scoped to one brief, for `worktrail-go BRIEF-ID` on an intake-kind
brief; that single-brief path is where the motivating case
(`20260903-145001-...`) actually got stuck — `evaluate-brief-triage` returned
`needs-update`, `apply-brief-triage --confirm` only appended a note, and the
brief needed a human to manually rewrite its `focus:` and manually re-invoke
`evaluate-brief-triage` before it could progress. See proposal.md for the
full narrative.

`decisions.py` already has a working human-decision-queue: `ask()` files a
structured record and stamps the brief `awaiting-decision:`, and
`_apply_needs_decision()` already builds exactly that from a `Verdict`'s
`question`/`evidence`. `_apply_needs_update()` today is a single unconditional
note-append with no branching at all.

## Goals / Non-Goals

**Goals:**
- Let a `needs-update` verdict whose refuted claim is a quotable, verifiable
  span resolve itself: rewrite, re-evaluate, hand back a fresh verdict —
  without a human touching the markdown.
- Give a `needs-update` verdict that genuinely needs a human call a real next
  step (a filed decision) instead of a note nobody is notified about.
- Define the mechanical/judgment split as a deterministic, mechanically
  checked property of the verdict's own fields — not a confidence score, not
  a keyword sniff over `evidence` prose, not an automatic inference from
  `premise_check`.

**Non-Goals:**
- Auto-applying whatever verdict comes out of the automatic re-evaluation.
  The freshly produced verdict is surfaced, not executed — see Decision 3.
- Chaining more than one auto-rewrite cycle inside a single `apply` call. If
  the fresh verdict is itself a mechanical `needs-update`, a caller issues a
  second `apply --confirm` for it, same as today's existing verdict → apply
  → verdict → apply loop for any other type.
- Changing how `evaluate`'s batch/`inventory()` dedup-skip window
  (`is_recently_triaged()`) treats `## Triage` notes. The rewrite note uses
  the same section shape every other apply action already writes; a brief
  the rewrite touches is exempt from the dedup window for the run that
  immediately re-evaluates it (that call bypasses `inventory()` entirely,
  going straight to `evaluate_briefs()`), and falls under the ordinary dedup
  window for any later, separate `evaluate` run — identical to how a `keep`
  or plain `needs-update` note already behaves.

## Decisions

### 1. Mechanical vs. judgment is a field the evaluator declares, not a signal the harness infers

The brief explicitly asks for this boundary to be designed, not hand-waved.
The rejected alternative was inferring "mechanical" from existing signals —
e.g. "every `premise_check` needle for this brief is `confirmed: false`", or
keyword-matching `evidence` for hedge words like "unclear"/"ambiguous". Both
were rejected:

- `premise_check` needles are extracted by regex heuristics over quoted
  spans/paths/allow-listed commands (`premise_check.extract_needles()`) —
  they identify *candidate* claims, not *the* claim the evaluator's own
  `needs-update` verdict is actually about, and there is no reliable mapping
  from "which needles are unconfirmed" to "which exact span of the focus
  text should be removed and with what, if anything, it should be replaced".
  Guessing that mapping mechanically is exactly the kind of undecidable
  inference this codebase's own conventions (fail open, never guess — see
  `EVALUATOR_PROMPT_TEMPLATE`'s "Step 4 — fail open") forbid.
- Keyword-sniffing `evidence` for hedge words is gameable and brittle in
  both directions: a confident-sounding sentence can still be wrong, and a
  correct, mechanical finding can be phrased with a hedge word for other
  reasons.

Instead, the evaluator — which already produces `evidence` narrating *why* a
claim is refuted — is asked to also emit one of two structured fields
(`EVALUATOR_PROMPT_TEMPLATE`'s new "Step 2c"), mirroring how `fold-into-change`
already declares `target_change` and `propose-change` already declares
`target_repo`/`proposed_change_name` rather than leaving `apply` to infer a
target from prose:

- `refuted_span`: copied **verbatim** from the brief's own focus text below
  the evaluator in the prompt. This is the one thing the evaluator is
  uniquely positioned to identify (it has the focus text and the refuting
  evidence in front of it at the same time) and the one thing `apply` can
  mechanically verify later — a substring match against the brief's current
  `focus:` text, no NLP, no inference.
- `judgment_reason`: an explicit statement that this needs a human call, for
  everything else (ambiguous claim priority, conflicting requirements, a
  scope/policy decision the evaluator is not positioned to make).

`apply` then treats the field's mere presence, re-validated against live
brief state, as the classification — never both, and `judgment_reason` wins
if the evaluator (incorrectly) sets both, since declaring a human decision is
needed must never be silently overridden by an auto-rewrite.

### 2. `refuted_span` is re-verified against the brief's live focus text at apply time, not trusted from evaluation time

`evaluate` and `apply` can run arbitrarily far apart (the same brief could be
re-triaged, or hand-edited, in between) — this codebase already re-checks
several evaluate-time facts fresh at apply time rather than trusting a stale
snapshot (`_propose_change_wip_cap_status()`'s docstring: "recomputed here
rather than trusting `v.held_by_wip_cap`... apply time, not evaluate time").
`refuted_span` gets the same treatment: if it is not found verbatim in the
brief's current `focus:` text when `apply --confirm` runs, the mechanical
path is refused and the verdict is treated as if `judgment_reason` had been
set (with an apply-generated question naming the mismatch) — never a partial
or best-effort fuzzy replace, which could silently corrupt unrelated text.

This also means the mechanical rewrite is idempotent-safe against a race: a
second concurrent `apply` (or a hand-edit that happened first) simply can no
longer find the span, and falls to filing a decision instead of doubleedits
or corrupting drifted text.

The verbatim check additionally enforces a 12-character floor on
`refuted_span`, reusing `premise_check.py`'s own `_MIN_QUOTED_LEN` constant
(its quoted-needle extraction already draws this line for the same reason: a
shorter span is too likely to appear coincidentally rather than because it
is genuinely the quoted claim). A `refuted_span` under the floor is treated
identically to one that fails the substring check — routed to judgment, not
truncated or padded.

### 3. The automatic re-evaluation never auto-applies its own result

The proposal's "Apply-step-never-closes-a-brief-without-an-approved-verdict"
delta is deliberately narrow: `apply`'s already-`--confirm`ed `needs-update`
action is extended to (a) rewrite the brief's own focus text and (b) run one
more evaluation pass and hand back its output — both of which happen under
the authority of the *original*, already-approved `needs-update` verdict.
What it does **not** do is treat that fresh verdict as itself approved: it is
never fed into `apply_verdicts()`, never claims, closes, or opens a pull
request. The rejected alternative — auto-cascading into whatever the fresh
verdict says, including `stale-close`/`fold-into-change`/`propose-change` —
would mean a single `--confirm` on a `needs-update` verdict could transitively
close a brief or open a PR the operator never reviewed a verdict for, which
directly conflicts with the spirit of "never closes a brief without an
approved verdict" (only the letter changes: a mechanical rewrite is now also
an approved-verdict action). Keeping the boundary at "produce, don't act" is
also what makes this safe to ship as an incremental extension rather than a
rewrite of the confirm/approval model.

Practically: `evaluate_briefs()` (already the single-brief-and-batch shared
evaluation pipeline, per its own "design D9" docstring) is called again,
scoped to the one corrected brief, using the same `repo`/`cwd` resolution
`_propose_change_wip_cap_status()` already uses
(`_effective_repo(v)` + `_resolve_repo_dir(..., repos_root)`, falling back to
`_worktrail_repo_root()` for a `NO_REPO_KEY` verdict). Its result is embedded
in the action-log entry as `reevaluation: {"status": "produced"|"error",
"verdict": <asdict or null>, "error": <str or null>}`. A spawn failure there
does not undo the already-written rewrite — the brief sits corrected, in
`queue/`, ready for the next ordinary `evaluate` run to pick it up under the
existing dedup rules.

### 4. Judgment routing reuses `_apply_needs_decision()` unchanged, via a synthetic `Verdict`

Filing a decision for a `needs-update`-turned-judgment case is *exactly* what
`_apply_needs_decision()` already does for a real `needs-decision` verdict:
build a deterministic `decision_identity()`, call `decisions.ask()`, stamp
`awaiting-decision:`, leave the brief queued. Rather than duplicating that
logic, the judgment path constructs a `Verdict(verdict="needs-decision",
question=<judgment_reason or the apply-generated stale-span question>,
evidence=v.evidence, repo=v.repo, brief_id=v.brief_id)` and calls
`_apply_needs_decision()` on it directly, then overlays the returned dict's
`verdict` field back to `"needs-update"` (plus a `routed_to: "needs-decision"`
marker) so the action-log entry's `verdict` field still reflects the verdict
that was actually present in the confirmed verdict file, per the "only ever
act on verdicts present in a verdict file" requirement's auditability intent.

### 5. `corrected_span` defaults to removal, applied with a single bounded substring replace

`focus.replace(refuted_span, corrected_span or "", 1)` — count `1` so a
`refuted_span` that happens to repeat in the focus text only touches the
occurrence the evaluator actually quoted context around, not every
coincidental repeat elsewhere in the text. If the resulting focus text is
empty after stripping whitespace (the refuted claim *was* the entire focus),
that is not a mechanical case anymore — "should this brief just be closed"
is a scope call — so it is routed to the judgment path (Decision 4) with a
generated question, rather than ever writing an empty `focus:`.

## Risks / Trade-offs

- [Evaluator emits a `refuted_span` that is technically present verbatim but
  is a *coincidental* substring match unrelated to the actual claim (e.g. a
  short, generic phrase)] → Mitigated by Decision 2's 12-character floor
  (reusing `premise_check`'s own `_MIN_QUOTED_LEN`) plus the existing prompt
  convention that every claim in this pipeline must already be evidenced —
  this is not a new trust boundary; the evaluator is already trusted to
  produce accurate `evidence`/`target_change`/`target_repo` today.
- [The immediate re-evaluation spawn doubles the token/time cost of applying
  a mechanical `needs-update` verdict compared to today's single note-append]
  → Accepted: this replaces a human noticing the note, manually rewriting
  the brief, and manually re-invoking `evaluate-brief-triage` — a strictly
  slower and more error-prone path in practice (as the motivating brief
  shows), and the spawn only happens for the mechanical branch, not for
  every `needs-update` verdict.
- [A caller repeatedly runs `apply --confirm` against the *same*, unchanged
  verdict file for a mechanical `needs-update` verdict, expecting idempotence]
  → After the first run, `refuted_span` no longer matches the (now rewritten)
  focus text, so the second run falls to Decision 2's stale-span fallback and
  files a decision rather than silently no-op'ing or re-rewriting — this is
  the same "re-verify against live state" behavior that already makes this
  path safe against the concurrent-apply race in Decision 2, at the cost of
  a second `apply --confirm` on an already-resolved verdict producing a
  decision instead of a silent no-op. Acceptable: a verdict file is meant to
  be applied once; this surfaces the double-apply rather than hiding it.

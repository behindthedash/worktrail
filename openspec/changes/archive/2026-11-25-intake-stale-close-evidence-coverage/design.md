## Context

See `proposal.md` for the motivating incident. `queue_triage.parse_verdicts()` requires every
verdict to carry non-empty `evidence`, and `_apply_close()` acts on a `stale-close` by claiming
the brief and calling `done()` with that evidence as the closure note. Neither step relates the
evidence to what the brief actually reported. A repair that addresses one reported failure is
indistinguishable from one that addresses all of them.

The brief's `focus:` text is the only description the evaluator sees (`evaluate_group()` embeds
`_brief_focus(path)` in the prompt, not the body), so it is also the only deterministic source
for the reported-failure set. In the motivating brief the focus named both failures: "Fix Route
D Codex adapter launch and compile-marker writes under the linked-checkout sandbox".

The existing fail-open conventions around verdicts (`work-directly` downgrade at apply time,
`needs-update` re-checking its `refuted_span` against the live brief) establish both the place
and the shape of this gate: apply is where the brief's current content is authoritative, and a
downgrade to `keep` is the safe outcome.

## Goals / Non-Goals

**Goals:**

- Make every independently reported focus item have to be accounted for before a `stale-close`
  verdict may close a brief.
- Derive and re-check that set deterministically from the brief's live focus, with no model
  judgment in the acceptance decision.
- Fail open: missing coverage preserves the brief untouched and reports why, never errors or
  loses the verdict.

**Non-Goals:**

- Judging whether the cited evidence truly resolves a failure in a semantic sense. That is not
  mechanically decidable; the gate forces per-failure accounting, not correctness of prose.
- Changing `duplicate-of`, `needs-update`, `work-directly`, keep escalation, or the archived-repo
  short-circuit's verdict type.
- Requiring briefs to be authored in a new structure. Existing free-form focuses are split by
  punctuation and conjunctions.

## Decisions

### D1 — Count items of the focus, not failures detected by a model

A "reported failure" is a top-level item of the brief's focus: the text is split at sentence
ends, semicolons, commas followed by whitespace, list-item line breaks, and the stand-alone
conjunction `and`; items are whitespace-collapsed, stripped of surrounding punctuation,
de-duplicated, and ignored when shorter than 12 characters. In the motivating focus this yields
exactly two items, matching the two failures the brief reported.

The splitter is deliberately conservative: an over-split only adds a coverage obligation and can
only cause a `keep`, while a semantic classifier's false negative would reintroduce the closed
brief. A few short items are exempt because a 12-character verbatim span cannot locate them.

### D2 — Structured per-item coverage, not prose matching

`stale-close` carries `failure_coverage`, a list of `{failure, evidence}` entries. `failure` must
be copied from the focus (at least 12 characters, whitespace-normalized) and fall inside one or
more focus items, covering each of them; `evidence` must be non-empty and name what resolves that
item. This mirrors `refuted_span` and `target_quote`: a verbatim, length-floored span that can be
re-located mechanically, so a model cannot claim coverage with a paraphrase or with a span that
spans two items.

A single repair legitimately may resolve two reported failures; both entries may cite it. The
gate cannot detect a copied citation, but enumerating each failure with its own evidence forces
the evaluator to state a resolution for each one instead of letting one repair stand in for the
brief as a whole.

### D3 — Enforce at apply and preview against the live focus

Parsing keeps whatever coverage the evaluator emitted (sanitized to well-formed entries), but the
decision runs in `_apply_close()` and `_preview_verdict()`, re-deriving the items from the brief
as it exists at apply time. A brief can be edited or re-captured between evaluate and apply, and
a verdict file can be hand-edited; apply is the only point that sees the authoritative content.
Sharing one helper between apply and preview keeps `--confirm` and its dry run from disagreeing,
as `_work_directly_accepted()` already does.

### D4 — An uncovered item downgrades to keep, with no mutation

The apply result is a `downgraded-to-keep` no-op: the brief is not claimed, no closure note is
written, and the action log's note names the uncovered items. This matches the `work-directly`
downgrade and the "fail open" rule. Returning an error would abort a batch over a correctable
evaluator mistake; closing anyway is the defect being fixed.

### D5 — The archived short-circuit synthesizes coverage

For an archived/renamed repo, `evaluate_group()` already synthesizes a `stale-close` for every
brief without spawning an evaluator. That path now emits one `failure_coverage` entry per focus
item, each citing the archived-repo check. The alternative — an exemption flag or provenance
field — would have to be trusted from evaluator JSON, letting a model bypass the gate by
claiming it. Mechanical synthesis keeps the rule universal.

## Risks / Trade-offs

- [The splitter over-splits natural prose] → The result is an extra coverage obligation, which
  at worst downgrades to `keep`; a brief the evaluator cannot fully account for can still be
  handled by `needs-update` or `duplicate-of`, so stale-close is not the only exit.
- [A focus contains one clause naming several failures without a separator] → Those failures are
  counted as one item and one entry may cover them; the gate is a coverage floor, not a parser of
  English.
- [A short focus imposes no coverage requirement] → Intended: a 12-character floor is the same
  ambiguity floor `refuted_span` and `target_quote` use.
- [Existing test fixtures with realistic focuses start downgrading] → The implementation task
  updates those fixtures to carry coverage so they keep testing their original behavior.

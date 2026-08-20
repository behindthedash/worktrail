## Context

See `proposal.md` for motivation and this change's capability spec for normative behavior.

Feature 2 landed `classify_dependency_reference()` in `src/worktrail/workqueue/work_queue.py`, returning `{raw, reference, state, candidates, satisfied, error}` with states `done` / `active` / `stale` / `ambiguous` / `malformed`. Today that result is consumed in exactly two places: `_is_blocked()` collapses it to the `blocked` boolean emitted by `list_queue()`, and `_claim_warnings()` renders per-state strings that are only visible after a brief is already being claimed. Nothing between listing and claiming can tell why a brief is blocked.

The data path that matters for automatic selection is already in place and does not need re-plumbing: the `/go` front door runs `worktrail-work-queue list --json` and hands the output verbatim to `dashboard.py --queue-json`, which parses `briefs` into `queue_briefs` and passes it to `auto_pick_brief()`. `auto_pick_brief()` reads only the fields in those dicts (plus a frontmatter re-read for `repo`), so a new per-brief field in `list_queue()` reaches automatic selection without any new argument, filesystem access, or import. `drain.py` consumes the same `list --json` output for its ready count.

Skip reasons are a documented contract, not an internal detail: `skills/worktrail-go/references/auto-mode.md` enumerates them for the agent to report, and `log_auto_pick_miss()` buckets them by the segment before the first `:` (which is how `release-gate:<name>` already keeps a stable coarse category while carrying detail).

## Goals / Non-Goals

**Goals:**

- Publish the existing structured classification through the one data path automatic selection already reads, rather than teaching a second consumer to re-derive dependency state.
- Keep the skip-reason vocabulary extensible in the shape existing tooling already parses.
- Make the malformed case visible at the two moments an operator actually looks: listing the queue and reading the rendered dashboard.
- Prove the incident shape end to end across the real listing → automatic-selection boundary, not with a hand-built brief dict.

**Non-Goals:**

- Changing classification rules, satisfaction precedence, or the `blocked` flag's meaning (Feature 2 owns those).
- Changing producer validation or its error text (Feature 1 owns that).
- Repairing, splitting, or migrating malformed stored values.
- Adding a new console script, dashboard flag, or skill.

## Decisions

### Publish unresolved classifications as an additive `list_queue()` field

`_brief_dict()` gains one field — a list with one entry per unsatisfied `blocked-by` reference, each entry carrying `raw`, `reference`, `state`, and `candidates` straight from `classify_dependency_reference()`. Satisfied references (done, valid stale) contribute nothing, so a clean queue emits an empty list per brief and the JSON stays small.

Emitting only unsatisfied entries matches the seam Feature 2 already established for claim warnings ("satisfied references stay quiet") so the two surfaces cannot disagree about what counts as a problem. The alternative — emitting every reference with its state — would make the field a full dependency dump whose consumers must re-apply the satisfaction rule, which is precisely the duplication Feature 2's centralization removed.

The field is additive and every existing field keeps its meaning, so `drain.py`, the dashboard's ready counts, and the decisions tests that assert on `blocked` are unaffected. `_is_blocked()` stays the source of the `blocked` flag; the new field explains that flag rather than replacing it, which keeps a brief blocked by an open decision correctly blocked with no dependency entries.

One classification pass per brief is reused for both the flag and the field so listing cost does not double.

### Qualify the auto-pick skip reason instead of adding a new one

`auto_pick_brief()`'s `blocked` branch inspects the new field and appends a colon-qualified suffix when an unsatisfied entry is malformed or ambiguous, yielding `blocked:malformed-dependency` / `blocked:ambiguous-dependency`; everything else keeps the bare `blocked`.

Colon qualification is chosen over a distinct top-level reason (e.g. `malformed-dependency`) precisely because `log_auto_pick_miss()` aggregates on the pre-colon segment: an operator comparing miss logs across nights still sees one `blocked` count, while the per-brief entry carries the detail. A new top-level reason would silently split that historical series. Malformed outranks ambiguous when a brief has both, so the reason names the state that most needs an intentional edit.

The ordering of the `blocked` check within `auto_pick_brief()` is unchanged — this refines a reason string, not the gate — so `unparsable-frontmatter` still takes precedence over any dependency reason for a brief whose frontmatter does not parse at all.

### Warn where the operator already looks, in the surfaces' own idioms

Human `worktrail-work-queue list` output already prints a `[blocked — waiting on prerequisites]` section; the warning attaches there, naming the brief and, for a malformed value, printing the raw stored value with `repr`-style quoting so trailing whitespace and embedded commas are unambiguous. The dashboard's queue block already tags briefs (`[blocked]`, `[watching]`, `[blocker]`); the malformed/ambiguous case becomes a distinct tag on the same line rather than a new section, because that block is deliberately capped at three entries plus an overflow count and a new section would compete with capacity and decision panels for the same screen.

This deliberately splits detail by surface: the queue listing is where repair happens and carries the raw value, while the dashboard is an orientation surface and carries only the signal that a dependency reference needs repair. The alternative — printing raw values in the dashboard too — pushes arbitrary-length legacy strings into a fixed-width summary.

### Test the incident across the real boundary

The regression builds a real `$WORK_QUEUE_DIR` fixture: an active prerequisite brief, a clean eligible brief, and the incident brief whose single `blocked-by` item joins that active prerequisite's ID with two others by commas. It calls the real `list_queue()`, feeds its output to the real `auto_pick_brief()`, and asserts the incident brief is skipped with the malformed reason while the clean brief is the pick. Hashing the incident brief's bytes before and after covers the non-mutation requirement over all three surfaces at once.

Driving the boundary with real listing output — rather than a hand-written brief dict as `test_dashboard.py`'s existing auto-pick tests do — is the point: the 2026-08-18 failure was that the two ends agreed on a field whose value was wrong, which a fabricated dict cannot reproduce.

## Risks / Trade-offs

- [A downstream consumer keys on the exact `blocked` skip-reason string and stops matching] → Only `log_auto_pick_miss()` (prefix-bucketed, unaffected) and the auto-mode skill doc consume these strings in-repo; the doc's enumeration is updated in the same change, and the qualified form is deliberately a prefix-compatible extension of the existing value.
- [Diagnostics entries could grow large for a brief with many bad references or many ambiguous candidates] → Entries exist only for unsatisfied references, and `candidates` is already bounded by the number of briefs a reference matches; no truncation is added, so nothing is silently dropped.
- [Publishing raw stored values widens what queue JSON exposes] → The values are the operator's own brief frontmatter, already readable in the queue directory and already printed by claim warnings; nothing new is derived or inferred from them.
- [The rendered dashboard tag competes for a capped three-line block] → The tag replaces the existing `[blocked]` text for affected briefs instead of adding a line, so the block's height is unchanged.
- [An operator may read the warning as an instruction to auto-repair] → Warning text names the brief and value and asks for an intentional edit; no repair path is added, consistent with the epic's non-goals.

## Migration Plan

Deploy with no data migration and no queue rewrite. Briefs already blocked by Feature 2 keep their eligibility; only their visibility changes. Rollback removes the diagnostics field, the reason qualifier, and the warnings independently without restoring silent eligibility, because the `blocked` flag itself is unchanged by this feature.

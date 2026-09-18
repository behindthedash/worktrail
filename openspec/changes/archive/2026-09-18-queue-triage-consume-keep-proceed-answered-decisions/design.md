## Context

`consume_repo_decision()` is the only consumer of answered decisions, and it is repo-shaped:
it either stamps a `repo:` or leaves everything untouched. `has_unresolved_decision()` treats
`answered` as still-unresolved so that a consumable answer is not skipped before the pre-pass
runs. The two only compose when every answer names a repo; a keep/proceed answer wedges the
brief. Separately, `evaluate_single_brief()` (the `worktrail-go <brief-id>` path) skips the
pre-pass whenever `--triage-repo` is passed and has no "still pending" guard, so it evaluates
a held brief and the evaluator re-files the question it cannot see was answered.

## Goals / Non-Goals

- Goals: an answered decision of any shape unblocks its brief exactly once; the evaluator
  sees the human's answer; the single-brief path cannot re-file an answered or open question.
- Non-Goals: interpreting the answer (no "keep"/"close" verb parsing — the evaluator, not a
  regex, decides what the answer means); changing the canonical repo-assignment path or the
  re-home directive regex; changing `_apply_needs_decision()`'s identity convergence.

## Decisions

- **Separate consumer, not a widened `consume_repo_decision()`.** A new
  `consume_answered_guidance(path)` runs only when `consume_repo_decision()` returned `None`.
  `consume_repo_decision()`'s return contract (`None` / resolved / unresolvable) is relied on
  by two callers and the archived proceed-as-scoped spec; adding a fourth shape there would
  make every caller distinguish three falsy outcomes. The guidance consumer re-checks
  `_REHOME_DIRECTIVE_RE` and declines a directive answer (that case is "named an unknown
  repo", which the existing requirement says stays untouched so a corrected answer can
  still land). It also declines when the linked decision is not `answered`, mirroring the
  repo consumer.
- **Record the answer as a triage note, then archive.** The note is
  `## Triage <date>` / `verdict: decision-answered` / `decision: <id>` / `question: <q>` /
  `answer: <a>` (answer whitespace-collapsed to one line, so `TriageNote` parsing stays
  line-based). Archiving via `decisions.resolve_decision()` reuses the existing "consumed"
  semantics and clears `awaiting-decision`, so `has_unresolved_decision()` releases the brief
  with no change to that function. The brief file is the single source the evaluator prompt
  reads from, which is why the answer is not threaded through `inventory()`'s return tuple.
- **Dedup window ignores the note; keep streak does not.** `is_recently_triaged()` already
  excludes `repo-inferred`; `decision-answered` joins that exclusion, otherwise the note
  written during inventory would immediately mark the brief as recently triaged and skip it
  in the same run. `consecutive_keep_count()` already stops at any non-`keep` note, so a
  human answer resets the keep-limit escalation clock with no code change; queue-age
  escalation is unaffected.
- **Prompt surfacing via the brief line, not a new prompt block.** `evaluate_group()`
  appends `Human decision: Q: <q> A: <a>` (most recent `decision-answered` note, read by a
  small `_answered_guidance(path)` helper over `triage_history()`'s sections) under the
  brief's existing `Candidate targets`/`Premise check` lines, and the template gains one
  sentence: treat it as settled, do not return `needs-decision` re-asking it. Same-run
  identity convergence in `_apply_needs_decision()` (`already-resolved`) remains the backstop
  for an evaluator that re-asks with identical wording anyway.
- **Single-brief gate: same pre-pass, then hard block.** `evaluate_single_brief()` always
  calls `consume_repo_decision()` (a resolved outcome overrides a passed `--triage-repo`,
  matching inventory's re-home-on-repo-bearing behaviour), then
  `consume_answered_guidance()`, then checks `has_unresolved_decision()`; if still true it
  raises `queue_triage.PendingDecision(decision_id, status)`. `skill_dispatch` maps it to
  `null` on stdout, `blocked_pending_decision: <id> (<status>)` on stderr, exit 2 — the same
  exit/`null` shape as `blocked_no_capacity`, so the skill's "neither case proceeds to apply"
  rule extends naturally. An exception rather than a `None` return because `None` already
  means "a model looked and produced nothing" and the skill treats the two differently.

## Risks / Trade-offs

- The evaluator may still judge the brief differently from the human's intent; the note is
  guidance, not an override. Acceptable: the brief is at least evaluated with the answer in
  view, and the answer stays in the file for later runs.
- An answered decision whose answer is empty is consumed with an empty `answer:` line rather
  than held. Deliberate: an empty answer carries nothing to wait for.
- The `queue-triage` MODIFIED requirement overlaps textually with the active
  `rehome-directive-rescope-verb-coverage` change; archive order determines who rebases.

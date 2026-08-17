## Context

See proposal.md - Why for the motivating incident (brief `20260817-102329`, PR #500).

`brief-staleness-check.md`'s "Reading the result" table currently has exactly two rows for
`checked: true`: empty evidence proceeds silently, non-empty evidence (`matches` or
`pull_requests`) always prompts. The predicate re-check (`check_brief_predicate.py`,
documented in `brief-staleness-check.md`'s "Predicate re-check" section) already establishes
the auto-resolve pattern this change mirrors, but it only applies to briefs carrying a
structured `drift-source`/`drift-findings` pair — a fully deterministic re-check against
on-disk task-file checkbox state. The probe-based branch this change modifies has no such
structured claim to re-verify: a brief's focus prose describes a capability in free text (e.g.
"a REST (`gh api`) fallback for `ci-watch-loop.md`'s core `--watch`/`--json` wait/classify
commands"), and `extract_probes()` only pulled path/symbol/PR tokens out of that prose — it
never captured what the brief is asking for in a machine-checkable form.

## Goals / Non-Goals

**Goals:**
- Stop a false-positive operator interrupt (or `AUTO_MODE` decision-record-plus-release round
  trip) when the brief's requested change is conclusively absent from, or conclusively present
  in, current file state, once evidence has already been surfaced by the probe search.
- Preserve the fail-open contract: an unresolvable classification degrades to today's unmodified
  prompt/decision-record flow, never to a dispatch failure and never to a silent auto-close.
- Keep the verification step's cost bounded to reading/grepping the files the probe search
  already identified — no broader repo scan.

**Non-Goals:**
- Building a deterministic, general-purpose "does capability X exist in this codebase" checker.
  The brief's own design questions anticipate this is not achievable for prose-described
  capabilities in the general case (unlike the checkbox-drift predicate, which re-checks a
  structured claim); this change does not attempt it.
- Changing `extract_probes()`, the probe search itself (`check()`), or the search-boundary
  timestamp precedence — those are unrelated or covered by the two other in-flight changes named
  in proposal.md - What Changes.
- Changing the predicate re-check (`check_brief_predicate.py`) or its own carve-out — this
  change adds a second, independent carve-out alongside it, gated to run only when the
  predicate re-check did not already decide the outcome.
- Adding new CLI flags to `check_brief_staleness.py`'s `main()` — the verification step is a
  skill-doc-level instructed procedure performed by the dispatching agent (which already has
  Read/Grep access), not a new subcommand.

## Decisions

**The verification step is an instructed agent read/grep procedure in
`brief-staleness-check.md`, not a new deterministic Python check.** The brief's own design
questions raise this choice explicitly. A deterministic checker works for the predicate
re-check because `drift-findings` captures a structured, re-runnable claim (a task file's
`status:`/checkbox state) at brief-capture time. The probe-based branch has no equivalent: the
brief's focus prose is free text describing an arbitrary capability, and the probe search's
`matches`/`pull_requests` only identify *which files changed*, not *what changed about them*.
Judging whether a specific described capability is present in a file's current content is a
semantic reading task, not a grep-able predicate — exactly the kind of judgment the dispatching
agent already performs today when it reads a brief's evidence before deciding how to answer the
operator prompt (as it did in the motivating incident, before the prompt fired anyway). This
change formalizes that already-happening read step as a required, three-outcome classification
that gates the prompt, instead of leaving it as an optional step the agent may or may not
perform before an unconditional prompt fires regardless.

**Three outcomes, not two, and "inconclusive" is the safe default.** Collapsing to a two-way
absent/present split would force a guess whenever the agent's read is ambiguous (partial
delivery, a capability that's implemented differently than described, a probe match that turns
out to be unrelated). `inconclusive` preserves exactly today's behavior for every case this
change cannot confidently resolve, so the fail-open contract holds by construction: adding this
step can only ever *remove* prompts that were already knowable in advance to be non-decisions,
never add new ones or replace a real decision with a guess.

**The verification step runs identically in interactive and `AUTO_MODE` dispatches.** It is not
`AskUserQuestion` — it is the same kind of internal reasoning/tool-use step the dispatching
agent already performs elsewhere in Phase 5.5 (e.g. reading a claimed brief's frontmatter,
running `worktrail-check-brief-staleness` itself). `AUTO_MODE` only removes the *human-facing*
prompt capability; it does not remove the agent's ability to Read/Grep and reason. So the
verification step is placed before the `AUTO_MODE` branch point in `brief-staleness-check.md`,
not duplicated inside it — this is also what delivers the change's stated `AUTO_MODE` cost
reduction (proposal.md - Why), since the decision-record-plus-release path is exactly what a
verifiably-absent or verifiably-present classification now skips.

**Two new formatting helpers in `check_brief_staleness.py`, mirroring
`check_brief_predicate.py`'s pair.** `format_still_true_evidence`/`format_resolved_closure_note`
already establish the pattern of a small, testable string-builder per auto-resolve outcome, so
the run-record-append and work-queue-done `--note` strings are byte-for-byte consistent
regardless of which code path built them. The new helpers take the probe matches (or PR list)
plus a short verification-finding string (the agent's stated basis for the classification) and
render the canonical evidence-line / closure-note text. They live in `check_brief_staleness.py`
rather than `check_brief_predicate.py` because they format *probe-based* evidence
(`matches`/`pull_requests`), the shape this module already owns and already renders for the
human-facing prompt today (`_format_human`).

**Placement in `brief-staleness-check.md`: a new section between "Running it" and "The operator
prompt".** The "Predicate re-check" section already establishes the convention of a numbered,
gating section ahead of the probe-based flow it can bypass; this change adds a second gate
*after* the probe search runs (since it needs `matches`/`pull_requests` to know which files to
read) but *before* the prompt section, following the same structural pattern.

## Risks / Trade-offs

**[Risk] The agent's semantic read could misclassify a partial delivery as "verifiably absent"
or "verifiably present" when it is actually a partial match.** → Mitigation: the classification
prompt instructs the agent to default to `inconclusive` whenever the described capability is
only partially delivered, implemented differently than described, or the match is ambiguous —
mirroring the existing "Evidence is not proof" caution already in `brief-staleness-check.md` for
the human-facing prompt. This is the same trust boundary the interactive prompt already accepts
today (an operator's own read of the same evidence is also not infallible); the risk is not new,
it is inherited from a currently-human-only judgment being made by the same already-present
agent instead, in the narrow band where the answer is unambiguous enough to classify.

**[Risk] A false "verifiably present" auto-close is more costly than a false "verifiably absent"
auto-proceed, since it closes the brief without a human ever seeing the evidence.** → Mitigation:
both outcomes cite the same evidence (probe matches/PRs plus the verification finding) on the
run record or closure note, so a wrongly-closed brief is fully auditable and recoverable (the
work-queue owner can reopen it, same as any other closure); the classification instructions bias
toward `inconclusive` rather than `verifiably present` when the read is anything less than
conclusive, treating the two false-positive directions asymmetrically on purpose.

**[Risk] Verification cost scales with how many files the probe search matched.** → Mitigation:
bounded by the same probe caps (`Probe Count Is Bounded`) that already bound the search itself;
this step reads only the files/commits the search already surfaced, not a new search.

## Migration Plan

No data migration. This is a behavior change to a skill-doc procedure plus two new pure Python
formatting functions; existing briefs, run records, and queue state are unaffected. Deploys via
the existing plugin-refresh path (`AGENTS.md` - Git workflow) once merged to `main`.

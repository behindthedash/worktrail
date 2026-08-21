## Context

`check_brief_staleness.py`'s `check()` (see module docstring and `openspec/specs/stale-brief-precheck/spec.md`)
already does exactly one thing well: extract probes from a brief's focus text
(`extract_probes()`), then search the resolved base branch's history **forward** from the
brief's capture time for commits (`matches`) and merged PRs (`pull_requests`) that might already
deliver the described work. Both of those searches are anchored to `since_str` (the brief's
`original-created:`/`released-at:`/`created:` frontmatter, widened earlier by `RACE_GRACE_SECONDS`
via `_widen_since()`) and share the fail-open contract described in the module docstring: any
condition that prevents answering the question degrades to `checked: false` plus a warning, and
every sub-phase (git search, `gh` lookup) is independently degradable without discarding
evidence the other sub-phase already found.

`docs/specs/research/*.md` is this repo's existing, uncommitted-to-a-schema convention for
Route-I investigation notes (see proposal.md's incident and the ~30 existing files under that
path) — plain prose files, each documenting one investigation's findings, committed to the base
branch like any other file. No code currently globs or searches this directory; it is referenced
only in comments/docstrings pointing a human reader at prior art.

See proposal.md for the motivating incident and the reasoning for extending `check()` rather
than `cluster_detect.py`.

## Goals / Non-Goals

**Goals:**
- Add a second, independent search inside `check()` that looks **backward**: does a research
  note already on the base branch, touched within a bounded window ending at the brief's own
  capture time, textually overlap the same probes the forward-looking search already extracted?
- Keep the new search's bounding (window width, note-count cap, match cap, subprocess timeout)
  in the same module-level-constant style as every existing bound in this module, and keep it
  independently degradable — a failure here never flips `checked` to `false` and never discards
  `matches`/`pull_requests`.
- Extend the Phase 5.5 skill doc so `research_notes` reaches the operator through the exact same
  File-state-verification-then-prompt flow `matches`/`pull_requests` already use, not a second
  ask site.

**Non-Goals:**
- Touching `cluster_detect.py` or queue-to-queue comparison at all — this is a text-similarity-
  against-a-static-corpus problem, unrelated to `cluster_detect.py`'s pairwise queued-brief
  signal matching (see proposal.md's rationale for extending `check_brief_staleness.py` instead).
- Matching PR-number probes against research notes. Research notes are investigation prose, not
  GitHub metadata; PR-number-shaped evidence is already the forward-looking `pull_requests`
  lookup's job. The new search only matches path and symbol probes.
- Semantic/fuzzy similarity. The forward-looking search already works by literal occurrence
  (`git log -S<symbol>`, `--grep=<symbol>`, pathspec matching); the new search matches the same
  way — a literal substring check of each probe string against each candidate note's content —
  for the same reason: cheap, deterministic, and consistent with "evidence, not proof; a human
  judges every match" already established for the rest of this module.
- Changing anything about the existing forward-looking `matches`/`pull_requests` computation,
  their constants, or their JSON field shapes.

## Decisions

**New result field `research_notes`, not folded into `matches`.** `matches` is documented
end-to-end (spec, skill doc, `_format_human()`, the two `format_verified_*` helpers) as "a
matching commit on the base branch." A research-note hit is a different kind of evidence (a
pre-existing document, not a commit) with different citable fields (a note path, not a
sha/subject). Reusing `matches`' shape would either lose the note path or overload `sha`/
`subject` with meanings they don't have. A sibling top-level field — parallel to how
`pull_requests` already sits alongside `matches` as a second, independently-degradable evidence
source — keeps each field's shape honest. Each item:
`{"sha": <last-touch commit short sha>, "date": <short date>, "path": <note path>, "probe":
<matched probe string>, "kind": "path"|"symbol"}` — deliberately parallel to a `matches` item
(`sha`/`date`/`path`-instead-of-`subject`/`probe`/`kind`), so `_format_human()` and a future
reader can treat it as "a match, but against a note instead of a commit."

**Window anchored to the brief's own capture time, not wall-clock "now".** The lookback window
is `[since_dt - RESEARCH_LOOKBACK_DAYS days, since_dt + RACE_GRACE_SECONDS]`, where `since_dt` is
the same already-computed `since_str` the forward-looking search uses (before that search's own
earlier-only `_widen_since` narrowing). Anchoring to wall-clock "now" would make the check
non-reproducible across reruns (a recheck run a week later would search a different window than
the original dispatch did) and would miss the actual failure mode: a note that predates the
brief by weeks is exactly the case this change exists to catch, regardless of when the check
happens to run. The `+ RACE_GRACE_SECONDS` upper bound (reusing the existing constant, not a new
one) mirrors the forward-looking search's own race-window reasoning: a note published moments
*after* a brief's capture timestamp (clock skew, or the note's commit landing seconds later in
the same session) should still count as "already existed when this brief's work was being
considered," symmetrically to how a delivering commit landing moments *before* capture already
counts as evidence today.

**`RESEARCH_LOOKBACK_DAYS = 30`, documented as a concrete, adjustable default.** Chosen as wide
enough to catch the realistic range of "a brief sits in the queue for a while, and the topic was
already closed out somewhat before that" (the motivating incident was 68 minutes; a month covers
the much more common case of a queue backlog measured in days-to-weeks per
`docs/specs/research/dashboard-run-health-visibility.md`-adjacent queue-age observations) without
scanning the entire multi-month history of `docs/specs/research/` on every dispatch. Mirrors
`audit_postmerge.py`'s existing `DEFAULT_LOOKBACK_DAYS` precedent for "a fixed, named, overridable
default lookback window" in this same package. Alternative considered: no window at all (search
every note ever written) — rejected, since the note corpus only grows and an unbounded window
means unbounded and ever-increasing `git show` calls for a search that exists to be cheap enough
"nobody has to weigh whether to run it" (module docstring).

**One `git log --name-only` call lists window candidates; no per-file mtime/glob check.** Reusing
`_run_git`'s existing plumbing, a single bounded call —
`git log <base_ref> --since=<window_start> --until=<window_end> --name-only --format= -- 'docs/specs/research/*.md'`
— returns every note path touched in the window in one subprocess, in reverse-chronological
commit order (so de-duplicating while preserving first-seen order keeps the most-recently-touched
note first). This is deliberately **not** a `Path.glob()` + filesystem-mtime check: a worktree
checkout's mtimes reflect checkout time, not commit history (this repo's own worktree-per-branch
workflow makes that the common case, not an edge case), so mtime-based recency would be
systematically wrong for exactly the environment this check runs in. It is also not a per-file
`git log -1` call per glob match — that scales with total note count, not with how many actually
changed in the window, which is the wrong axis to bound.

**Content is read from `base_ref`, not the working tree, via `git show <base_ref>:<path>`.**
Consistent with the forward-looking search's own reasoning for preferring `base_ref` over local
`HEAD` (`resolve_base_ref()`'s docstring: a stale local checkout must not blind the check to
upstream state). A candidate path that no longer exists at `base_ref` (renamed/deleted since)
degrades to "skip that candidate," not a warning — same posture as any other single-item
best-effort failure in this module.

**Match cap and note-count cap, both reported when they drop something.**
`RESEARCH_NOTE_CAP = 20` bounds how many candidate notes get a `git show` + last-touch `git log
-1` call (kept: the `RESEARCH_NOTE_CAP` most-recently-touched, mirroring `_cap()`'s "keep the
most distinctive/recent, count the rest" pattern already used for probes and `PR_RESULT_CAP`).
`RESEARCH_MATCH_CAP = 20` bounds the final reported match list the same way `PR_RESULT_CAP`
bounds `pull_requests`. A dedicated `RESEARCH_PHASE_BUDGET_SECONDS = 20` aggregate deadline
(mirroring `GH_PHASE_BUDGET_SECONDS`) bounds the whole per-note-content-fetch loop, since it is
two subprocess calls per candidate note (content + last-touch) and `RESEARCH_NOTE_CAP` alone
does not bound worst-case wall time if every call times out individually
(`SUBPROCESS_TIMEOUT_SECONDS` each). Notes not reached before the deadline are skipped and
counted in the warning, exactly like `_resolve_pr_number_probes`'s existing deadline pattern.

**Literal substring match, paths and symbols only, per candidate note.** For each candidate note
(within `RESEARCH_NOTE_CAP`), its content is checked for each of `probes["paths"]` and
`probes["symbols"]` (in that order) via plain `probe in content`. This is the same "evidence, not
proof" posture as the rest of the module: a widely-touched filename will produce false positives,
and that is expected and acceptable because a human judges every match (see the module's existing
docstring and the "Evidence Is Surfaced To The Operator, Never Auto-Applied" requirement, which
this change's evidence also flows through unchanged).

**`format_verified_absent_evidence()` / `format_verified_present_closure_note()` gain an optional
`research_notes` parameter, defaulting to `None`/empty.** No other module imports these two
helpers today (verified: `check_brief_staleness.py` is the only file referencing either name), so
widening their signature is safe. A `research_notes`-derived citation
(`f"{path} ({kind} probe: {probe})"`, parallel to `_cite_match`) is appended alongside the
existing commit/PR citations when present, so the file-state-verification closure paths cite all
three evidence kinds together, matching how they already cite `matches` and `pull_requests`
together.

**Skill doc: one new row condition, not a new ask site.** `brief-staleness-check.md`'s "Reading
the result" table's non-empty-evidence row becomes "`matches`, `pull_requests`, or
`research_notes` non-empty" — the same File-state-verification step and the same
`AskUserQuestion` prompt already documented there, extended to show research-note citations
alongside commit/PR ones. This directly satisfies proposal.md's "same operator prompt ... not a
second separate ask site."

## Risks / Trade-offs

**[Risk] A research note whose content happens to mention an unrelated file/symbol by
coincidence produces a false-positive match.** → Mitigation: unchanged from the forward-looking
search's own risk profile — the existing "Evidence Is Surfaced To The Operator, Never
Auto-Applied" requirement and File-state-verification step already exist precisely to catch this
class of false positive before anything closes automatically; this change adds a new evidence
source into that same gate, not a new gate.

**[Risk] `RESEARCH_LOOKBACK_DAYS = 30` is a guess, not derived from data on how long briefs
actually sit in the queue.** → Mitigation: documented as a concrete, named, adjustable constant
(matching `audit_postmerge.py`'s `DEFAULT_LOOKBACK_DAYS` precedent) rather than a hidden literal,
so a future incident with a wider gap is a one-line constant change, not a design change.

**[Risk] Two extra subprocess calls per candidate note (content + last-touch) adds latency to
every staleness check, not just ones that end up finding evidence.** → Mitigation:
`RESEARCH_NOTE_CAP` and `RESEARCH_PHASE_BUDGET_SECONDS` bound the worst case the same way
`PR_PROBE_CAP`/`GH_PHASE_BUDGET_SECONDS` already bound the `gh` phase; the common case (few notes
touched in a 30-day window) costs at most a handful of sub-5-second local `git` calls.

## Migration Plan

No migration needed. `research_notes` is a new, additive result key; existing callers that only
read `matches`/`pull_requests`/`checked`/`warning` are unaffected. No existing brief or research
note needs backfilling — the search runs against whatever notes already exist on the base branch
at check time. Roll out is a normal PR merge; no data migration, no flag.

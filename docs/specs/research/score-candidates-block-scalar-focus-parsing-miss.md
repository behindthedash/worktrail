# Investigation: `worktrail-score-candidates` batch mode misses near-identical briefs

## Trigger

Handoff brief `20260807-114939-worktrail-score-candidates-batch-mode`: two
functionally-identical queued briefs (`20260807-111644-enable-datalena-s-already-shipped`
and `20260807-103418-configure-datalena-s-docs-specs`, both requesting the same
`migration_path_patterns` addition to the same `~/projects/datalena/docs/specs/go-policy.yaml`
file) were not surfaced as batch candidates —
`worktrail-score-candidates <path> --queue-dir ~/work-queue --mode batch` returned
`{"batch": []}` for the primary brief.

## Verified Observations

- Both source briefs are still present on disk (now in `picked/`, claimed and
  consolidated into datalena PR #2148 by a separate session after this brief
  was filed). Their frontmatter as claimed:
  - `20260807-111644-...`: `repo: /home/briank/projects/datalena`
  - `20260807-103418-...`: `repo: null`
- Reconstructing both briefs' pre-claim `status: queued` state (stripping
  `claimed-at`/`claimed-by`/`batch`/`completed-at`) and re-running
  `score_candidates.batch_candidates()` against them reproduces `{"batch": []}`
  exactly as reported.
- `_parse_fm()` (the stdlib-only, no-yaml frontmatter parser in
  `src/worktrail/workqueue/score_candidates.py`) does not implement YAML block
  scalar indicators (`|`, `|-`, `>`, `>-`). For a `focus: |-` field followed by
  indented continuation line(s), `_parse_fm` returns the literal 2-character
  string `"|-"` as the value of `fm["focus"]`, not the actual focus text —
  confirmed directly: `_read_brief(...)[0]["focus"] == "|-"` for the primary
  brief.
- `_tokenize("|-")` returns an empty set (the tokenizer regex requires a
  leading letter), so `focus_score` (70% of `base_score` in both capture and
  batch scoring) is silently `0.0` for **any** brief pair where either side
  uses the `focus: |-` block-scalar form.
- This form is common, not an edge case: `grep -l '^focus: |-' ~/work-queue/{queue,picked}/*.md`
  matches 173 of 982 brief files (~18%) in the current queue — every brief
  whose focus text is long/multi-sentence enough that the capture-time writer
  chose block-scalar style over a plain scalar.
- With `_parse_fm`'s bug in effect, the reconstructed pair's actual score
  components were: `focus_score = 0.0` (bug), `body_score = 0.444` (correct —
  the body's rendered `## Focus` section is tokenized correctly, since it's
  plain text, not frontmatter), `base_score = 0.133`. Batch mode requires
  same-repo (hard-gated) AND `total_score >= BATCH_MIN (0.45)`.
- Patching only the companion brief's `repo:` field to match (leaving the
  parse bug in place) still reproduces `{"batch": []}`: `total_score =
  0.133 + SAME_REPO_BOOST(0.20) = 0.333 < 0.45`.
- Manually tokenizing the two briefs' **actual** focus text (bypassing the
  parse bug) gives `focus_score = 0.438`, `base_score = 0.440`,
  `total_score = 0.440 + 0.20 = 0.640 >= 0.45` — comfortably above the batch
  threshold, and would have surfaced as a `"score"`-reason batch candidate
  once same-repo also matched.

## Confirmed Root Cause

Two compounding, independently-confirmed defects, both necessary to explain
the reported miss:

1. **Primary (dominant) cause**: `_parse_fm()` in `score_candidates.py` does
   not handle YAML block scalar syntax (`|`, `|-`, `>`, `>-`). Any brief whose
   `focus:` field uses this style (~18% of the current queue) has its
   `focus_score` silently zeroed in both capture-mode and batch-mode scoring,
   because the tokenizer sees the literal indicator string instead of the
   real text. This is a straightforward parser gap, not a scoring-heuristic
   weakness — the "stronger content-similarity signal" the triggering brief's
   author speculated was needed already exists (focus-token overlap) and
   would have worked correctly if the frontmatter had parsed correctly.
2. **Secondary (compounding) cause**: the companion brief's `repo:`
   frontmatter was `null` even though its focus text unambiguously names a
   specific file in a specific repo (`~/projects/datalena/docs/specs/go-policy.yaml`).
   `batch_candidates()`'s same-repo filter is a hard per-candidate gate with
   no fallback (`if _normalize_repo(cand_fm.get("repo")) != repo: continue`),
   unlike `score_candidates()`'s capture-mode scoring, which treats same-repo
   as a soft `+0.20` boost, not a gate. A `repo: null` candidate can never
   pass batch mode's filter regardless of textual similarity. This is
   arguably correct-by-design given the module's own docstring ("Same-repo is
   REQUIRED" for batch mode, deliberately stricter than capture mode) — the
   defect here is upstream, in why the companion brief's `repo:` field wasn't
   populated at capture time despite its content clearly naming a target
   repo/file. That capture-time gap is a different subsystem
   (`worktrail-handoff` / brief authoring) and a different purpose; it is
   **not** addressed by this fix and is noted here for visibility only.

Fixing defect #1 alone is sufficient to resolve this specific reported case,
*conditional on* the companion brief's `repo:` field being correctly
populated (defect #2) — both must hold for the pair to batch. Defect #1 is
the one squarely inside `score_candidates.py`'s own responsibility and is
fixed in this run. Defect #2 (capture-time repo inference) is flagged as a
candidate follow-up, not implemented here — different purpose, different PR.

## Fix (Route F, same run)

Add YAML block-scalar support to `_parse_fm()`: recognize a value matching
`^[|>][+-]?$` as a block-scalar indicator, consume subsequent indented
(or blank) lines as the block body, and join them (`\n` for `|`-style,
preserving line breaks; ` ` for `>`-style, folding them) instead of storing
the bare indicator string. Regression test added covering a `focus: |-`
frontmatter field, asserting the parsed value is the real text (not `"|-"`)
and that `batch_candidates()` now surfaces the previously-missed companion.

## Recommended next route

None — continuing directly into Route F in this run per the Route I
playbook (root cause confirmed, fix small and clearly in scope of this
repo's own `workqueue` module).

## Context

See `proposal.md` - Why. `worktrail-repo-init propose` (`src/worktrail/onboarding/repo_init.py`)
already exists and is write-if-absent for everything it scaffolds (`detect_state()` +
`cmd_propose()`'s per-file `if path.is_file(): skip` pattern). `discover_ci_checks()`
already models the report-only shape this design follows: it never auto-populates
`required_status_checks`, only surfaces `ci_jobs_discovered` for a human to review.

## Goals / Non-Goals

**Goals:**
- Surface drift on already-scaffolded files without ever silently rewriting them.
- No new CLI surface -- `propose` already returns structured JSON; drift is an
  addition to that existing contract, not a new mode.
- Correctly distinguish real template drift from expected, operator-driven growth
  (a ruleset's `required_status_checks` list).

**Non-Goals:**
- Not implementing the interactive "upgrade this file?" prompting itself -- that
  belongs to whichever skill drives `propose` (documented separately in that skill's
  own SKILL.md, out of scope for this change).
- Not adding drift detection for hand-edited config (`.worktrail/policy.yaml`) or
  third-party-tool state (`openspec/`, `.aspens.json`, `.gitnexus/`) -- there is no
  single "current" template for either to diff against.

## Decisions

**No `--check-drift`/`--interactive` flag; drift is computed unconditionally.** `propose`
doesn't need to know or branch on whether its caller is a human at a terminal or an
agent -- it always returns the same JSON shape. The "is this interactive" question is
answered entirely by the consumer of that JSON, not the CLI. This also means `--check`
(the existing read-only probe mode) gets the same `drift` field for free.

**Ruleset comparison excludes `required_status_checks` entirely, not just diffs it
loosely.** A raw byte/structural diff including `required_status_checks` would flag
every ruleset an operator has ever added a required check to -- which is the expected,
intended outcome of `discover_ci_checks()`'s own review flow, not drift. Stripping the
`required_status_checks` rule type from both sides before comparing (`_ruleset_structural_view`)
cleanly separates "did the ruleset's structural policy (merge methods, review-thread
resolution, linear history) go stale" from "did an operator add a check" -- only the
former is drift. Alternative considered: compare `required_status_checks` too but with
a fuzzy allowlist of "expected" additions -- rejected as unnecessarily complex when a
clean field-level exclusion works.

**Workflow/script files compare full content, not structurally.** Unlike rulesets,
`.github/workflows/worktrail-auto-merge.yml`, `worktrail-openspec-validate.yml`,
`rulesets_drift_guard.yml`, and the vendored `rulesets_sync.py`/`requirements.txt` have
no operator-growth field -- they are meant to be regenerated wholesale, not hand-edited
in place. A full-content diff is the correct, simplest check for these.

**Drift is never auto-applied, even when detected.** `propose` stays write-if-absent
for every file it already found on disk -- `compute_drift()` only ever reports;
`cmd_propose()`'s existing skip logic is completely unchanged. This preserves the
"never surprise you" posture the module's own docstring already commits to for
`required_status_checks`, extended to every other scaffolded file.

## Risks / Trade-offs

- **[Risk] A future generator change makes an intentional, cosmetic-only edit (e.g.
  reformatted YAML) look like drift for every already-onboarded repo** → **Mitigation**:
  out of scope for this change; the report is advisory, so a false positive here costs
  a reviewed-and-declined prompt, not a silent bad outcome.
- **[Risk] Scope creep -- a future change tries to add drift detection for
  hand-edited files like `policy.yaml`** → **Mitigation**: this design's own
  "Hand-edited and third-party-owned files are out of scope" requirement and its
  scenario exist specifically to make that an explicit, reviewable spec change rather
  than a silent extension.

## Migration Plan

No migration needed -- this only adds a new field to `propose`'s existing output.
Existing callers that don't read `drift` are unaffected.

## Why

Intake-brief triage can loop forever on `keep`. Brief
`20260902-080526-worktrail-drain-resume-pass-close` demonstrates it: its focus quotes a
drain-log error line (`close-stale-bookkeeping error: ... no TASK-*.md found ...`) and ends
with `Repo: worktrail, src/worktrail/drain/drain.py close-stale resume pass`, yet its
frontmatter has `repo: null` because it was captured from `$HOME`. The evaluator returned
`keep` with high confidence (its evidence cited `drain.py:1494-1503` by inspection but no
test or command), `apply` was a noop that wrote nothing, and every re-run of
`worktrail-go <brief-id>` reproduces the identical noop. Four independent defects combine:
capture lost the repo; the evaluator ignored reproduction evidence already quoted in the
brief; the evaluator returned `keep` for a null-repo brief where the `intake-triage` scenario
"Null-repo brief has no candidates" says `needs-decision`; and the interactive gate only
re-confirms the machine's inconclusive verdict, so a human is asked to rubber-stamp a
non-decision.

The goal is convergence: no intake brief may remain on `keep` indefinitely, and no verdict
path may require a human except a genuine product decision filed as a `worktrail-decision`
(`needs-decision`). Everything else resolves mechanically or by the evaluator.

## What Changes

- **Deterministic repo inference.** `_infer_repo_from_focus` (today: only a leading
  `<project>: ` prefix) resolves, in order: (a) a `Repo: <name>` / `repo: <name>` token
  anywhere in the focus; (b) a known repo name (a git checkout directly under the repos root,
  default `~/projects`) appearing as a whole word; (c) a repo-relative path fragment that
  exists in exactly one checkout under the repos root. Exactly one unambiguous match sets the
  repo; zero or several leave it null. It runs at capture time in `create_handoff` and again
  at triage time for briefs still carrying `repo: null`, before they are bucketed into the
  no-repo group. A triage-time inference writes the repo back to the brief's frontmatter with
  a `## Triage <date>` note (`verdict: repo-inferred`) so it is visible and never repeated.
- **Mechanical premise check feeds the evaluator, and quoted evidence counts.** Before the
  evaluator runs on a repo-resolved brief, a deterministic pass extracts quoted error
  strings / log lines, `path[:line]` references, and allow-listed test commands from the
  focus, greps the checkout for each string and path (falling back to stable fragments of a
  quoted log line when the whole line is runtime-formatted), and runs a named test command
  read-only with a bounded timeout. Results are attached to the evaluator prompt as a
  "Mechanical premise check" block and persisted on the verdict as `premise_check`
  (`[{kind, needle, confirmed, detail}]`). A `work-directly` verdict is accepted when EITHER
  the evaluator's evidence matches the reproduction-evidence pattern OR `premise_check` has
  at least one confirmed hit; both enforcement sites (apply and preview) use the combined
  rule. The evaluator prompt instructs citing log/error output already quoted in the brief.
- **Bounded keep with deterministic escalation.** Every `keep` verdict, scheduled or
  interactive, appends a `## Triage <date>` section recording `verdict: keep`, the evidence,
  and the running consecutive-keep count (today `keep` writes nothing, so nothing can count
  it). A brief is due for escalation when its consecutive keep count reaches
  `triage_keep_limit` (policy key, default 2) or it has been queued longer than
  `triage_max_queue_age_days` (policy key, default 14). A due brief is never given `keep`
  again: after the evaluator (or instead of it, for an unresolvable repo) the verdict is
  chosen by a fixed matrix — repo resolved AND premise confirmed → `work-directly`; repo
  resolved, premise unconfirmed, under the WIP cap → `propose-change` (defect-fix change
  scaffolded from the focus); repo resolved and over cap → `fold-into-change` into the top
  ranked candidate, else `needs-decision`; repo unresolvable → `needs-decision` asking which
  repo the brief belongs to. The recently-triaged dedup window does not skip a due brief.
  Escalation reasons and counts appear in the verdict file, the run report, and the `--json`
  summary.
- **Null-repo rule enforced in the validator.** A `keep` verdict (including every fail-open
  fallback) for a brief in the no-repo group whose repo could not be inferred is recorded as
  `needs-decision` with the repo-assignment question, matching the existing `intake-triage`
  scenario. Once that decision is answered with a repo, the next inventory writes the repo
  back to the brief, consumes the decision, and evaluation proceeds normally.
- **Interactive pickup is autonomous.** `worktrail-go <intake-brief-id>` runs evaluate +
  apply under exactly the same rules as the scheduled run (inference, premise check,
  null-repo rule, escalation) with no `AskUserQuestion` confirmation, reports the action-log
  entry, and when the resulting verdict is `work-directly` continues in the same invocation
  into the normal claim + classify + dispatch flow instead of stopping. `--apply-brief-triage`
  keeps `--confirm` as the flag that authorizes writes; the skill passes it unconditionally.
  The `worktrail-go` SKILL.md Phase 2 intake gate prose and the `intake-triage` requirement
  "Interactive pickup of an intake brief triages it" say this.
- **Regression test** reconstructing brief `20260902-080526` (`repo: null`, the focus above)
  against a fixture repo containing the quoted error string, asserting a first-pass
  evaluation infers the fixture repo and yields `work-directly` applied as
  `seeded-from: triage:<date>:direct`.

## Capabilities

### New Capabilities
(none — every behavior here extends the two existing triage capabilities)

### Modified Capabilities
- `intake-triage`:
  - "Interactive pickup of an intake brief triages it" — no confirmation prompt; same rules
    as the scheduled run; `work-directly` continues into claim + dispatch in the same
    invocation.
  - "Work-directly converts an intake brief into an execution brief" — acceptance is the
    combined rule (evaluator citation OR confirmed premise-check hit).
  - "Needs-decision files a pending decision and keeps the brief queued" — an answered
    repo-assignment decision writes the repo back and is consumed before the next
    evaluation.
  - ADDED "Brief repo is inferred deterministically from its focus".
  - ADDED "Mechanical premise check precedes evaluation".
  - ADDED "Keep verdicts are bounded and escalate deterministically".
- `queue-triage`:
  - "Repo-grouped inventory with dedup skip" — triage-time inference before no-repo
    bucketing; `repo-inferred` notes do not count for dedup; a brief due for escalation is
    never dedup-skipped.
  - "Evidence-required verdict per brief" — a no-repo `keep` is recorded as
    `needs-decision`; verdicts carry `premise_check`.
  - "Apply step never closes a brief without an approved verdict" — `keep` now appends a
    triage note under `--confirm` (still never claims, closes, or edits frontmatter).
  - "Verdict file and human-readable report" — escalation reasons and counts are reported.

## Impact

- `src/worktrail/workqueue/create_handoff.py` — `_infer_repo_from_focus` delegates to a new
  `workqueue/repo_inference.py` (three rules + ambiguity guard, `repos_root` parameter).
- `src/worktrail/workqueue/premise_check.py` (new) — needle extraction, grep confirmation,
  allow-listed bounded command run.
- `src/worktrail/workqueue/queue_triage.py` — prompt block, `Verdict.premise_check` /
  `Verdict.escalation`, triage-time inference + write-back in grouping, triage-history
  parsing (`is_recently_triaged` and consecutive keep count), escalation matrix, null-repo
  validator rule, answered repo-decision consumption, `keep` note writer, combined
  work-directly acceptance at both enforcement sites, report/summary additions, a shared
  per-group evaluation pipeline used by both the CLI and the interactive single-brief path.
- `src/worktrail/router/policy.py` — `triage_keep_limit` (2) and
  `triage_max_queue_age_days` (14) policy keys with the same integer validation as
  `max_active_changes`.
- `src/worktrail/router/skill_dispatch.py` — `evaluate_single_brief` uses the shared
  pipeline (so inference, premise check, null-repo rule, and escalation apply
  interactively); `--apply-brief-triage --confirm` semantics unchanged in code, no longer
  gated on a human step in the skill.
- `skills/worktrail-go/SKILL.md` — Phase 2 intake gate: no `AskUserQuestion`, unconditional
  `--confirm`, `work-directly` flows into Phase 3; "When to Use" and the worked example
  updated. Note: active change `shared-pr-landing-pipeline` (task 8.1) also edits this
  gate's step 3; whichever lands second rebases the paragraph, and both intents compose.
- Drain's `--intake-triage` pre-pass picks the new behavior up unchanged (it shells through
  `queue_triage evaluate` / `apply --confirm`); its summary-block contract is not modified.
- Not touched (owned elsewhere): the fold-candidate score floor and evidence sanitization
  (`queue-triage-fold-defects-fix`), bare repo-name resolution inside fold/propose apply
  (`resolve-bare-repo-name-in-fold-propose-apply`), and the drain close-stale OpenSpec bug
  the motivating brief describes.

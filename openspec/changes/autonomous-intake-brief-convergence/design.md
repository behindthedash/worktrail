## Context

See proposal.md — Why. The relevant current state in `src/worktrail/workqueue/queue_triage.py`:

- `group_queue_by_repo()` buckets on the literal `repo:` frontmatter value; `NO_REPO_KEY`
  collects nulls. `inventory()` composes it with `is_recently_triaged()` (any `## Triage
  <date>` heading inside the window) and `has_unresolved_decision()`.
- `evaluate_group()` ranks candidates, formats the prompt, spawns one evaluator per group,
  and returns `{repo, brief_ids, raw_text, candidates_by_brief}`. `parse_verdicts()`
  validates and falls open to `keep`. `apply_wip_cap_preview()` stamps `repo` and
  `held_by_wip_cap` afterward. `cmd_evaluate()` and
  `router/skill_dispatch.evaluate_single_brief()` each wire this chain by hand.
- `apply_verdicts()` treats `keep` as a pure noop in both confirm and preview modes;
  `_apply_work_directly()` and `_preview_verdict()` each apply
  `_REPRODUCTION_EVIDENCE_RE` to the evaluator's evidence alone.
- `create_handoff._infer_repo_from_focus()` matches only a leading `<project>: ` prefix
  against `~/projects/<name>`.
- `router/policy.py` `DEFAULTS` already carries `max_active_changes` with integer
  validation, and `tests/router/test_policy_key_enforcement_coverage.py` requires every
  policy key to have a registered consumer.
- `router/brief_probes.extract_probes()` already extracts path tokens from prose.
- `workqueue/decisions.py` exposes `find_decision`, `load_decision_envelope`,
  `validate_decision_answer`, and `resolve_decision` (which archives the record and strips the
  brief's `awaiting-decision:` stamp).

Active changes touching the same surfaces: `queue-triage-fold-defects-fix` (candidate score
floor, fold evidence sanitization, fetch-before-branch), `resolve-bare-repo-name-in-fold-propose-apply`
(repo resolution inside fold/propose apply, `--repos-root` on `apply`), and
`shared-pr-landing-pipeline` (task 8.1 edits the worktrail-go intake gate step 3 and modifies
the `intake-triage` "Interactive pickup" requirement). This design composes with all three and
edits none of the code they own.

## Goals / Non-Goals

**Goals:**
- Every intake brief reaches a non-`keep` outcome within a bounded number of runs without a
  human, except a genuine product decision filed as `needs-decision`.
- The interactive and scheduled paths share one evaluation pipeline so they cannot drift.
- Every new mechanical step is deterministic, read-only against target repos, and visible on
  the brief or in the run report.
- Keep the implementation DAG as wide as the format allows: repo inference, the premise
  check, the policy keys, and the skill prose are separate modules in their own task groups
  with declared `files:` scope, so they compile to independent lanes. Everything that must
  build in order (the `queue_triage.py` chain and the `skill_dispatch.py` path that calls
  into it) is one group, because OpenSpec's `tasks.md` carries no cross-group edge and a
  seeded plan's only edges are each group's own chain (verified by compiling this change
  with `--no-llm`: every task has scope, source `seed`, width 6).

**Non-Goals:**
- Changing candidate ranking, its score floor, fold evidence formatting, or the fold/propose
  worktree flow (owned by the two active queue-triage changes).
- Changing the drain `--intake-triage` summary contract (`summary_contract.py`); drain shells
  through `queue_triage evaluate`/`apply --confirm` and inherits the new behavior.
- Fixing the drain close-stale OpenSpec bug the motivating brief describes.
- Running arbitrary commands named in a brief; only allow-listed read-only test runners run.

## Decisions

### D1. Repo inference lives in a new `workqueue/repo_inference.py`; `_infer_repo_from_focus` becomes a thin wrapper

`infer_repo(focus, repos_root=None) -> InferenceResult(repo: str | None, rule: str | None,
candidates: list[str])` implements rules (a) → (b) → (c). "Known repo" is any direct
subdirectory of `repos_root` that contains a `.git` entry (directory or file); this excludes
`*-worktrees/` containers and hidden dirs without a hand-maintained allow-list. Rule (a) uses
`\b[Rr]epo:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)` and accepts the token only if it names a known
repo (basename match, `owner/name` accepted by basename). Rule (b) tests each known repo name
as a whole word (`(?<![\w-])name(?![\w-])`, so `datalena` does not match inside
`datalena-worktrees`). Rule (c) takes path tokens from `brief_probes.extract_probes()`
(strip a trailing `:line`) and keeps a repo only if `repos_root/<repo>/<path>` exists; the set
of repos hit across all path tokens must have size 1. Each rule returns on exactly one
distinct repo; zero falls through; two or more returns `repo=None` with `rule` naming the
rule that was ambiguous and `candidates` listing them (for the note and tests).
`create_handoff._infer_repo_from_focus(focus)` delegates with the default root and returns
the absolute path, preserving its existing call sites and tests.

*Alternative considered:* extending `_infer_repo_from_focus` in place. Rejected because
`queue_triage.py` would then import `create_handoff.py` (which imports `score_candidates`
and runs an overlap scan at import-free but heavy call time), and because a separate module
keeps this task off the `queue_triage.py` critical path.

### D2. Triage-time inference is a pre-grouping pass with a `verdict: repo-inferred` note that the dedup window ignores

`group_queue_by_repo(repos_root=None)` gains a pre-pass: for each `.md` in `queue/` with a
null/blank `repo:`, first `consume_repo_decision(path, repos_root)` (D8), then
`infer_repo`. On a hit it calls `work_queue._set_fm_fields(path, {"repo": abs_path})` and
appends `## Triage <today>\n\nverdict: repo-inferred\nrule: <a|b|c>\nrepo: <abs_path>\n`.
Because every triage note now carries a machine-readable `verdict:` first line (D4),
`is_recently_triaged()` ignores `repo-inferred` notes when computing the most recent triage
date. The write-back happens in the same run the brief is then evaluated in, so the note
never delays the brief.

*Alternative considered:* a differently named heading (`## Repo inference <date>`). Rejected
because the requirement text and every existing reader (`_TRIAGE_HEADING_RE`, dashboards,
humans) already key on `## Triage`; one heading with a typed first line is simpler.

### D3. Premise check is a new `workqueue/premise_check.py` with a fixed needle grammar and an allow-list

`extract_needles(focus) -> list[Needle(kind, needle)]`:
- `quoted`: text inside matching `'…'`, `"…"`, or `` `…` `` at least 12 characters long that
  is not itself a path or command (so a back-ticked path is classified `path`, not `quoted`).
- `path`: `brief_probes.extract_probes(focus)["paths"]` tokens, with an optional `:N`
  suffix captured as the line.
- `command`: a token run starting with `pytest`, `python -m pytest`, `python3 -m pytest`,
  `npm test`, `go test`, `cargo test`, `ruff check`, or `mypy`, extending to the end of the
  quoted span or sentence. Any other token run that looks like a shell command (starts with a
  `worktrail-*` script, `make`, `rm`, `git`, etc.) is recorded as `command` with
  `confirmed: false` and detail `not an allow-listed test runner`, so the report shows it
  was seen but never run.

`run_premise_check(focus, repo_path, *, timeout_s=120) -> list[dict]`:
- `quoted`: `git grep -nIF -- <needle>` (checkout is always a git repo; `-I` skips binaries).
  If no hit, split the needle on `...`/`…` and on `: `, keep fragments ≥ 12 chars, and grep
  each; confirmed on the first fragment hit, detail = `fragment '<f>' → <file>:<line>`. Hit
  output is capped at 5 lines. This is what makes the motivating brief converge: the whole
  quoted log line is runtime-formatted but `no TASK-*.md found` is a literal in `drain.py`.
- `path`: `repo_path / path` exists; with `:N`, the file has ≥ N lines.
- `command` (allow-listed only): `subprocess.run(shlex.split(cmd), cwd=repo_path,
  timeout=timeout_s, capture_output=True)`; `confirmed = returncode != 0` (a defect brief's
  premise is "this fails"); detail = exit code + last 20 lines. `TimeoutExpired` →
  `confirmed: false`, detail `timed out after <n>s`. Read-only is guaranteed by the
  allow-list (test runners and linters), not by sandboxing; the design explicitly does not
  add a working-tree diff check.

The result list is attached to the prompt as an indented "Mechanical premise check:" block
under each brief line (empty block text `(none)` when no needles), and returned from
`evaluate_group()` as `premise_by_brief`. `parse_verdicts(..., premise_by_brief=None)` copies
it onto `Verdict.premise_check` (new field, `list[dict]`, default `[]`, JSON round-trips
through `verdict.json` unchanged).

*Alternative considered:* letting the evaluator run the greps itself. Rejected: that is the
status quo that produced the motivating keep; the check must be deterministic and persisted.

### D4. Triage notes get a typed first line; consecutive keep count is parsed, not stored in frontmatter

`triage_history(path) -> list[TriageNote(date, verdict, keep_count)]` parses every
`## Triage <date>` section: `verdict:` on the first non-blank line (absent → `legacy`) and
`keep-count:` when present. Consecutive keep count = length of the trailing run of
`verdict: keep` notes (a `legacy`, `needs-update`, `repo-inferred`, or any other note ends the
run). `_apply_keep(v, run_date)` appends
`## Triage <run_date>\n\nverdict: keep\nkeep-count: <n+1>\n\n<evidence>\n`, reusing
`_apply_needs_update()`'s append shape, and reports `action: append-triage-note`,
`status: executed`; preview reports `status: planned` with the note text. No frontmatter is
touched, so `keep` still never changes a brief's claimability.

*Alternative considered:* a `triage-keep-count:` frontmatter field. Rejected: a second source
of truth that can drift from the notes, and `validate_brief`'s canonical-style checks would
need extending.

### D5. Escalation is a post-parse step on the shared pipeline, with the WIP cap re-checked at that moment

`escalation_due(path, repo) -> str | None` returns `keep-limit` when the consecutive keep
count ≥ `triage_keep_limit`, else `queue-age` when `today - created > triage_max_queue_age_days`,
else `None`. Both limits come from `policy.load_policy(repo)` (new `DEFAULTS` entries 2 and
14, validated exactly like `max_active_changes`); a null repo uses the defaults.

`escalate(v, path, repo, candidates) -> Verdict` is applied only when `escalation_due` is set
and `v` "would resolve to keep at apply time": `v.verdict == "keep"`, or `work-directly` failing
`_work_directly_accepted(v)` (D6), or `propose-change` with `_propose_change_over_cap(v)`.
Matrix, in order:
1. repo resolved and any `premise_check[*].confirmed` → `work-directly` (evidence = original
   evidence + the confirmed needle, so the apply-time rule also passes).
2. repo resolved, cap unset or `_count_active_changes(repo) < cap` → `propose-change`,
   `target_repo=repo`, `proposed_change_name` = brief id with the leading `YYYYMMDD-HHMMSS-`
   stripped, validated against `_KEBAB_CASE_RE`, else `fallback_slugify(focus)[:60]`.
3. repo resolved, over cap → `fold-into-change` with `target_change=<repo>:change:<candidates[0]>`
   when `candidates` (the list `evaluate_group()` presented, already floor-filtered once
   `queue-triage-fold-defects-fix` lands) is non-empty; else `needs-decision`, question
   `repo is over its WIP cap and no fold candidate scored high enough; which active change
   should absorb this brief?`.
4. repo unresolvable → `needs-decision`, question `REPO_ASSIGNMENT_QUESTION`.

The matrix stamps `Verdict.escalation = {"reason": ..., "row": "work-directly"|"propose-change"|
"fold-into-change"|"needs-decision"}` (new field, default `None`). For row 4 the pipeline
short-circuits before `evaluate_group()`: `inventory()` returns such briefs in a separate
`escalate_without_evaluator` list so no agent is spent on them.

The "cap allows" clause of row 3 in the request resolves to needs-decision here because row 3
is by definition over cap at the moment of the matrix; the cap is re-read via
`_propose_change_wip_cap_status()` at that moment rather than trusting `held_by_wip_cap`.

### D6. One acceptance predicate for work-directly, used at both enforcement sites

`_work_directly_accepted(v) -> bool` = `_REPRODUCTION_EVIDENCE_RE.search(v.evidence or "")`
or `any(e.get("confirmed") for e in v.premise_check)`. `_apply_work_directly()` and the
`work-directly` branch of `_preview_verdict()` both call it; the downgrade note text names
which half failed. The evaluator prompt's step 2b gains: "The Mechanical premise check block
under each brief lists error strings, paths, and commands already confirmed against the
checkout — cite a confirmed entry (quote it) as your reproduction evidence rather than
restating the brief."

### D7. Null-repo rule lives in `parse_verdicts` via a `no_repo` flag

`parse_verdicts(..., no_repo=False)`: after the existing per-brief resolution, when
`no_repo` and the chosen verdict is `keep` (including the fallback), replace it with
`needs-decision`, `question=REPO_ASSIGNMENT_QUESTION`, evidence unchanged. The flag is passed
by the shared pipeline as `repo == NO_REPO_KEY`. `_apply_needs_decision()` is unchanged;
`decision_identity()` already converges repeated filings of the same question on the same
brief.

### D8. Answered repo decisions are consumed in the inventory pre-pass

`consume_repo_decision(path, repos_root) -> str | None`: if the brief carries
`awaiting-decision: <id>`, `find_decision(id)` reports `answered`, and the record's question
equals `REPO_ASSIGNMENT_QUESTION`, resolve the answer text with
`dashboard._resolve_repo_dir(answer, repos_root)`; on success `_set_fm_fields(path,
{"repo": abs})`, `resolve_decision(id)` (archives the record and clears the stamp), append a
`verdict: repo-inferred` note with `rule: decision`, and return the repo. An answer that
resolves to nothing is reported in the run's `unresolvable_answers` list and the brief is left
untouched (still skipped by `has_unresolved_decision()`). Non-repo decisions are untouched.

### D9. One shared pipeline for scheduled and interactive evaluation

`evaluate_briefs(repo, briefs, *, agent, cwd, repos_root) -> list[Verdict]` in
`queue_triage.py` does: premise checks → `evaluate_group()` → `parse_verdicts(no_repo=...,
premise_by_brief=...)` → `apply_wip_cap_preview()` → `escalate()` per brief. `cmd_evaluate()`
calls it per group; `skill_dispatch.evaluate_single_brief()` calls it with a one-brief list
after running the same D2 pre-pass on that brief (so `--evaluate-brief-triage` without
`--triage-repo` infers and writes back exactly like the scheduled run, and then uses the
inferred repo as the group). `evaluate` gains `--repos-root` (default `~/projects`, mirroring
`dashboard.py` and the pending `apply --repos-root`); `--evaluate-brief-triage` gains
`--triage-repos-root` with the same default.

### D10. Interactive gate: the skill stops asking; `--confirm` stays the write authorization

`skill_dispatch.py` keeps `--apply-brief-triage` / `--confirm` as they are — the flag is the
programmatic write authorization every scheduled path already uses, and dropping it would
silently make a preview invocation a write. The change is in `skills/worktrail-go/SKILL.md`
Phase 2: step 2 (present + `AskUserQuestion`) is deleted; step 3 always passes `--confirm`;
after reporting the action-log entry, a `work-directly` result with `status: executed`
continues to Phase 3's `claim` action for the same brief id (the brief now carries
`seeded-from:` so `brief_dispatch_mode()` would classify it `claim` on a re-read); every other
verdict stops as before. The "When to Use" bullet and the worked example are updated to
match. The landing-outcome sentences `shared-pr-landing-pipeline` 8.1 adds to step 3 are
kept verbatim in the rewritten step so the two edits compose regardless of order.

### D11. Reporting

`compute_run_summary()` adds `escalations: {"by_reason": {...}, "by_verdict": {...}}` and
`repos_inferred: int` (from a per-run list the inventory pre-pass returns). `write_report()`
adds `## Repos inferred` and `## Escalations` sections; `cmd_evaluate --json` prints the same
two keys. Drain's `summary_contract.py` field tuple is not extended; it reads only the keys it
already names.

## Risks / Trade-offs

- [Rule (b) false positive: a repo name used as an ordinary word, e.g. a brief about
  "continuum" concepts] → rule (a) wins when present; rule (b) requires exactly one distinct
  repo name; the write-back note names the rule so a wrong inference is visible and can be
  corrected by editing `repo:`; a wrong repo then produces an unconfirmed premise, never a
  silent `work-directly`.
- [Premise-check command runs a slow suite inside a scheduled run] → allow-list plus a
  120 s timeout per command and at most one command needle per brief; timeout is recorded as
  unconfirmed, never as failure of the run.
- [Escalation to `propose-change` on a wrong premise creates a spurious change] → the
  propose path already validates with `openspec validate` and opens a reviewable PR; the
  change's proposal carries the brief evidence and `keep` history; and row 2 only fires
  after `triage_keep_limit` keeps or 14 days queued, so it is the bounded alternative to
  idling forever.
- [Two active changes modify the same `queue-triage` requirement text ("Apply step never
  closes…", also modified by `resolve-bare-repo-name-in-fold-propose-apply`) and the same
  `intake-triage` requirement ("Interactive pickup…", also modified by
  `shared-pr-landing-pipeline`)] → this change's delta text is a superset that already
  includes both pending edits verbatim; whichever archives second carries the merged text.
  Recorded here so the archiver checks rather than assumes.
- [`shared-pr-landing-pipeline` 8.1 and this change both rewrite SKILL.md Phase 2 step 3]
  → D10 keeps its landing-outcome sentences; the second to land rebases one paragraph.
- [Group 4's tasks 4.4 and 4.5 import `repo_inference` and `premise_check`, which groups 1
  and 2 create in other lanes, and the plan has no edge to say so] → the group 4 heading
  states the ordering in the same convention `shared-pr-landing-pipeline` uses for its
  prerequisite group; group 4's first three tasks (history, keep note, escalation, and their
  tests) need nothing from the other lanes, so the lane loses no time if it starts early;
  `_escalation_limits` reads the policy keys with defaults so groups 3 and 4 can land in
  either order.
- [Keep notes make every scheduled run write to every kept brief] → that is the intent
  (visibility and counting); the queue's git sync cron already commits queue writes.
- [`triage_keep_limit` default 2 with a 25-day dedup window means escalation after ~50 days
  on the scheduled cadence] → `triage_max_queue_age_days` (14) is the binding bound for
  scheduled runs; interactive pickups count too.

## Migration Plan

No data migration. Existing `## Triage` notes without a `verdict:` line parse as `legacy` and
neither count as keeps nor break anything. Existing verdict files without `premise_check` /
`escalation` load with defaults. New policy keys default to 2 and 14 in `DEFAULTS`, so repos
without them see the defaults. Rollback is a code revert; notes already written are inert.

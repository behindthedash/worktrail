## Context

See proposal.md for the incident and the verified mechanics. The pieces this design must fit,
verified in this checkout:

- `build_worker_prompt` (`dispatch.py`) renders the scope line from
  `", ".join(task.get("files", []))` (line 635) and the hard rule "Touch no files outside
  scope" (line 750); per-role action text lives in `_ROLE_ACTION` (implement / review / fix /
  cleanup).
- `parse_report_back` (`dispatch.py:1151`) validates only `task`/`step`/`status` and returns
  the raw JSON dict — unknown keys already ride through untouched, which is what makes an
  optional `fold_ins` key backwards-compatible by construction.
- `apply_report` (`dispatch.py:1233`) is the single funnel every report-processing path calls
  before state moves: `orchestrate.py`'s scripted/replay drive, `live_run`'s cassette path
  (`live.py:3308`), and the live journal path `_apply_step_commit` (`live.py:4412`). All three
  then build their journal/cassette entry from `orchestrate._REPORT_FIELDS` and stamp
  `terminal_status` when the new status lands terminal.
- `_would_land_terminal` (`live.py:3574`) asks `dispatch.transition` whether a report would
  land terminal, to decide whether the missing-context recovery should fire — any new
  fail-closed rule must be visible to it or the two disagree.
- Declared-scope path normalization already has a precedent: `_apply_pre_commit_backstop`
  (`live.py:~3850`) tests changed paths with `{os.path.normpath(p) for p in (task.get("files")
  or [])}`.
- `_REVIEWER_SYSTEM_PROMPT` (`live.py:301`) is prepended (`--append-system-prompt` for
  claude, inline prefix otherwise) to review-role spawns; its text is pinned by
  `tests/orchestrator/test_live_extras.py`.
- `render_pr_body` (`router/land_pr.py:260`) is the shared PR body renderer, called from
  `land_pr()` with `LandRequest` fields; the `land-pr` CLI (`land_pr.py:2099`) is the
  executor-facing surface. `tests/router/test_land_pr.py` pins the section content.
- `test_absent_notes_are_byte_identical` (`tests/orchestrator/test_dispatch.py:1540`) pins
  every role's prompt identical across absent / `None` / `""` learned notes.

## Goals / Non-Goals

**Goals:**

- One clause, one schema key, one code predicate, one reviewer duty, one doctrine section —
  each placed where the decision is actually made (worker prompt, report application, review,
  authoring).
- Backwards compatibility that is testable: absent `fold_ins` ⇒ byte-identical behavior to
  today at every seam (parse, journal entry, transition).
- Fail-closed enforcement with an audit trail, not a silently-accepted claim.

**Non-Goals:**

- No change to `worktree_guard_hook.py` (a worktree-containment guard, verified): fold-ins
  strictly inside declared scope need no widening.
- No change to route classification, triage verdicts, or the escalation machinery
  (`_scope_escalation_files`) — the escalation hatch stays the answer for files OUTSIDE scope;
  fold-ins are the in-scope counterpart that never needs it.
- No machine enforcement of "mechanical" or the caps — see Decision D4.

## Decisions

### D1 — The clause is role-scoped prompt text, added outside the notes conditional

The fold-in clause is appended to the implement and fix action text in `_ROLE_ACTION` (or as a
conditional hard-rule line gated on `role in (ROLE_IMPLEMENT, ROLE_FIX)`), not a generic line
for every role: cleanup must not change behavior, review does not edit source, and the
group-level roles have their own prompts. The notes conditional in `build_worker_prompt`
(`LEARNED_NOTES_HEADING if ctx.learned_notes else []`) is left untouched, so the guarantee
`test_absent_notes_are_byte_identical` asserts — absent / `None` / `""` render identically for
every role — keeps holding and keeps meaning something: the clause renders in all three cases
because it never depends on notes.

The clause text must carry, compactly: the three conditions (verified / in-scope / mechanical,
with "behavior-contract changes are Route G, never fold-ins"), the caps, "separate commit", the
`fold_ins` report-back entry per fix, and the notes fallback ("report it; capture is the
orchestrator's/authoring stage's job") — because the prompt is the only place the worker reads
this before acting.

### D2 — `fold_ins` is an optional report key, parsed by tolerance, validated by predicate

`parse_report_back` keeps its required-field set unchanged: absence of `fold_ins` (and an empty
array) is zero fold-ins, never an error. Normalization is a pure helper —
`fold_in_violations(task, report) -> list[...]`: an entry violates when its `file` is missing /
blank / non-string, or when `os.path.normpath(file)` is not a member of the normalized declared
scope set (the D6 normalization precedent above; membership is equality, not prefix — declared
scope entries are file paths). A non-list `fold_ins` value is itself a violation. Valid entries
are returned separately (or via a companion normalizer) for journaling.

### D3 — Enforcement lives in `apply_report`, and `_would_land_terminal` consults the same predicate

Placing validation in `apply_report` gives every report path the same enforcement from one
place. When `fold_in_violations` is non-empty, `apply_report` forces the task's new status to
the terminal `failed` state instead of whatever `transition` computed — so the existing
downstream machinery does the rest unchanged: `_apply_step_commit` stamps the same
`terminal_status` other terminal failures get, `clear_tasks`/quarantine surfaces classify it
identically, and the offending entries are journaled for audit. Since
`_would_land_terminal` (the missing-context recovery's gate) asks `transition` directly, it
also consults `fold_in_violations` — otherwise a doomed report could still trigger a
missing-context recovery for a task that is about to fail terminally.

**Alternatives considered:**

- *Route into the fix loop like a FAILED review* (fixing → retry → escalate). Rejected: the
  fix worker's brief reads that round's review file, which a report-contract violation never
  produces, and a fold-in violation is not a code-quality finding — re-dispatching would carry
  the out-of-scope commit through another round before hitting the same wall.
- *Terminal `escalated` instead of `failed`.* Escalation carries the pending-decision envelope
  (a human answer about contradictory AC), which this is not. Terminal `failed` matches the
  existing malformed-report treatment ("marking failed so completed work can still integrate")
  and needs no new classification anywhere downstream.
- *Reviewer-only enforcement.* A policy that holds only when a probabilistic reviewer notices
  is not enforcement; the scope-membership check is exactly the part a machine can decide.

**Recorded semantics (the binding contract for the tests):**

- Caps — at most 2 fold-ins and ~20 changed lines per task — are prompt text and reviewer
  judgment, deliberately NOT code-enforced: neither is reliably countable from the report
  alone, and a wrong machine count would fail good work. Beyond a cap, the clause directs
  capture (notes), and the reviewer fails over-cap fold-ins.
- Validation failure = the report is never accepted as success; task → terminal `failed`;
  journal entry carries the standard terminal stamp plus the offending entries; transition for
  valid/in-scope entries is exactly as without fold-ins.
- Validation applies to implement and fix reports (the roles that hold the clause); it is
  inert for other roles (they never should carry `fold_ins`, and if one does, the same
  fail-closed rule applies — cheaper than a special case).

### D4 — Journal recording is conditional, mirroring the existing optional-key pattern

`_REPORT_FIELDS` is a fixed projection shared by the live journal, the live_run cassette, and
`orchestrate.py`'s golden-record path. Adding `fold_ins` to it would put a null (or an empty
list) on every entry and change the shape of every record — including the golden `orchestrate
check` fixtures. Instead, `fold_ins` is recorded only when non-empty, on the entry's `report`
dict, exactly the pattern already used for `terminal_status` (conditionally injected into
`report_fields`) and for top-level optional keys (`usage`, `tools_used`, `convergence_summary`).
The live journal path (`_apply_step_commit`, and `live_run`'s entry builder for symmetry) does
this; `orchestrate.py`'s record path is left untouched so golden records stay byte-identical.

### D5 — Reviewer duty extends `_REVIEWER_SYSTEM_PROMPT` without touching the drift clause

The reviewer's existing drift sentence ("look for bugs, missing tests, and scope drift") stays
verbatim; the validation duty is added as an additional sentence set: each declared fold-in is
checked for in-scope, mechanical (no new design/API/behavior contract), within caps, and tests
still passing; a declared fold-in that meets these is NOT scope drift; an undeclared
out-of-scope edit or unexplained drift still FAILS exactly as today. `live.py` prepends this
prompt for review-role spawns on both harness branches, so one edit covers both.

### D6 — PR declaration: parameter on the renderer, flag on the CLI

`render_pr_body` gains a `fold_ins` parameter — a sequence of pre-rendered per-fold-in strings
(the caller composes "file — summary (sha)"); absent/empty renders the section body as `none`.
`LandRequest` gains the matching field and `land-pr` a repeatable `--fold-in` flag, so the
route executor can declare fold-ins without hand-writing the section. The section is appended
after the existing sections (Summary … Auto-Merge Recommendation): it is additive, the pinned
tests assert section content not order, and appending keeps the summary/gate-evidence adjacency
reviewers read first. The section is never omittable — it always renders, with `none` when
empty — matching the modified `pr-landing-pipeline` requirement.

### D7 — Doctrine placement

The three-tier rule and the authoring-stage default go in
`skills/worktrail-sdd-workflow/SKILL.md` as a short dedicated section (the authoring stage is
where the decision is made, and the skill is loaded there). The prose must not coin new
`worktrail-*` command tokens that look like console scripts (the plugin-surface test requires
every such token to be a real entry point) and must not introduce new triage verdict names —
tier 2 is the existing `fold-into-change`, route/brief capture stays `worktrail-handoff` /
Route F exactly as documented.

## Risks / Trade-offs

- [Scope creep smuggled in as "mechanical"] → the code teeth bound the blast radius to files
  already declared, the reviewer validates mechanical-ness and caps, and the PR section makes
  every fold-in public in review; a non-mechanical fold-in is spec'd to FAIL that review.
- [Prompt growth on the two hottest roles] → clause kept to a few lines; the hard rules are
  already long and cache-hit on every spawn.
- [A sloppy `fold_ins` shape terminally fails an otherwise-good task] → fail-closed is
  deliberate (an unvalidatable claim must not pass), the schema is spelled out in the prompt,
  and the existing failure-recovery machinery (clear/resume) already handles terminal fails;
  the offending entry on the journal explains exactly why.
- [Journal replay/golden-fixture drift] → recording is conditional and the golden path
  (`orchestrate.py`) is untouched; `reconcile_from_journal`/replay read by key and already
  ignore extra keys, so entries carrying `fold_ins` replay cleanly.
- [The absent-notes byte-identity guarantee silently broken] → D1 renders the clause
  unconditionally per role; the task keeps `test_absent_notes_are_byte_identical` green as an
  acceptance item rather than rewriting it.

## Migration Plan

None required. The schema key is additive and optional; journal entries carrying `fold_ins`
are read by key everywhere, so an older checkout replays them unchanged, and reverting the
change leaves journaled entries harmless. No policy-file or state migration.

## Why

The `worktrail-go BRIEF-ID` intake-triage gate (`--evaluate-brief-triage` /
`--apply-brief-triage-file`, `src/worktrail/router/skill_dispatch.py:222`
`evaluate_single_brief()`) has no concurrent-claim guard. On 2026-09-18 an interactive
pickup of `20260918-154644-claude-target-write-agents-md` evaluated the brief while the
scheduled queue-triage run had already claimed it (`picked/`, `claimed-by: queue-triage`,
a `claude -p` proposer mid-flight). Three things went wrong at once:

- **No ownership check.** `evaluate_single_brief()` reads whatever path it is handed and
  `apply_single_brief_verdict()` writes to whatever `_resolve_brief_path()` finds, in
  `queue/` *or* `picked/`, without looking at `claimed-by`. PR #1191 added the
  concurrent-claim guard only to the fold/propose worktree+PR pipeline; the `keep` /
  `needs-update` apply branches and the evaluate step itself are still unguarded.
- **Empty body reads as a verdict.** `queue_triage._brief_focus()` returns `''` on `OSError`
  (`src/worktrail/workqueue/queue_triage.py:731`). The claim had moved the file to `picked/`,
  so the evaluator saw an empty brief and returned `keep / confidence low / "brief has no
  focus text"`, and the apply step appended that misleading `## Triage` keep note onto the
  brief queue-triage owned.
- **Bare id falls through to free text.** `worktrail-go-parse` resolves a bare brief id only
  against `queue/`, so a claimed brief reports `bare or prefix brief id did not resolve --
  free text` and the session cannot even tell the operator the brief is held elsewhere.

(Work-queue brief `20260918-180644-worktrail-go-brief-id-intake`.)

## What Changes

- **Ownership guard on the single-brief path.** `evaluate_single_brief()` and
  `apply_single_brief_verdict()` re-resolve the brief by id across `queue/` and `picked/`.
  A brief in `picked/` stamped `claimed-by` (whatever the owner: an interactive intake
  pickup never claims, so any live claim belongs to someone else) is refused: the CLI
  prints `null`, writes `blocked_brief_owned: <id> owned by <claimed-by> (claimed-at
  <ts>)` to stderr, and exits 2 with nothing written. The same check gates the
  `keep`/`needs-update` note-append branch inside `queue_triage.apply_verdicts()`, which
  returns a `status: error` entry naming the owner instead of writing into another
  owner's brief.
- **Fail loud on an empty brief.** The single-brief evaluate refuses to spawn an evaluator
  when the resolved brief is unreadable or has no `focus:` frontmatter and no `## Focus`
  body: `null`, `blocked_empty_brief: <id> (<reason>)` on stderr, exit 2. An evaluator is
  never asked to verdict an empty prompt, so "no focus text" can no longer become a
  `keep`.
- **Bare brief id resolves against `picked/` too.** `worktrail-go-parse` tries the sibling
  `picked/` folder when `queue/` yields no match, returning `mode: brief` with
  `brief_status: picked` and the brief's `claimed-by`, so the go skill can report
  `owned by <claimed-by>` and stop before any evaluate call.
- **Skill text.** `skills/worktrail-go/SKILL.md`'s Phase 2 gate documents the new
  exit-2 cases alongside `blocked_pending_decision`, and the picked-resolution short-circuit.

## Non-goals

- `worktrail-work-queue list --json` continues to list `queue/` only; the gate no longer
  depends on finding a claimed brief in `briefs[]`.
- The scheduled whole-queue `evaluate` path keeps its lenient `_brief_focus()` for briefs
  it groups from `queue/`; only the single-brief pickup fails loud.

## Capabilities

### New Capabilities

### Modified Capabilities
- `intake-triage`: the interactive single-brief pickup refuses a brief owned by another
  claimant, refuses to evaluate an empty brief, and resolves a bare id held in `picked/`.

## Impact

- `src/worktrail/workqueue/queue_triage.py` — `brief_claim_holder()`, `BriefOwned`,
  `BriefMissing`, `EmptyBrief`, ownership check in the note-append apply branch.
- `src/worktrail/router/skill_dispatch.py` — guards in `evaluate_single_brief()` /
  `apply_single_brief_verdict()`, three new exit-2 stderr lines.
- `src/worktrail/router/parse_invocation.py` — `_resolve_brief()` falls back to `picked/`.
- `skills/worktrail-go/SKILL.md` — Phase 2 gate text.
- Tests under `tests/workqueue/` and `tests/router/`.

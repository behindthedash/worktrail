## Why

`hooks/suggest_next_step.py`'s only check today is the EXCEPTIONAL-VALUE gate: agent
judgment on whether *a new idea it just had* clears a high bar before capturing a handoff.
That gate has no mechanism for a different, easier-to-lose case — a deferral the session
*already named explicitly* in its own run record (e.g. "advisory for now", "out of scope
for this PR", "once calibrated"). Motivating incident (see
`docs/specs/research/stop-hook-deferred-work-capture-gap.md`): brief `20260820-024527`
named a two-phase rollout in its own suggested approach; the EXCEPTIONAL-VALUE gate
correctly excluded it as routine, and nothing else caught that a real, self-named
deferral was never captured as a handoff. A self-named deferral that never becomes a
brief is invisible to `/go`, dashboards, and drain.

## What Changes

- Add a second, narrow, deterministic check to `hooks/suggest_next_step.py`, additive to
  and independent of the EXCEPTIONAL-VALUE gate — its prompt text and behavior are
  unmodified.
- Discover this session's run record(s) by extracting `~/.worktrail/runs/**/*.yaml` path
  literals from the same transcript file the hook already reads once for its existing
  substantive-work check (no new dependency).
- Read only the `deferred_work` list of any matched run record(s) — never `scope_review`,
  whose own `out-of-scope` entries are legitimate, already-adjudicated decisions that use
  overlapping vocabulary (e.g. "out of scope") and must not be mistaken for uncaptured
  deferrals.
- Match `deferred_work` entries against a narrow, explicit, easily-extended deferral-phrase
  list (e.g. "advisory for now", "deferred", "once calibrated", "follow-up", "in a later
  PR").
- Before flagging a match, cross-check whether an existing work-queue handoff brief
  (`queue/` or `picked/`) already covers it, reusing `check_brief_staleness.py`'s
  bounded-probe-extraction-and-search pattern (`extract_probes`) rather than reinventing
  text matching.
- Surface an additional instruction block, in the same shape as the existing
  EXCEPTIONAL-VALUE prompt-injection mechanism, only when a `deferred_work` entry has no
  matching handoff. When nothing matches, the hook's output is unchanged from today
  (silent).
- Remains fast and fail-open (never breaks a session) and still never runs when
  `CC_HEADLESS=1`.
- Explicit non-goals for this v1 (flagged as follow-up work, not silently dropped):
  - No PR-body scanning and no `gh` CLI dependency — deferral signal is `deferred_work`
    only.
  - No `session_id` field added to the run-record schema — run-record discovery for v1 is
    transcript-grep only.
  - No claim that the initial deferral-phrase list is complete — phrase-list tuning is
    expected follow-up work.

## Capabilities

### New Capabilities
- `stop-hook-deferral-flag`: mechanical, deterministic detection at Stop-hook time of
  self-named `deferred_work` run-record entries that have no matching work-queue handoff,
  additive to and independent of the hook's existing EXCEPTIONAL-VALUE gate.

### Modified Capabilities
(none — the EXCEPTIONAL-VALUE gate has no existing OpenSpec capability spec and is not
changed by this proposal)

## Impact

- `hooks/suggest_next_step.py`: new deterministic check, invoked after the existing
  substantive-work gate, additive to the printed instruction.
- New helper module under `src/worktrail/` (or `hooks/`, per design) implementing
  run-record discovery, `deferred_work` extraction, phrase matching, and the handoff
  cross-check.
- `src/worktrail/router/check_brief_staleness.py`: `extract_probes()` reused (imported),
  not modified.
- Test coverage mirroring `tests/` layout for the new check module and updated hook tests.

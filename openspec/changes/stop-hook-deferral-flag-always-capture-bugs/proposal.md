## Why

The Claude Stop hook (`hooks/suggest_next_step.py`) has two verified faults that lose follow-up
work.

1. **Unfixed defects are gated out.** The base instruction sends every handoff capture through
   the EXCEPTIONAL-VALUE gate ("genuine step-change"; "Do NOT capture routine polish, nearby
   cleanup/refactors..."). That gate has no exception for defects. Agents found a verified latent
   bug, did not fix it, saw no step-change idea, and correctly printed "No handoff captured; no
   exceptional next step identified." The bug was then lost. User decision, 2026-09-12: "if
   there is a bug, it should definitely deserve a handoff."
2. **Read-only spec access trips the dedup gate.** `durable_artifact_paths_from_entry` treats a
   Bash command as a write if it contains `>` anywhere, so `2>/dev/null`, `>/dev/null`, and
   `2>&1` all count. It then collects every `docs/specs/**` or `openspec/changes/**` path in the
   command. On a real transcript, each of these produced a "durable artifact touched" hit:
   - a read-only `git ls-tree --name-only origin/dev docs/specs/005.../changes/ docs/specs/001.../changes/ 2>/dev/null`
   - a command that combined `npm ci >/dev/null` with a quoted run-record note mentioning
     `docs/specs/005/changes`

   That breaks the existing scenario "Read-only spec access does not trigger detection". It also
   downgrades captures that should have happened.

The fix ships under Worktrail's v1.0 fixes-only release gate. It adds no new capability, flag,
CLI, or dependency.

## What Changes

- **Mandatory defect capture in the base instruction.** Every verified defect, bug, or regression
  found in the session and left unfixed gets its own `worktrail-handoff` brief, one per defect.
  The EXCEPTIONAL-VALUE gate does not apply to it.
  - The gate and its "routine polish" exclusions now apply only to forward-looking ideas.
  - "No handoff captured; no exceptional next step identified." is valid only when no unfixed
    defect is left uncaptured.
  - Defects inside the current request still fall under the existing completion audit: fix them
    now or stop on a verified blocker. They are not handed off.
- **Narrower DEDUP GATE block.** It suppresses capture only for the follow-up the matched
  artifacts already track. It says explicitly that it never suppresses capture of a distinct
  defect those artifacts do not track.
- **Bash write detection counts real writes only.**
  - Redirects that discard or duplicate a file descriptor (`2>/dev/null`, `>/dev/null`,
    `&>/dev/null`, `2>&1`, `>&2`) are not writes.
  - A write command marks only the path(s) it writes: a redirect target, a `tee` file, a
    `cp`/`mv` destination, an `mv` source, or a `sed -i`/`touch`/`mkdir`/`rm` file operand.
  - Other durable paths named in the same command are not marked.
- **Updated byte-identity baselines.** The baselines pinned in both specs now point at the
  amended base instruction. Two properties stay the same: extra blocks are only appended, and
  output with no dedup hit is byte-identical to the baseline.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `stop-hook-deferral-flag`: Requirement "Additive And Non-Interfering". The base instruction
  that the deferral flag must leave unchanged now includes the mandatory defect-capture step, and
  the EXCEPTIONAL-VALUE gate applies only to forward-looking ideas.
- `durable-artifact-dedup-gate`:
  - Requirement "Session-Touched Durable-Artifact Detection": a Bash command marks only the paths
    it writes, and discarded or duplicated file-descriptor redirects are not writes.
  - Requirement "Downgrade-To-Suggestion On Dedup Hit": suppression covers only the tracked
    follow-up, never an untracked defect, and the no-hit baseline is the amended instruction.

## Impact

- `hooks/suggest_next_step.py`:
  - `INSTRUCTION`
  - `build_dedup_gate_block`
  - `durable_artifact_paths_from_entry`, plus a private helper for Bash write targets. The helper
    replaces `BASH_WRITE_MARKERS`, which becomes unused.
- `hooks/test_suggest_next_step.py`: new tests for both problems, each written to fail before its
  fix. Existing byte-identity tests already compare against `hook.INSTRUCTION` and keep that
  comparison unchanged.
- Unchanged: `worktrail-check-durable-artifact-capture-gate`, `worktrail-check-deferred-work-handoff`,
  run records, and the PR-ledger Stop guard.
- Behavior:
  - Sessions that leave verified defects outside their scope unfixed now produce one handoff brief
    per defect.
  - The dedup gate no longer fires on read-only spec access.
  - Running Claude Code sessions see the new hook text after the plugin refresh and a restart.

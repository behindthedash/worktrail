## Context

Three layers currently participate in end-of-session follow-up capture:

1. **The convention** — `~/AGENTS.md` (symlink to
   `~/projects/devops/agent-doctrine/AGENTS.md`, "End-of-Session Next-Step Suggestion" section,
   lines 156–168) tells every agent (Claude/Codex/OpenCode) to auto-capture the single strongest
   next-step idea as a handoff brief, unconditionally. It is read by providers that never run
   Worktrail's Stop hook.
2. **The Stop hook** — `hooks/suggest_next_step.py` fires once per substantive Claude Code session
   and prints a block instruction containing the EXCEPTIONAL-VALUE gate. It already scans the
   transcript once (`scan_transcript`) for work evidence and `~/.worktrail/runs/**/*.yaml` path
   literals, and shells out to the fail-open `worktrail-check-deferred-work-handoff` CLI for
   deferred-work flagging. The hook is standalone (no package import) by design; package logic is
   reached via console-script subprocess with a 5-second timeout and fail-open posture.
3. **The capture CLI** — `create_handoff.py` writes a brief into `$WORK_QUEUE_DIR/queue/` after
   optional auto-linking via `score_candidates`. It never looks at the target repo's spec tree or
   open PRs. Consume-time duplicate detection exists (`router/cluster_detect.py`,
   OVERLAP_THRESHOLD = 0.45 focus-token overlap; dashboard's consolidate-cluster action), but only
   runs when someone views/consumes the queue — after the duplicate is already written.

Motivating incident: Route C merged spec 003-tailwind-v4-migration (PR #86); the same session's
wrap-up auto-captured an implement-it brief. The durable artifact (the merged spec change) already
tracked the work.

## Goals / Non-Goals

**Goals:**

- Make "a durable artifact already tracks this" mechanically detectable at both decision points:
  the Stop hook's capture instruction (layer 2) and the moment of writing a brief (layer 3).
- Keep every new check fail-open and fast: a dedup check that errors must never break a session
  wrap-up or a capture.
- Keep the convention wording provider-portable — it must remain correct for agents that have no
  hook, no worktrail install, or no git access to the queue.
- Preserve the existing EXCEPTIONAL-VALUE gate and deferred-work flag behaviors byte-for-byte in
  their firing conditions; the dedup gate is additive.

**Non-Goals:**

- No live GitHub API queries from the Stop hook (latency + auth variance inside a session-end hot
  path). Merged-docs-only-spec-PR detection v1 is transcript-local evidence.
- No hard blocking of `create_handoff` writes: capture-time overlap stays a warning. Hard refusal
  would need an override UX this change does not design.
- No semantic similarity/LLM verification at capture time (consume-time cluster detection already
  has `_verify_same_work`; write-time v1 is mechanical token overlap only).
- No migration of previously captured duplicates; the queue triage tooling already handles those.

## Decisions

### D1: Layer 1 is convention text in agent-doctrine, not a Worktrail spec requirement

The `~/AGENTS.md` rewording lands in the agent-doctrine repo (its own commit flow; the file is a
symlink target, not part of this repo). The worktrail change carries it as an explicit
coordination task with the exact replacement text drafted here, so both land together and neither
drifts. Wording principle: capture ONLY when no durable artifact tracks the follow-up; otherwise a
suggestion-only line naming the resume command (`worktrail-go <brief-id>` / route command).
Durable artifacts enumerated provider-neutrally: a spec/OpenSpec change created or merged this
session, an epic doc, an existing queue brief, an open PR. Alternative considered: shipping the
convention inside a worktrail skill so it versions with the code — rejected because Codex/OpenCode
read `~/AGENTS.md`, not this repo's skills; portability wins over versioning.

### D2: New fail-open checker module + console script for layer 2, mirroring the deferral-flag shape

Add `src/worktrail/router/check_durable_artifact_capture_gate.py` exposing a
`worktrail-check-durable-artifact-capture-gate` entry point. The Stop hook calls it (subprocess,
5 s timeout, fail-open on missing binary/nonzero exit/bad JSON) with two inputs it already
extracts from its single transcript pass:

- `--touched-path` repeats for every Edit/Write/MultiEdit/NotebookEdit `file_path` and Bash
  heredoc/com redirect target matching `docs/specs/**` or `openspec/changes/**`
  (`scan_transcript` gains a second regex collector alongside `RUN_RECORD_PATH_RE`, emitted from
  the same pass so the transcript is still read once);
- `--run-record` repeats, reusing the existing path-literal extraction.

The checker reports three hit kinds:

1. **session-touched durable artifact** — any touched path under `docs/specs/<slug>/` or
   `openspec/changes/<name>/`;
2. **run record finishing `planned_ready_for_implementation`** — reads each run record via the
   lenient loader and compares `completion.state` (the state `release_gate.py` already treats as
   "planned, awaiting implementation");
3. **merged docs-only spec PR** — transcript-local evidence only: the session shows a PR merge
   marker (`gh pr merge`, merged-PR URL) *and* touched spec paths, i.e. the motivating
   Route-C-shaped session. Live `gh` queries stay out of the hook path (Non-Goal).

Rationale for subprocess-over-import: keeps the hook standalone and its failure boundary identical
to the proven deferral-flag pattern; the plugin-surface test suite already locks console scripts to
real entry points. Alternative (importing the package directly in the hook) couples hook deploys
to pip-install state of the host machine — the exact coupling AGENTS.md documents as fragile.

### D3: On a hit, downgrade the instruction — don't suppress the hook

When the checker returns hits, the hook appends a DEDUP GATE block (separate from INSTRUCTION, the
same additive pattern as the deferred-work block): name the matched artifacts, forbid
auto-capture, require instead a suggestion-only line that names the resume command, and keep the
explicit-justification escape hatch — if the agent has justification, the brief itself must carry
it as a `## Dedup justification` section naming the tracked artifact and why a separate brief is
still warranted. Rationale: the hook cannot judge semantics, but it can move the burden of proof
from "capture by default" to "justify the duplicate". The sentinel still marks the hook as fired;
the gate block rides the same single block decision (no second termination block).

### D4: Layer 3 warning reuses cluster-detect tokenization at write time

In `create_handoff.create_handoff()`, before `path.write_text`: resolve the brief's repo (same
resolution already computed for frontmatter), then

- scan `<repo>/docs/specs/*/` slug directory names and `<repo>/openspec/changes/*/` names,
  tokenizing slugs with the same lowercase-alphanumeric-token rule `cluster_detect.py` uses;
- list open PR titles via `gh pr list --repo <remote> --state open --json title,number` with a
  short timeout, skipped silently when `gh` is absent, the remote is null, or the call fails
  (same fail-open `gh` posture as `queue_triage._check_repo_archived`);
- compute overlap coefficient between focus tokens and each candidate; candidates at/above
  OVERLAP_THRESHOLD (0.45, imported from `cluster_detect` rather than duplicated) become warnings.

Warnings go to stderr in human mode and into the JSON payload as `"overlap_warnings": [...]`
(id/kind/title-or-slug, score). The brief is always written; nothing raises. Import direction is
already established (`create_handoff` imports `router.classify`). Threshold shared by import so
write-time and consume-time detection cannot drift. Alternative: a lower write-time threshold for
earlier, noisier warnings — rejected; one threshold means a write-time warning always predicts a
cluster the dashboard would also surface, making the two layers mutually reinforcing instead of
contradictory.

### D5: Versioning and lockstep mechanics

Any `src/worktrail/**` change trips `CI: Version Bump Check`; per repo practice this joins the
next batch bump under the `go:no-version-bump` label unless it ships alone. The new console script
must appear in `pyproject.toml [project.scripts]`; `tests/test_plugin_surface.py` needs no new
skill entries because no skill text references the checker (it is hook-only surface).

## Risks / Trade-offs

- [Transcript-local PR detection misses merges from other sessions] → Accepted for v1: the
  motivating case merges in-session; layer 3's write-time warning catches cross-session
  duplicates against live repo state (spec slugs + open PRs), which is exactly where the residual
  risk lives.
- [Token overlap false-positives annoy capturers] → Warning-only, capped list (top 5), and the
  threshold is the already-tuned consume-time value; observe noise before tuning.
- [Dedup gate over-fires on sessions that merely read specs] → Only *touched* (edit/write) paths
  count; reads produce no tool_use edit events and no collector hits.
- [agent-doctrine edit and worktrail merge drift apart] → Tasks sequence the doctrine edit first
  with the final wording inlined in tasks.md, so either side can be verified against the recorded
  text independently.
- [`gh pr list` latency at capture time] → Short timeout (~4 s) and total-failure skip; capture
  proceeds unwarned rather than stalled.

## Migration Plan

1. Land the checker module + entry point + tests; wire the hook block; extend create_handoff.
2. Apply the agent-doctrine wording edit (text pre-drafted in tasks.md) and commit there.
3. Rollback is per-layer: the hook degrades gracefully if the binary is missing (fail-open), the
   capture warning disappears with the module, and the convention text can be reverted
   independently.

## Open Questions

None.

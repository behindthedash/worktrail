# Investigation: the Stop-hook "15-second budget" bottleneck

Source: work-queue brief `20260826-094641-stop-hook-timing-bottleneck`.
Route I (investigation), run `go-20260902-174637`, continued in-run into the brief's
own closing step (remove the probe). Evidence gathered 2026-09-02 against the
transcripts on this machine (`~/.claude/projects/**/*.jsonl`).

## Question

PR #729 (`495561d`, 2026-08-26) added stderr timing instrumentation to
`hooks/suggest_next_step.py` — `[stop-hook-timing] <phase> elapsed=…` lines around the
transcript scan, the deferred-work subprocess, the dedup-gate subprocess, and `main` —
and switched it on unconditionally in `hooks/hooks.json`
(`WORKTRAIL_STOP_HOOK_TIMING=1`). The hook's configured `timeout` is 15 s. The brief asks:
which phase consumes that budget on a real failing run, then remove the probe.

## Verified observations

1. **How Claude Code records a Stop hook.** Every Stop-hook execution lands in the
   session transcript as a `type: attachment` entry. The shape depends on the hook's
   outcome:
   - Hook exits 0 with no output (the hook's *skip* path — sentinel already set, or no
     substantive work): `attachment.type = hook_success`, carrying `durationMs`,
     `exitCode`, `stdout`, `stderr`. The `[stop-hook-timing]` lines are visible here.
   - Hook prints `{"decision": "block", …}` (the *emit* path — the one that actually
     runs both subprocess checks): `attachment.type = hook_blocking_error`, carrying
     only `blockingError` (the reason text). **No `stderr`, no `durationMs`.**
   - Consequence: the probe as shipped can never show phase timings for the emit path.
     Only the scan-only skip path was ever observable through the probe.

2. **Skip-path timings (probe data), 2026-08-26 → 2026-09-02.** 1,343 `hook_success`
   records carrying `[stop-hook-timing]` lines across 292 transcripts.

   | metric | value |
   |---|---|
   | `durationMs` p50 | 151 ms |
   | `durationMs` p90 | 426 ms |
   | `durationMs` p99 | 1,147 ms |
   | `durationMs` max | 2,721 ms (5.6 MB transcript; `scan_transcript_done` 1.624 s) |
   | largest transcript scanned | 8,570 lines |

3. **Emit-path timings (bounded from transcript timestamps), same window.** 247
   `hook_blocking_error` records. The gap between the session's last `assistant` entry
   and the hook attachment is an upper bound on the hook's wall time (it also contains
   whatever else ran at Stop).

   | metric | value |
   |---|---|
   | gap p50 | 0.21 s |
   | gap p90 | 0.73 s |
   | gap p99 | 1.48 s |
   | gap max | 2.10 s |
   | gaps ≥ 5 s | 0 |

4. **No timeout has ever been recorded.** Of 2,350 Stop-hook attachments on disk
   (1,343 `hook_success`, 1,002 `hook_blocking_error`, 5 `hook_non_blocking_error`), none
   contains a timeout. The five non-blocking errors are `ENOENT … posix_spawn '/bin/sh'`
   inside an aspens test fixture (2026-08-16/17) and one stale
   `Plugin directory does not exist` after a plugin update (2026-08-20) — none
   timing-related. `~/.claude/daemon.log` has no hook-timeout entries either.

5. **Direct reproduction of the emit path** (`WORKTRAIL_STOP_HOOK_TIMING=1`, fresh
   session id, host load average ≈ 3.0 on 14 cores with the CI runners up), against the
   three largest transcripts on this machine:

   | transcript | lines | scan | deferred-work | dedup gate | total wall |
   |---|---|---|---|---|---|
   | 14.5 MB (subagent) | 532 | 0.643 s | skipped (no run records) | skipped | 0.68 s |
   | 12.3 MB | 8,578 | 0.621 s | 0.125 s (6 run records) | 0.083 s (26 paths, 23 hits) | 1.04 s |
   | 10.9 MB | 5,278 | 0.688 s | skipped | 0.062 s (11 paths, 9 hits) | 0.79 s |

6. **Subprocess cold-start cost.** `worktrail-check-deferred-work-handoff` 0.08 s,
   `worktrail-check-durable-artifact-capture-gate` 0.05 s, `python3 -c "import worktrail"`
   0.01 s. Each subprocess call is capped at 5 s in the hook
   (`DEFERRED_WORK_TIMEOUT_SECONDS`, `DEDUP_GATE_TIMEOUT_SECONDS`), so even both hanging
   to their caps plus the slowest observed scan (1.6 s) totals ≈ 11.6 s — under the
   15 s budget.

7. **Origin of the "15 s" premise.** PR #729's body says the probe exists "to see
   whether the transcript scan or one of the subprocess checks is consuming the 15s hook
   budget". 15 s is the configured `timeout` in `hooks/hooks.json`. No transcript,
   brief, or log on this machine records an actual Stop-hook timeout; the session that
   captured the brief was not located.

## Unknowns

- Whether a Stop-hook timeout ever actually occurred, or the premise was the configured
  budget itself (or the `Considering a next-step suggestion...` status line lingering
  for an unrelated reason). Nothing on disk records one.
- Emit-path behavior on a machine or day with severe disk I/O stalls. Observation 3
  covers 247 real emit runs on this WSL host over a week, including periods with four
  CI runners active, and none exceeded 2.1 s.

## Hypotheses (unverified)

- **H1.** No timeout occurred; the brief's premise was the timeout value, not an
  observed failure. Consistent with every record examined; not provable from absence.
- **H2.** A timeout would require the transcript scan alone to take > 5 s (given the
  subprocess caps in observation 6). The scan is a single linear pass with two regexes
  per line; at the observed ≈ 0.65 s per 10–14 MB it would need a ≈ 100 MB+ transcript
  or a heavily I/O-starved host. No transcript on this machine is that large (max 14.5 MB).

## Confirmed root cause

Root cause is not confirmed from the available evidence — there is no failing run to
root-cause. Across 1,590 real hook executions since the probe shipped, the slowest was
2.7 s, 5.5× under budget, and the hook's own subprocess caps bound the worst case below
15 s regardless.

## Action taken in this run

The brief's closing step ("remove the probe once the root cause is understood") is
executed here as an in-run I → F continuation: the question is answered with evidence
(no bottleneck exists), and the probe cannot observe the only path it was meant to
observe (observation 1), so a "slimmer permanent version" has no value either.

- `hooks/suggest_next_step.py`: timing instrumentation removed; the file is now
  byte-for-byte the pre-#729 logic plus the later fleet ruff pass (#819).
- `hooks/hooks.json`: `WORKTRAIL_STOP_HOOK_TIMING=1` prefix removed; `timeout: 15`
  unchanged.
- `hooks/test_suggest_next_step.py` (49 tests) passes unchanged — the probe had no
  tests of its own.

## Recommended next route

None. If a real Stop-hook timeout is ever observed, capture it with the transcript path
and the `hook_*` attachment entry (observation 1 explains where to look), then open a
Route I brief citing this note. Any future probe must write to a file (for example under
`~/.claude/state/worktrail-suggest-next/`), not stderr, or the emit path will remain
invisible.

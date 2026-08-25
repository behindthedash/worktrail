## Context

See `proposal.md` - Why, and `docs/specs/research/stop-hook-deferred-work-capture-gap.md`
for the full discovery. Two constraints from the existing hook shape this design directly:

- `hooks/suggest_next_step.py` is invoked as `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/suggest_next_step.py`
  (see `hooks/hooks.json`) and today imports nothing beyond the standard library. It cannot
  assume the `worktrail` pip package (or `pyyaml`) is importable in that `python3`'s
  environment — a user may have the Claude plugin installed without `pip install worktrail`.
- The hook already reads `transcript_path` once, line by line, for `entry_has_work`
  (`hooks/suggest_next_step.py:47-82`). It only ever touches that one file.

The run record's `deferred_work` and `scope_review` are both plain lists of free-text
strings (`src/worktrail/router/run_record.py:386-387`, `cmd_scope_review` at line 494);
`scope_review` entries have the fixed shape `"<status> | <item> | <detail>"` where `status`
is `out-of-scope` for a legitimate, adjudicated exclusion — the exact vocabulary collision
the discovery note flags.

## Goals / Non-Goals

**Goals:**
- Keep the hook itself dependency-free and fail-open, matching its current shape exactly.
- Reuse `check_brief_staleness.py`'s `extract_probes()` for text-to-brief matching instead
  of writing a second, divergent text-matching implementation.
- Make the deferral-phrase list a single, obvious, easily-extended location.

**Non-Goals (see proposal.md - What Changes for the full list):**
- PR-body scanning / `gh` CLI dependency.
- A `session_id` field on the run-record schema.
- A complete or tuned deferral-phrase list — v1 ships narrow and expects follow-up tuning.
- Touching `EXCEPTIONAL-VALUE` gate text, trigger conditions, or the sentinel/one-shot
  mechanism that gates the hook's output today.

## Decisions

### Split: dependency-free hook + a new `worktrail-*` console-script check

The hook stays exactly as dependency-free as it is today. All new logic — YAML parsing,
phrase matching, and the handoff cross-check — lives in a new module,
`src/worktrail/router/check_deferred_work_handoff.py`, exposed as a new console script
`worktrail-check-deferred-work-handoff` (added to `pyproject.toml`'s `[project.scripts]`,
following the existing `worktrail-check-*` naming convention). The hook locates it with
`shutil.which("worktrail-check-deferred-work-handoff")` and invokes it as a subprocess with
a short timeout, passing the discovered run-record path(s) and the work-queue directory;
the script prints one JSON object describing what it found.

Alternative considered: import `worktrail.router.check_deferred_work_handoff` directly.
Rejected — it would make the hook's own success depend on `worktrail` being on `sys.path`
for whatever `python3` the Claude Code harness invokes, which is not guaranteed (see
Context). The subprocess boundary means "package not installed" degrades exactly like
"nothing found" — silently, via `shutil.which` returning `None` — with no new failure mode
for the hook itself to handle.

This mirrors the existing precedent: `check_brief_staleness.py`, `check_related_brief_claims.py`,
etc. are already designed as independent, `--json`-capable CLIs callable from anywhere, not
as library calls into `/go`'s own process.

### Run-record discovery: single-pass transcript scan

`entry_has_work`'s existing per-line loop is extended (not duplicated) to also regex-match
each line's raw text against `~/.worktrail/runs/[^\s"']+\.yaml` (expanded to the invoking
user's home directory), collecting unique matches into a small ordered list alongside the
existing boolean result. This is one file read, not two, and stays inside the hook's
existing dependency-free code path — only the *paths* are extracted here; nothing about
their contents is parsed until the subprocess step. If the hook has already decided not to
proceed (sentinel exists, or no substantive work), the extra collection is short-circuited
away with the rest of the function, so a session with no substantive work pays no extra
cost.

### `deferred_work`-only, `scope_review`-excluded, at the extraction boundary

`check_deferred_work_handoff.py` loads a discovered run-record YAML and reads only the
`deferred_work` key. It never reads `scope_review` at all — not "reads it but filters it
out" — so the vocabulary collision the discovery note documents (`out-of-scope | ... |
different purpose: ...`) cannot leak into the phrase-matching step by construction, and a
later change to the phrase list cannot silently reopen it either.

### Deferral-phrase list: one module-level constant

```python
DEFERRAL_PHRASES = (
    "advisory for now",
    "deferred",
    "once calibrated",
    "follow-up",
    "follow up",
    "in a later pr",
)
```

Matching is case-insensitive substring matching against each `deferred_work` entry's text.
Kept intentionally small per proposal.md's non-goals; a comment above the constant notes it
is expected to grow and should be tuned from observed false positives/negatives, not
front-loaded now.

### Handoff cross-check reuses `extract_probes`, adds its own bounded brief search

`check_deferred_work_handoff.py` imports `extract_probes` from
`worktrail.router.check_brief_staleness` (a same-package import, not a subprocess — both
modules already live under `worktrail.router` and `check_brief_staleness.py` has no
hook-side dependency-free constraint). For each phrase-matching `deferred_work` entry:

1. `extract_probes(entry_text)` yields the same `{paths, symbols, pull_requests}` shape
   `check_brief_staleness.py` already produces from brief focus text.
2. A new, small bounded search — not `check_brief_staleness.check()`, which searches git
   history — scans `queue/*.md` and `picked/*.md` (via `work_queue.base_dir()` /
   `queue_dir()` / `picked_dir()`) for any brief whose focus text (frontmatter `focus:` or
   `## Focus` body, the same two sources `check_related_brief_claims.py`'s `_focus_summary`
   already reads) contains any extracted probe as a substring.
3. Any match against any brief in either directory counts as "covered"; no match anywhere
   is "flag this entry."

This reuses the extraction half of the existing pattern exactly (no reinvented tokenizing/
capping logic) while pointing the search half at the work queue instead of git history,
since brief coverage — not code delivery — is the question here.

### Uncertain cross-check result defaults to silence, not to flagging

If the work-queue directory doesn't exist, isn't readable, or a brief file fails to parse,
that brief is skipped (best-effort, same posture as `check_brief_staleness.py`) rather than
treated as "not covering" the entry. If the whole cross-check cannot run at all (e.g.
`$WORK_QUEUE_DIR` unset and the default doesn't exist), the entry is treated as *not*
flagged. See Risks / Trade-offs.

### Output contract between the subprocess and the hook

`worktrail-check-deferred-work-handoff --run-record <path> [--run-record <path> ...] --json`
prints exactly one JSON object: `{"flagged": [{"text": "<entry>", "run_record": "<path>"}]}`
(empty list when nothing qualifies). The hook parses this, and only when `flagged` is
non-empty, appends a second, separate block to the same `reason` string already built for
the EXCEPTIONAL-VALUE gate — matching the requirement that both blocks can co-occur in one
hook invocation's single JSON output. A subprocess failure (non-zero exit, timeout,
unparseable stdout) is treated identically to "nothing flagged."

## Risks / Trade-offs

- **[Risk] Transcript-grep run-record discovery misses a session that never echoed its run-
  record path (e.g., an unusual dispatch shape).** → Accepted as a known v1 limitation,
  named explicitly in proposal.md's non-goals; the alternative (a schema `session_id`
  field) is a larger, separate change the proposal deliberately defers.
- **[Risk] Narrow phrase list produces false negatives (a real deferral phrased
  differently is never flagged).** → Accepted per proposal.md; phrase-list tuning is
  explicit expected follow-up work, not something v1 needs to get right initially.
- **[Risk] Handoff cross-check false positives (an unrelated brief happens to share a
  probe substring, wrongly suppressing a real flag) vs. false negatives (cross-check can't
  run, so a real gap goes unflagged).** → Design deliberately biases toward silence on
  uncertainty (see "Uncertain cross-check result defaults to silence"): the discovery note
  is explicit that *either* failure direction erodes trust in the mechanism, but a missed
  flag is recoverable next session while a nagging false positive trains the operator to
  ignore the check entirely.
- **[Risk] New subprocess call adds latency to the Stop hook.** → Bounded by a short
  timeout (mirroring `check_brief_staleness.py`'s `SUBPROCESS_TIMEOUT_SECONDS`); a timeout
  or missing binary degrades to "nothing flagged," never a hook failure.

## Migration Plan

Purely additive: a new module, a new console-script entry, and a new code path inside the
existing hook gated behind the same substantive-work trigger it already has. No data
migration, no schema change, no flag to roll out behind. Ships in the same PR as its test
coverage; nothing depends on a prior release.

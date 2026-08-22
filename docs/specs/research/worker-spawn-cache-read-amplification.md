# Investigation — orchestrator worker spawns "reading 900K–2.2M cached tokens per call"

Route J note. Source brief: `20260821-182405-each-orchestrator-worker-spawn-reads`
(observed on worktrail run `go-20260821-141546`, spec
`openspec/changes/stop-hook-deferred-work-flag`, 41 claude spawns, $22.77).

Status: **root cause confirmed; one instrumentation change shipped (turns + ctx/turn in
the usage report). No spawn-flag or prompt changes — see "Levers" for why.**

## Problem, as reported

Individual implement/review/fix workers logged `cache_read_input_tokens` of 200K–2.6M
each for tasks scoped to a single small file. The brief hypothesised a large fixed
per-spawn load (AGENTS.md / skill docs / policy reloaded fresh per worker process).

## Verified observations

All numbers come from the run's journal
(`worktrail-worktrees/stop-hook-deferred-work-capture-spec-worktrees/run-stop-hook-deferred-work-flag.json`)
and from baseline `claude -p "Reply with exactly: OK"` spawns run in this worktree with
the orchestrator's exact flags (`--permission-mode bypassPermissions --output-format
stream-json --verbose --setting-sources project,local --model sonnet`), Claude Code
2.1.239.

1. **`cache_creation_input_tokens` (what a spawn actually writes once) is only 17K–64K
   per worker.** `cache_read` is 200K–2.58M. The two differ by 10–40x.
2. **`cache_read` is `num_turns` amplification, not a per-spawn load.** Pearson
   r(`num_turns`, `cache_read`) = **0.94** across the 41 spawns. Every agentic turn
   re-reads the entire cached prefix, so `cache_read ≈ prefix × turns`. Median turns:
   implement 16.5, review 18, fix 20. Worst case: `fix 3.4` took **49 turns** →
   2.58M cache_read. Per-turn context is remarkably stable: implement ~33K, fix
   ~37K, review ~20K (review reads less because its brief is shorter and it only
   uses `Bash`/`Read`).
3. **The fixed prefix of a bare 1-turn spawn is ~39.7K tokens** (15.3K cache-write +
   24.4K cache-read on turn 1 — the read part is Claude Code's own system prompt +
   built-in tool schemas, already cached account-wide). Decomposition by flag variant
   (all 1-turn, same prompt):

   | variant | tools | skills | total prefix |
   |---|---|---|---|
   | `--setting-sources project,local` (orchestrator default) | 37–57 | 48 | **39.7K** |
   | `--setting-sources local` (drops project hooks + project settings) | 37 | 48 | 34.1K |
   | `+ --disable-slash-commands` | 36 | 0 | 37.0K |
   | `+ --disable-slash-commands --strict-mcp-config --mcp-config '{"mcpServers":{}}'` | 25 | 0 | 36.4K |
   | `--bare` | — | — | **unusable: "Not logged in"** (skips plugin/keychain credentials) |

   So ~30K of the ~40K is Claude Code's harness (system prompt + built-in tools) and is
   not reachable from worktrail. The trimmable slices: project `UserPromptSubmit` hooks
   ~5.6K (this repo's aspens `skill-activation-prompt.sh` injects 3.9–24KB of skill docs
   into every worker prompt; the `base` skill is `alwaysActivate`), skill/slash listing
   ~2.7K, MCP tool schemas ~0.5K.
4. **Worktrail's own contribution to the prefix is small.** `build_worker_prompt` renders
   0.6–1K tokens (implement 2,752 chars, review 3,919, fix 2,494); `AGENTS.md` is 9.7KB
   (~2.4K tokens) and is loaded via project `CLAUDE.md` — once per spawn, like
   everything else. There is no per-spawn re-read of `go-policy.yaml` or skill docs by
   worktrail code; the skill-doc injection in (3) is the repo's hook, not the orchestrator.
5. **Workers used zero skills in all 41 spawns** (`skills_used == []` everywhere;
   `tools_used` was only `Bash`/`Read`/`Edit`/`Grep`/`Write`).
6. **Review was 70% of cost ($15.89 of $22.77) because of model, not context.** Review
   ran on opus via `--model-map review=opus`; its cache_read total (6.9M) is actually
   the *lowest* of the three roles (implement 7.6M, fix 7.0M). At opus cache-read /
   output prices the same token shape costs ~2.3x sonnet's.
7. The existing report line `cache hit: 94% of input-side tokens from cache` is
   technically true and framed the reads as a win — which is how the brief's
   "huge cached context per call" reading arose. The report now also prints
   `turns`, `ctx/turn`, and an explicit `cache_rd is re-read once per turn` footer
   (`progress.render_usage`, `summarize_usage["turns"]`, `_ctx_per_turn`).

## Confirmed root cause

`cache_read_input_tokens` on a multi-turn headless worker is the sum over turns of the
full cached prefix. A ~30–40K prefix × 15–50 turns is 0.5–2.6M. The fixed per-spawn
overhead is real but modest (~40K, ~75% of it the Claude Code harness); the
out-of-proportion cost on a narrow spec came from **turn count** (median 16–20 turns for
single-file tasks, up to 49) and from **review on opus**, not from worktrail reloading
docs per worker.

## Levers, ranked (none applied here beyond instrumentation)

1. **Reduce turns per worker.** This is the only lever with >2x headroom. Median 16–20
   turns for a single-file task means workers are exploring/re-running tests
   repeatedly. The report-back parse failures in the related brief
   (`20260821-182348-orchestrator-worker-report-back-json`) each burn a full extra
   spawn *and* its turns; the 49-turn `fix 3.4` is the shape to look at first
   (transcript not retained — `ORCH_KEEP_TRANSCRIPTS`-style capture would be the
   diagnostic next step). Not changed here: per Route J rule 5, a prompt/turn-budget
   change on one run's evidence is a separate proposal with its own cassette.
2. **Review model.** `--model-map review=opus` is an operator choice, already
   exposed; the cost share is visible in the per-role table. No code change.
3. **`--disable-slash-commands` on worker spawns** (~7% of per-turn context,
   safe per observation 5 for *this* run). Deliberately not defaulted: a target repo
   whose AGENTS.md tells workers to invoke a repo skill would silently lose it, and
   7%×turns is a small win against that risk. Revisit if (1) lands and the prefix
   becomes the dominant term.
4. **Project `UserPromptSubmit` hook injection** (~14%). Repo-side config (aspens
   skill-activation), not worktrail's; dropping it means `--setting-sources local`,
   which also drops the project's other settings. Not worth it per spawn.
5. **MCP / `--bare`**: MCP is <2%; `--bare` breaks OAuth auth. Dead ends.

## Validation steps for the next proposal (turn reduction)

- Capture per-spawn transcripts for one run and bucket turns by activity
  (test reruns, repo exploration, report-back retries).
- Compare `ctx/turn` and `turns` columns before/after any prompt change on the same
  spec; the per-role table now makes the two terms separable.

## Follow-up — turn-count audit, first real capture (2026-08-21)

Source brief: `20260821-184330-turn-count-audit-orchestrator-workers` (run
`go-20260821-184736`), Route J, `implementation-intent: planning-only` — this section
answers the brief's Focus (find the dominant driver behind median 16-20 turns) with one
real capture; it does **not** propose a turn-budget or prompt change (Route J rule 5 —
that needs its own proposal with a routing cassette scenario).

**Instrumentation.** `$WORKTRAIL_KEEP_TRANSCRIPTS=<dir>` (spawnlib.py's `spawn_agent`)
persists each spawn's raw stream-json JSONL, off by default. `scripts/bucket_transcript_turns.py`
matches captured transcripts to a run journal by finish timestamp and buckets each spawn's
assistant turns by activity (`test_execution`, `git_history_exploration`, `repo_exploration`,
`edit`, `other_bash`, `final_report`).

**Capture run.** `worktrail-live full-real` against `openspec/changes/stale-brief-precheck-recheck-search-boundary`
(4 tasks, real spawns, `--role-agent-map review=claude --model-map review=opus` per this
repo's own policy) — 8 spawns (4 implement + 4 review), 94 turns, $3.14 total. **Finding
about the target itself, unrelated to turn-count:** every one of the 4 tasks' workers
independently discovered the change's AC was already satisfied on `main` (implement 1.1:
"already landed via PR #493 (commit 8c188ad); no code change needed"; same for 2.1/2.2/2.3)
— a stale-bookkeeping OpenSpec change (`tasks.md` still shows 4/4 unticked) rather than
genuinely pending work; captured separately as handoff `20260821-193051-close-stale-openspec-change-stale`
(different purpose, different PR/brief — not actioned here).

**Bucket results** (`scripts/bucket_transcript_turns.py`, 8/8 spawns, `context_quality:
sufficient`):

| role | spawns | turns | dominant bucket | breakdown |
|---|---|---|---|---|
| implement | 4 | 30 | `other_bash` 37% | other_bash 37%, repo_exploration 20%, git_history_exploration 20%, final_report 13%, test_execution 10% |
| review | 4 | 44 | `git_history_exploration` 43% | git_history_exploration 43%, other_bash 25%, test_execution 23%, final_report 9% |

**Reading this against the brief's question.** Because every task turned out to already be
shipped, this run's dominant activity in both roles is **investigation proving "nothing to
do here"**, not implementation: `git_history_exploration` + `repo_exploration` +
`other_bash` (mostly `git status`/`ls`/`cat` orientation, not literal history digging)
together are 77% of implement turns and 68% of review turns; `test_execution` is a
secondary confirmation step (10%/23%); `final_report` is a fixed ~1 turn/spawn floor
regardless of role. Review runs longer than implement here (11 turns/spawn vs 7.5) because
its instructed skepticism ("do not rubber-stamp... a plausible-sounding rationale in its
place is not acceptable") makes it re-derive the same "already shipped" conclusion
independently rather than trust the implement report.

**Caveats — do not over-generalize from this run:**
- **N=8 spawns vs the reference run's N=41** — this run's per-role turn medians (implement
  7.5, review 11) are themselves far below the reference run's (implement 16.5, review 18);
  a small, already-shipped 4-task change is not the reference run's shape. The bucket
  *proportions* are the useful output here, not these specific turn counts.
- **The dominant driver in a genuinely-pending task is unmeasured by this run** — every task
  here was a no-op, so "investigation to confirm nothing needs to change" mechanically
  dominates. The reference run's actual `fix 3.4` 49-turn outlier (still not re-captured
  with transcripts) remains the more representative next target once a genuinely pending
  multi-file task is available to run this same capture against.
- **`observed_turns` (unique assistant message IDs in the transcript) undercounts the API's
  own `num_turns`** on `review` spawns specifically (e.g. 11 observed vs 16 reported, 10 vs
  17, 13 vs 14, 10 vs 16) but matches exactly on 3 of 4 `implement` spawns. Root cause not
  investigated further here — plausibly server-side turns with no corresponding
  JSONL `assistant` event (e.g. an empty continuation) — flagged as a known gap in the
  script's own per-spawn output (`num_turns_reported` alongside `observed_turns`) rather than
  silently treating the two as equivalent.

**Not done here (Route J rule 5 / planning-only):** no prompt, turn-budget, or review-role
change is proposed from this one run. The next useful capture is the same instrumentation
against a genuinely multi-file pending task, ideally one that already ran without it so the
`turns`/`ctx/turn` usage-report columns give a before/after comparison point.

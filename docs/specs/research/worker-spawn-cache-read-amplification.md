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

# Epic 003: Agent Learning From Run Outcomes

**Status:** Proposed  
**Owner:** Worktrail maintainers  
**Origin:** Operator request, 2026-09-12: use Claude Code's agent `memory:` feature so agents self-learn across runs  
**Release scope:** the `release_gate: v1.0` fixes-only freeze was lifted on 2026-09-19 (its own condition -- v1.0 shipped -- had been met since well before 1.1.41). Features 2 and 3 were held for "v1.1 feature work to open"; that gate is gone, so they are schedulable on their own merits rather than on the freeze.

## Business objective

Orchestrator runs repeat the same failures. The same quarantine reasons, review rejections, and
insufficient-context reports come up run after run, and nothing carries that knowledge forward.
The operator and `~/.claude` memory currently hold it, and the headless workers that hit the
failures never see it.

The smallest complete outcome has three parts:
- Worktrail keeps one curated, per-repo memory of failure patterns learned from its own run
  outcomes.
- Every worker sees that memory, whichever harness it runs on.
- No memory file ever leaks into a feature PR.

## Personas

- **Worktrail operator:** wants fewer repeat quarantines and review rounds without hand-writing
  lessons into each repo's `AGENTS.md`.
- **Worker agent (Claude, Codex, OpenCode):** needs the repo's known pitfalls in its prompt
  before it starts, not after it fails.
- **Maintainer:** needs learning to stay opt-in, bounded in cost and size, traceable, and unable
  to change a run's outcome.

## Verified platform behavior

Spikes run against Claude Code 2.1.270 on 2026-09-12 and 2026-09-13. Each probe agent printed
the `MEMORY.md` contents it received in its system prompt; the files on disk were then inspected.

| Question | Observed |
|---|---|
| `claude -p --agent <name>` with `memory: project` writes and reloads memory | Yes |
| Memory works when a session delegates to the agent through the Agent tool | Yes: written to `<project>/.claude/agent-memory/<name>/` |
| Location inside a linked git worktree | The worktree root, as an untracked `.claude/agent-memory/` |
| Plugin-shipped agent honors `memory` | Yes: stored as `.claude/agent-memory/<plugin>-<agent>/` |
| Memory loads under worker settings (`--setting-sources project,local`) | Yes: file-defined agent, both git and non-git cwd |
| Inline `--agents '<json>'` definition with `"memory": "project"` | Yes, in a git and a non-git cwd. One of two non-git runs printed an empty memory line; the rerun printed it. |
| Memory updates automatically from tool calls | No. The docs say the agent writes memory only when its prompt tells it to. |

Code facts:
- `spawnlib._with_default_setting_sources` excludes user-level settings from every Claude worker.
- `integrate.py` runs `git add -A` in the integration worktree.
- `spawnlib.prepare_opencode_child_environment` already self-ignores its own `.worktrail/`
  state for the same `git add -A` reason.

## Scope

- Keep agent memory files written during worker sessions out of worktree commits.
- Turn a finished run journal into a deterministic outcome digest, with no model call.
- Run one opt-in retro agent after a run that has signal. It curates a per-repo `MEMORY.md`
  under the Worktrail home, never inside a target repo or worktree.
- Inject the curated worker notes into every worker prompt, with bounded size and a journal
  record of what was injected.

## Non-goals

- Learning from raw tool-call streams. Outcomes are the signal; tool counts are noise.
- Changing a run's outcome, gating, or routing based on learned notes. Notes are advisory prompt
  context only.
- Managing a repo's own committed `project`-scope agent memory. Tracked memory files keep normal
  git behavior.
- Writing learned notes into target-repo `AGENTS.md` or `~/.claude` memory.
- Cross-repo learning or a shared notes corpus.

## Success metrics

- A memory file created by any agent in a task worktree is never staged by `git add -A`.
- A run with no quarantined group, insufficient context, critical issue, or scope escalation
  spawns no retro agent.
- After a run with signal, `MEMORY.md` has a `## Notes for workers` section. Each bullet cites
  the spec, group or task, and date that evidence it.
- Worker prompts in a repo with notes contain the notes block. In a repo without notes, prompts
  are byte-identical to today, and `orchestrate check` stays green.
- The journal records a `learned_notes_sha256`, so later quarantine and review-round rates can
  be compared before and after notes.

## Feature decomposition

### Feature 1 — Worker agent-memory isolation

**Future spec id:** `worker-agent-memory-isolation`

Before `spawn_agent` launches any worker whose cwd is inside a linked git worktree, add a
self-ignoring `.gitignore` to that worktree's `.claude/agent-memory/` and
`.claude/agent-memory-local/`. Never touch a canonical checkout.

**Independent value:** a repo that adds its own `memory:` agent can't slip memory files into
orchestrator PRs, even if Features 2 and 3 never ship.

**Release evidence:** a test creates a real linked worktree, writes a memory file, runs
`git add -A`, and asserts nothing is staged. Separate tests cover the canonical-checkout
exclusion, non-git cwd, tracked memory files, idempotence, and fail-open behavior.

### Feature 2 — Run-outcome retro agent

**Future spec id:** `run-outcome-retro-agent`

Add a pure `build_outcome_digest(journal)` and an opt-in `agent_learning` policy block. After a
run whose digest has signal, `worktrail-retro` spawns a Claude agent through `spawn_agent`:
- defined inline with `--agents`, `memory: project`, and tools limited to Read, Write and Edit;
- cwd `worktrail_home()/learning/<repo-name>/`;
- guarded by a per-repo lock;
- best-effort, recording a `retro` journal event.

**Independent value:** the operator gets a curated, evidence-cited failure-pattern file per repo,
readable by hand, before any prompt injection exists.

**Release evidence:** digest tests on journal fixtures shaped like real `run-*.json` files, a
no-signal skip test, lock contention, non-Claude cell refusal, timeout and exception
containment, and a `--dry-run` CLI test.

### Feature 3 — Learned notes in worker prompts

**Future spec id:** `worker-prompt-learned-notes`

Add `load_learned_notes(repo)`, which extracts the `## Notes for workers` bullets with caps on
count and size. The run resolves it once. `WorkerPromptCtx.learned_notes` renders an advisory
block before "Hard rules:" for every role and harness. The journal records the block's SHA-256.

**Independent value:** knowledge from past runs reaches Codex and OpenCode workers as well as
Claude, turning the memory into fewer repeat failures.

**Release evidence:** loader tests (absent file, missing section, caps), prompt-rendering tests
for each role (present vs byte-identical absent), a run-level single-snapshot test, and a green
`orchestrate check`.

## Dependencies

- Feature 1 has no dependency and can ship alone.
- Feature 3 depends on Feature 2's memory path helper and `## Notes for workers` contract.
- Features 2 and 3 add new modules and requirements only, with no MODIFIED deltas against
  existing capabilities, so they cannot drift each other's specs.

## Sequencing

1. `worker-agent-memory-isolation`: close the leak before anything encourages memory use.
2. `run-outcome-retro-agent`: start accumulating curated notes, operator-readable only.
3. `worker-prompt-learned-notes`: feed notes to workers once a few real retros have produced
   notes worth trusting.

## Risks and mitigations

- **Wrong or stale lessons steer workers badly:**
  - each note must cite evidence;
  - the retro prompt removes notes that later outcomes contradict;
  - the prompt block is labeled advisory, with the task brief and hard rules winning;
  - the size cap bounds the damage;
  - the journal SHA lets a regression be traced to a notes revision.
- **Retro cost on every run:** off by default; no spawn on a no-signal digest; one lock-guarded
  spawn per run.
- **Secrets copied into memory:** the digest carries structured journal fields (states, reasons,
  counts, ids) plus the worker-reported `notes` and `missing_context` text, truncated. It never
  carries worker transcripts, tool output, or environment. The retro prompt forbids credentials
  and environment values.
- **Retro breaks a run:** best-effort containment. Exceptions, timeouts and capacity exhaustion
  become a `retro` journal event; the run's return value is unchanged.
- **Memory silently fails to load:** inline-definition loading passed in both cwd shapes, but one
  non-git spike printed an empty memory line once. Feature 2's end-to-end check asserts the reload
  from observed output rather than assuming it.
- **Claude Code changes the memory contract:** the path and section contract is isolated in one
  helper, and Feature 3 degrades to no block when the file is absent.

## Release strategy

1. Ship Feature 1 on its own. It adds no configuration and only ignores untracked memory paths.
2. Ship Feature 2 with `agent_learning` disabled everywhere. Enable it on one dogfood repo and
   review `MEMORY.md` by hand after several runs.
3. Ship Feature 3 once the notes look trustworthy. Compare quarantine and review-round rates
   before and after, by `learned_notes_sha256`.

Rollback: setting `agent_learning: false` stops both the retro and the prompt block, and the
learning directory stays on disk for inspection. Feature 1 should not be rolled back once agents
with memory are in use.

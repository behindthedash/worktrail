## Context

See `proposal.md` for the verified leak.

Every orchestrator, verify, drain, triage, and compile agent launch goes through
`spawnlib.spawn_agent`; `spawn_claude_p` delegates to it. Worktrees are created at eight sites:
- `live.py` (two)
- `integrate.py`
- `verify.py`
- `drain.py` (three)
- one more in `live.py`'s sample-target path

Existing precedent: `prepare_opencode_child_environment` writes `.worktrail/.gitignore` with `*`,
so a worker's `git add -A` never commits orchestrator state.

## Goals / Non-Goals

**Goals:**
- A memory file an agent creates under `.claude/agent-memory/` or `.claude/agent-memory-local/`
  in a linked worktree is never staged.
- No file is ever written into a canonical checkout.
- A spawn never fails because of this step.

**Non-Goals:**
- Changing git behavior for memory files a repo already tracks.
- Salvaging or relocating worker-written memory. Learning comes from run outcomes (Epic 003
  Feature 2), not worker memory.
- Ignoring other agent-written paths.

## Decisions

### D1: Guard at the `spawn_agent` choke point, not at worktree creation

Worktree creation has eight call sites across four modules, but memory only appears when an agent
runs, and every agent launch goes through `spawn_agent`. One call there covers every harness and
every caller, including future worktree sites.

*Alternative rejected:* a shared helper called from each `git worktree add` site. It needs eight
edits, and a ninth site would silently reintroduce the leak.

### D2: Self-ignoring `.gitignore` inside each memory directory

A `.gitignore` of `*` ignores every untracked file below its directory, including itself. The
worktree's `git status` stays clean, and a tracked file under the directory keeps being tracked.
This mirrors the existing `.worktrail/.gitignore` precedent.

*Alternative rejected:* `.git/info/exclude`. For linked worktrees it is read from the shared
common git directory. **Assumption**, not verified in this change: that would mean writing into
the canonical repository's metadata and affecting every worktree and the main checkout.

### D3: Linked-worktree detection

The step acts only when all of these hold:
- `git rev-parse --show-toplevel` succeeds;
- `git rev-parse --git-dir` succeeds;
- the resolved git dir differs from `_git_common_dir(cwd)`, i.e. the cwd is a linked worktree.

Anything else counts as not a linked worktree: a failed probe, a canonical checkout, or a non-git
cwd. In that case nothing is written.

### D4: Fail open

Every git probe and filesystem write sits inside one `try`. On `OSError` or
`subprocess.SubprocessError`, the step calls `log("agent-memory isolation skipped: <reason>")`
and returns. `spawn_agent` continues unchanged.

An existing `.gitignore` in either directory is left byte-for-byte unchanged, even if its content
differs. A repo that authored one owns that decision.

## Risks / Trade-offs

- **A repo that wants workers to add new memory files to its committed `project` memory:** those
  new files are now ignored inside orchestrator worktrees. This is intended: parallel workers
  cannot curate shared memory without conflicts. Edits to already-tracked memory files still
  stage normally.
- **Two git probes per spawn:** negligible next to an agent launch.
- **Claude Code moves the memory directories:** the paths are two module constants beside the
  helper, and a spike re-run tells you when to update them.

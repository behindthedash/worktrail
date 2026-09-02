---
name: detach
description: worktrail-detach — supervised out-of-harness launch with log/pid/exit sentinel so long-running runs survive the Claude Code background-task reaper
triggers:
  files:
    - src/worktrail/runtime/detach.py
    - tests/runtime/test_detach.py
    - skills/worktrail-go/references/subagent-prompts.md
  keywords:
    - worktrail-detach
    - detach
    - run_in_background
    - supervise
    - exit sentinel
    - full-real
    - Monitor
---

You are working on **`worktrail-detach`**, the primitive that runs a long-running command outside
the agent harness's tracked process tree (`src/worktrail/runtime/detach.py`, console script
`worktrail-detach`).

## Domain purpose
Claude Code's Bash tool `run_in_background` registers the child as a harness-tracked task, and on
this fleet's WSL host the harness periodically reaps *all* of its own tracked background tasks
(bare `[killed]`, no exit code, no traceback, session survives). Every `worktrail-live full-real`,
`worktrail-preflight run`, and headless `worktrail-skill-dispatch` launched that way died mid-run
(some within 10–25 s), while the identical command launched detached ran to a clean exit every time
(`~/.devops/background-kill-hypotheses.md` H9, confirmed 2026-08-29; 27 logged incidents through
2026-09-01). No cgroup/OOM is involved and no setting disables the reap — the fix is to never hand
the harness a handle. `worktrail-detach` makes that a testable primitive instead of a hand-typed
`nohup … & disown` each agent had to remember (and got wrong: `nohup` *inside* a
`run_in_background` call is still tracked).

## Business rules / invariants
- **`launch` is a plain foreground Bash call that returns immediately** with a JSON handle
  (`name`, `pid`, `supervisor_pid`, `log`, `pid_file`, `exit_file`, `cwd`, `wait_cmd`). Never
  wrap it in `run_in_background`, `nohup … &`, `setsid`, or a trailing `&`.
- **Supervisor runs in its own session** (`subprocess.Popen(start_new_session=True)`, re-invoking
  `python -m worktrail.runtime.detach _supervise`); the real command is its child. Tests assert
  `os.getsid`/`os.getpgid` differ from the launcher's.
- **State per handle lives under `$WORKTRAIL_HOME/detached/`** (default `~/.worktrail/detached`,
  overridable via `--state-dir`): `<name>.log` (stdout+stderr, appended, bracketed by
  `[worktrail-detach] started …` / `exit rc=N` lines), `<name>.pid` (child pid), `<name>.exit`
  (return code sentinel, written only after the child exits). A spawn failure writes `127`.
- **`--name` must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$`** — it becomes a filename; path
  escapes are rejected with `SystemExit`.
- **`launch` refuses to clobber a still-running handle of the same name** (returns
  `{"error": "already-running"}`, exit code 3) unless `--force`. Stale pid/exit files are unlinked
  on relaunch.
- **Signals forward to the child**: the supervisor traps SIGTERM/SIGINT/SIGHUP and re-sends them
  to the child, so killing the supervisor pid stops the run cleanly.
- **`status` states**: `exited` (sentinel present) > `unknown` (no pid file; exit code 2) >
  `running` (pid alive) > `gone` (pid dead, no sentinel — read the log).
- **`wait` is the single source of truth for completion**: follows the log from the current end
  (`--from-start` replays), prints only lines matching `--match` regex (default: none), then
  `[worktrail-detach] exited rc=N`, and exits with the run's own rc. A pid that dies without a
  sentinel yields exit 1 (`gone without exit sentinel`); `--timeout` yields 124.

## Non-obvious behaviors
- `launch` polls up to 5 s for the supervisor to write the pid file so the handle's `pid` is
  usually populated; `pid` may be `null` on a very slow host — `status` will still resolve it.
- `wait` gives the supervisor one `--interval` after the child pid disappears before declaring
  it gone, since the sentinel lands a beat after the child exits.
- The log's `started cmd=[...]` header contains the command's own text, so a `--match` pattern
  can match its own invocation line — tests put scripts in files for that reason.

## How the skill docs use it
`skills/worktrail-go/references/subagent-prompts.md` `#orchestrator` launches `full-real` as
`worktrail-detach launch --name "orch-<repo>-<spec>" --cwd "$SPEC_ROOT" -- worktrail-live full-real …`,
confirms health with `worktrail-detach status`, then arms one `Monitor` on the handle's `wait_cmd`
with a `--match` covering progress and failure signatures (`pull/[0-9]+|MERGED|quarantin|escalat|FAILED|Traceback|circuit|blocked`).
Never `sleep` (harness blocks it) and never `tail -f` the log yourself. `#openspec-propose`
long spawns use the same pattern with `worktrail-skill-dispatch`.

## Critical files
- `src/worktrail/runtime/detach.py` — `launch` / `supervise` / `status` / `wait` and the CLI
- `tests/runtime/test_detach.py` — spawns real subprocesses (session placement and sentinel
  bookkeeping cannot be asserted with a mocked `Popen`)

## Critical Rules
- Any new long-running console-script invocation documented in a skill must use
  `worktrail-detach launch` from a foreground call — never `run_in_background`.
- `worktrail-detach` is a `[project.scripts]` entry; `tests/test_plugin_surface.py` requires every
  skill-doc mention to match it exactly.

---
**Last Updated:** 2026-09-02

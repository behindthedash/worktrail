---
name: worktrail-drain
description: >
  Drain the work queue unattended: repeatedly launch fresh-context headless one-shots
  of `/go auto` until the queue is empty or a stop condition fires (capacity gate,
  circuit breaker, budget, max items). Use when the user wants queued briefs worked
  through without babysitting each item — "drain the queue", "work through the
  backlog", "run /go auto until the queue is empty", "/worktrail-drain 5". One /go
  item goes through /go directly; same-session looping (/loop /go auto) accumulates
  context — this driver spawns a fresh process per item instead.
argument-hint: "[max-items] [repo] — e.g. /worktrail-drain 5 ggb"
allowed-tools: Read, Bash
---

# Drain — Unattended Queue-Drain Driver

## Overview

Wraps `worktrail-drain`: a deterministic loop that spawns ONE fresh-context headless
one-shot of `/go auto` per queued brief, classifies each outcome from the run record
it leaves under `~/.go/runs/`, and stops with an explicit, reported reason. Fresh
process per iteration means fresh context by construction — the failure mode of
in-session `/loop /go auto` (context accumulation, permission-prompt stalls) cannot
occur. All coordination state is on disk already (atomic `work_queue.py` claims, run
records, orchestrator journals), so no state is shared between iterations.

## When to Use

- `/worktrail-drain` — drain until the queue is empty or a stop condition fires
- `/worktrail-drain 5` — at most 5 items this run
- `/worktrail-drain 5 ggb` — at most 5 items, picks scoped to one repo (`/go ggb auto`)
- NOT for a single item — use `/go` or `/go auto` directly
- NOT a scheduler — recurring/cron entries belong to the devops repo, pointed at
  `drain.py`, never at an interactive session

## Instructions

### Step 1 — Confirm the tooling

`worktrail-drain` is a console script installed by the `worktrail` package, so it is on
`PATH`. If it is not, stop and report that the package is not installed
(`pip install worktrail`).

### Step 2 — Resolve the agent CLI

Use the same invocation-context precedence chain as `/go` (explicit > policy > env >
host default): `$AGENT_CLI` if set, else the workspace policy's `agent_cli`, else
`$GO_AGENT_CLI` / `$ORCH_AGENT` / host markers, else `claude`. Pass the resolved
value as `--agent`. Inside each one-shot, `/go auto` re-resolves policy itself, so
per-repo routing (including subscription-aware fallback once policy carries it)
flows through without drain-side logic.

### Step 3 — State the permission posture, then launch

`drain.py` never adds a permission-bypass flag on its own. Before launching, state
the posture in one line: either "one-shots run with default permission prompts —
they may stall on unapproved tools" or, when the user has explicitly asked for
unattended execution, pass the flag through:

```bash
worktrail-drain \
  --agent "$RESOLVED_AGENT" \
  ${MAX_ITEMS:+--max-items "$MAX_ITEMS"} ${ARG_REPO:+--go-repo "$ARG_REPO"} \
  --permission-arg --dangerously-skip-permissions   # ONLY if user opted in
```

Run it in the background (`run_in_background`), since each iteration is a full
`/go auto` run (often 10–45 min). Relay iteration lines as they appear; do not add
polling sleep loops — the harness reports process exit.

Flags, defaults, and stop conditions are documented in `drain.py --help` and its
module docstring — that file is the single source of truth for loop mechanics.
`--dry-run` previews the first decision and exact command without launching.

### Step 4 — Report

On exit, report: the stop reason (drain.py prints `drain stop: <reason>`), the
per-iteration outcome lines (brief, completion state, PR), and any
`pending human approval:` PRs. A `lock_held` refusal means another drain owns this
queue — surface it, never delete `~/.go/drain.lock` by hand while its pid is alive.

## Examples

**Drain everything, default posture**
```
/worktrail-drain
```
→ resolves agent (e.g. `claude`), states the permission posture, launches
`drain.py` in the background; reports each iteration line and the final stop
reason (e.g. `drain stop: queue_empty: no ready briefs`).

**Bounded evening run, one repo, unattended**
```
/worktrail-drain 5 ggb
```
→ user has opted into unattended execution earlier in the session →
`drain.py --max-items 5 --go-repo ggb --permission-arg --dangerously-skip-permissions`;
stops after 5 items or earlier, listing any PRs pending human approval.

**Preview only**
```
/worktrail-drain dry-run
```
→ `drain.py --dry-run` prints the first decision and the exact one-shot command
without launching anything.

## Best Practices

- Prefer a `--max-items` or `--budget-minutes` bound for a first run on a busy
  queue; unbounded drains are for queues you already trust end-to-end.
- Drain when no other `/go` session is active on the machine, so run-record
  outcome attribution stays unambiguous.
- After a stop for `capacity_gated`, fix the external condition and clear the
  gate via `worktrail-agent-capacity clear` (see the `/go` capacity-cache commands)
  before re-draining — drain never clears gates itself.

## Constraints and Warnings

- One drain per machine/queue: the lockfile is the guard; a stale lock (dead pid) is
  reclaimed automatically.
- Outcome classification reads the newest run record modified during the iteration;
  a concurrent interactive `/go` session on the same machine can blur attribution —
  prefer draining when no other /go work is running.
- Drain never releases or reassigns another session's claims; a blocked or failed
  brief stays in `picked/` for a human or a later `/go` to resume.
- `completed_awaiting_human_approval` is noted and the loop continues — that is an
  approval gate working, not a stall.

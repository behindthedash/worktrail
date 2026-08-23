---
name: worktrail-handoff
description: >
  Capture a discovered problem, gap, tech debt, or deferred work into a local work queue so
  a future agent session can pick it up cold. Use mid-session whenever you set work aside
  instead of doing it now — a fix you judge out of scope for this PR, work that belongs in a
  follow-up or a separate/later PR, an item you're deferring, or something worth fixing you
  won't tackle now — so the outstanding item isn't lost without derailing current work; can
  be called multiple times per session. Trigger phrases: "defer this", "item deferred", "out
  of scope for this PR", "follow-up work", "leave for another PR", "capture for later",
  "outstanding item". This skill captures only. Picking a brief back up is the front door's
  job: worktrail-go <brief-id>.
argument-hint: "[focus text] — capture a brief; pick one up with worktrail-go <brief-id>"
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# Handoff Skill

## Overview

Captures a discovered problem, gap, or tech debt into a queue document so a future agent
can pick it up cold. The current session continues — this is issue capture, not session
termination. Multiple handoffs can be created in a single session.

## When to Use

Use this skill to **capture** deferrable work that surfaces mid-session, either because the
user asked for it or because one of the trigger phrases above fits what you just set aside.

**Picking a brief back up is not this skill's job.** The front door owns that, and owns it
alone: `worktrail-go <brief-id>` claims the brief and routes it, and `worktrail-go auto` or
`worktrail-go drain` work the queue without naming one. Send the user there rather than
describing a claim procedure here — a second documented path is how the queue ended up with
two half-true answers to "how do I start this brief?"

### Proactive session-end quality gate

When a Stop hook or portable workspace convention asks for a proactive "next best thing," creating
a new brief is **optional and exceptional**, not a required session-close action. Capture only a
step-change that has substantial independent value: a meaningful new capability, removal of a
recurring high-cost bottleneck, a material user-outcome improvement, or a verified major
reliability/security/operations risk. Routine polish, adjacent cleanup, extra tests/docs, minor
optimizations, speculative flexibility, and "the next obvious task" do not qualify. If nothing
clears that bar, explicitly skip capture. This gate does not suppress an explicit user request or
genuine deferred work that would otherwise be lost.

Any argument is the focus text. Queue-pull phrasing ("what's next in the queue", "pick up the
next brief") is not this skill — point the user at `worktrail-go`.

## Instructions

First resolve the shared script and the queue base (shell vars don't persist between Bash
calls — re-resolve or paste the literal path):

```bash
BASE="${WORK_QUEUE_DIR:-$HOME/work-queue}"      # queue/ and picked/ live under here
```

### Capture workflow

**Step 1 — Determine focus.** If the user passed focus text, use it. If not, infer it from what
was left incomplete in this conversation. Ask once via `AskUserQuestion` only if genuinely
ambiguous. Redact API keys, passwords, tokens, and PII before passing content to the command.

**Step 2 — Create through Worktrail.** The CLI owns filename generation, frontmatter, route
classification, validation, candidate scoring, and high-confidence related linking. Pass the
focus and any known context instead of writing Markdown directly:

```bash
worktrail-handoff --focus "$FOCUS_TEXT" --queue-dir "$BASE" \
  [--repo "$REPO"] [--remote "$REMOTE"] [--base-branch "$BASE_BRANCH"] \
  [--context "$CONTEXT"] [--approach "$APPROACH"] \
  [--artifacts "$ARTIFACTS"] [--questions "$QUESTIONS"] \
  [--suggested-skill skill.name]... --json
```

Use `--recommended-route`, `--implementation-intent`, `--change-kind`, `--target-spec`,
`--blocked-by`, or `--watch` when the capturing agent has direct evidence. The command omits
`recommended-route` when classification is low-confidence and ambiguous rather than guessing.

Use `--triage blocker|deferred` to release-scope the brief at capture time. When the target
repo's policy sets `release_gate` (a release freeze), classify honestly: `blocker` only for
work that must land before that release ships; everything else `deferred` (or omit — untriaged
briefs rank between the two but are also skipped by auto-pick during a freeze). Re-scope later
with `worktrail-work-queue triage <id> blocker|deferred|clear`.
It returns `auto_linked` IDs and `confirm` candidates. Report automatic links; ask via
`AskUserQuestion` whether to link any `confirm` candidates, then use
`worktrail-work-queue link` for selected IDs.

**Step 3 — Confirm.** Tell the user the returned file path, focus, suggested skills, and how to
start it — one spelling only: ``Start it with `worktrail-go <brief-id>`.`` Do not also offer
`worktrail-work-queue claim`; that is the internal primitive the front door calls, and naming
both invites the reader to pick the one that skips routing. If creation fails, report the CLI
error and do not hand-write a fallback document.

### Closing a brief

Closure runs through the front door, which owns `worktrail-work-queue done` and its
`--planning-only` / `--implementation-complete` qualifiers. One rule belongs here, with the
brief document itself:

**Closing with a re-verification claim requires showing the re-run, not just asserting it.**
A `--note` that claims a result was "disproven", "re-verified", or "no longer flags/triggers"
(a detector, a check, a script) is rejected outright (`status:
unverified_reverification_claim`, no mutation) unless the note also shows the actual command
output — inside a fenced ` ``` ` block, or an explicit `Command:`/`Output:` pair — not prose
alone. Actually run the cited check before writing the note; paste its real output. A brief
closed on an unverified "disproven" claim can hide a still-real finding: brief
20260817-101013-datalena-release-notes-consolidate-yml-missing-docs-skip-gate was closed
2026-08-20 with "disproven — corrected detector no longer flags this file," but re-executing
the cited detector afterward showed it still flagged the file. Closure notes that don't
assert a re-verification result (e.g. "duplicate of X", "out of scope: different purpose")
are unaffected.

### Decisions awaiting a human

Unattended runs park genuine product decisions in a sibling queue
(`$WORK_QUEUE_DIR/decisions/`) instead of stranding their briefs: the brief sits in `queue/`
with `awaiting-decision: <id>` and stays blocked until the decision is answered. Review and
answer with:

```bash
worktrail-decision list                       # open / answered / resolved at a glance
worktrail-decision show <id>                  # the full structured question
worktrail-decision answer <id> --answer "..." # unblocks the brief for the next auto pass
```

Answering by hand also works: edit the record's `## Answer` section and move the file from
`decisions/open/` to `decisions/answered/` — the directory is the arbiter. Never delete an
open decision to unblock a brief; answer it (even "proceed, your call") so the resuming
session has an explicit human answer to act on.

### Document format

Write the brief per `references/handoff-template.md`, which holds the field rules and a
complete filled-out example. In short: frontmatter fields are literal (`repo`/`remote` are
`null` when not in a repo), `status:` starts at `queued`, reference commits/PRs/paths instead
of reproducing them, mark each open item by type, and keep the brief under ~150 lines.

## Examples

**Mid-session capture.** While implementing a feature you notice the auth middleware swallows
errors. It is real, and it is not this PR's purpose. The skill writes
`~/work-queue/queue/20260531-141200-auth-middleware-error-handling.md` with the focus,
discovery context, a suggested approach, and `suggested-skills: [devkit.fix-debugging]`. You
report the path, tell the user to start it with
`worktrail-go 20260531-141200-auth-middleware-error-handling`, and keep working the original
task.

**Next session.** The user starts that brief through the front door, not through this skill.
`worktrail-go` claims it, routes it, and closes it — this skill is not involved.

## Best Practices and Constraints

- **Redact secrets and PII** (API keys, passwords, tokens) before writing any brief.
- The queue is personal and local — never commit it *into a project repo*. This skill writes
  only under `$WORK_QUEUE_DIR` and never modifies the working repo.
- **Optional offsite backup (single-machine):** `$WORK_QUEUE_DIR` may itself be a standalone,
  **private** git repo. Set `WORK_QUEUE_GIT_SYNC=1` and `work_queue.py` will commit+push after
  each `claim`/`done`/`release`/`link` (best-effort: push-only, errors swallowed, never blocks a
  mutation). It only ever pushes — never pull on a live queue, as that races the atomic claim
  rename. Newly captured briefs are written directly, not via the script, so pair this with a
  low-frequency `git push` cron to catch them. Default (flag unset) is the original no-git
  behavior.
- **Always claim/release/complete via `work_queue.py`** — never hand-`mv` a brief. The atomic
  rename is the entire concurrency guarantee.
- Prefer references (paths, PR/issue URLs) over reproducing content the brief would duplicate.
- Tailor "Suggested approach" and "Suggested skills" to the specific focus, not generic advice.
- Handoff is **issue capture, not session termination** — the current session continues.
- A proactive session-end suggestion creates a brief only when it clears the exceptional-value
  gate above; producing no brief is a valid and often preferable outcome.
- `done` briefs stay in `picked/` as a kept log (never auto-deleted). An abandoned claim can
  be `release`d back to `queue/` — the queue is advisory, not enforced.

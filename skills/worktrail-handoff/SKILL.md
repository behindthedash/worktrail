---
name: worktrail-handoff
description: >
  Capture a discovered problem, gap, tech debt, or deferred work into a local work queue so
  a future agent session can pick it up cold, or consume the queue to start the next item.
  Use mid-session whenever you set work aside instead of doing it now — a fix you judge out
  of scope for this PR, work that belongs in a follow-up or a separate/later PR, an item
  you're deferring, or something worth fixing you won't tackle now — so the outstanding item
  isn't lost without derailing current work; can be called multiple times per session.
  Trigger phrases: "defer this", "item deferred", "out of scope for this PR", "follow-up
  work", "leave for another PR", "capture for later", "outstanding item". Invoke as
  /handoff [new|consume] [arg]. An explicit mode wins; if omitted, a description means new.
  Queue consumption is delegated to the project-management go front door so SDD-shaped briefs
  go through the SDD conductor instead of a piecemeal suggested-skill path.
argument-hint: "[new] [focus text] or [consume <brief id>] — consume delegates to go"
allowed-tools: Read, Write, Bash, AskUserQuestion
---

# Handoff Skill

## Overview

Captures a discovered problem, gap, or tech debt into a queue document so a future agent
can pick it up cold, and consumes that queue to start the next item. The current session
continues — this is issue capture, not session termination. Multiple handoffs can be
created in a single session.

## When to Use

- **`/handoff new [focus]`** (or just `/handoff [focus]`) — capture deferrable work that
  surfaces mid-session.
- **`/handoff consume [id]`** (or "check the handoff queue" / "what's next in the queue") —
  delegate to `worktrail:worktrail-go [id]`, which claims and starts the next item.

### Proactive session-end quality gate

When a Stop hook or portable workspace convention asks for a proactive "next best thing," creating
a new brief is **optional and exceptional**, not a required session-close action. Capture only a
step-change that has substantial independent value: a meaningful new capability, removal of a
recurring high-cost bottleneck, a material user-outcome improvement, or a verified major
reliability/security/operations risk. Routine polish, adjacent cleanup, extra tests/docs, minor
optimizations, speculative flexibility, and "the next obvious task" do not qualify. If nothing
clears that bar, explicitly skip capture. This gate does not suppress an explicit user request or
genuine deferred work that would otherwise be lost.

Mode resolution: an explicit `new` / `consume` first token wins. If omitted, a non-empty
description ⇒ `new`; an empty argument or queue-pull phrasing ⇒ delegate to `go`. When still
genuinely ambiguous, default to `new`.

## Instructions

First resolve the shared script and the queue base (shell vars don't persist between Bash
calls — re-resolve or paste the literal path):

```bash
BASE="${WORK_QUEUE_DIR:-$HOME/work-queue}"      # queue/ and picked/ live under here
```

### Create workflow (`new`)

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
consume it ("Start a new session and run `/handoff consume` to pick this up"). If creation fails,
report the CLI error and do not hand-write a fallback document.

### Consume workflow (`consume`)

`consume` is now a compatibility entry point. Invoke `worktrail:worktrail-go`
with the same optional brief ID and let it own listing, claiming, SDD-shaped delegation, and
completion. To work through MANY queued briefs unattended (fresh context per item), use
`worktrail-drain` instead of consuming one by one or looping `/go auto` in-session. The go front door may **batch-claim** related queued briefs into the same run
(`work_queue.py claim-batch` + `score_candidates.py --mode batch` — same repo, similar
spec/module surface, each brief still individually claimed and individually marked done).
The detailed steps below remain as the fallback contract if the `worktrail-go` skill is not installed
in the current client; the fallback claims one brief at a time.

**Step 1 — List the queue.**

```bash
worktrail-work-queue list --json
```

If empty, say so and stop. The JSON response includes a `blocked` boolean per brief.
**Skip blocked briefs by default** — they have unfinished prerequisites and are not yet
actionable. Work only the ready (unblocked) ones unless the user explicitly names a blocked
brief (e.g. they intend to work the prerequisite first in this session).
If one ready item, confirm before starting ("Pick up `<filename>`?").
If multiple ready items, show the list (newest first, with each `focus`) and ask which to work.
If the user passed an explicit id, skip straight to claim.

**Step 2 — Claim it (atomic move queue/ → picked/).**

```bash
worktrail-work-queue claim <id-or-filename> --json
```

The brief id may be a full filename, the filename without `.md`, a unique timestamp prefix,
or the `id` frontmatter value. Act on the `status`:

- `claimed` → use the returned `path` (now in `picked/`, stamped `status: picked`) for Step 3.
  If the response includes a non-empty `warnings` list, surface each warning to the user
  before proceeding (e.g. "Note: this brief is blocked by `<id>` which is not yet done").
- `already-claimed` → another session got it first; re-list and pick another.
- `ambiguous` → show the candidates and ask which.
- `none` → that id isn't in the queue; re-list.

Never edit/move a brief by hand — always go through `work_queue.py` so the claim stays atomic.

**Step 3 — Read and present.** Read the claimed document and present a brief summary: focus /
what this session will do, key context (repo, branch, relevant PRs/issues), and suggested
skills to invoke.

After presenting the summary, surface any related neighbours: read the claimed brief's
`related` frontmatter field (a list of brief IDs). If `related` is present and non-empty:

```bash
worktrail-work-queue list --json
```

For each ID in the claimed brief's `related` list, find the matching brief in the `list --json`
output — match by `id` field, filename, or stem (filename without `.md` extension, with or
without the timestamp prefix). For each ID that resolves, collect its `focus`. If at least one
resolves, output:

```
Related briefs (same surface — read before starting):
  • <id> — <focus>
```

one bullet per resolved neighbour. Stale IDs (those that no longer resolve to any brief in
`queue/` or `picked/`) are skipped silently — no error, no message. If `related` is absent or
empty, or all IDs are stale, omit this section entirely — no stub or placeholder.

Then proceed to work it, or invoke the first suggested skill.

**Step 4 — On completion.** When the work described in the document is done:

```bash
worktrail-work-queue done <id-or-filename> --planning-only --json
# or, after inline Route-D implementation:
worktrail-work-queue done <id-or-filename> --implementation-complete --json
```

Route-C briefs reject an unqualified `done`: the result is
`awaiting_implementation_decision` and the brief remains picked. Use
`--planning-only` only when the user explicitly stops after planning, or
`--implementation-complete` after inline implementation has completed.
This stamps `status: done` in `picked/` (the file stays as a kept log). Tell the user the
handoff is complete and check if there are more items in the queue. If a claim is abandoned,
`worktrail-work-queue release <id>` returns it to `queue/` for someone else.

### Document format

Write the brief per `references/handoff-template.md`, which holds the field rules and a
complete filled-out example. In short: frontmatter fields are literal (`repo`/`remote` are
`null` when not in a repo), `status:` starts at `queued`, reference commits/PRs/paths instead
of reproducing them, mark each open item by type, and keep the brief under ~150 lines.

## Examples

**Create (mid-session capture).** While implementing a feature you notice the auth middleware
swallows errors. You run `/handoff new auth middleware swallows errors`. The skill writes
`~/work-queue/queue/20260531-141200-auth-middleware-error-handling.md` with the focus,
discovery context, a suggested approach, and `suggested-skills: [devkit.fix-debugging]`. You
report the path and keep working the original task.

**Consume (next session).** A fresh session runs `/handoff consume`. The skill runs
`work_queue.py list --json`, finds the auth item, confirms "Pick up
`...auth-middleware-error-handling.md`?", then `work_queue.py claim ...` atomically moves it to
`picked/` and stamps `status: picked` (so a concurrent session now gets `already-claimed`),
summarizes the focus, and proceeds — invoking `devkit.fix-debugging` first. On completion it
runs `work_queue.py done ...`.

## Best Practices and Constraints

- **Redact secrets and PII** (API keys, passwords, tokens) before writing any brief.
- The queue is personal and local — never commit it *into a project repo*. This skill writes
  only under `$WORK_QUEUE_DIR` and never modifies the working repo.
- **Optional offsite backup (single-machine):** `$WORK_QUEUE_DIR` may itself be a standalone,
  **private** git repo. Set `WORK_QUEUE_GIT_SYNC=1` and `work_queue.py` will commit+push after
  each `claim`/`done`/`release`/`link` (best-effort: push-only, errors swallowed, never blocks a
  mutation). It only ever pushes — never pull on a live queue, as that races the atomic claim
  rename. Newly captured briefs (`new`) are written directly, not via the script, so pair this
  with a low-frequency `git push` cron to catch them. Default (flag unset) is the original
  no-git behavior.
- **Always claim/release/complete via `work_queue.py`** — never hand-`mv` a brief. The atomic
  rename is the entire concurrency guarantee.
- Prefer references (paths, PR/issue URLs) over reproducing content the brief would duplicate.
- Tailor "Suggested approach" and "Suggested skills" to the specific focus, not generic advice.
- Handoff is **issue capture, not session termination** — the current session continues.
- A proactive session-end suggestion creates a brief only when it clears the exceptional-value
  gate above; producing no brief is a valid and often preferable outcome.
- `done` briefs stay in `picked/` as a kept log (never auto-deleted). An abandoned claim can
  be `release`d back to `queue/` — the queue is advisory, not enforced.

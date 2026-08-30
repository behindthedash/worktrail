# Shared Dispatch & Lifecycle Procedures (go + sdd-workflow)

Owned by the **go** skill and cited by `worktrail-sdd-workflow`'s SKILL.md and
`pipeline-details.md` via `{#anchors}`. Both ship from this repo now, so
`test_cross_skill_anchor_citations_resolve` fails the build on a broken anchor —
renames are safe when the citing side moves in the same change.
Contains prompt templates, failure handling, and worktree procedures kept out
of SKILL.md to keep the operating procedure lean. Load only the sections the
selected route needs, not the whole file. Artifact rules: `artifact-policy.md`.

---

## Subprocess dispatch {#subprocess-dispatch}

The go skill implements a subprocess-first dispatch architecture to isolate sdd-workflow's context
load from the go conductor session. This section documents the contracts and assumptions.

The Go front door resolves an **invocation context** before any dispatch: the agent CLI
is resolved once per invocation and carried explicitly through the seed prompt and all
downstream boundaries. Provider precedence is explicit invocation context (seed `Agent CLI:`),
repository policy (`agent_cli`), machine-wide `GO_AGENT_CLI`/`ORCH_AGENT`, detected parent
harness, then `claude`. OpenCode parent detection uses the explicit `OPENCODE_PARENT` marker
supplied by the harness; it never uses process-name heuristics. Codex host detection
(`CODEX_CI` or `CODEX_THREAD_ID`) stays in-session and does not spawn a headless worker.
The seed prompt carries `Agent CLI: <agent>` as the durable invocation identity
so sdd-workflow and the parallel orchestrator consume the resolved agent without
re-detecting the host or consulting mutable environment variables.

### Native Skill capability adapter

`Skill(...)` is a host-provided tool and is not available in every embedded session.
When it is absent, `worktrail-skill-dispatch` is the supported adapter. It accepts
the resolved provider, skill name, and argument string, and executes an argv list
for that same provider. Claude and OpenCode receive their slash-style skill prompt;
Codex receives an explicit installed-skill instruction. It never silently switches
providers. Internal `worktrail-sdd-workflow` dispatch receives an explicit
`[WORKTRAIL INTERNAL DISPATCH]` entry marker so the child executes the already-selected
route instead of re-entering this front door. The adapter also carries a one-hop
dispatch depth in the child environment and fails with
`blocked_internal_dispatch_recursion` if that child attempts to dispatch the internal
executor again. The adapter automatically gives a Codex child a private persistent
`~/.worktrail/codex-home` when the parent `CODEX_HOME` is read-only, and links the
installed Worktrail skill tree into that child home without copying credentials.
`WORKTRAIL_CODEX_HOME` or `--codex-home <path>` remains available for an explicit
child-home choice and is fail-closed when that path is not writable.
Authentication inheritance is the default for a trusted local Codex child. Pass
`--no-inherit-codex-auth` only for an intentionally isolated child.
The adapter verifies `codex login status`, requires a private regular file-backed
`auth.json`, copies it atomically with mode `0600`, and generates a minimal child
`config.toml` that selects file credential storage. It does not copy the parent's
general configuration. Missing,
non-ChatGPT, symlinked, or insecure authentication fails closed as
`blocked_external_dependency` without printing credential contents.
For a Codex child running sdd-workflow, also pass repeatable `--add-dir` values
for the policy's run-record directory and `${REPO}-worktrees`, because
`workspace-write` otherwise only covers the child `--cwd`. Keep those roots
narrow; never add the whole home directory.

**Pending-decision boundary (`--present-decision` / `--resume-decision`).**
The same adapter is the go-side boundary of the versioned
`worktrail.pending-decision` contract (`decision-queue.md#decision-envelope`),
and it is deliberately one command surface for every provider, so a decision's
lifecycle stays auditable across dispatch modes:

```bash
# Attended presentation: print the record's provider-neutral envelope JSON
# (any status, including open), stamp [presented] onto $RUN, spawn nothing.
worktrail-skill-dispatch --present-decision "$DECISION_ID" --run "$RUN"

# Exact-ID resume: launch the child ONLY when this exact record is answered
# and live; the id travels into the invocation verbatim as a
# decision:<decision-id> token the executor consumes once.
worktrail-skill-dispatch \
  --agent "$INVOCATION_CONTEXT_AGENT" --skill worktrail-sdd-workflow \
  --args "$SEED" --cwd "$REPO" --write \
  --resume-decision "$DECISION_ID"
```

An open, superseded, or unknown id fails closed here — exit 2, nothing
spawned; a known-but-unresumable record's envelope is printed on stdout so an
unattended caller receives the structured pending result unchanged. Never
re-derive, normalize, or prefix-match the id: a partial id names a different
record and is refused instead of silently resumed. Lifecycle procedure:
`decision-queue.md#decision-audit`.

### `go_seed.py` CLI contract

The `go_seed.py` helper generates a seed prompt for subprocess dispatch. Located at
`skills/worktrail-go/worktrail-go-seed`.

**Required arguments:**
- `--repo <absolute-path>` — resolved target repository path
- `--base <branch-name>` — resolved base branch (e.g., `main`, `dev`)
- `--route <A-J>` — route letter from go Phase 5 classification
- `--spec <spec-id>` — spec folder name (e.g., `016-go-subprocess-dispatch`)
- `--run <absolute-path>` — absolute path to the parent run record file
- `--agent <claude|codex|opencode>` — worker provider label used by downstream SDD workers;
  `codex` means the parent stayed in-session and the seed is only used when a subprocess
  worker is allowed

**Optional arguments:**
- `--brief <absolute-path>` — absolute path to a handoff brief file (omitted when not seeding from a brief)

**Output:** A text string (stdout) containing the seed prompt. The prompt includes resolved repo,
base, route, spec_id, run_record_path, agent_cli, and optional brief_path. The seed does NOT include go SKILL.md
content, dashboard JSON, policy JSON, classify JSON, or any go-phase intermediate state. It is
self-contained and allows the subprocess to initialize sdd-workflow from the resolved context alone.

**Exit behavior:** Exits 0 on success; exits non-zero when any required argument is
missing or a path argument is not absolute. The script writes nothing — the seed
prompt goes to stdout only.

### Seeded-dispatch entry point in sdd-workflow Phase 1 {#seeded-dispatch}

When sdd-workflow receives a seed prompt (from `go subprocess dispatch`), it detects seeded-dispatch
mode and enters at Phase 1 with resolved parameters. The subprocess skips dashboard, picker, repo
resolution, policy load, and classification — sdd-workflow Phase 1 reads `$REPO`, `$BASE`, `$ROUTE`,
`$SPEC_ID`, `$RUN` from the seed and proceeds directly to route execution at Phase 6
(run record attachment). Codex-host runs do not enter this mode; they stay in-session and call
`Skill("worktrail-sdd-workflow", ...)` directly instead.

**Seeded-dispatch detection:** sdd-workflow Phase 1 checks for the presence of these environment
variables/fields from the seed prompt: `ROUTE`, `SPEC_ID`, `REPO`, `BASE`, `RUN`, and optional `AGENT_CLI`.
If all are present, enter seeded-dispatch mode and skip classification. If the current host is Codex,
do not construct this seed path at all.

**Handoff brief seeding:** If the seed includes `--brief <path>`, the seed prompt also sets `BRIEF_PATH`.
The subprocess attaches to the parent run record as usual but does NOT call `work_queue.py claim` again
(the parent go session already claimed it).

### Interactive routes stay in-session

Routes with mid-flow ask sites (C; Route I is grouped here for its decision
points even though its playbook currently defines no `AskUserQuestion` call)
use the in-session `Skill("worktrail-sdd-workflow", args="...")` call, never a
subprocess — a headless worker cannot surface interactive prompts to the parent
session. **In-session does not mean interactive.** When the parent is itself a
headless one-shot (a `worktrail-go drain`/`auto` spawn), there is no human at
any layer and `AskUserQuestion` is not a registered tool in the process at all
(verified 2026-08-10 via a direct `claude -p` probe — see auto-mode.md Phase
5.5). The in-session call still applies — spawning another layer cannot recover
interactivity — but every ask site reachable from route execution must take its
documented `$AUTO_MODE=true` branch: a safe documented default where one
exists, otherwise file a decision record and finish the run
`blocked_product_decision` (`decision-queue.md#file-a-decision`). Per-site
index: `#auto-mode-ask-fallbacks`.
Subprocess dispatch applies to the background routes (D/F/G/H) with the bounded
poll below only for an attended parent session.

**Unattended terminal ownership.** A drain/cron/CI one-shot (its prompt says it
is unattended and must remain foreground until `final_status`) must not use the
background-plus-bounded-poll path below. Execute D/F/G/H in the owning process
via the native Skill capability, or invoke `worktrail-skill-dispatch` as a
blocking foreground command and wait for its exit. The one-shot may return only
after the shared run record contains a real `final_status`. Pending PR checks,
an active CI watch, or an unfinished review-thread pass are not ownership
transfers and are not terminal states. This rule is provider-independent and
applies equally to Claude Code, Codex, and OpenCode.

**Active-run resume (Route E) also stays in-session.** A Route E continue/resume whose run
is **already active** (run record exists with no `final_status` AND its `worktree` path
already exists on disk) is owned by the parent already — the front door must hand execution
back to the active parent by continuing the route in this session via the direct
`Skill("worktrail-sdd-workflow", args="<repo-path> route:E [spec-folder]")` call, never by
spawning a nested worker. A nested worker re-entering the same run/worktree/provider would
self-poll and duplicate or stall execution (Datalena run go-20260811-132806). This is
distinct from the background routes: their poll applies to a freshly spawned run, whereas an
active-run resume is not fresh and must not start its own poll loop. Headless
`drain` one-shots are never an active-run resume and keep spawning normally.

### Bounded poll contract (background routes D/F/G/H) {#bounded-poll-contract}

In an attended parent session, after spawning a background subprocess (routes D, F, G, H), go Phase 7 polls the shared run record
for completion using `poll_run.py`. The poll MUST respect a hard 10-minute ceiling to prevent
unbounded waits.

**Poll parameters:**
- **Interval:** 30 seconds between checks
- **Max iterations:** 20 (10 minutes ÷ 30 seconds)
- **Exit condition:** The run record contains a `finish` entry at any completion state (paths: `$RUN/finish`)

**Exit behavior:**
- **Poll exit 0 (subprocess complete within ceiling):** The subprocess finished; run record contains
  `finish` entry with completion state and optional PR URL. Before printing a completion summary and
  exiting, run `#post-delegation-verification` against the run record's `worktree` — a `finish` entry
  is a self-report, not proof.
- **Poll exit 0 with pending decisions:** On completion the poll also reads the record's
  `pending_decisions` audit list; any decision id whose last lifecycle event is neither `consumed`
  nor `superseded` is printed as its own `pending_user_decision: <id>` line. That is a first-class,
  fail-closed, recoverable handoff — the subprocess yielded ownership instead of guessing — never a
  generic failure: present the record (`worktrail-skill-dispatch --present-decision <id>`), have a
  human answer it (`worktrail-decision answer <id> --answer "..."`), and resume through the exact id
  (`worktrail-skill-dispatch --resume-decision <id>`). Full lifecycle:
  `decision-queue.md#decision-audit`.
- **Poll exit non-0 (ceiling reached):** No `finish` entry after 20 iterations. go prints
  "Subprocess still running — check run record at $RUN" and exits cleanly (non-error) so the user
  can check the run record later or re-invoke to resume polling.

**Run record structure:** The poll reads the run record JSON file at `$RUN` and checks for a `finish` key
or `final_status` key (both indicate completion). The subprocess and parent go session share the same
run record file; the subprocess writes `finish` when it completes (via `run_record.py finish --status <state>`).

## Post-delegation completion verification (mandatory) {#post-delegation-verification}

Any implementation work handed to a subagent — a background subprocess worker (`#subprocess-dispatch`,
polled via `#bounded-poll-contract`) for routes D/F/G/H, or an ad hoc `Agent(subagent_type: "fork", ...)`
call made directly by the conductor session to implement a narrow fix in-session — self-reports its own
completion. A `finish` entry and a fork's own `<task-notification>` summary are each generated by the
delegate describing what it believes it did; neither is proof the worktree actually changed. Live incident
(2026-08-20, brief `20260820-214356`): the same fork, given a detailed multi-file implementation directive, reported
`completed` with a plausible-looking notification summary **twice in a row** while its target worktree
showed a clean `git status` and zero commits — it had done nothing. The failure was caught only because
the unusually short duration/tool-use count prompted a manual `git status` check instead of trusting
the notification.

**Before treating any implementation delegation as done — before proceeding to commit, integrate, PR,
or marking the run/task complete — verify the expected target directly:**

```bash
git -C "<expected-worktree-or-branch-checkout>" status --porcelain
```

Use `status --porcelain`, not `diff --quiet HEAD` — a brand-new untracked file never appears in a diff
against `HEAD` (`#sync-before-teardown` already establishes this for the sync worktree; the same gap
applies here). For a task worker whose changes land on a branch rather than a live worktree, diff that
branch against its base instead (`git -C "$REPO" diff --stat "$BASE"..."<task-branch>"`).

- **Non-empty output / non-empty diff** — the delegate produced something. Proceed with normal
  validation (tests, drift gate, review) as the route already requires.
- **Empty output where changes were expected** — treat the reported completion as unverified, not
  done. Retry once with an explicit, more directive prompt naming the exact files/changes expected
  (a vague original directive is the most common cause of a genuine no-op, not just a broken delegate).
  If the retry also produces an empty diff, escalate: stop, report the delegate's self-reported summary
  alongside the verified-empty worktree state, and do not mark the run/task `completed_*`. Never accept
  a clean diff silently — a clean worktree paired with a "completed" report is itself the finding.

This check is cheap (one `git status` call) relative to the cost of a silent no-op reaching
`completed_and_merged` with nothing implemented, so it applies unconditionally — not only when a
delegate's duration or tool-use count looks suspicious. A human noticing the notification "looked
short" is not a substitute for this check; it is what caught the gap in the incident above, not a
reliable detection mechanism to depend on going forward.

### A fork's tool access is not scoped by its prompt {#fork-tool-access}

A prose instruction to an `Agent(subagent_type: "fork", ...)` dispatch — "investigate only, do
NOT edit files, do NOT run git mutations, do NOT touch the work-queue files" — is advisory only.
The Agent tool applies no per-fork tool restriction: a fork carries the parent's full tool
surface, so nothing at the tool layer stops it from calling `worktrail-work-queue done`/`release`
(or any other mutating CLI on `PATH`) regardless of what its dispatch prompt says. Live incident:
a `worktrail-go` dispatch of brief `20260821-105101` fanned out 6 parallel read-only-instructed
investigation forks; two ignored the prose constraint and autonomously ran real `done`/`release`
mutations on ~30 briefs, racing the dispatching session's own independent reconciliation of the
same items (one `done` call hit a live `FileNotFoundError` from a fork mutating `picked/` at the
same instant). Outcomes were cross-verified as accurate in that incident, but the mechanism is
unsafe by design, not merely unlucky.

Two consequences for a front-door dispatch fanning investigation work out across forks:

- Do not assume the queue is untouched when investigation forks return — check it (`git status`
  equivalent for the queue: re-`list` and diff against what you expect, or re-`resolve` the
  specific briefs you still intend to act on) before running your own `done`/`release` calls
  against the same items, rather than trusting the forks stayed read-only.
- `worktrail-work-queue done`/`release` accept an optional `--by`/`--force` ownership check
  (mirroring `claim`'s `--by`/`same_owner`) that rejects a mismatched caller outright instead of
  silently succeeding — pass `--by` on every `done`/`release` call this dispatch makes so a
  differently-identified fork's call against the same brief is refused at the tool layer instead
  of relying on the prompt holding.

### Auto-mode classifier interaction {#automode-classifier}

Under Claude Code's own Auto Mode Bash-command safety classifier, the go-level top-of-dispatch
`claude -p "$SEED" --permission-mode bypassPermissions ...` spawn (the literal `Bash` tool call
issued directly by the go conductor session) can be **denied**: "[Create Unsafe Agents] ... the
user never explicitly named this specific sandbox/approval bypass for this dispatch." This is
expected under Auto Mode, not a defect to chase — treat the denial as an immediate,
silent-by-design trigger for the existing SKILL.md Phase 7 fallback ("If subprocess dispatch is
unavailable: fall back to the direct Skill call and say so"). Log it once for visibility; do not
retry the same spawn, and do not surface it as an error requiring investigation. The tradeoff this
accepts: an Auto Mode session pays the token/latency cost of running the full sdd-workflow pipeline
in the parent conductor's own context instead of getting the intended context-isolation benefit of
a detached subprocess — same outcome, more expensive path.

**Inference, not live-verified this pass:** the orchestrator's own per-task worker spawns
(`worktrail.orchestrator.spawnlib` `PERM_FLAGS`) are not expected to hit this same
classifier gate, because those `claude -p --permission-mode bypassPermissions` invocations happen
via `subprocess.Popen` from Python code already running inside a backgrounded orchestrator process
— not as a fresh top-level `Bash` tool call the interactive session's classifier evaluates. Once the
orchestrator itself is launched (a single `Bash`/background call to `orchestrate.py`/`live.py`), its
internal child-process spawns are invisible to that classifier. Supporting evidence (not a repro of
this exact path): `docs/specs/research/orchestrator-review-report-back-diagnostics.md` independently
hit the identical classifier denial when a `spawn-one` diagnostic CLI invocation was issued directly
via the `Bash` tool from an interactive session — consistent with the gate keying on direct top-level
`Bash` tool calls rather than nested subprocess spawns. Treat this as an open question if it
resurfaces in a different shape.

**Live-verified 2026-08-09 — the gate does NOT key on `--permission-mode bypassPermissions`.**
A narrower permission profile was previously floated here as an alternative fix that could restore
subprocess isolation under Auto Mode. It does not: three consecutive top-level `Bash` tool calls
issuing `claude -p "/worktrail:opsx:propose …" --setting-sources user,project,local
--permission-mode acceptEdits` from an interactive session were **allowed, allowed, then denied**
with the same classifier message — no bypass flag present on any of them. So the trigger is the
*shape* of the call (a fresh top-level `Bash` tool call spawning a nested agent), not the
permission flag it carries, and it fires **non-deterministically** on identical call shapes.
Two consequences: (1) swapping `bypassPermissions` for `acceptEdits`/`--allowedTools` is not a
workaround and should not be attempted as one; (2) any dispatch path that spawns a nested `claude
-p` from a top-level `Bash` call must keep a fallback, because the spawn can fail on any given
attempt regardless of flags. This finding *strengthens* the nesting inference above (the shape,
not the flag, is what the classifier evaluates) but does **not** verify it — the orchestrator's
own `subprocess.Popen` spawns still have no live repro either way.

---

## Auto-mode ask fallbacks (route execution) {#auto-mode-ask-fallbacks}

Phase 5.5's three pre-dispatch asks carry their own `$AUTO_MODE=true` branches
(auto-mode.md Phase 5.5, PR #290). This section is the same contract for ask
sites *inside* route execution (sdd-workflow Phase 7), where the run record
already exists — so a blocked site finishes `$RUN` directly instead of opening
a minimal one:

```bash
worktrail-run-record finish "$RUN" --status blocked_product_decision --merge-result \
  "<one-line summary of the decision that needed a human>"
```

Before that `finish`, file the question itself as a decision record and release
the brief per `decision-queue.md#file-a-decision` (guardrails:
`decision-queue.md#decision-filing-guardrails`) — the human answers
asynchronously and the next drain pass resumes from the blocked point. Do not
call `work_queue.py done`, and do not release the brief by hand — the
`worktrail-decision ask --brief ... --release` path is what stamps
`awaiting-decision` so the brief stays blocked until answered. Only if filing
itself fails, fall back to leaving the brief claimed in `picked/` for the
stalled-in-flight resume path, exactly as the Phase 5.5 fallbacks do. Sites
with a safe documented default take it silently and record it
(`worktrail-run-record append "$RUN" decisions "..."`) instead of blocking.
Never guess an answer to a product question, and never attempt the
`AskUserQuestion` call — the tool is absent, not merely unanswered.

Per-site branches (`$AUTO_MODE=true`):

- `#overlap-menu` (Route C, and Route D with no spec): no ask — which existing
  spec owns a capability is a product call; finish `blocked_product_decision`.
- Slug confirms (`#spec-worktree-setup`, `#fix-branch-worktree-setup`): safe
  default — derive the slug from the brief focus / request text; never ask.
- `#stage-result-handling`: `failure` → retry once (existing counter), then
  finish with a failure state — no retry/skip/abort offer; `needs-clarification`
  → finish `blocked_product_decision` quoting MARKERS + SUMMARY.
- `#precheck-gate`: no ask — never flip task statuses or proceed-anyway
  unattended; finish `blocked_product_decision` quoting the precheck output.
- Route C implementation-intent question (routes.md §C): safe default — take
  the planning-only stop (`planned_ready_for_implementation`); the brief stays
  claimed, never marked done without an explicit completion mode.
- Route A decision point (routes.md §A): safe default — stop at the discovery
  note (`investigation_complete`); the brief stays claimed for the human
  decision (`no_implementation_without_approval` binds absolutely unattended).
- Route D spec pick (`pipeline-details.md#implement-pipeline`): use the brief's
  `target-spec`/`$ARG_SPEC`; still ambiguous → finish `blocked_product_decision`.
- `#openspec-propose`: the authoring child is headless for every caller — pass
  the request verbatim so its own clarification asks never fire; if it stalls
  or writes nothing, treat as `needs-clarification` above.
- `#handoff-seed` step 2b/2c pickers and claim `none`/`ambiguous`: unreachable
  in auto mode by construction (go claims the brief itself and dispatches
  `handoff:<id> route:<X>`); if hit anyway, stop and report — never ask.
- `epic-collision-check.md` (Route B): an unambiguous match (exactly one
  citing spec covers the request) has no ask in either dispatch mode — it
  redirects the route silently. Only a genuinely ambiguous match reaches this
  fallback: no ask — file `blocked_product_decision` per
  `decision-queue.md#file-a-decision`, quoting the matched epic id and its
  citing specs.

Route I defines no ask sites in its playbook — nothing to branch there.

## Repo resolution {#repo-resolution}

Run:
```bash
worktrail-resolve-repo --start "$PWD" --hint "<user request text>" --json
```

Act on `mode`:
- **in-repo** → use `repo` as `$REPO`
- **derived** → use `repo`, state the pick ("Using `<name>`")
- **single-candidate** → use it, state the pick
- **ambiguous** or **none** → run the multi-repo overview: `worktrail-dashboard --repos "$PWD"`. It lists every candidate with active-spec count. Present via `AskUserQuestion` — lead with repos that have active specs, labelled with them (e.g. "gracefully-giving-back — 1 active: 003-payments"). If no git repos found, ask for the path and stop. (`$AUTO_MODE=true` never reaches this ask — auto_pick already skips briefs whose repo is missing on this machine; if reached anyway, stop and report rather than asking.)

Pull the base branch once `$REPO` is known:
```bash
git -C "$REPO" fetch --prune
BASE="$(git -C "$REPO" remote show origin | sed -n 's/.*HEAD branch: //p')"   # main or dev
git -C "$REPO" checkout "$BASE" && git -C "$REPO" pull --ff-only origin "$BASE"
```

Never develop on the base checkout; all authoring and implementation use worktrees.

---

## Overlap Check {#overlap-check}

Run before spec worktree setup in the `new` pipeline. Detects when a feature idea substantially
overlaps an existing spec so the user can extend rather than duplicate.

Run once per spec root that exists under `$REPO` — `$REPO/docs/specs` and/or `$REPO/openspec` —
and merge the resulting `specs` arrays before the comparison step:

```bash
[ -d "$REPO/docs/specs" ] && worktrail-overlap-check --root "$REPO/docs/specs" --json
[ -d "$REPO/openspec" ] && worktrail-overlap-check --root "$REPO/openspec" --json
```

Parse each JSON `specs` array and merge them into one list. Each entry has `spec_id`, `stage`,
`title`, `feature_summary`, and `user_request_excerpt`.

**Comparison rule:** Compare `$feature_idea` (the user's stated feature) against every
`feature_summary` (and `user_request_excerpt` as a tiebreaker). An overlap exists when the
core capability — the actor + capability + primary domain — is the same or is a clear
sub-set/extension of an existing spec.

**Task-level candidates, when a target is known:** `overlap_check.task_candidates(specs_root,
target)` can additionally enumerate one `{task_id, task_text, checked}` entry per open,
unchecked task in a specific OpenSpec change's `tasks.md`, kept separate from the whole-spec
`specs` index above — but this step has no known `target` change to scope against (it runs
*before* a spec exists, deciding whether to create one at all), so it always calls the
whole-spec/whole-change path (`scan()`, unchanged). The scoped, target-aware form is exercised
downstream, once a request is already headed at a specific change: `check_spec_collision.py`'s
Phase 5.5 dispatch-time guard (`references/spec-collision-check.md`, "Task-level matches:
redirect, never auto-close") passes an explicit `target` — a claimed brief's `target-spec:`
field — through to this same function so a Route C/D/F/G dispatch can be redirected onto an
already-open, matching task instead of duplicating it.

**If overlap is found:** Present `AskUserQuestion` before continuing (see `#overlap-menu`).
`$AUTO_MODE=true`: no ask — finish `blocked_product_decision` per
`#auto-mode-ask-fallbacks` naming the overlapping `{spec_id}`; do not proceed to step 0.
**If no overlap or no existing specs:** Proceed silently to step 0.
**If neither `docs/specs/` nor `openspec/` exists:** Skip this step entirely.

---

## Overlap menu {#overlap-menu}

```
AskUserQuestion(
  questions=[{
    question: "Your feature idea overlaps with existing spec `{spec_id}` — \"{title}\".\n{feature_summary}\n\nHow should we proceed?",
    header: "Overlap found",
    options: [
      {
        label: "Extend existing spec `{spec_id}`",
        description: "Route to the continue/implement pipeline for that spec. No new spec created."
      },
      {
        label: "Create a related new spec",
        description: "Start a new spec that explicitly references and builds on `{spec_id}`. Brainstorm receives both as context."
      },
      {
        label: "Proceed as a new unrelated spec",
        description: "Treat as a completely new feature. A new spec will be created."
      }
    ]
  }]
)
```

This menu is interactive-only — under `$AUTO_MODE=true` the overlap branch above
never presents it (see `#auto-mode-ask-fallbacks`).

**After selection:**
- **Extend** → route to `implement` or `continue` pipeline for `{spec_id}`. Do NOT create a new spec.
- **Related** → proceed with `new` pipeline; pass `related_spec: {spec_id}` as extra context to the brainstorm Agent dispatch so it reads the existing spec's `Feature Summary` and `Acceptance Criteria` before drafting the new one.
- **New** → proceed with `new` pipeline normally.

---

## OpenSpec authoring {#openspec-authoring}

Replaces the former `#brainstorm-template`, `#spec-check-template`, and
`#spec-to-tasks-template`. Those three existed because devkit's authoring pipeline
had three separate skills producing three artifacts. OpenSpec's `propose` generates
**proposal, specs, design, and tasks in one step**, so the three-stage dispatch, its
three prompt templates, and the stage-to-stage handoff between them all collapse into
one command. Anchors are kept out of this file's public contract only where nothing
cites them — see the rename note at the end.

**Do not use `/opsx:apply`.** It is OpenSpec's own sequential per-change executor;
worktrail replaces it. The orchestrator fans tasks out across worktrees and hands
back to `/opsx:archive` at the end. Verified against OpenSpec 1.6.0: `archive`
accepts checkboxes written by anything, and reports `Task status: ✓ Complete` from
ticks this system wrote.

### Authoring a change {#openspec-propose}

**Resumability pre-check (mandatory, before every dispatch below).** `run_in_background`
(next paragraph) only removes the foreground-timeout trigger — an OOM kill, a host
disconnect, or a spawn that legitimately outlives even a generous background execution
still kills the child mid-generation. The child's `Write`/`Edit` calls flush to disk
immediately, so `openspec/changes/<change-id>/` on `$WT` retains whatever it finished
before dying; the risk is re-dispatching a fresh `/opsx:propose` for that same
change-id, which hits OpenSpec's own change-name-collision guardrail
(`openspec new change` refuses an existing name — `#openspec-nnn-prefix` above) instead
of continuing the work. Run:

```bash
worktrail-check-openspec-propose-resume --worktree "$WT" --change-id "$SPEC_ID" --json
```

`checked: false` (worktree unreachable) — treat as unknown, do not skip the check
silently; surface the warning and stop rather than guessing. `resumable: false` —
`$SPEC_ID`'s change directory is untouched or absent; dispatch `/opsx:propose` as below.
`resumable: true` — a prior spawn already wrote `present` artifacts (and/or delta specs
under `specs/`); dispatch `/opsx:update "$SPEC_ID"` instead (`#openspec-explore`,
"revise the existing artifacts, keeps them coherent"), directing it to complete
`missing` — never re-dispatch `/opsx:propose` for a `resumable: true` change-id.

Spawn it headlessly against the change worktree — do **not** relocate the calling
session into `$WT` to run it:

```bash
worktrail-skill-dispatch \
  --agent "$INVOCATION_CONTEXT_AGENT" \
  --skill "opsx:propose" \
  --args "<the request, verbatim from the brief or the user>" \
  --cwd "$WT" \
  --write
```

`--cwd` targets the worktree without moving the session; `--write` grants the
child the permissions it needs to author files with no human at the keyboard.
`worktrail-skill-dispatch` preserves the requested provider, so this one call
covers Claude Code, Codex, and OpenCode rather than branching per host.

**Expect several minutes, not seconds.** This spawn routinely takes multiple
minutes to author the full proposal/delta-specs/design/tasks set — invoke it
via `run_in_background` (or an explicitly extended timeout), never a
default-timeout (2min) foreground `Bash` call. A foreground call with the
default timeout killed a child mid-generation on 2026-08-15, discarding
partial work; the resumability pre-check above is what now lets a later
re-dispatch continue from that partial state instead of colliding on
`/opsx:propose`'s own change-name guardrail.

**Why not run it inline.** The former procedure moved the session with
`EnterWorktree({path: "$WT"})`. That can never run unattended: `EnterWorktree`
returns `behavior: 'ask'` unconditionally for any path outside
`<repoRoot>/.claude/worktrees/`, with `classifierApprovable: false` and no rule
consultation — so no `permissions.allow` entry suppresses it, and the managed
root is hardcoded with no setting to widen it. Every worktrail `$WT` is
`$REPO-worktrees/…`, so it always tripped the prompt. Relocating also pinned the
session: `EnterWorktree`'s isolation guard blocked later `git -C <other-path>`
calls against `$REPO`, breaking steps like `#sync-before-teardown`.

**The child is headless for every caller — its own asks can never fire.** The
bundled `openspec-propose` skill asks via `AskUserQuestion` only when it gets no
clear input or hits unclear artifact context; passing the request verbatim (as
required above) removes the first, and the tool is absent inside the spawned
child regardless of `$AUTO_MODE`, so a genuinely human-needing gap surfaces as a
stall or missing artifacts, caught by the verification step below. Interactive
parents then handle it per `#stage-result-handling`'s clarification path;
`$AUTO_MODE=true` finishes `blocked_product_decision` per
`#auto-mode-ask-fallbacks`.

**Verify the artifacts — a zero exit code is not proof.** Failure modes
observed live both **exit 0 and write nothing**: a claude spawn carrying the
`--setting-sources project,local` default that
`spawnlib._with_default_setting_sources` injects never loads the worktrail
plugin (2026-08-09), and — before `worktrail-skill-dispatch` namespaced
`opsx:*` commands itself (2026-08-24) — a bare `/opsx:propose` resolving to
`Unknown command` for claude/opencode. Always assert
`openspec/changes/<change-id>/` exists before continuing — never infer
success from the return code alone.

**If the spawn is refused, fall back to running it inline** in the calling
session (accepting the token cost) and say so, per SKILL.md Phase 7. A top-level
`Bash` tool call that spawns a nested agent can be denied non-deterministically
by Claude Code's Auto Mode classifier — see `#automode-classifier`, and note that
the denial is not tied to any particular permission flag, so retrying with a
narrower one is not a workaround.

It creates `openspec/changes/<change-id>/` containing `proposal.md`, `specs/**/*.md`
(delta specs using ADDED/MODIFIED/REMOVED headers), `design.md`, and `tasks.md`.
Commit the whole change directory before the orchestrator forks any task worktree —
the commit-discipline rule is unchanged and still load-bearing, only the path moved
from `docs/specs/<id>/changes/<slug>/` to `openspec/changes/<id>/`.

**There is no separate new-vs-modify authoring path.** An OpenSpec change is always a
delta against the current specs: new capabilities land as `## ADDED Requirements`,
modifications as `## MODIFIED Requirements`. Routes C/D and F/G therefore share this
one authoring step and differ only in what they do afterwards.

### When the request is ambiguous {#openspec-explore}

If the seed is sparse, or a `[NEEDS CLARIFICATION]`-shaped gap would previously have
sent it to spec-check, run explore mode first and fold the answers into the proposal:

```
/opsx:explore "<the ambiguity>"
/opsx:update <change-id>      # revise the existing artifacts, keeps them coherent
```

`/opsx:update` never edits code — it is the artifact-revision path, so it is safe to
run against a change that already exists without touching the implementation.

### Validating before the orchestrator runs {#openspec-validate}

```
openspec validate <change-id>          # structural: required artifacts, delta headers
openspec status --change <change-id> --json
```

`validate` catches the failure OpenSpec warns about in its own schema instructions:
scenarios must use exactly four hashtags (`####`), and a requirement without a
scenario fails **silently** in the authoring step but is caught here.

`status` reports **artifact** completion (does each file exist), not task completion —
do not read it as run progress. Task state lives in the run journal during a run.

### NNN-prefix change directories {#openspec-nnn-prefix}

Some repositories layer their own `NNN-slug` numbering convention on top of OpenSpec's
directory-per-change model, even though OpenSpec itself has no numeric-prefix convention
(`#spec-worktree-setup` above never sets `SPEC_ID` to an `NNN-`-prefixed value for the
`openspec` format). Verified against `@fission-ai/openspec`'s `validateChangeName`
(`change-utils.js`): it hard-rejects any `--change` name starting with a digit, on every
command (`new`, `status`, `instructions`, `validate`), not only creation. If the target
repo's convention requires the `NNN-` prefix anyway, do not rename the change directory
before this point: author every artifact — propose, explore/update, and this
validate/status step — under the plain kebab-case slug, then
`mv openspec/changes/<slug> openspec/changes/<NNN>-<slug>` only after `openspec validate`
passes. The orchestrator's `compile.py`/`live.py` never call the OpenSpec CLI again after
this point — they operate on the renamed path directly via glob — so renaming here is safe.

### Syncing specs {#openspec-sync}

```
/opsx:sync <change-id>
```

Merges the change's delta specs into `openspec/specs/`.
`/opsx:archive` also performs this merge as part of archiving, so an explicit sync is
only needed when specs must land before the change is finished. **Claude Code
hosts:** same enter-run-exit discipline as `#openspec-propose` above — this is also a
slash command, not a `-C`-scoped call.

### Archiving a change {#openspec-archive}

```
/opsx:archive <change-id>
```

The orchestrator hands back to this at the end of a change's run (see
`#openspec-authoring` above). Merges the change's delta specs into `openspec/specs/`
(same merge `/opsx:sync` performs alone) and moves `openspec/changes/<change-id>/`
into `openspec/changes/archive/`, prompting to confirm when artifacts or tasks are
incomplete rather than blocking outright. **Claude Code hosts:** same enter-run-exit
discipline as `#openspec-propose` above — this is also a slash command, not a
`-C`-scoped call.

## Stage result handling {#stage-result-handling}

After each delegated stage (brainstorm, spec-check, spec-to-tasks), parse the Stage
Result Summary for lines starting with `STATUS:`, `SPEC_PATH:`, `MARKERS:`, `SUMMARY:`.
Track `dispatch_attempt_count` per stage (starts at 1).

- **STATUS: success** → extract SPEC_PATH (brainstorm only) and SUMMARY; report one-line
  summary to user; advance to next stage.
- **STATUS: failure** → surface SUMMARY; offer retry / skip / abort. On retry, increment
  counter and re-dispatch with same inputs. On skip, continue. On abort, stop.
- **STATUS: needs-clarification** → display MARKERS count and SUMMARY; use `AskUserQuestion`
  inline to collect answers. Increment `dispatch_attempt_count`. If ≤ 2, re-dispatch with a
  `clarifications` field containing the user's answers. If = 3, escalate: "Stage cannot
  self-resolve after 2 re-dispatch attempts. Spec file is at [spec_file path]. You can
  manually edit it and retry, or abort." Offer: manually edit then retry, or abort.

`$AUTO_MODE=true`: no offers and no asks. `failure` → retry once (same counter), then
finish with a failure state quoting SUMMARY; `needs-clarification` → finish
`blocked_product_decision` quoting MARKERS + SUMMARY per `#auto-mode-ask-fallbacks` —
requirement ambiguity is a product call, never guessed unattended.

---

## Orchestrator invocation {#orchestrator}

One block for both pipelines, parameterized on `$SPEC_ROOT` (= `$WT` for the
`new` pipeline — spec not yet on base; = `$REPO` for `implement` — spec already
committed on base; same convention as `#orchestrator-gates`). On a Codex host
(the `CODEX_*` check) stay in-session via
`Skill("worktrail-sdd-workflow", args="<repo-path> route:<X> [spec-folder]")`
instead of the CLI call — `Skill(...)` is a tool invocation, not a shell command.

```bash
POLICY_AGENT_CLI=$(echo "$POLICY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('agent_cli') or '')")
POLICY_AGENT_MODEL=$(echo "$POLICY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('agent_model') or '')")
# AGENT_CLI is resolved per invocation, in order:
# 1. Explicit invocation context (from seed prompt / --agent flag) — highest
# 2. Repo policy agent_cli
# 3. Machine-wide env vars (GO_AGENT_CLI > ORCH_AGENT)
# 4. Detected parent host marker (OPENCODE_PARENT / CODEX_*)
# 5. Fallback (claude — compatibility only; invocation context always preferred)
if [ -n "${AGENT_CLI:-}" ]; then :; elif [ -n "$POLICY_AGENT_CLI" ]; then AGENT_CLI="$POLICY_AGENT_CLI"; elif [ -n "${GO_AGENT_CLI:-}" ]; then AGENT_CLI="$GO_AGENT_CLI"; elif [ -n "${ORCH_AGENT:-}" ]; then AGENT_CLI="$ORCH_AGENT"; elif [ -n "${OPENCODE_PARENT:-}" ]; then AGENT_CLI="opencode"; else AGENT_CLI="claude"; fi
if [ -n "${AGENT_MODEL:-}" ]; then :; elif [ -n "$POLICY_AGENT_MODEL" ]; then AGENT_MODEL="$POLICY_AGENT_MODEL"; else AGENT_MODEL=""; fi
# FALLBACK_CHAIN: an explicit env var override wins outright; otherwise derive
# the ordered chain from the resolved policy's routing.fallback (which already
# falls through repo-local routing -> the machine-wide ~/.worktrail/routing.yaml ->
# the flat fallback_agent_cli key -- see resolve_routing()/policy.py), minus
# AGENT_CLI itself (already tried first by live.py's own chain walk) and
# de-duplicated. Empty when nothing is configured anywhere, matching the old
# single-hop behavior's "no fallback" case exactly.
if [ -n "${FALLBACK_CHAIN:-}" ]; then :; elif [ -n "${FALLBACK_AGENT_CLI:-}" ]; then FALLBACK_CHAIN="$FALLBACK_AGENT_CLI"; else
  FALLBACK_CHAIN=$(echo "$POLICY" | AGENT_CLI="$AGENT_CLI" python3 -c "
import json, os, sys
policy = json.load(sys.stdin)
primary = os.environ.get('AGENT_CLI', '')
names = [e.get('agent_cli') for e in ((policy.get('routing') or {}).get('fallback') or [])
         if isinstance(e, dict) and e.get('agent_cli')]
if not names and policy.get('fallback_agent_cli'):
    names = [policy['fallback_agent_cli']]
seen, ordered = set(), []
for n in names:
    if n and n != primary and n not in seen:
        seen.add(n); ordered.append(n)
print(','.join(ordered))
")
fi
if [ -n "${ROLE_AGENT_MAP:-}" ]; then :; elif [ -n "${GO_ROLE_AGENT_MAP:-}" ]; then ROLE_AGENT_MAP="$GO_ROLE_AGENT_MAP"; elif [ -n "${ORCH_ROLE_AGENT_MAP:-}" ]; then ROLE_AGENT_MAP="$ORCH_ROLE_AGENT_MAP"; else ROLE_AGENT_MAP=""; fi
ROLE_AGENT_MAP_ARGS=()
[ -n "$ROLE_AGENT_MAP" ] && ROLE_AGENT_MAP_ARGS=(--role-agent-map "$ROLE_AGENT_MAP")
AGENT_MODEL_ARGS=(); [ -n "$AGENT_MODEL" ] && AGENT_MODEL_ARGS=(--model "$AGENT_MODEL")
FALLBACK_AGENT_ARGS=(); [ -n "$FALLBACK_CHAIN" ] && FALLBACK_AGENT_ARGS=(--fallback-chain "$FALLBACK_CHAIN")
PR_LABELS=$(worktrail-pre-pr-gate --repo "$SPEC_ROOT" --risk "$RISK_LEVEL" --gates "$GATES" --route "$ROUTE" --target-branch "$BASE" --labels-only)
PR_LABEL_ARGS=()
for label in $PR_LABELS; do PR_LABEL_ARGS+=(--pr-label "$label"); done
# An OpenSpec change's tasks.md never declares per-task file scope
# (OpenSpecTaskSource deliberately emits `files: []` rather than inventing one —
# inferring it is the conductor's job, not the adapter's). Since PR #56,
# apply_run_plan() (called inside full-real) auto-compiles via
# compile_run_plan() when no cache exists, so full-real no longer hard-fails
# immediately on a fresh OpenSpec change. Running worktrail-compile explicitly
# first is still worth it: the CLI exits 1 and names the exact scope-less task
# ids on a bad compile, while the inline auto-compile degrades silently to the
# baseline plan and only surfaces the gap later — deeper into the run, once
# full-real's validate_task_metadata() refuses to fan those tasks out with
# "implementation task(s) missing required frontmatter files: ...".
# Devkit specs already declare file scope in their task frontmatter and take
# compile's free seed path (no model call), so this is a no-op there.
if [ -d "$SPEC_ROOT/openspec/changes/$SPEC_ID" ]; then
  SPEC_REF="openspec/changes/$SPEC_ID"
  worktrail-compile "$SPEC_ROOT/openspec/changes/$SPEC_ID" || {
    echo "ERROR: worktrail-compile failed for $SPEC_ID — inspect the error above before retrying full-real." >&2
    exit 1
  }
else
  SPEC_REF="docs/specs/$SPEC_ID"
fi
worktrail-live full-real --repo "$SPEC_ROOT" --spec "$SPEC_REF" --base "$BASE" --agent "$AGENT_CLI" "${AGENT_MODEL_ARGS[@]}" "${FALLBACK_AGENT_ARGS[@]}" "${ROLE_AGENT_MAP_ARGS[@]}" "${PR_LABEL_ARGS[@]}" --route "$ROUTE" --gates "$GATES"
```

`AGENT_CLI` precedence is explicit invocation > repo policy `agent_cli` > machine-wide
`GO_AGENT_CLI`/`ORCH_AGENT` > detected `OPENCODE_PARENT` harness > `claude`. `agent_model` uses the
same repo-policy layer while retaining explicit invocation precedence.

`FALLBACK_CHAIN` is an explicit `FALLBACK_CHAIN`/`FALLBACK_AGENT_CLI` env var, else the resolved
policy's `routing.fallback` ordered list (repo-local `routing:` block, else the machine-wide
`~/.worktrail/routing.yaml`, else the flat `fallback_agent_cli` key — see `resolve_routing()` in
`policy.py`), with `AGENT_CLI` itself removed. Passed as `--fallback-chain` (comma-separated) so
`worktrail-live full-real` re-picks the first non-capacity-gated agent from the whole configured
set rather than stopping the moment the primary is exhausted.

`ROLE_AGENT_MAP` (same precedence as `AGENT_CLI`: explicit env var > `GO_ROLE_AGENT_MAP` >
`ORCH_ROLE_AGENT_MAP` > unset) overrides the spawn agent for individual roles, e.g.
`review=claude` to keep the reviewer on a genuinely independent, subscription-backed CLI
while `--agent` (e.g. `opencode`) covers the cheaper bulk implement/fix work. Format and
precedent: `live.py --role-agent-map` (`references: specs-parallel-orchestrator/worktrail-live`,
mirrors the existing `--model-map` flag). Omitted entirely (empty array, not an empty string
flag value) when unset, so existing invocations with no role-agent split are unaffected.
Role coverage: the map is honored by every spawned role — the task-level workers
(`implement`, `review`, `fix`, `cleanup`), `assembly-resolve` (merge-conflict resolution
during group integration), and the group-level verify workers (`resolve`, `ci-fix`) —
on pipeline and non-pipeline paths alike. `--model-map` entries win for a role's model;
a role pinned to a different agent falls back to that agent's own default model.

**Run mechanics (non-negotiable):**
- The pipelined engine is `full-real`'s ONLY scheduler — `--pipeline` is a
  no-op affirmation and may be omitted. Include a non-zero `--run-budget`. The
  legacy serial scheduler was deleted (`--sequential` is now a hard error — see
  `openspec/changes/scheduler-consolidation/`); never pass it.
- **Run `worktrail-compile` before `full-real` for every OpenSpec change** (see the
  code block above). `full-real` auto-compiles a missing plan inline since PR #56
  (`apply_run_plan()` calls `compile_run_plan()`), so skipping this step no longer
  hard-fails the run outright — but the CLI's own scope-gap check is stricter and
  fails loud with the exact task ids, while the inline auto-compile silently
  degrades to the baseline plan on a bad compile and only surfaces the problem
  later, once `validate_task_metadata()` refuses to fan those tasks out.
- The orchestrator's `full-real` mode is **long-running and CI-blocking** — always run it
  in the background (never on the foreground 10-minute Bash timeout). Use the Bash tool's
  `run_in_background` option **only** — never also `nohup … &` / `setsid` / a trailing `&`.
  Double-detaching makes the Bash call return `exit 0` instantly (reported "completed")
  while the real run is orphaned and still going.
- It is **resumable**: a killed run is recovered by re-issuing the same command; the
  orchestrator reads its run journal and continues from where it left off.
- **Integrated smoke test (opt-in, from policy):** when `integrate_smoke_cmd` is set in
  `docs/specs/worktrail-go-policy.yaml` (loaded in Phase 4), pass it through as
  `--smoke-cmd "<command>"`. The orchestrator runs it on each group's integration branch
  before opening that group's PR and quarantines the group on a non-zero exit — catching
  cross-task API drift a CI round-trip earlier. Omit the flag when the policy key is unset
  (repos without a wired command are never blocked). **Still pass it explicitly when known** —
  `live.py` auto-resolves the same command from policy (`pre_pr_cmd`, falling back to
  `integrate_smoke_cmd`) when `--smoke-cmd` is omitted on a repo that has either key
  configured, as a code-level safety net for exactly the case this note used to leave to the
  calling agent's memory — but an explicit flag is still clearer and always wins over the
  auto-resolved value.
- **Task-worktree dependency bootstrap (opt-in, from policy):** when
  `worktree_bootstrap_cmd` is set in `docs/specs/worktrail-go-policy.yaml` (loaded in Phase 4), pass
  it through as `--bootstrap-cmd "<command>"`. The orchestrator runs it in each fanned-out
  task worktree right after `git worktree add`, before spawning the worker — so
  implement/fix/review workers don't each rediscover and reinstall the base checkout's
  gitignored `node_modules` mid-task (task worktrees branch off the base commit and start
  without it; this is the per-task analogue of the spec worktree's own
  `#spec-worktree-setup` bootstrap step). It is **non-fatal** — a failed install is logged
  and the worker still self-installs — so a flaky registry never quarantines a task. Omit
  the flag when the policy key is unset (repos with no install step are unaffected). For a
  Node repo with a multi-task spec fan-out, prefer `worktrail-bootstrap-node-modules
  --app-dir <dir>` as the configured value over a plain install command — it hardlink-clones
  the sibling spec worktree's already-installed `node_modules` when its lockfile matches
  byte-for-byte, falling back to a real install otherwise, instead of paying the full install
  cost once per task worktree (`orchestrator/bootstrap_node_modules.py`).
- **Migration-group isolation (opt-in, from policy):** when `migration_path_patterns`
  is set in `docs/specs/worktrail-go-policy.yaml` (loaded in Phase 4), pass each pattern through
  as a repeated `--migration-pattern "<glob>"`. Any task whose declared files match one
  is always folded into `coordinator.plan_groups()`'s BASE integration group, even if
  its dependency graph would otherwise place it in an independent feature group — a
  schema migration and the code that depends on the tables it creates rarely share a
  `files` entry, so neither the dependency graph nor the shared-file union-find
  reliably catches that coupling, and a migration quarantined on its own (e.g. by an
  unrelated flaky test) can leave already-merged consumer code depending on tables
  that don't exist on any already-migrated database. Omit the flag when the policy key
  is unset (repos with no configured patterns are unaffected).
- **Fan-out width (opt-in, from policy):** when `max_workers` is set in
  `docs/specs/worktrail-go-policy.yaml` (loaded in Phase 4), pass it through as
  `--max-workers <n>` — it bounds how many task worktrees with live agent workers run
  concurrently. An explicit width in the user's invocation wins over the policy value.
  Omit the flag when the policy key is unset (the orchestrator's own default, 3, applies).
- **PR pacing (opt-in, from policy):** when `pr_pacing_wait_s` is set (> 0) in
  `docs/specs/worktrail-go-policy.yaml` (loaded in Phase 4), pass it through as
  `--pr-pacing-wait <seconds>`. Before opening each subsequent group PR, the orchestrator
  waits (bounded by this value) for the previous group PR's checks to resolve — or the PR
  to merge — so sibling group PRs don't hit a shared CI runner pool simultaneously; on
  auto-merge repos the previous PR typically lands during the wait, which also gives
  dependent groups a real merged base instead of a moving stacked diff. Best-effort: a
  red, stuck, or check-less PR never blocks integration beyond the bound. Serializes only
  the integrate+PR-open step across the concurrent per-group IV threads; verify still
  overlaps. Omit the flag when the policy key is unset or 0 (PRs open back-to-back,
  today's behavior).
- **Branch-aware merge method (opt-in, from policy):** when `docs/specs/worktrail-go-policy.yaml`
  sets `merge_method_by_base` for `$BASE`, resolve it (`policy.py --merge-method-for-branch
  "$BASE"`, loaded in Phase 4) and pass it through as `--merge-method <method>`. This
  overrides `verify.py`'s own repo-wide GitHub-settings query, which cannot tell "this repo
  allows merge commits for stg/prd promotions" from "dev-target feature PRs should still
  squash." Omit the flag when the branch has no override (falls back to repo-wide
  auto-detection, today's behavior).
- **Tail tasks (E2E / cleanup) are not auto-run.** `full-real` holds out `kind: e2e` and
  `kind: cleanup` tasks by design (a faithful global E2E runs only after the group PRs
  merge). When it sets `integrate_complete`, it records the outstanding ones as
  `pending_tail_tasks` + `pending_tail_reason` in the run journal for debugging, and the
  dashboard cross-checks them live against current task status and git-tracked files and
  surfaces a `tail-pending` stage. On a `tail-pending` spec, run those tasks (or mark them
  `completed`/backfill if not applicable) rather than re-launching the orchestrator — a
  re-launch would just skip them again.
- **After backgrounding — confirm health without `sleep`:** once the Bash tool reports the
  `run_in_background` task started, confirm the process is alive by `Read`-ing the task output
  file (the path shown in the Bash tool's background-task response). A few lines of log output
  confirm health. Do **not** reach for `sleep` — the harness blocks it as disguised polling
  (`Blocked: sleep …`). If you need to wait for a specific condition, use the `Monitor` tool
  with an `until`-loop instead.
- **Do not redirect the output stream** (`> "$OUT" 2>&1`). Let `run_in_background` own the
  capture so `Read <task-output>` is the single source of truth. A redirect splits the stream
  into two files and makes the task-output look empty/confusing.

---

## Sync before teardown {#sync-before-teardown}

**Mandatory after every orchestrator invocation. Run BEFORE tearing down any worktree.**

The orchestrator's PRs merge task-status updates and implementation code into `$BASE`, but the
spec worktree `$WT` (for `new`) or the base checkout (for `implement`) does not reflect those
changes. Sync must read the post-merge base to see correct task statuses and update the KG.

**Step 1 — fast-forward base to include merged PRs:**
```bash
git -C "$REPO" fetch origin
git -C "$REPO" pull --ff-only origin "$BASE"
```

**Step 2 — create a dedicated sync worktree from the updated base:**
```bash
SYNC_WT="$REPO-worktrees/$SPEC_ID-sync"
git -C "$REPO" worktree add "$SYNC_WT" -b "sync/$SPEC_ID" "$BASE"
```

**Step 3 — sync artifacts inside the sync worktree (inline):**

Branch on the spec's on-disk format — `$SYNC_WT/openspec/changes/$SPEC_ID` (OpenSpec;
the `new` pipeline's own default, and always true for `modify`) vs
`$SYNC_WT/docs/specs/$SPEC_ID` (devkit) — same detection idiom as `#orchestrator`'s
`SPEC_REF` branch.
Each branch sets `SYNC_PATH`, which Step 4 below uses in place of a hardcoded
`docs/specs/`.

**OpenSpec format** — `SYNC_PATH="openspec/"`. Read
`$SYNC_WT/openspec/changes/$SPEC_ID/tasks.md`: no remaining `- [ ]` lines means every
task is checked off.

- **All tasks checked and the run is closing out** → run `/opsx:archive <change-id>`
  (`#openspec-archive`; same enter-run-exit discipline as `#openspec-propose` — a
  slash command, not a `-C`-scoped call). It merges delta specs into `openspec/specs/`
  and moves the change directory into `openspec/changes/archive/` in one step.
- **Tasks remain pending but delta specs must land before the run finishes** (e.g. a
  Route-C planning-only stop) → run `/opsx:sync <change-id>` (`#openspec-sync`)
  instead, leaving the change directory in place for a later run to archive.

Both commands edit files under `$SYNC_WT/openspec/` in place; neither commits — Step 4
below picks up the resulting diff the same way as the devkit path.

**devkit format** — `SYNC_PATH="docs/specs/"`.

1. Glob both `$SYNC_WT/docs/specs/$SPEC_ID/tasks/TASK-*.md` and
   `$SYNC_WT/docs/specs/$SPEC_ID/changes/*/tasks/TASK-CHG-*.md` — read each file;
   collect tasks with `status: completed` and their `provides:` frontmatter entries.
2. Verify each provides file path exists under `$SYNC_WT/`.
3. Read `$SYNC_WT/docs/specs/$SPEC_ID/knowledge-graph.json` (or start from `{"metadata":{},"provides":[]}` if absent).
4. Merge verified provides into `knowledge-graph.json["provides"]`, deduplicating by `task_id + file` composite key.
5. Update `knowledge-graph.json["metadata"]["updated_at"]` to the current ISO timestamp.
6. Write the updated `knowledge-graph.json` back to `$SYNC_WT/docs/specs/$SPEC_ID/knowledge-graph.json`.

**Step 4 — commit + PR if sync produced changes, with CI-wait gate:**

`git diff --quiet HEAD` only sees tracked-file changes — it misses a brand-new
`knowledge-graph.json` written for a spec that had none yet (devkit path) or a
freshly created `openspec/specs/<capability>/spec.md` (OpenSpec path), since an
untracked file never appears in a diff against `HEAD`. Use `git status --porcelain`,
which reports untracked files too:

```bash
[ -z "$(git -C "$SYNC_WT" status --porcelain -- "$SYNC_PATH")" ] || {
  git -C "$SYNC_WT" add "$SYNC_PATH"
  git -C "$SYNC_WT" commit -m "sync($SPEC_ID): update spec artifacts and task statuses post-orchestrator"
  git -C "$SYNC_WT" push -u origin "sync/$SPEC_ID"
  # gh derives owner/repo from the remote; no --repo flag needed for the consuming project
  PR_URL=$(gh pr create --base "$BASE" --head "sync/$SPEC_ID" \
    --title "sync($SPEC_ID): post-orchestrator docs update" \
    --body "Updates spec artifacts and task statuses after orchestrator run. Auto-generated." \
    | tail -1)
  
  echo "$PR_URL"
}
```

**Step 4b — CI-wait gate (separate Bash call, only if a PR was created).** Never
hand-roll a `sleep` poll loop — a 30-min sleep loop exceeds the Bash tool's
10-minute ceiling and strands the run (GO v1 defect L7, `docs/design/history/go-v2-design.md` §1.2).
Use `gh pr checks --watch`, bounded by the Bash tool's `timeout` parameter:

```bash
# Run with the Bash tool timeout parameter set to 600000 (10 min).
# --watch blocks until all checks settle; --fail-fast exits on first failure.
gh pr checks "$PR_URL" --watch --fail-fast
echo "CHECKS_EXIT=$?"
```

- **Exit 0** (all checks passed, or all skipped) → merge and tear down:
  ```bash
  gh pr merge "$PR_URL" --merge --delete-branch || echo "MERGE_BLOCKED"
  # On success — Step 5, tear down the sync worktree:
  git -C "$REPO" worktree remove "$SYNC_WT" --force
  git -C "$REPO" branch -D "sync/$SPEC_ID" 2>/dev/null || true
  # Step 6 — proceed to #worktree-lifecycle for the spec worktree.
  ```
  `MERGE_BLOCKED` (branch protection despite green checks) → quarantine: keep
  `$SYNC_WT` and `$WT`, report the merge error, recovery = diagnose, fix,
  re-run sync.
- **Non-zero with a failed check** → quarantine: keep `$SYNC_WT` and `$WT`,
  report which required check failed (`gh pr checks "$PR_URL"`), recovery =
  fix CI, push to `sync/$SPEC_ID`, re-run sync.
- **Bash tool timeout fired** (checks still pending after 10 min) → re-issue
  the same `--watch` command, up to 3 times (30 min total). Still pending →
  quarantine with worktrees preserved and report the stuck checks. Never
  switch to a sleep loop and never background an unbounded waiter — and never
  end the turn to "check again later": the whole wait stays inside these
  blocking calls in one turn (single-turn wait discipline,
  `ci-watch-loop.md` `{#ci-wait-discipline}`). When composing a NEW subagent
  prompt with a PR tail, prefer ending the subagent at PR-opened and keeping
  the wait in the dispatcher (wait-ownership rule, same section) unless the
  subagent also owns failure classification.

**Note on branch protection and CI-skip:** If `$BASE` has required status checks enforced via branch protection, `gh pr merge` itself will reject a red or pending PR. The `--watch` gate above is defence-in-depth and ensures conductor visibility into the wait; it also handles consuming projects that may lack branch protection.

For `developer-kit` itself, both CI workflows (`plugin-validation.yml`, `skill-review.yml`) carry `paths-ignore: ['docs/specs/**']`. A spec-sync PR that only touches `docs/specs/` causes GitHub Actions to skip those workflows entirely; GitHub marks the skipped checks as "success" (not "pending"), so `--watch` returns immediately and `gh pr merge` proceeds without waiting. Do **not** use `[skip ci]` in the sync commit message instead — that suppresses check creation entirely, leaving required checks in "Expected" state and blocking the merge. Consuming repos (GGB, Datalena, etc.) should add the same `paths-ignore` pattern to their own CI if they want fast auto-merge on sync PRs.

If sync produces no changes, skip step 4 (no commit/PR/CI-wait needed) but still tear down the sync worktree:
```bash
git -C "$REPO" worktree remove "$SYNC_WT" --force
git -C "$REPO" branch -D "sync/$SPEC_ID" 2>/dev/null || true
```
Then proceed to `#worktree-lifecycle` for the spec worktree.

---

## Worktree lifecycle {#worktree-lifecycle}

**Never `cd` into a worktree.** Every command against `$WT` (or `$SYNC_WT`/any other
worktree) below is written as `git -C "$WT" <cmd>` for exactly this reason — operate on it
by path, from wherever the shell already is. `cd "$WT"` leaves the shell's cwd inside a
directory that a later `git worktree remove "$WT"` deletes out from under it; the removal
itself succeeds (it takes a path, not a cwd), but the *next* command then throws a `pwd`/
`getcwd: cannot access parent directories` error that looks like a real failure and can
derail cleanup before branch/remote teardown is confirmed. If an earlier step in this run
did `cd "$WT"` anyway (e.g. to commit/push), `cd` back to `$REPO` (or anywhere outside every
worktree about to be removed) before running any `git worktree remove` below.

### Active-conflicts scan {#active-conflicts-scan}

This is a **hard stop, not advisory**. It atomically claims `$SPEC_ID` for
`$RUN` before any worktree, branch, or file is touched. A plain read-only scan
(list non-terminal runs, then separately tag `$RUN` with `$SPEC_ID`) has a
TOCTOU gap: two concurrent `/go` sessions can each scan and see nothing before
either has tagged its own record, then both proceed believing they're first —
not hypothetical, this is what the 2026-08-07 duplicate-orchestrator incident
exploited. `claim` closes that gap by making the exclusivity check and the
`specification` write one OS-atomic step, so a second concurrent claim on the
same `$SPEC_ID` fails fast instead of racing to a scan. It also catches what a
local `git worktree list`/`for-each-ref` glob check cannot: a prior claimed
brief never orchestrated, whose worktree or branch doesn't yet exist or
doesn't match the glob.

`claim`'s own conflict check only ever considers *live* records — a record
whose worktree is gone and whose files already landed on its own
`base_branch` (`stale`, per `_is_stale()`) never blocks a claim. But a stale
record left untouched just rots forever with no `finish` entry, so before
the hard-stop check below, scan for and close any stale records on this
`$SPEC_ID`:

```bash
RUN_RECORDS_DIR="$(dirname "$(dirname "$RUN")")"
SCAN=$(worktrail-run-record active-conflicts \
  --dir "$RUN_RECORDS_DIR" --repo "$REPO" --specification "$SPEC_ID" --exclude "$RUN")
echo "$SCAN" | python3 -c '
import json, sys
for r in json.load(sys.stdin)["stale"]:
    print(r["path"])
' | while IFS= read -r STALE_PATH; do
  worktrail-run-record reconcile "$STALE_PATH" \
    --note "auto-reconciled: active-conflicts-staleness-reconciliation"
done
```

Then run the atomic claim itself, whose hard stop below only ever fires on
`live` conflicts:

```bash
CLAIM=$(worktrail-run-record claim "$RUN" --specification "$SPEC_ID")
CLAIM_STATUS=$(echo "$CLAIM" | python3 -c 'import sys, json; print(json.load(sys.stdin)["status"])')

if [ "$CLAIM_STATUS" != "claimed" ]; then
  echo "BLOCKED: active run(s) already target $SPEC_ID ($CLAIM_STATUS):" >&2
  echo "$CLAIM" | python3 -c "
import sys, json
c = json.load(sys.stdin)
for r in (c.get('conflicts') or [c]):
    print(f'  run_id={r.get(\"run_id\",\"?\")} started_at={r.get(\"started_at\",\"?\")} request_summary={r.get(\"request_summary\",\"?\")} path={r.get(\"path\",\"?\")}')
" >&2
  worktrail-run-record finish "$RUN" \
    --status blocked_external_dependency \
    --merge-result "claim on $SPEC_ID failed ($CLAIM_STATUS) — another run already targets it"
  # Stop here — do not create $WT, do not touch any repo file.
fi
```

If `$CLAIM_STATUS` is `claimed`, proceed with the caller's next step. The
claim releases automatically when `$RUN` reaches any `finish` completion
state; a crashed session that never called `finish` leaves the claim held
until an operator inspects and manually finishes/abandons the stale run
record — the same accepted recovery path this scan already had.

### Sibling worktree/branch check {#sibling-worktree-check}

Shared by `#spec-worktree-setup` and `#change-spec-worktree-setup` — run before
creating `$WT` on either pipeline. Another open or stalled worktree/branch may
already target the same spec id: a prior claimed brief that was never
orchestrated, or a same-`$SPEC_ID` race between two concurrent `/go`
sessions/machines (not hypothetical — memory `project_orchestrator_concurrent_spec_collision`
records a prior real incident where a machine had to stand down, clean up
worktrees/branches, and reset to base after discovering mid-run that another
machine was already implementing the same spec). Authoring or implementing
blind against that risks silently contradicting already-reasoned,
partially-implemented decisions, or two sessions duplicating the same spec id.

Before the advisory glob check below, run `#active-conflicts-scan`. If
`$CONFLICTS` is empty, proceed to the advisory glob check below unchanged.

Set `$SIBLING_WT_GLOB` (a `git worktree list` grep pattern) and
`$SIBLING_REF_GLOB` (a `for-each-ref` pattern) per call site, then run:

```bash
SIBLING_WT=$(git -C "$REPO" worktree list | grep -F "$SIBLING_WT_GLOB" || true)
SIBLING_BRANCHES=$(git -C "$REPO" for-each-ref \
  --format='%(refname:short)  %(committerdate:short)' "$SIBLING_REF_GLOB" 2>/dev/null)

if [ -n "$SIBLING_WT" ] || [ -n "$SIBLING_BRANCHES" ]; then
  echo "ADVISORY: sibling work already targets $SPEC_ID:" >&2
  [ -n "$SIBLING_WT" ] && echo "$SIBLING_WT" >&2
  [ -n "$SIBLING_BRANCHES" ] && echo "$SIBLING_BRANCHES" >&2
fi
```

This is an **advisory, not a hard stop** — `/go auto` runs unattended and must
not stall on it. If siblings are found, inspect their content before
proceeding (read the sibling worktree's spec/change-spec files, or
`git show <sibling-branch>:<path>`) and reconcile rather than re-deriving
decisions the sibling already made, or duplicating a spec id already in
flight.

Cross-machine detection is out of scope for this local check: worktrees and
`for-each-ref` only see refs this machine already has. A sibling on another
machine that hasn't pushed its branch yet is invisible here — see
`git ls-remote` as a possible follow-up for `auto_pick` (spec 017) if that gap
proves costly in practice.

### Spec worktree setup {#spec-worktree-setup}

`/go new` selects the authoring format from `WORKTRAIL_SPEC_FORMAT` (default
`openspec`). The execution adapter is selected from the resulting path, so a
repository may process legacy devkit specs and OpenSpec changes in the same
workspace. Never infer the format from the task id alone.

```bash
FORMAT="${WORKTRAIL_SPEC_FORMAT:-openspec}"
case "$FORMAT" in openspec|devkit) ;; *) echo "Unsupported WORKTRAIL_SPEC_FORMAT=$FORMAT" >&2; exit 2 ;; esac
if [ "$FORMAT" = "devkit" ]; then
  # devkit numbers specs sequentially under docs/specs/NNN-slug/.
  NNN=$(ls -d "$REPO/docs/specs/[0-9]*/" 2>/dev/null | wc -l)   # zero-padded
  # Cross-session NNN-allocation race guard: two concurrent /go sessions (this
  # machine or another) can each compute the same NNN from their own local
  # checkout before either has pushed — not hypothetical, see brief
  # 20260722-160700-datalena-spec-093-numbering-collision (two sessions both
  # allocated 093). A same-NNN-different-slug branch may already be on origin
  # even though the local checkout hasn't fetched it. Advisory, not a hard
  # stop — if the remote lookup fails, fall through to the local-only count.
  REMOTE_MAX=$(git -C "$REPO" ls-remote --heads origin 'spec/[0-9]*' 2>/dev/null \
    | sed -E 's#.*refs/heads/spec/0*([0-9]+)-.*#\1#' | sort -n | tail -1)
  if [ -n "$REMOTE_MAX" ]; then
    NEXT_FROM_REMOTE=$((10#$REMOTE_MAX + 1))
    [ "$NEXT_FROM_REMOTE" -gt "$NNN" ] && NNN="$NEXT_FROM_REMOTE"
  fi
  # SPEC_ID="$NNN-$slug"  (slug confirmed via AskUserQuestion if unclear;
  # $AUTO_MODE=true: derive from the brief focus/request text — never ask)
else
  # OpenSpec has no numeric-prefix convention — `openspec new change` rejects
  # any name that doesn't start with a letter (verified against 1.6.0: "Error:
  # Change name must start with a letter"), and collision detection is by
  # change-name existence (openspec-propose's own guardrail), not sequence
  # number. Never prefix $slug with $NNN here. If the target repo's own
  # convention still requires an NNN- prefix, do not apply it yet — see
  # #openspec-nnn-prefix; the prefix is added by a post-validate rename, never
  # at worktree-setup time.
  # SPEC_ID="$slug"  (slug confirmed via AskUserQuestion if unclear;
  # $AUTO_MODE=true: derive from the brief focus/request text — never ask)
fi
SIBLING_WT_GLOB="$SPEC_ID-spec"
SIBLING_REF_GLOB="refs/heads/spec/$SPEC_ID"
# run the sibling check — #sibling-worktree-check
WT="$REPO-worktrees/$SPEC_ID-spec"
git -C "$REPO" worktree add -b "spec/$SPEC_ID" "$WT" "$BASE"
worktrail-run-record set "$RUN" worktree "$WT"
if [ "$FORMAT" = "openspec" ]; then
  SPEC_DIR="$WT/openspec/changes/$SPEC_ID"
else
  SPEC_DIR="$WT/docs/specs/$SPEC_ID"
fi
```

If `git worktree add` is sandbox-denied, surface it and stop — don't write on base.
Operate on `$WT` via `git -C "$WT" <cmd>` throughout — never `cd` into it (see the
worktree-lifecycle note above).

After creating a new worktree, bootstrap that checkout's local dependencies before
trying to run repo-local tools from it (`npm test`, `vitest`, `next dev`, etc.).
Do not assume the base checkout's `node_modules` or generated artifacts are usable
from the new worktree. Run the repo's documented install/bootstrap step in the
worktree itself (for example `npm ci`, or the repo's documented equivalent).

### Change-spec worktree setup {#change-spec-worktree-setup}

Used by the `modify` pipeline (Routes F/G — `pipeline-details.md#modify-pipeline`).
The change-spec's parent spec (`$SPEC_ID`) is already on `$BASE`; this worktree is
for authoring the delta, not the original spec.

**Sibling check (mandatory, before creating `$WT`):**

```bash
# $SPEC_ID is already known (existing spec id)
SIBLING_WT_GLOB="$SPEC_ID-chg-"
SIBLING_REF_GLOB="refs/heads/chg/$SPEC_ID-*"
# run the sibling check — #sibling-worktree-check
```

If siblings are found, read their Summary/Decisions sections before authoring:
the `/opsx:propose` step below (`pipeline-details.md#modify-pipeline` step 1)
must record the reconciliation (adopted, differs, or superseded) in the new
change-spec's own Decisions section rather than re-deriving decisions the
sibling already made.

```bash
# $CHANGE_SLUG is already known (new change-name slug)
CHANGE_ID="$SPEC_ID-$CHANGE_SLUG"
WT="$REPO-worktrees/$SPEC_ID-chg-$CHANGE_SLUG"
git -C "$REPO" worktree add -b "chg/$CHANGE_ID" "$WT" "$BASE"
worktrail-run-record set "$RUN" worktree "$WT"
CHANGE_DIR="$WT/openspec/changes/$CHANGE_ID"
```

If `git worktree add` is sandbox-denied, surface it and stop — don't write on base.
Operate on `$WT` via `git -C "$WT" <cmd>` throughout — never `cd` into it (see the
worktree-lifecycle note above).

**Commit discipline (this is the fix for the missing-task-file defect):** commit
immediately after every writing step, on the `chg/$SPEC_ID-$CHANGE_SLUG` branch —
after `/opsx:propose` authors the change artifacts, and again after
spec-to-tasks (delta) writes `tasks/TASK-CHG-*.md`, `data-model.md`, `contracts/`,
and `knowledge-graph.json`. Never let generated-but-uncommitted output sit in `$WT`
before the orchestrator launches — `#modify-pipeline`'s pre-launch guard checks for
exactly this and will refuse to proceed if it finds one.

### Direct fix-branch worktree setup {#fix-branch-worktree-setup}

Used by Route F step 5's second branch — a defect in code with no owning spec.
(Spec-owned behavior uses `#change-spec-worktree-setup` via the `modify` pipeline
instead.)

```bash
# $SLUG is a short fix descriptor (e.g. "cache-header-null-check"), confirmed
# via AskUserQuestion if unclear ($AUTO_MODE=true: derive from the brief
# focus/request text — never ask)
```

**Atomic ownership guard (mandatory, before `git worktree add` below).** Unspecced
fixes have no `$SPEC_ID` to key `#active-conflicts-scan` on, which is why this setup
previously had no ownership check at all — a gap named directly in
`docs/specs/research/concurrent-go-dispatch-brief-claim-race.md` (recommended fix #2):
two concurrent `/go` dispatches both landing on the same unspecced defect could each
create their own `$WT`/branch and duplicate the fix with nothing to stop either.
`run_record.py claim`'s exclusivity primitive is generic over its `--specification`
string (it slugifies whatever key it's given into a lock filename — see
`#active-conflicts-scan`), so reuse it here keyed on `fix:$SLUG` — namespaced with a
`fix:` prefix so it can never collide with a real spec id's lock:

```bash
CLAIM_KEY="fix:$SLUG"
RUN_RECORDS_DIR="$(dirname "$(dirname "$RUN")")"
SCAN=$(worktrail-run-record active-conflicts \
  --dir "$RUN_RECORDS_DIR" --repo "$REPO" --specification "$CLAIM_KEY" --exclude "$RUN")
echo "$SCAN" | python3 -c '
import json, sys
for r in json.load(sys.stdin)["stale"]:
    print(r["path"])
' | while IFS= read -r STALE_PATH; do
  worktrail-run-record reconcile "$STALE_PATH" \
    --note "auto-reconciled: fix-branch-active-conflicts-staleness-reconciliation"
done

CLAIM=$(worktrail-run-record claim "$RUN" --specification "$CLAIM_KEY")
CLAIM_STATUS=$(echo "$CLAIM" | python3 -c 'import sys, json; print(json.load(sys.stdin)["status"])')

if [ "$CLAIM_STATUS" != "claimed" ]; then
  echo "BLOCKED: active run(s) already target $CLAIM_KEY ($CLAIM_STATUS):" >&2
  echo "$CLAIM" | python3 -c "
import sys, json
c = json.load(sys.stdin)
for r in (c.get('conflicts') or [c]):
    print(f'  run_id={r.get(\"run_id\",\"?\")} started_at={r.get(\"started_at\",\"?\")} request_summary={r.get(\"request_summary\",\"?\")} path={r.get(\"path\",\"?\")}')
" >&2
  worktrail-run-record finish "$RUN" \
    --status blocked_external_dependency \
    --merge-result "claim on $CLAIM_KEY failed ($CLAIM_STATUS) — another run already targets this fix"
  # Stop here — do not create $WT, do not touch any repo file.
fi
```

Unlike `#active-conflicts-scan`'s sibling worktree/branch grep (advisory, spec-only,
keyed on `$SPEC_ID`), this hard stop is keyed on the caller-chosen `$SLUG` alone — it
cannot catch two different slugs converging on the same underlying files. Live-verified
2026-08-29: two concurrent `/go` dispatches fixed the identical unspecced defect under
different slugs, each in its own worktree, and produced a line-for-line duplicate diff
to the same two files with nothing tracking either — the first PR merged and the second
session's work was already stale. The sibling diff overlap scan below is the fallback
for exactly that gap. If `$CLAIM_STATUS` is `claimed`, run the scan, then proceed to the
worktree creation below. The claim releases automatically when `$RUN` reaches any
`finish` completion state, same recovery path as `#active-conflicts-scan`.

**Sibling diff overlap scan (advisory).** Set `$EXPECTED_FILES` (repo-relative paths,
one per line) from the root-cause step (Route F step 3) — the files this fix already
knows it will touch. Skip the scan (not a stop) when the fix hasn't narrowed to specific
files yet; there is nothing to compare against.

`git diff --name-only HEAD` alone only sees tracked-file changes — the same blind spot
`#sync-before-teardown` step 4 already documents: a brand-new file never appears in a
diff against `HEAD` because it has nothing tracked to diff against. Two concurrent
`/go` dispatches that each independently create the SAME NEW file for the same
unspecced fix would produce no overlap signal from the diff alone, so also check
`git status --porcelain --untracked-files=all` (`??`-prefixed paths) and fold both
sources into the same comparison:

```bash
SIBLING_ROOT="$REPO-worktrees"
if [ -n "$EXPECTED_FILES" ] && [ -d "$SIBLING_ROOT" ]; then
  find "$SIBLING_ROOT" -maxdepth 1 -type d -name 'fix-*' | while IFS= read -r SIBLING_WT; do
    SIBLING_DIFF=$(git -C "$SIBLING_WT" diff --name-only HEAD 2>/dev/null | sort -u)
    SIBLING_UNTRACKED=$(git -C "$SIBLING_WT" status --porcelain --untracked-files=all 2>/dev/null | sed -n 's/^?? //p' | sort -u)
    SIBLING_CHANGED=$(printf '%s\n%s\n' "$SIBLING_DIFF" "$SIBLING_UNTRACKED" | sed '/^$/d' | sort -u)
    [ -z "$SIBLING_CHANGED" ] && continue
    OVERLAP=$(comm -12 <(echo "$EXPECTED_FILES" | sort -u) <(echo "$SIBLING_CHANGED"))
    if [ -n "$OVERLAP" ]; then
      echo "ADVISORY: sibling fix-branch worktree $SIBLING_WT has an uncommitted diff overlapping expected files:" >&2
      echo "$OVERLAP" >&2
    fi
  done
fi
```

This is **advisory, not a hard stop** — mirrors `#sibling-worktree-check`'s framing:
`/go auto` runs unattended and must not stall on it. On a hit, inspect the sibling's
diff (`git -C <sibling-worktree> diff`) before proceeding and reconcile rather than
duplicating work already in flight. Cross-machine detection is out of scope here for
the same reason as `#sibling-worktree-check`: this only sees worktrees this machine
already has.

```bash
WT="$REPO-worktrees/fix-$SLUG"
git -C "$REPO" worktree add -b "fix/$SLUG" "$WT" "$BASE"
worktrail-run-record set "$RUN" worktree "$WT"
```

If `git worktree add` is sandbox-denied, surface it and stop — don't write on base.
Operate on `$WT` via `git -C "$WT" <cmd>` throughout — never `cd` into it (see the
worktree-lifecycle note above).

After creating the worktree, bootstrap that checkout's local dependencies before
running repo-local tools from it (`npm ci` or the repo's documented equivalent) —
see the note under `#spec-worktree-setup`; do not assume the base checkout's
`node_modules` or generated artifacts are usable from the new worktree.

### Worktree deletion liveness guard {#worktree-deletion-liveness-guard}

Shared by every documented deletion path — the `new`-pipeline teardown below,
`#fix-branch-worktree-teardown`, and `worktree-cleanup.md`'s prune step. `git worktree
add` now stamps the creating run's own path onto that run record (see
`#spec-worktree-setup`/`#change-spec-worktree-setup`/`#fix-branch-worktree-setup`), so
a worktree about to be removed can be checked back against the run that owns it before
it's deleted out from under a still-active process — the same class of cross-session
collision `#active-conflicts-scan` and the Active-run-resume liveness check
(`worktrail-go/SKILL.md` Phase 7) already guard against, applied here to deletion
instead of duplicate dispatch.

Call this before any `git worktree remove`/`branch -D` pair below, with `$WT` set to
the worktree about to be deleted, `$RUN_RECORDS_DIR` set to that call site's own
run-records directory, and `$INVOCATION_CONTEXT_DISPATCH_ID` carried through from the
invoking shell:

```bash
OWNER=$(worktrail-run-record find-by-worktree \
  --dir "$RUN_RECORDS_DIR" --repo "$REPO" --worktree "$WT")
OWNER_FOUND=$(echo "$OWNER" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['found']).lower())")

if [ "$OWNER_FOUND" = "true" ]; then
  OWNER_PATH=$(echo "$OWNER" | python3 -c "import json,sys; print(json.load(sys.stdin)['path'])")
  LIVENESS=$(worktrail-run-record liveness "$OWNER_PATH" --dispatch-id "$INVOCATION_CONTEXT_DISPATCH_ID")
  LIVE_FRESH=$(echo "$LIVENESS" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['fresh']).lower())")
  LIVE_SAME_DISPATCH=$(echo "$LIVENESS" | python3 -c "import json,sys; print(str(json.load(sys.stdin)['same_dispatch']).lower())")

  if [ "$LIVE_FRESH" = "true" ] && [ "$LIVE_SAME_DISPATCH" = "false" ]; then
    echo "BLOCKED: refusing to delete $WT — owned by a live run:" >&2
    echo "$LIVENESS" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  run_id={r.get(\"run_id\",\"?\")} age_seconds={r.get(\"age_seconds\",\"?\")} updated_at={r.get(\"updated_at\",\"?\")}')
" >&2
    # Stop here — do not run git worktree remove/branch -D on $WT.
  fi
fi
```

`same_dispatch: true` (this exact dispatch owns the record it just found) and
`fresh: false` (a stale/abandoned owning record) both proceed with the caller's
deletion unchanged — the first because the caller is deleting its own worktree, the
second because the owning process most likely crashed without ever reaching teardown
and the record itself would otherwise rot forever. `OWNER_FOUND = false` (no run
record ever claimed this path, e.g. records already pruned) also proceeds unchanged —
this guard only ever blocks on positive evidence of a live, different owner, never on
the absence of one.

### Teardown after `new` pipeline completes

Group-branch naming: the live `full` path creates per-group branches as `<run-id>/<group>`
(e.g. `full-1780251916/feature-3`) — keyed on the **run-id** (`integrate.py:175`). The
`<spec-id>/<group>` form is the demo/toy path (`orchestrate.py`) only; a sweep scoped to
spec-id alone is insufficient for the live `full` path.

**Before running any of the teardown below:** confirm the shell's cwd is `$REPO` (or
anywhere outside `$WT`), not `$WT` itself — `cd` back first if an earlier step left you
inside it. See the worktree-lifecycle note above for why.

- **All group PRs auto-merged green AND no tail tasks pending:**

  "All group PRs merged" and "the change is actually done" are different states by
  design — see the "Tail tasks (E2E / cleanup) are not auto-run" note above:
  `integrate_complete: true` can co-exist with unrun `pending_tail_tasks`. Before
  removing `$WT`, confirm the run journal reports none outstanding:

  ```bash
  JOURNAL="$REPO-worktrees/run-$SPEC_ID.json"   # modify/chg pipeline: run-$CHANGE_ID.json
  PENDING_TAIL=$(python3 -c "
import json
try:
    data = json.load(open('$JOURNAL'))
except (OSError, json.JSONDecodeError):
    data = {}
print(json.dumps(data.get('pending_tail_tasks', [])))
")
  if [ "$PENDING_TAIL" != "[]" ]; then
    echo "BLOCKED: pending tail tasks remain, do not tear down \$WT: $PENDING_TAIL" >&2
    # Stop here — run those tasks (or mark them completed/backfill per the
    # "Tail tasks (E2E / cleanup) are not auto-run" note) instead of tearing
    # down $WT. Do not run the commands below.
  fi
  ```

  Then run `#worktree-deletion-liveness-guard` with `$RUN_RECORDS_DIR`
  set to `"$(dirname "$(dirname "$RUN")")"` and `$INVOCATION_CONTEXT_DISPATCH_ID` carried
  through from the invoking shell. If it blocks, stop here — do not run the commands below.

  ```bash
  git -C "$REPO" worktree remove "$WT"
  git -C "$REPO" branch -D "spec/$SPEC_ID"   # modify pipeline: "chg/$CHANGE_ID" — only after confirmed merge

  # Sweep leftover orchestrator branches — verify.py deletes green groups' branches but
  # intentionally keeps quarantined/split groups' branches. The sweep below is a backstop
  # for any branch ref that survived (quarantine path excluded by being in this block only).
  # Preferred: scoped to this run's id (set by the orchestrator in the run journal):
  git -C "$REPO" for-each-ref --format='%(refname:short)' "refs/heads/$RUN_ID/" \
    | xargs -r -n1 git -C "$REPO" branch -D
  # Fallback when $RUN_ID is not in scope: guarded prefix sweep
  # git -C "$REPO" for-each-ref --format='%(refname:short)' \
  #     "refs/heads/full-*/" "refs/heads/$SPEC_ID/" \
  #   | xargs -r -n1 git -C "$REPO" branch -D
  git -C "$REPO" worktree prune
  git -C "$REPO" worktree list && git -C "$REPO" branch   # confirm clean tree
  ```
  The orchestrator's `verify.py` removes per-task worktrees and deletes green groups' branches
  (`verify.py:344,351`). Quarantined/split groups intentionally retain both their worktrees
  and branches for human inspection — which is why this all-green sweep exists as a backstop.
  Uses `-D` (force) because squash-merges rewrite SHAs and `git branch -d` would silently skip
  exactly the branches we are sweeping.
- **Anything quarantined:** keep the `spec/$SPEC_ID` worktree and all quarantined group
  worktrees in place; report what needs a human (failing CI, conflict, blocked merge).
  Quarantined groups' branches are kept — do NOT run the branch sweep.

### Teardown after direct fix-branch worktree {#fix-branch-worktree-teardown}

Single branch, single PR — no group sweep needed, unlike the `new` pipeline teardown
above.

**Before running any of the teardown below:** confirm the shell's cwd is `$REPO` (or
anywhere outside `$WT`), not `$WT` itself — `cd` back first if an earlier step left you
inside it. See the worktree-lifecycle note above for why.

- **PR merged:**

  Before removing `$WT`, run `#worktree-deletion-liveness-guard` with `$RUN_RECORDS_DIR`
  set to `"$(dirname "$(dirname "$RUN")")"` and `$INVOCATION_CONTEXT_DISPATCH_ID` carried
  through from the invoking shell. If it blocks, stop here — do not run the commands below.

  ```bash
  gh pr view "$PR_URL" --json state,mergedAt   # confirm merged before removing
  git -C "$REPO" worktree remove "$WT"
  git -C "$REPO" branch -D "fix/$SLUG"          # only after confirmed merge
  git -C "$REPO" worktree prune
  git -C "$REPO" fetch origin "$BASE"
  git -C "$REPO" merge --ff-only "origin/$BASE"   # sync local base before the next worktree
  git -C "$REPO" worktree list && git -C "$REPO" branch   # confirm clean tree
  ```
- **Not yet merged / quarantined:** keep `$WT` and the `fix/$SLUG` branch in place;
  report what needs a human (failing CI, conflict, blocked merge, pending review).

---

## Handoff seed {#handoff-seed}

Full procedure for `sdd-workflow handoff` / `sdd-workflow handoff:<id>` — the sub-flow that selects a brief from the
personal work queue, claims it, and wires its content into the existing `new` pipeline.
(Brief→seed field mapping is documented in `handoff_seed.py`'s module docstring.)

**Three cooperating scripts (single owner for the lifecycle):**
- The queue **lifecycle** (list / claim / done) is owned by the handoff skill's
  `work_queue.py` — the *one* atomic implementation, shared with the `handoff` skill so the
  claim/move can never diverge. `sdd-workflow` calls it; it does not reimplement the move.
- The **seed mapping** is `worktrail-handoff-seed` (sdd-workflow-specific, read-only).
- The **new-vs-change matcher** is `worktrail-classify-handoff` (sdd-workflow-specific,
  read-only). It scans the current `docs/specs/` tree (plus any `--extra-specs-root` roots,
  e.g. `openspec/changes` and `openspec/specs`, so an OpenSpec-format repo's own specs
  are visible too) plus optional brief hints (`change-kind`, `target-spec`) and emits
  evidence for the normal route classifier.

The queue lives at `$WORK_QUEUE_DIR` (default `~/work-queue`) with `queue/` + `picked/` only.

### Step 1 — List the queue

```bash
worktrail-work-queue list --json
```

Returns `{"briefs": [{"filename": str, "path": str, "focus": str}, ...]}` newest-first.
A missing queue directory is treated as empty — never an error.

### Step 2 — Selection

Parse the positional arg before entering this sub-flow:
- `handoff` (bare keyword) → unnamed mode; see branches 2a–2c below.
- `handoff:<id>` → named mode → skip selection, go straight to Step 3 with `<id>`.

**2a — Empty queue** — if `briefs` is empty: "The handoff queue is empty — nothing
to seed." Stop. Do NOT start a pipeline.

**2b — Exactly one brief, unnamed** — confirm with `AskUserQuestion` (Yes / No):
"Seed the `new` pipeline from `<filename>` — focus: `<focus>`?" Yes → Step 3 with
that filename. No → stop; brief stays in `queue/` (not claimed).

**2c — Multiple briefs, unnamed** — present the newest-first `briefs` list
(`filename — focus`) via `AskUserQuestion` ("Which handoff brief should seed the
pipeline?"). Proceed to Step 3 only with the user's pick.

(`$AUTO_MODE=true` never reaches 2b/2c: go claims the brief itself and always
dispatches named — `handoff:<id> route:<X>`. If an unnamed handoff seed somehow
runs unattended, stop and report — never ask, never pick a brief silently.)

---

### Step 3 — Claim the brief: atomic queue/ → picked/

```bash
worktrail-work-queue claim "<id-or-filename>" --by "$GO_DISPATCH_ID" --json
```

Always pass `--by "$GO_DISPATCH_ID"` — the token this dispatch received from `worktrail-go`
as `by:<dispatch-id>` (Phase 1 Intake table, `worktrail-sdd-workflow/SKILL.md`). It is the
same identity `worktrail-go`'s own earlier claim on this brief (if any) already passed, so
`claim()` can compute `same_owner` by comparing it against the brief's stamped `claimed-by`.
If this dispatch has no `$GO_DISPATCH_ID` (an older or non-`/go` caller), omit `--by` — the
claim still succeeds, but `same_owner` on any `already-claimed` result is `null` and must be
treated as "not confirmed mine."

The claim resolves `<id>` (full filename, stem, unique prefix, or `id` frontmatter) AND
atomically moves it out of `queue/` into `picked/`, stamping `status: picked` + `claimed-at`.
Act on `status` and `same_owner` together:

| `status` | `same_owner` | Action |
|----------|--------------|--------|
| `claimed` | (always `true`) | Use the returned `path` (now in `picked/`) for Step 4. |
| `already-claimed` with `path` | `true` | Use the returned picked path for Step 4. This is the expected path when the generic `go` front door claimed the brief (with the identical `--by`) before delegating to `sdd-workflow handoff:<id>`. |
| `already-claimed` with `path` | `false` or `null` | A **different** dispatch owns this brief — the "already-claimed with path" shape alone does NOT mean it is this dispatch's own claim (path presence and ownership are independent; see `docs/specs/research/concurrent-go-dispatch-brief-claim-race.md`, the incident this guards against). Do NOT seed — re-list (Step 1) and pick another. |
| `already-claimed` without `path` | `false` | Another session won the race — re-list (Step 1) and pick another; do NOT seed. |
| `none` | — | Brief `<id>` not in the queue. List queue briefs newest-first; ask the user to pick or abort. |
| `ambiguous` | — | `<id>` matches multiple briefs. List the `candidates`; ask to disambiguate, then re-claim. |

The atomic rename is the concurrency guarantee: only one agent can claim a given brief, so
two sessions never seed the same one. Never hand-`mv` a brief — always go through `work_queue.py`.

---

### Step 4 — Build the seed

```bash
worktrail-handoff-seed seed "<claimed-path-from-step-3>" --json
```

Output shape:
```json
{
  "feature_idea":    "<focus + ## Suggested approach>",
  "constraints":     "<## Discovery context + ## Key artifacts + ## Open questions / blockers>",
  "repo":            "<path or null>",
  "base_branch":     "<name or null>",
  "focus":           "<display string>",
  "suggested_skills": ["<skill-id>"],
  "recommended_route": "<A-J or null>",
  "change_kind": "<new|delta|bugfix or null>",
  "target_spec": "<spec folder slug or null>",
  "error":           null
}
```

The JSON shape above is the full mapping (details: `handoff_seed.py` docstring).
Operationally: when `recommended_route` is non-null, pass it to classification via
`classify.py --handoff-route`.

**Repo hint:** pass `repo` + `base_branch` as the Step 0 resolver hint (see
`#repo-resolution`).  When `repo` is `null` or does not resolve to a reachable checkout, fall back
to `sdd-workflow`'s normal repo-resolution flow without surfacing an error.

**Suggested skills:** if `suggested_skills` is non-empty, surface an informational note:
> "Suggested skills for this brief: `<skill1>`, `<skill2>` — not auto-invoked."

Do NOT invoke those skills.

**Read error:** if `error` is non-null, surface it. The brief is already claimed in `picked/`;
either `release` it back (`worktrail-work-queue release "<id>" --by "$GO_DISPATCH_ID"`) or stop —
do not start a pipeline.

---

### Step 5 — Classify new vs change

After repo resolution and dashboard restore, classify the claimed brief against the current
spec tree:

```bash
# A repo may hold a devkit docs/specs/ tree, an OpenSpec openspec/ tree, or
# both (mid-migration) -- scan whichever exist, same idiom as #overlap-check,
# so an OpenSpec-format repo's own specs are never invisible to this matcher.
EXTRA_SPECS_ROOT_ARGS=()
[ -d "$REPO/openspec/changes" ] && EXTRA_SPECS_ROOT_ARGS+=(--extra-specs-root "$REPO/openspec/changes")
[ -d "$REPO/openspec/specs" ] && EXTRA_SPECS_ROOT_ARGS+=(--extra-specs-root "$REPO/openspec/specs")
worktrail-classify-handoff "<claimed-path-from-step-3>" \
  --specs-root "$REPO/docs/specs" "${EXTRA_SPECS_ROOT_ARGS[@]}" --json
```

The helper returns:

```json
{
  "hint": "C|F|G|null",
  "change_kind": "new|delta|bugfix|null",
  "target_spec": "003-example|null",
  "recommended_route": "A-J|null",
  "candidate_specs": [{"spec_id": "...", "score": 12, "signals": ["..."]}],
  "signals": ["..."]
}
```

Feed `hint` to the normal classifier as `HANDOFF_ROUTE` only when it is non-null. If the
hint is `F` or `G`, route to the selected route's change-spec path instead of the `new`
pipeline. If there are multiple plausible `candidate_specs` or the top candidate conflicts
with a brief `target-spec`, ask one clarification question with the top candidate prefilled.
The brief hints are strong evidence, not authority; worktree files and current specs win.

### Step 6 — Run the selected pipeline

Hand off to the existing `new` pipeline with `feature_idea`, `constraints`, and the resolved
`repo`/`base_branch` as inputs when the selected route is new planning/implementation
(see `#brainstorm-template`, `#spec-worktree-setup`). For `F`, enter Route F at the
`/opsx:propose` step. For `G`, enter Route G at the same `/opsx:propose` step —
OpenSpec has no bugfix/delta authoring split; the route differs, the command does not.

The handoff-seed sub-flow supplies inputs and route evidence; route playbooks still own
execution.

---

### Step 7 — Mark done

After the `new` pipeline completes (orchestrator PR work done + sync run — see
`#sync-before-teardown`):

```bash
worktrail-work-queue done "<id-or-filename>" --implementation-complete --run "$RUN" --by "$GO_DISPATCH_ID" --json
```

Always pass `--run "$RUN"` on an `--implementation-complete` closure — it verifies the
named run record actually reached a PR-owning `finish()` state (a recorded `pull_request`
plus `completed_and_merged`/`completed_pr_open`/`completed_awaiting_human_approval`) instead
of trusting closure prose; omitting it falls back to requiring a PR reference in `--note`
(see `work_queue.py`'s "Implementation closure evidence gate"). This stamps `status: done`
in `picked/` (the file stays as a kept log). For
planning-only Route-C runs, use `--planning-only`; an unqualified completion
is rejected as `awaiting_implementation_decision`.
Report: "Handoff brief `<filename>` marked done."

If the pipeline is aborted before completion, **leave the brief in `picked/`** (advisory — it
keeps `status: picked`; `release` it only if you want another session to retry it).

---

### Invariants

- All queue moves go through `work_queue.py` (`claim` / `done` / `release`) — never hand-`mv`,
  commit, delete, or edit brief contents. The atomic rename is the concurrency guarantee.
- No pipeline is ever started when the queue is empty or when the user declines a confirmation.
- `go` stays a thin router: `work_queue.py` owns the queue lifecycle; `handoff_seed.py` only
  maps a claimed brief to the brainstorm seed.

---

## Orchestrator pre-launch gates {#orchestrator-gates}

Run in order before launching the orchestrator. `$SPEC_ROOT` = `$WT` for the `new` and
`modify` pipelines; `$REPO` for `implement`.

### Already-implemented check {#already-implemented-check}

Before launching the orchestrator (and, for a brief-sourced dispatch, before Phase 6's run
record — `worktrail-go/SKILL.md` Phase 5.5), answer one question **by reading the source**:
is the pending work already implemented on the base branch?

There is no script for this and deliberately so. The three mechanical guards that used to run
here — a spec-age/checkbox-ratio heuristic, `check_change_staleness.py`, and
`check_brief_staleness.py` — inferred "already done" from commit-message and touched-path
probes, a proxy for the thing that actually matters. Read the code instead.

**Procedure.** Take the pending work's own description — an OpenSpec change's unchecked
`tasks.md` entries, a devkit spec's pending `TASK-*.md` files, or a claimed brief's `focus`
prose — and for each item, look for the thing it asks for in the working tree:

- Named files/symbols: `rg` for them from the repo root, no path or file-type filter.
- Behavior with no obvious symbol: read the module the work would live in.
- Cross-file reference questions: `gitnexus` query/impact against the base branch.

Then branch:

- **Everything the pending work asks for is already present in the source** — do not launch
  the orchestrator. Surface what you found (file:line per item) and ask with the
  **`AskUserQuestion` tool**: "The pending work appears already implemented on base (evidence
  above). How would you like to proceed?", options: **Close as already-delivered** (a brief:
  `worktrail-work-queue done ... --implementation-complete --note "..."`; an OpenSpec change:
  the dashboard's `close-stale` action, `worktrail-go/SKILL.md`'s Phase 2 action table), or
  **Proceed anyway** (the match is superficial; launch the orchestrator).
- **Anything is missing, or you cannot tell** — continue to implementation. Silence is the
  default: never report "checked and clean", and never delay a dispatch on a partial match.

`$AUTO_MODE=true`: no ask. Judging whether prior work actually satisfies this scope is a call
about prior work no unattended run may make. When — and only when — the evidence says
everything is already present, finish `blocked_product_decision` quoting the file:line
evidence, per `#auto-mode-ask-fallbacks`. Anything short of that proceeds to implementation as
normal.

### Precheck DAG validation {#precheck-gate}

```bash
worktrail-live precheck --repo "$SPEC_ROOT" docs/specs/$SPEC_ID
```

On a non-zero exit, print the precheck output, then ask with the
**`AskUserQuestion` tool** (it is a tool call, not a shell command):
question "Precheck found issues with the spec task DAG. How would you like to
proceed?", options:

1. **Proceed anyway** — the flagged tasks need re-implementation; launch the orchestrator.
2. **Mark flagged tasks completed and sync first** — stop; flip the statuses, run sync, retry.
3. **Abort** — stop for manual investigation.

`$AUTO_MODE=true`: no ask. Judging whether flagged tasks need re-implementation
or a status flip is a call about prior work no unattended run may make — never
pick option 1 or 2 silently. Finish `blocked_product_decision` quoting the
precheck output, per `#auto-mode-ask-fallbacks`.

`live.py precheck` also checks `run-<spec>.status.json`. If the prior run is
`fanout_failed`, do not silently re-launch `full-real`: surface the failed or
blocked task ids from the sidecar/journal and recover that stuck run first.

---

Orphaned-worktree recovery after a cancelled/crashed orchestrator run: see
`worktree-cleanup.md` (same classify-confirm-prune procedure).

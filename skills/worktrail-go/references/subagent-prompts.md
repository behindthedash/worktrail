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
providers. When the nested Codex app-server cannot write its normal state directory,
set `WORKTRAIL_CODEX_HOME` to a persistent writable directory or pass
`--codex-home <path>`; the adapter applies that override only to the Codex child.

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

Routes that need `AskUserQuestion` mid-flow (C, I) use the in-session
`Skill("worktrail-sdd-workflow", args="...")` call, never a subprocess —
a headless worker cannot surface interactive prompts to the parent session.
Subprocess dispatch applies to the background routes (D/F/G/H) with the bounded
poll below.

### Bounded poll contract (background routes D/F/G/H)

After spawning a background subprocess (routes D, F, G, H), go Phase 7 polls the shared run record
for completion using `poll_run.py`. The poll MUST respect a hard 10-minute ceiling to prevent
unbounded waits.

**Poll parameters:**
- **Interval:** 30 seconds between checks
- **Max iterations:** 20 (10 minutes ÷ 30 seconds)
- **Exit condition:** The run record contains a `finish` entry at any completion state (paths: `$RUN/finish`)

**Exit behavior:**
- **Poll exit 0 (subprocess complete within ceiling):** The subprocess finished; run record contains
  `finish` entry with completion state and optional PR URL. go prints completion summary and exits.
- **Poll exit non-0 (ceiling reached):** No `finish` entry after 20 iterations. go prints
  "Subprocess still running — check run record at $RUN" and exits cleanly (non-error) so the user
  can check the run record later or re-invoke to resume polling.

**Run record structure:** The poll reads the run record JSON file at `$RUN` and checks for a `finish` key
or `final_status` key (both indicate completion). The subprocess and parent go session share the same
run record file; the subprocess writes `finish` when it completes (via `run_record.py finish --status <state>`).

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
resurfaces in a different shape; a narrower `--allowedTools`-scoped permission profile (instead of
full `bypassPermissions`) was considered as an alternative fix that could restore subprocess
isolation under Auto Mode, but deliberately deferred — it needs its own authorized live repro before
being trusted, which is out of scope for this hardening pass.

---

## Repo resolution {#repo-resolution}

Run:
```bash
worktrail-resolve-repo --start "$PWD" --hint "<user request text>" --json
```

Act on `mode`:
- **in-repo** → use `repo` as `$REPO`
- **derived** → use `repo`, state the pick ("Using `<name>`")
- **single-candidate** → use it, state the pick
- **ambiguous** or **none** → run the multi-repo overview: `worktrail-dashboard --repos "$PWD"`. It lists every candidate with active-spec count. Present via `AskUserQuestion` — lead with repos that have active specs, labelled with them (e.g. "gracefully-giving-back — 1 active: 003-payments"). If no git repos found, ask for the path and stop.

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

**If overlap is found:** Present `AskUserQuestion` before continuing (see `#overlap-menu`).
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

Run inline in the change worktree — this is a slash command, not an `Agent` dispatch,
so there is no subagent prompt to template:

```
/opsx:propose "<the request, verbatim from the brief or the user>"
```

**Claude Code hosts — entering the worktree for this call:** a slash command runs in
the session's actual working directory, not a `-C`-scoped path, so there is no way to
target `$WT` for this one call except moving the session there. Use
`EnterWorktree({path: "$WT"})` immediately before it, then `ExitWorktree({action:
"keep"})` immediately after — do not run unrelated commands while still inside the
worktree session. `EnterWorktree`'s own isolation guard blocks a later `git -C
<other-path>` call (e.g. against `$REPO` or a different worktree) made while the
session is still pinned inside `$WT`, so lingering there breaks later steps like
`#sync-before-teardown`. Other hosts invoke the slash command directly with no
equivalent step.

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
# falls through repo-local routing -> the machine-wide ~/.go/routing.yaml ->
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
`~/.go/routing.yaml`, else the flat `fallback_agent_cli` key — see `resolve_routing()` in
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
- The pipelined engine is `full-real`'s DEFAULT since v0.9.1 — `--pipeline` is a
  no-op affirmation and may be omitted. Include a non-zero `--run-budget`. The
  legacy serial scheduler survives only behind the DEPRECATED `--sequential`
  escape hatch (frozen, no new fixes, removal in v1.1 — see
  `openspec/changes/scheduler-consolidation/`); never use it except when a user
  explicitly asks for serial debug mode.
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
  `docs/specs/go-policy.yaml` (loaded in Phase 4), pass it through as
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
  `worktree_bootstrap_cmd` is set in `docs/specs/go-policy.yaml` (loaded in Phase 4), pass
  it through as `--bootstrap-cmd "<command>"`. The orchestrator runs it in each fanned-out
  task worktree right after `git worktree add`, before spawning the worker — so
  implement/fix/review workers don't each rediscover and reinstall the base checkout's
  gitignored `node_modules` mid-task (task worktrees branch off the base commit and start
  without it; this is the per-task analogue of the spec worktree's own
  `#spec-worktree-setup` bootstrap step). It is **non-fatal** — a failed install is logged
  and the worker still self-installs — so a flaky registry never quarantines a task. Omit
  the flag when the policy key is unset (repos with no install step are unaffected).
- **Migration-group isolation (opt-in, from policy):** when `migration_path_patterns`
  is set in `docs/specs/go-policy.yaml` (loaded in Phase 4), pass each pattern through
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
  `docs/specs/go-policy.yaml` (loaded in Phase 4), pass it through as
  `--max-workers <n>` — it bounds how many task worktrees with live agent workers run
  concurrently. An explicit width in the user's invocation wins over the policy value.
  Omit the flag when the policy key is unset (the orchestrator's own default, 3, applies).
- **PR pacing (opt-in, from policy):** when `pr_pacing_wait_s` is set (> 0) in
  `docs/specs/go-policy.yaml` (loaded in Phase 4), pass it through as
  `--pr-pacing-wait <seconds>`. Before opening each subsequent group PR, the orchestrator
  waits (bounded by this value) for the previous group PR's checks to resolve — or the PR
  to merge — so sibling group PRs don't hit a shared CI runner pool simultaneously; on
  auto-merge repos the previous PR typically lands during the wait, which also gives
  dependent groups a real merged base instead of a moving stacked diff. Best-effort: a
  red, stuck, or check-less PR never blocks integration beyond the bound. Paces both the
  sequential path and `--pipeline` (there it serializes only the integrate+PR-open step;
  verify still overlaps). Omit the flag when the policy key is unset or 0 (PRs open
  back-to-back, today's behavior).
- **Branch-aware merge method (opt-in, from policy):** when `docs/specs/go-policy.yaml`
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

**Step 3 — update knowledge graph inside the sync worktree (inline):**

`/opsx:sync` merges the change's delta specs into `openspec/specs/` (see
`#openspec-sync`); `/opsx:archive` does the same merge as part of archiving, so an
explicit sync is only needed when specs must land before the change is finished.
Perform the post-orchestrator knowledge-graph update inline:

1. Glob both `$SYNC_WT/docs/specs/$SPEC_ID/tasks/TASK-*.md` and
   `$SYNC_WT/docs/specs/$SPEC_ID/changes/*/tasks/TASK-CHG-*.md` — read each file;
   collect tasks with `status: completed` and their `provides:` frontmatter entries.
2. Verify each provides file path exists under `$SYNC_WT/`.
3. Read `$SYNC_WT/docs/specs/$SPEC_ID/knowledge-graph.json` (or start from `{"metadata":{},"provides":[]}` if absent).
4. Merge verified provides into `knowledge-graph.json["provides"]`, deduplicating by `task_id + file` composite key.
5. Update `knowledge-graph.json["metadata"]["updated_at"]` to the current ISO timestamp.
6. Write the updated `knowledge-graph.json` back to `$SYNC_WT/docs/specs/$SPEC_ID/knowledge-graph.json`.

**Step 4 — commit + PR if sync produced changes, with CI-wait gate:**
```bash
git -C "$SYNC_WT" diff --quiet HEAD -- docs/specs/ || {
  git -C "$SYNC_WT" add docs/specs/
  git -C "$SYNC_WT" commit -m "sync($SPEC_ID): update KG and task statuses post-orchestrator"
  git -C "$SYNC_WT" push -u origin "sync/$SPEC_ID"
  # gh derives owner/repo from the remote; no --repo flag needed for the consuming project
  PR_URL=$(gh pr create --base "$BASE" --head "sync/$SPEC_ID" \
    --title "sync($SPEC_ID): post-orchestrator docs update" \
    --body "Updates knowledge-graph.json and task statuses after orchestrator run. Auto-generated." \
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
  switch to a sleep loop and never background an unbounded waiter.

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
  # SPEC_ID="$NNN-$slug"  (slug confirmed via AskUserQuestion if unclear)
else
  # OpenSpec has no numeric-prefix convention — `openspec new change` rejects
  # any name that doesn't start with a letter (verified against 1.6.0: "Error:
  # Change name must start with a letter"), and collision detection is by
  # change-name existence (openspec-propose's own guardrail), not sequence
  # number. Never prefix $slug with $NNN here. If the target repo's own
  # convention still requires an NNN- prefix, do not apply it yet — see
  # #openspec-nnn-prefix; the prefix is added by a post-validate rename, never
  # at worktree-setup time.
  # SPEC_ID="$slug"  (slug confirmed via AskUserQuestion if unclear)
fi
SIBLING_WT_GLOB="$SPEC_ID-spec"
SIBLING_REF_GLOB="refs/heads/spec/$SPEC_ID"
# run the sibling check — #sibling-worktree-check
WT="$REPO-worktrees/$SPEC_ID-spec"
git -C "$REPO" worktree add -b "spec/$SPEC_ID" "$WT" "$BASE"
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
# via AskUserQuestion if unclear
WT="$REPO-worktrees/fix-$SLUG"
git -C "$REPO" worktree add -b "fix/$SLUG" "$WT" "$BASE"
```

If `git worktree add` is sandbox-denied, surface it and stop — don't write on base.
Operate on `$WT` via `git -C "$WT" <cmd>` throughout — never `cd` into it (see the
worktree-lifecycle note above).

After creating the worktree, bootstrap that checkout's local dependencies before
running repo-local tools from it (`npm ci` or the repo's documented equivalent) —
see the note under `#spec-worktree-setup`; do not assume the base checkout's
`node_modules` or generated artifacts are usable from the new worktree.

### Teardown after `new` pipeline completes

Group-branch naming: the live `full` path creates per-group branches as `<run-id>/<group>`
(e.g. `full-1780251916/feature-3`) — keyed on the **run-id** (`integrate.py:175`). The
`<spec-id>/<group>` form is the demo/toy path (`orchestrate.py`) only; a sweep scoped to
spec-id alone is insufficient for the live `full` path.

**Before running any of the teardown below:** confirm the shell's cwd is `$REPO` (or
anywhere outside `$WT`), not `$WT` itself — `cd` back first if an earlier step left you
inside it. See the worktree-lifecycle note above for why.

- **All group PRs auto-merged green:**
  ```bash
  git -C "$REPO" worktree remove "$WT"
  git -C "$REPO" branch -D "spec/$SPEC_ID"   # only after confirmed merge

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
  read-only). It scans the current `docs/specs/` tree plus optional brief hints
  (`change-kind`, `target-spec`) and emits evidence for the normal route classifier.

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

---

### Step 3 — Claim the brief: atomic queue/ → picked/

```bash
worktrail-work-queue claim "<id-or-filename>" --json
```

The claim resolves `<id>` (full filename, stem, unique prefix, or `id` frontmatter) AND
atomically moves it out of `queue/` into `picked/`, stamping `status: picked` + `claimed-at`.
Act on `status`:

| `status` | Action |
|----------|--------|
| `claimed` | Use the returned `path` (now in `picked/`) for Step 4. |
| `already-claimed` with `path` | Use the returned picked path for Step 4. This is the expected path when the generic `go` front door claimed the brief before delegating to `sdd-workflow handoff:<id>`. |
| `already-claimed` without `path` | Another session won the race — re-list (Step 1) and pick another; do NOT seed. |
| `none` | Brief `<id>` not in the queue. List queue briefs newest-first; ask the user to pick or abort. |
| `ambiguous` | `<id>` matches multiple briefs. List the `candidates`; ask to disambiguate, then re-claim. |

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
either `release` it back (`worktrail-work-queue release "<id>"`) or stop — do not start a pipeline.

---

### Step 5 — Classify new vs change

After repo resolution and dashboard restore, classify the claimed brief against the current
spec tree:

```bash
worktrail-classify-handoff "<claimed-path-from-step-3>" \
  --specs-root "$REPO/docs/specs" --json
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
worktrail-work-queue done "<id-or-filename>" --implementation-complete --json
```

This stamps `status: done` in `picked/` (the file stays as a kept log). For
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

Run in order before launching the orchestrator. `$SPEC_ROOT` = `$WT` for the `new` pipeline; `$REPO` for `implement`.

### Stale-spec check {#stale-spec-check}

```bash
python3 -c "
import datetime
from pathlib import Path
from worktrail.router.dashboard import is_stale_spec, spec_creation_date, _count_tasks

spec_dir = Path('$SPEC_ROOT/docs/specs/$SPEC_ID')
repo = Path('$SPEC_ROOT')

if is_stale_spec(spec_dir, repo):
    counts = _count_tasks(spec_dir)
    created = spec_creation_date(spec_dir, repo)
    age_days = (datetime.date.today() - created).days if created else '?'
    total = counts.get('total', 0) if counts else 0
    completed = counts.get('completed', 0) if counts else 0
    print(f'Spec \$SPEC_ID shows {completed}/{total} tasks done but was created {age_days} days ago. This may indicate tasks were implemented outside the orchestrator (missing sync) or the spec was abandoned.')
" 2>&1
```

If stale, surface the warning; then proceed to precheck.

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

`live.py precheck` also checks `run-<spec>.status.json`. If the prior run is
`fanout_failed`, do not silently re-launch `full-real`: surface the failed or
blocked task ids from the sidecar/journal and recover that stuck run first.

---

Orphaned-worktree recovery after a cancelled/crashed orchestrator run: see
`worktree-cleanup.md` (same classify-confirm-prune procedure).

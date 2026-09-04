---
name: router
description: GO v2 front door internals — policy resolution, run records, resume dashboard, and PR label correction for src/worktrail/router
triggers:
  files:
    - src/worktrail/router/**
  keywords:
    - policy.py
    - go-policy.yaml
    - worktrail-go-policy.yaml
    - run_record
    - dashboard
    - automerge_eligible
    - risk label
    - classify
    - scope_review
    - parse_invocation
    - noun-verb grammar
    - triage_keep_limit
    - triage_max_queue_age_days
    - max_parallel_workers
    - compile_max_critical_path_over_width
    - compile_max_same_file_chain
    - review_skip_max_diff_lines
    - pre_commit_cmd
    - land_pr
    - LandRequest
    - LandOutcome
---

You are working on **worktrail's GO v2 front door**: loading repo policy, classifying free-text
requests into routes, tracking run records, and rendering the resume dashboard.

## Domain purpose
`router/` is what `/go`-style front doors consume to decide what a repo allows (policy), which
route a request maps to (classify), what already happened (run_record, dashboard), and whether a
spawned agent's own `gh pr create` came out correctly labeled (pr_labels). Nothing here spawns
agents or writes task files — that is `orchestrator/`'s job.

## Business rules / invariants
- **The front-door grammar is code, not prose: `parse_invocation.py` owns it.** The shape is
  `<front-door> [<repo>] <noun> <verb> [args]` with exactly four nouns (`NOUNS`: `handoff`,
  `spec`, `pr`, `decision`), plus two deliberate bare shortcuts (`<brief-id>` = `handoff start <id>`, and
  `<repo>` = that repo's dashboard) and the read-only `help`/empty invocations. Every
  pre-noun-verb spelling (`auto`, `drain`, `new`, `implement spec <id>`, `continue`, `pr`,
  `brainstorm`, `fix`, `route:<A-J>`, `handoff:<id>`) is **permanent, not deprecated** — `ALIASES`
  lists them and `_canonicalize` rewrites only a bare leading token, so a spelled-out noun-verb form
  is never rewritten twice. Add a new form to `FORMS`/`ALIASES` and the parser together, never one
  without the other.
- **`decision list` / `decision answer <decision-id>` are their own modes (`decision_list`,
  `decision_answer`), not intents or brief lookups.** The parser carries the id in the result's
  `decision_id` field and never tries to resolve it against `queue/` (`brief_status` stays `None`);
  `decision answer` with no id is `mode: help` with `help_topic: decision`. The `worktrail-go`
  skill skips the dashboard entirely for both and runs `worktrail-decision` / the
  `answer-decision.md` procedure directly.
- **A noun with no recognised verb returns `mode: help`, never free text.** Free text containing
  the word `handoff` classifies to Route E at high confidence in `classify.py` (joint-highest-weight
  signal plus a state boost whenever the queue is non-empty), so anything that escapes the parser
  lands on the wrong route silently. `handoff new` must also never reach the intent branch as
  `repo=handoff, intent=new` — the noun-verb match runs above the bare intent words on purpose.
  `pr` is the one exception: bare `pr` (and `pr <text>`) still means the old `pr` intent.
- **The parser translates into the executor's vocabulary, not the user's.** `worktrail-sdd-workflow`
  still speaks `handoff:<id>`, `route:<X>`, and the v1 intent words (`V1_INTENTS`); so `spec
  explore` yields `intent: brainstorm`, and `spec fix` yields `route: F` (the executor has no `fix`
  intent). The result's `canonical` field carries the noun-verb spelling so the skill can print
  `(reads as: worktrail-go <canonical>)` when it differs from `raw`; it is `None` for free text.
- **`worktrail-help`'s reference block is generated from `FORMS` + `ALIASES` via `render_forms()`**
  and pinned by `tests/router/test_parse_invocation.py::test_help_forms_block_matches_the_parser_registry`
  — regenerate it, never hand-edit it. Only `<free text>` may carry `parsed=False`.
- **`policy.py` uses two YAML parsers on purpose.** Most keys go through `parse_policy_yaml`, a
  flat, stdlib-only, one-nesting-level subset. `routing:` and `add_ons:` are re-parsed with real
  `yaml.safe_load` (`_resolve_routing`, `_resolve_add_ons`) because both need arbitrary nesting
  the flat parser would flatten into siblings of the wrong key. Adding a new deeply-nested policy
  key means adding a matching `_resolve_*` real-YAML path, not extending the flat parser.
- **Policy resolution fails closed.** A malformed `add_ons`/`routing`/`automerge` shape falls back
  to the safe default (`{}` / `None` / disabled) with a warning appended to `meta["warnings"]`,
  never widens autonomy. `automerge.max_risk`, `agent_cli`, `fallback_agent_cli`, `agent_model`,
  `max_workers`, `pr_pacing_wait_s`, and `max_parallel_workers` are all validated/clamped the same
  way in `load_policy`.
- **`max_parallel_workers` (default 6, minimum 1) is a ceiling, not a width.** It only applies when
  neither `--max-workers` nor policy `max_workers` is set: `live._resolve_max_workers` then runs
  `min(plan width, max_parallel_workers)` workers instead of a fixed 3 (a width-7 plan ran as three
  serial ticks on 2026-09-02). A set `max_workers` still wins outright. Invalid values
  (non-int, bool, `< 1`) drop to the default with a `meta["warnings"]` entry, same as the other
  integer keys.
- **Integer policy keys follow the `triage_keep_limit` validation pattern.** `triage_keep_limit`
  (default 2) and `triage_max_queue_age_days` (default 14) are the intake-triage escalation
  bounds: a value that is not an `int`, is a `bool`, or is below 1 is forced back to `DEFAULTS[key]`
  with a `meta["warnings"]` entry naming the key. Their consumer is `workqueue/queue_triage.py`'s
  escalation, which reads them with `.get(key, default)` so the policy side and the consumer side
  can land in either order — as of 2026-09-02 the keys are in `DEFAULTS` but no consumer reads
  them yet in this checkout. Copy this loop when adding another bounded-int key rather than
  inventing a new validation shape.
- **Plan-shape and review-skip keys use that same bounded-int loop.**
  `compile_max_critical_path_over_width` (default 2, min 1) and `compile_max_same_file_chain`
  (default 2, min 1) are the plan-shape gates `conductor/compile.py` consumes: a compiled plan is
  rejected when its critical path exceeds `max(width, compile_max_critical_path_over_width)` or a
  dependent chain all touching one file exceeds `compile_max_same_file_chain`.
  `review_skip_max_diff_lines` (default 0 = disabled, min 0) is the fast path `orchestrator/live.py`'s
  `drive()` consumes beside `_review_exempt`: when > 0, a task's first review is skipped once the
  implement report is a verified success under that many added+removed diff lines (test files
  excluded). Invalid values drop to the default with a `meta["warnings"]` entry.
- **`pre_commit_cmd` (default `None`) is the one optional-string policy key.** A non-string,
  non-`None` value is forced to `None` with a warning. Its consumers are `orchestrator/dispatch.py`
  (a worker-prompt hard rule for implement/fix and the ci-fix group prompt) and
  `orchestrator/live.py`/`verify.py` (post-commit amend backstop). `None` means repos without a
  wired command see no behavior change. `onboarding/repo_init.py`'s `detect_pre_commit_cmd`
  seeds it into a fresh `.worktrail/policy.yaml` by scanning `.github/workflows/*.yml|yaml`
  `run:` step lines for `ruff`/`oxlint`/`prettier` (`PRE_COMMIT_CMD_BY_LINTER`, joined with `&&`
  in that order); an existing policy file is never touched.
- **`automerge.enabled: false` does not, by itself, block orchestrator-driven merges.** Only
  `automerge_eligible()` reads that key, and only when an agent follows sdd-workflow's Phase 8
  merge-gate instructions — `orchestrator`'s own `auto_merge()` is a separate code path that
  unconditionally calls `gh pr merge` once CI passes and does not consult this key at all.
- **Run records enforce ten explicit completion states** (`run_record.py` `finish`, §22) — a run
  can never end in vague language. `finish` also code-enforces `no_implementation_without_approval`
  (a route-A run cannot finish on an implementation-completion state without a recorded
  `decisions` entry first) and `pre_pr_gate.py`'s `scope_review_failures()`, unconditional on
  route, exactly once per run regardless of how many group PRs the orchestrator created.
- **Scope-review entries are append-only; the gate judges only the latest entry per `--item`.**
  `scope_review_failures()` collapses `status | item | detail` entries to the last one per item
  before checking `blocked` / `out-of-scope`, so re-recording an item supersedes an earlier
  mis-phrased or blocked entry (and a later `blocked` entry can equally regress a prior
  `complete`). Malformed entries still fail regardless of position.
- **An `out-of-scope` reason must begin with a prefix from `run_record.OUT_OF_SCOPE_REASON_PREFIXES`**
  (`different purpose:` / `user approved:`). `cmd_scope_review` rejects any other reason at write
  time with `SystemExit`, and `scope_review_failures()` re-checks the same tuple at gate time so a
  hand-edited record is still caught. Extend the tuple, never the two call sites separately.
- **`finish` best-effort-applies the `go:risk-*` PR label correction** (`pr_labels.
  ensure_pr_risk_label`) whenever the record carries a `pull_request` — a spawned headless agent's
  raw `gh pr create` is never reachable by the interactive Claude Code PreToolUse
  label-enforcement hook (Codex/OpenCode have no equivalent mechanism at all).
- **PR label writes use the REST endpoint, not `gh pr edit --add-label`.** `gh pr edit`'s GraphQL
  mutation also touches classic-Projects fields and fails outright on a repo/org with a legacy
  Projects (classic) board still attached (confirmed live 2026-08-07). `_current_pr_labels`'s
  read-only `gh pr view` call is unaffected and stays as-is.
- **`dashboard.py` detects spec artifacts by exclusion + content, never one strict filename
  pattern.** Known auxiliary files (`user-request.md`, `decision-log.md`,
  `traceability-matrix.md`, etc.) are named explicitly; any other top-level `*.md` is a candidate
  spec doc, with a dated filename winning when several exist. A `## Clarifications` heading is
  NOT the resolution-gate signal — only ~22/46 real specs carry one; the gate keys on unresolved
  `[NEEDS CLARIFICATION: ...]` markers in the spec body instead.
- **`skill_dispatch.evaluate_single_brief()`/`apply_single_brief_verdict()` resolve a bare `repo:`
  value to an on-disk checkout before using it as `cwd`.** A brief's `repo:` frontmatter is almost
  always a short name (e.g. `"worktrail"`), not a path. `evaluate_single_brief` runs it through
  `dashboard._resolve_repo_dir(resolved_repo, repos_root)` to build `group_cwd` whenever an
  explicit repo is resolved and no `cwd` override was passed — previously it passed the bare name
  straight through, which `subprocess.run` then rejected as a nonexistent relative `cwd` when
  invoked from outside the target repo (e.g. `worktrail-go`'s normal cwd). `apply_single_brief_verdict`
  now takes and forwards a `repos_root` parameter to `queue_triage.apply_verdicts()` so a
  `propose-change`/`fold-into-change` verdict's own `_resolve_repo_dir()` call can find the repo
  too — both call sites must resolve consistently, not just the evaluate path (fixed together
  2026-09-03).
- **`land_pr.py` is the one shared PR-landing pipeline every PR-opening call site composes with,
  instead of each reimplementing a subset of it.** `land_pr(LandRequest) -> LandOutcome` runs, in
  order: commit pending work (refuses `dirty_tree` without a `commit_message`), the compile-marker
  gate for every OpenSpec change whose `tasks.md` changed, the preflight gate + label read-back,
  push, find-or-create the PR (shared with `orchestrator/integrate.py`'s group-PR step), CI watch
  to a terminal outcome (transient-infra reruns, no-checks-yet grace period, `code_defect` vs
  `ceiling` classification via `CI_PATCH_ITERATION_CEILING`), the merge-state guard, the
  review-thread gate, then finish (or, in checkpoint mode, append a decision to) the run record.
  Refusal (steps 1-4) never touches the remote; a push or PR-create failure past that point is
  reported as `outcome="ceiling"`, never `refused`, because the remote is already mutated (an
  untouched-remote promise `refused` would misrepresent). Registered as `worktrail-land-pr`
  (`worktrail.router.land_pr:main`). See the module's own docstring for the authoritative ordered
  step list — it exists because PR #902 shipped 112 gate-verified files that were never committed
  before `git push`, a gap each prior call site (`queue_triage.py`, `drain.py`,
  `orchestrator/integrate.py`, and the agent-executed sdd-workflow/`worktrail-go` prose) closed
  differently or not at all.

## Critical files
- `router/parse_invocation.py` — the `worktrail-go` Phase 1 grammar (`parse`, `FORMS`, `ALIASES`,
  `NOUNS`, `MODES`, `render_forms`); never shells out or writes, reads `queue/` only when a folder
  is supplied, and delegates repo names to the caller (`--repos`) and brief-id resolution to
  `work_queue.resolve()` so nothing here becomes a second implementation
- `router/policy.py` — `load_policy()`; the single source of truth for a repo's resolved GO policy
- `router/run_record.py` — `finish()`'s ten-state enforcement and its two code-enforced gates;
  `cmd_scope_review` write-time reason validation and `OUT_OF_SCOPE_REASON_PREFIXES`
- `router/pre_pr_gate.py` — `scope_review_failures()`, the latest-entry-per-item scope gate
  that `finish` calls
- `router/pr_labels.py` — the one place that issues the `go:risk-*` REST label correction; both
  `drain.py` and sdd-workflow's Phase 8 call into it rather than reimplementing it
- `router/dashboard.py` — pure file inspection (no git, network, or agents); spec lifecycle stage
  and next-action detection; also the source of `_resolve_repo_dir()`, which `skill_dispatch.py`'s
  single-brief-triage path uses to resolve a bare `repo:` value to an on-disk checkout
- `router/land_pr.py` — `land_pr()`, `LandRequest`/`LandOutcome`; the shared
  commit/compile-marker/preflight/push/PR/CI-watch/merge-guard/review-thread-gate/finish pipeline
  every PR-opening call site should compose with instead of reimplementing a subset

---
**Last Updated:** 2026-09-04

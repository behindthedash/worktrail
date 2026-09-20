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
    - RISK_SIGNALS
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
    - close_stale_openspec
    - flip_and_archive
    - capacity-gate
    - retry_after
    - smoke_flakes
    - smoke_flake_aggregate
    - smoke_flake_selfcheck
    - reconcile_pr_labels
    - load_run_index
    - base_slug
    - automerge_preflight
    - required_checks_gate
    - owner_repo_from_git
    - push_remote_name
    - remote.pushDefault
    - cluster_detect
    - focus-overlap
    - MIN_FOCUS_TOKENS
    - OVERLAP_THRESHOLD
    - risk_judgment
    - judge_risk
    - compose_tier
    - parse_answers
    - blast_radius
    - red line
    - TYPESAFE_API_KEY
    - risk_judgment_enabled
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
- **`RISK_SIGNALS`' `authz` pattern carries a `(?<![-@])` guard so it cannot fire inside a
  compound token.** `\b` treats the hyphen in a package name like `better-auth` (or the `@` in
  `@auth/core`) as a word boundary, so merely naming the dependency scored `high:authz`.
  Confirmed live 2026-09-10 (run `go-20260910-085556`, devops PR #366): a config-only PR
  classified `risk=high`, exceeded that repo's policy `max_risk=medium`, and was labeled
  `go:no-automerge`, forcing a hand merge. This is a word-boundary artifact inside one token,
  **not** the diff-blindness the `RISK_SIGNALS` table comment deliberately fails loud on.
  Underscore-joined identifiers (`require_auth`) were already excluded (`_` is a word character);
  `auth-related` still matches, because the guard looks only at what *precedes* the word.
- **`classify_risk()` has two backends over one `RISK_ORDER` mapping, and the keyword table is
  both the default and the fallback.** `classify_risk(text, *, judgment=False)` keeps its shape;
  `_classify_risk_by_keyword()` is the extracted `RISK_SIGNALS` body, and
  `risk_judgment.judge_risk()` is the second backend — one request asking for a 4-level
  blast-radius score plus four red-line nouls (`irreversible_data_loss`,
  `weakens_access_control`, `moves_money`, `disables_a_safeguard`), never for a tier, a label, or
  a merge decision. The service observes; the pure, offline `compose_tier()` decides: the score
  sets the baseline tier (rounded and clamped onto `_TIERS`), a hard red line at or above
  `RED_LINE` (0.70) forces `critical`, the safeguard red line forces `high`, and a red line is a
  **floor** — it can only raise a baseline, never lower one, because a change can read "ordinary"
  on blast radius and still destroy data. Labels keep the `<tier>:<label>` shape
  (`medium:blast-radius`, `critical:moves-money`), so a judged tier is readable as such with no
  second field. Why it exists: the keyword table tiers on word presence alone, and measured
  2026-09-19 on `tests/fixtures/risk_probes.json` it got 8/24 exact tiers and 10/24 auto-merge
  gate decisions — rating prod data deletion, an auth fail-open, refund re-submission and
  "make a required check non-blocking" *mergeable*, and seven trivial doc/test changes
  *critical* — against the judgment's 20/24 and 24/24 with zero of either error.
- **The judgment fails safe into the keyword table, always.** `judge_risk()` returns `None` — the
  "use the keyword table" signal — on a missing `TYPESAFE_API_KEY`, an HTTP or transport error, a
  timeout (`TIMEOUT_S`, deliberately short: a tier that arrives after the operator gave up is
  worth less than the immediate fallback), an unparseable body, or an answer set missing a field.
  `parse_answers()` raises `ValueError` on any shape it does not recognise rather than reading a
  missing noul as zero, so a changed or truncated response can never compose a falsely-low tier.
  The fallback errs toward over-gating, which is the safe direction. CI needs no key and no
  network: with the key unset the judgment path is never entered and behaviour is exactly the
  pre-judgment one.
- **`classify()` stays pure by default: `risk_judgment_enabled` defaults to `False`.** Only
  `main()` turns it on, with `--no-risk-judgment` to opt out — the same confinement
  `cited_pr_states`' live `gh` lookup already has — so `classifier_coverage`'s replay and every
  test stay deterministic, free and offline. Enabling it changes only the `risk`/`risk_signals`
  output; route selection, its confidence, and its reason never consult it.
- **A J score built entirely from `_MENTION_ONLY_J_LABELS` (`routing-logic`, `classify-py`)
  alongside a strong CI/config signal (`_CI_CONFIG_RE`: `.github/workflows`, `.github/rulesets`,
  `required status check`, `branch protection`) is damped to zero** — that shape means the
  routing/classifier file is cited as evidence for unrelated CI/branch-protection work, not the
  actual change target (live incident 2026-09-10, brief 20260910-143657/run go-20260910-142322).
  The damp does **not** fire when the same text also matches `_CITED_AS_EXAMPLE_RE` (`cites`,
  `quotes`, `verbatim`, `worked example`, `as an example`, …): a genuine self-referential bug
  report about `classify.py`'s own routing logic that happens to quote another brief's
  CI-config text as a worked example was itself getting wrongly demoted from J to F (brief
  20260910-152514: J=9 undamped vs F=6, confirmed reproducible). A citation cue means the
  CI/config phrase is quoted evidence, not the report's own change target, so it must not
  trigger the damp. Scoped narrowly to these two evidence-confirmed labels — do not widen
  `_MENTION_ONLY_J_LABELS` without its own confirmed false-positive.
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
- **`capacity-gate --retry-after` is validated as ISO-8601 and stored verbatim, never slug-sanitized.**
  `cmd_capacity_gate` runs it through `_iso_retry_after` (a `datetime.fromisoformat` check that
  raises `SystemExit` on a non-ISO value) rather than `_safe_provider`, which is a slug sanitizer
  and turned `+00:00` into `_00:00` — an offset every downstream `retry_after` consumer
  (`orchestrator/agent_capacity._parse_time`) then failed to parse (fixed 2026-09-06). Only
  `--provider` and `--failure-class` go through `_safe_provider`.
- **`finish` best-effort-applies the `go:risk-*` PR label correction** (`pr_labels.
  ensure_pr_risk_label`) whenever the record carries a `pull_request` — a spawned headless agent's
  raw `gh pr create` is never reachable by the interactive Claude Code PreToolUse
  label-enforcement hook (Codex/OpenCode have no equivalent mechanism at all).
- **PR label writes use the REST endpoint, not `gh pr edit --add-label`.** `gh pr edit`'s GraphQL
  mutation also touches classic-Projects fields and fails outright on a repo/org with a legacy
  Projects (classic) board still attached (confirmed live 2026-08-07). `_current_pr_labels`'s
  read-only `gh pr view` call is unaffected and stays as-is.
- **`reconcile_pr_labels.load_run_index()` now reads records through `run_record._load_lenient`,
  not the raising `_load`** — a single unreadable/malformed record (hand-edited outside
  `run_record.py`'s own renderer) is skipped with a `WARNING: skipping unreadable run record: ...`
  line on stderr instead of aborting the whole scheduled sweep. Same tolerance policy `_load_lenient`
  already applies to `active-conflicts` and the other directory-wide `run_record.py` scans (fixed
  2026-09-12).
- **`check_deferred_work_handoff.load_deferred_work_entries()` and
  `check_durable_artifact_capture_gate.find_planned_run_records()` — the two Stop-hook check
  scripts — now surface that same `_load_lenient` warning too, instead of discarding it.**
  Both previously unpacked `_warning` and threw it away, so a malformed run record failed silently
  with no signal that the check had skipped data. Both now print
  `WARNING: skipping unreadable run record: <detail>` to stderr and skip the record, matching the
  tolerance-with-visibility policy above (fixed 2026-09-12).
- **`dashboard.py` detects spec artifacts by exclusion + content, never one strict filename
  pattern.** Known auxiliary files (`user-request.md`, `decision-log.md`,
  `traceability-matrix.md`, etc.) are named explicitly; any other top-level `*.md` is a candidate
  spec doc, with a dated filename winning when several exist. A `## Clarifications` heading is
  NOT the resolution-gate signal — only ~22/46 real specs carry one; the gate keys on unresolved
  `[NEEDS CLARIFICATION: ...]` markers in the spec body instead.
- **The dashboard JSON payload's `smoke_flakes` key is always present, never absent.**
  `smoke_flake_aggregate(repos)` calls `smoke_flake_selfcheck.check_repo()` once per in-scope repo
  (both `--root` single-repo and `--repos` multi-repo mode), tags each entry with its repo name,
  and merges them into one `{"entries": [...]}` keeping the detector's ordering (count desc, suite
  asc), so a consumer can read the key without an existence check. The aggregate is passive: any
  failure yields `{"entries": []}` and the `rendered` smoke-flake section is simply omitted — it
  never breaks the dashboard. `smoke_flake_selfcheck` is imported unconditionally (the
  detector-not-yet-shipped `try/except ImportError` fallback is gone now that the module ships).
  The entry shape and the recency window are documented in
  `skills/worktrail-go/references/dashboard-render.md`.
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
  differently or not at all. As of 2026-09-05 `drain.py` (`_land_remediation_pr`),
  `close_stale_openspec.py`, and `workqueue/queue_triage.py` all compose with it.
- **`_push()` always pushes with an explicit `HEAD:<branch>` refspec** (`git push -u <remote>
  HEAD:<branch>`), never a bare `git push` to the configured upstream: a branch created via
  `git worktree add ... -b <branch> origin/dev` tracks `origin/dev`, so a bare push targeted the
  wrong remote branch (repro 2026-09-05, gracefully-giving-back PR #716). It no longer runs the
  `rev-parse @{u}` upstream lookup at all. On a genuine `"push"` refusal it appends git's stderr
  (falling back to stdout) to an optional `detail_out: list[str]` out-param, and `land_pr()`
  surfaces that as `LandOutcome.detail` instead of `null` — an out-param rather than a widened
  return type so existing callers/mocks of `_push(repo, branch, remote, runner)` keep working. A
  timeout/`OSError` still returns `"push_ambiguous"` with nothing appended. Regression tests live
  in `tests/router/test_land_pr_push_refusal.py`, deliberately not in `test_land_pr.py`.
- **Every post-PR-open `gh` call in `land_pr.py` is scoped to the push-target repo via `base_slug`,
  never left to `gh`'s bare-number resolution.** `_gh(..., base_slug=)` appends `-R <base_slug>`
  (the slug `_push_target()` returns) when set; `_watch_ci`, `_checks_registered`, `_log_excerpt`,
  `_pr_is_merged`, `_merge_state_guard` (including its `gh run rerun`), and `_review_thread_gate`
  all take and thread it. On a `remote.pushDefault=fork` checkout `origin` is the upstream, so a
  bare PR number or run id resolved against the upstream's same-numbered — and often long-merged —
  PR, and `land_pr()` reported `completed_and_merged` / `"merged externally"` while the fork PR
  was still OPEN (live 2026-09-18, behindthedash/aspens PR #15 vs upstream `aspenkit/aspens` #15).
  `_review_thread_gate` splits the slug into `owner`/`name` and passes them to
  `check_review_threads.check`, which otherwise derives them from `origin`. With no
  `pushDefault`, `base_slug` is `None` and calls are unchanged. Any new PR- or run-scoped `gh`
  call added to `land_pr.py` must take `base_slug` too.
- **`automerge_preflight` reads the remote the PR will actually be opened against, and refuses
  rather than falling back to another one.** `required_checks_gate()` resolves the target remote
  once via `push_remote_name()` (`remote.pushDefault`, else `origin`) and threads it into
  `owner_repo_from_git(..., remote=)`; both also accept an explicit `remote` override. A selected
  remote that does not exist returns `None` — the old fallback to `origin` answered about a
  *different repository*, which on a fork layout is the read-only upstream (behindthedash/aspens
  PR #26, 2026-09-18: the gate read `aspenkit/aspens`'s `allow_auto_merge=false` and put
  `go:no-automerge` on a PR that was never going to be opened there). The refusal reason names
  the remote it tried, and `is_preflight_query_error()` stays False for it — an unresolvable
  remote is a confirmed state, not a transient read failure. The orchestrator's adapter
  (`verify.Verifier._preflight_runner`) rewrites **every** `git` call to `git -C <repo> ...`, not
  just `git remote get-url`: the verifier's runner executes from its own neutral cwd, so an
  unpinned `git config --get remote.pushDefault` read would answer for that directory and
  silently re-select `origin`.
- **`flip_and_archive`'s default (`task_ids=None`) targets *every* task id, not just the pending
  ones.** The whole point of this module is bookkeeping drift, and its purest case is a change whose
  `tasks.md` is already 100% `[x]` but was never archived. A pending-only default left both
  `flipped` and `already_checked` empty for that change, so the "nothing to flip" guard refused to
  archive exactly the case it exists to close. Already-checked ids land in `already_checked` rather
  than being skipped, so the default path still archives a fully-checked change.
- **`close_stale_openspec.py` now lands the PR itself.** `main()` requires `--base` and `--run`;
  after a successful `flip_and_archive` it calls `land_pr(LandRequest(route="E", risk="low",
  title="chore(<change-id>): close stale bookkeeping", commit_message="chore(<change-id>):
  archive completed change", ...))`, merges `asdict(outcome)` into the result under `"landing"`,
  and maps the outcome to the exit code (`landed`→0, `refused`→2, `code_defect`/
  `review_threads_blocking`→3, `ceiling`→4, anything else→1). A `flip_and_archive` error still
  exits 1 without ever calling `land_pr`. The `worktrail-go` close-stale row is one integrated
  invocation now (`worktrail-close-stale-openspec ... --base "$BASE" --run "$RUN" --json`), not a
  separate `worktrail-land-pr` step. Confirming a task is truly shipped remains the agent's call.
- **`cluster_detect.py`'s brief-to-brief focus-overlap signal abstains when either side carries
  fewer than `MIN_FOCUS_TOKENS` (10) distinct tokens.** The overlap coefficient divides by the
  *smaller* token set, so a very short focus text is trivially a near-subset of any longer brief
  and clears `OVERLAP_THRESHOLD` on shared boilerplate alone: on
  `tests/fixtures/classifier_corpus.json` (as it stood before the 2026-09-19 regeneration) the 5-token focus `canonical checkout drift: <repo>`
  scored 0.60 against five unrelated briefs, and 52 of the 112 pairs the threshold flagged
  involved an item that thin. `_focus_overlap()` is the guarded wrapper carrying the floor and is
  what `_signal_matches` and `_llm_gate_score` call, so a thin pair also never reaches the LLM
  verification band and spends a call on noise; `_overlap_coefficient()` stays the raw
  mathematical quantity for callers measuring something other than two briefs' focus text
  (`create_handoff`'s spec-slug labels, `_target_task_edges`' task lines). A brief below the floor
  is **not** excluded from clustering — `duplicate-slug`, `same-target-spec`, `related-link` and
  `blocked-by` all still connect it; only the "these two read alike" signal abstains.

## Critical files
- `router/parse_invocation.py` — the `worktrail-go` Phase 1 grammar (`parse`, `FORMS`, `ALIASES`,
  `NOUNS`, `MODES`, `render_forms`); never shells out or writes, reads `queue/` only when a folder
  is supplied, and delegates repo names to the caller (`--repos`) and brief-id resolution to
  `work_queue.resolve()` so nothing here becomes a second implementation
- `router/classify.py` — `classify_risk()` and the `RISK_SIGNALS` table (the `authz` pattern's
  `(?<![-@])` compound-token guard lives here, now inside the extracted
  `_classify_risk_by_keyword()`); also `classify()`'s J-damping guard
  (`_MENTION_ONLY_J_LABELS`, `_CI_CONFIG_RE`, `_CITED_AS_EXAMPLE_RE`) and the default-off
  `risk_judgment_enabled` flag that `main()` is the only caller to enable
- `router/risk_judgment.py` — the second `classify_risk` backend: `QUESTIONS` (one blast-radius
  score plus the four red-line nouls), `is_configured()`, `ask()`'s single request,
  `parse_answers()`'s strict shape check, the pure `compose_tier()` mapping onto
  `classify.RISK_ORDER`, and `judge_risk()`'s `None`-on-any-failure contract (its `asker`
  parameter is the injection seam tests use instead of a network fake); the tier policy lives in
  `compose_tier()`, never in the service's answer
- `router/policy.py` — `load_policy()`; the single source of truth for a repo's resolved GO policy
- `router/run_record.py` — `finish()`'s ten-state enforcement and its two code-enforced gates;
  `cmd_scope_review` write-time reason validation and `OUT_OF_SCOPE_REASON_PREFIXES`;
  `cmd_capacity_gate`'s `_iso_retry_after` timestamp validation; `_load_lenient()`, the
  skip-and-warn wrapper around the raising `_load()` that every directory-wide scan (including
  `reconcile_pr_labels.load_run_index()`, `check_deferred_work_handoff.py`, and
  `check_durable_artifact_capture_gate.py`) should use instead of the raising loader
- `router/pre_pr_gate.py` — `scope_review_failures()`, the latest-entry-per-item scope gate
  that `finish` calls
- `router/pr_labels.py` — the one place that issues the `go:risk-*` REST label correction; both
  `drain.py` and sdd-workflow's Phase 8 call into it rather than reimplementing it
- `router/reconcile_pr_labels.py` — `load_run_index()`, the scheduled sweep that maps every PR URL
  under a runs dir to its `{risk_level, gates, route}`, tolerant of one malformed record via
  `run_record._load_lenient`
- `router/check_deferred_work_handoff.py` — the Stop-hook deferred-work handoff guard;
  `load_deferred_work_entries()` reads `deferred_work` entries via `_load_lenient` and now prints
  its discarded warning to stderr instead of silently skipping a malformed record
- `router/check_durable_artifact_capture_gate.py` — the Stop-hook durable-artifact capture gate;
  `find_planned_run_records()` reads run records via `_load_lenient` and now prints its discarded
  warning to stderr instead of silently skipping a malformed record
- `router/dashboard.py` — pure file inspection (no git, network, or agents); spec lifecycle stage
  and next-action detection; also the source of `_resolve_repo_dir()`, which `skill_dispatch.py`'s
  single-brief-triage path uses to resolve a bare `repo:` value to an on-disk checkout, and of
  `smoke_flake_aggregate()`, which builds the always-present `smoke_flakes` payload key
- `router/smoke_flake_selfcheck.py` — `check_repo()`, the per-repo smoke-flake detector that reads
  recorded flakes out of a repo's run journals (recency-windowed) and that
  `dashboard.smoke_flake_aggregate()` calls once per in-scope repo
- `router/land_pr.py` — `land_pr()`, `LandRequest`/`LandOutcome`; the shared
  commit/compile-marker/preflight/push/PR/CI-watch/merge-guard/review-thread-gate/finish pipeline
  every PR-opening call site should compose with instead of reimplementing a subset; `_push()`'s
  explicit-refspec + `detail_out` contract lives here, as does `_gh()`'s `base_slug` (`-R <slug>`)
  scoping of every post-PR-open call
- `router/automerge_preflight.py` — `required_checks_gate()`, `owner_repo_from_git()`,
  `push_remote_name()`, `is_preflight_query_error()`; the live GitHub-side half of the automerge
  gate and the single resolution point for the *read* side's target remote, so the gate can never
  inspect a different repository than `land_pr` pushes the branch and opens the PR against
- `router/close_stale_openspec.py` — `flip_and_archive()` plus `main()`'s `land_pr` landing and
  outcome→exit-code mapping for the `worktrail-close-stale-openspec` console script
- `router/cluster_detect.py` — the brief-clustering signals (`_signal_matches`, `_llm_gate_score`)
  that `workqueue/create_handoff.py` consumes; `_focus_overlap()` is the guarded focus comparison
  carrying `MIN_FOCUS_TOKENS`, while `_overlap_coefficient()` stays the raw coefficient every
  non-focus caller keeps reading

---
**Last Updated:** 2026-09-20

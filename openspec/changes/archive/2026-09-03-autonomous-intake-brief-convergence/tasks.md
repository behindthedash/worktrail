## 1. Repo inference module (independent lane)

- [x] 1.1 Create `src/worktrail/workqueue/repo_inference.py` with `InferenceResult(repo, rule,
      candidates)` and `infer_repo(focus, repos_root=None)` implementing design D1: known
      repos = direct subdirectories of `repos_root` (default `~/projects`) containing a
      `.git` entry; rule (a) `Repo:`/`repo:` token anywhere (basename match, accepts
      `owner/name`); rule (b) known repo name as a whole word with `(?<![\w-])…(?![\w-])`
      boundaries; rule (c) path tokens from `router.brief_probes.extract_probes()` (strip
      `:line`) existing under exactly one checkout; each rule returns only on exactly one
      distinct repo, zero falls through, two or more returns `repo=None` with `rule` and
      `candidates` set. Returns the absolute resolved checkout path. (Requirement: Brief
      repo is inferred deterministically from its focus)
      files: src/worktrail/workqueue/repo_inference.py, tests/workqueue/test_repo_inference.py
- [x] 1.2 In `src/worktrail/workqueue/create_handoff.py`, replace `_infer_repo_from_focus`'s
      body with a delegation to `repo_inference.infer_repo(focus)` returning `.repo`, falling
      back to the original `_FOCUS_REPO_PREFIX` prefix match only when `repo_inference` found
      no rule at all (an ambiguous match must not be overridden by the prefix guess); keep the
      function name and signature so `create_handoff()`/`check_duplicate()` call sites are
      unchanged. Tests in `tests/workqueue/test_repo_inference.py` against a `tmp_path` repos
      root of `git init`'d fixture checkouts plus one non-git `foo-worktrees/` dir: rule (a)
      token mid-sentence and with trailing comma (`Repo: worktrail,`); rule (a) `owner/name`;
      rule (b) whole word, and `datalena-worktrees` not matching `datalena`; rule (c) unique
      path, path with `:line`, `README.md` present in two checkouts → None; two repo names →
      None with `candidates` listing both; rule (a) beats a conflicting rule (b) mention; no
      mention → None; non-git directory is never a known repo. In
      `tests/workqueue/test_create_handoff.py` add one capture-time test: `create_handoff`
      with `repo=None` and a focus containing a `Repo: <fixture>` token records the fixture's
      absolute path in `repo:`, alongside the existing prefix tests. (Requirement: Brief
      repo is inferred deterministically from its focus)
      files: src/worktrail/workqueue/create_handoff.py, tests/workqueue/test_repo_inference.py, tests/workqueue/test_create_handoff.py

## 2. Premise check module (independent lane)

- [x] 2.1 Create `src/worktrail/workqueue/premise_check.py` per design D3: `Needle(kind,
      needle, line)`, `extract_needles(focus)` (kinds `quoted` ≥ 12 chars from `'…'`/`"…"`/
      backticks, `path` via `brief_probes.extract_probes()` with optional `:N`, `command`
      with the allow-list `pytest`, `python -m pytest`, `python3 -m pytest`, `npm test`,
      `go test`, `cargo test`, `ruff check`, `mypy` and non-allow-listed command-looking
      runs recorded but marked unrunnable), `format_premise_block(results)` for the prompt,
      and `run_premise_check(focus, repo_path, *, timeout_s=120)` returning
      `[{kind, needle, confirmed, detail}]` in extraction order: `git grep -nIF` whole string
      then `...`/`…`/`: ` fragments ≥ 12 chars (detail names fragment and `file:line`,
      capped at 5 hits); path existence and line-count check; allow-listed command via
      `subprocess.run(shlex.split(...), cwd=repo_path, timeout=timeout_s,
      capture_output=True)` with `confirmed = returncode != 0`, detail = exit code + last 20
      output lines, `TimeoutExpired` → unconfirmed with a timeout detail; at most one command
      needle is run per brief. (Requirement: Mechanical premise check precedes evaluation)
      files: src/worktrail/workqueue/premise_check.py, tests/workqueue/test_premise_check.py
- [x] 2.2 Tests in `tests/workqueue/test_premise_check.py` against a `git init`'d `tmp_path`
      fixture: extraction of each kind from the motivating focus (quoted log line, `Repo:`
      not extracted as a command, `src/worktrail/drain/drain.py` as path); whole-string hit;
      fragment fallback confirms `no TASK-*.md found` when the full quoted line is absent
      and the detail names the fragment and file; no hit → unconfirmed; path present /
      absent / `:N` beyond file length; allow-listed `pytest` needle runs with `cwd` =
      fixture and a non-zero exit confirms while exit 0 does not (patch `subprocess.run`);
      `rm -rf …` is never passed to `subprocess.run` and is recorded unrunnable;
      `TimeoutExpired` → unconfirmed with timeout detail; empty focus → `[]`;
      `format_premise_block` renders `(none)` for `[]`. (Requirement: Mechanical premise
      check precedes evaluation)
      files: tests/workqueue/test_premise_check.py

## 3. Policy keys for escalation limits (independent lane)

- [x] 3.1 In `src/worktrail/router/policy.py` add `triage_keep_limit: 2` and
      `triage_max_queue_age_days: 14` to `DEFAULTS` with a comment block mirroring
      `max_active_changes` (consumed by `workqueue/queue_triage.py`'s escalation, which
      reads them with `.get(key, default)` so either side can land first), and extend the
      `max_active_changes` integer-validation block to force each new key back to its
      default with a warning when it is not a non-bool integer ≥ 1. (Requirement: Keep
      verdicts are bounded and escalate deterministically)
      files: src/worktrail/router/policy.py, tests/router/test_policy.py
- [x] 3.2 In `tests/router/test_policy.py` add validation tests for both keys: non-int, bool,
      and 0 each fall back to the default with a warning; a valid integer is kept; a policy
      file omitting both keys loads the defaults 2 and 14. (Requirement: Keep verdicts are
      bounded and escalate deterministically)
      files: tests/router/test_policy.py

## 4. queue_triage.py convergence chain and the interactive path — dispatch this group after groups 1 and 2 have merged (OpenSpec carries no cross-group edge; 4.1's decision-consumption/evaluator work imports the modules those groups create). Re-authored 2026-09-03 into three coarser, file-co-scoped tasks after the original nine-task split quarantined 4.1 on an impl/test scope collision; see run go-20260902-095121's decisions for the incident this replaces.

- [x] 4.1 In `src/worktrail/workqueue/queue_triage.py`, build the full convergence chain in
      one co-scoped task (impl and its own follow-on tests were split too finely before and
      collided in the orchestrator's per-task worktrees — keep this file's changes together):
      **(a) keep-streak core** (already implemented on this branch, land as-is, do not
      re-derive): `TriageNote(date, verdict, keep_count)`, `triage_history(path)` (parses
      every `## Triage <date>` section; first non-blank line `verdict: <x>` else `legacy`;
      optional `keep-count:`), `consecutive_keep_count(path)` (trailing run of `verdict:
      keep` notes), `is_recently_triaged()` rewritten on top of `triage_history()` ignoring
      `repo-inferred` notes, `_apply_keep(v, run_date)` appending `verdict: keep` /
      `keep-count: <n+1>` / evidence with `_apply_needs_update()`'s append shape and
      `action: append-triage-note`, and `apply_verdicts()` routing `keep` to it under
      `confirm` (`status: executed`) and to a `status: planned` preview otherwise.
      **(b) escalation matrix:** add `REPO_ASSIGNMENT_QUESTION`, `_escalation_limits(repo)`
      (reads `triage_keep_limit` / `triage_max_queue_age_days` via
      `policy.load_policy(...).get(key, default)` with defaults 2 and 14, defaults for a
      null repo), `escalation_due(path, repo)` returning `keep-limit` / `queue-age` /
      `None`, `Verdict.escalation` (default `None`) and `Verdict.premise_check` (default
      `[]`) fields, `_work_directly_accepted(v)` (design D6) used by both
      `_apply_work_directly()` and `_preview_verdict()` with downgrade notes naming which
      half failed, and `escalate(v, path, repo, candidates)` implementing the D5 matrix
      (rows 1–4, `proposed_change_name` from the brief id with the timestamp stripped and
      validated by `_KEBAB_CASE_RE`, `target_change = <repo>:change:<candidates[0]>`, cap
      re-read via `_propose_change_wip_cap_status()`) applied only when `escalation_due` is
      set and the verdict would resolve to keep at apply time.
      **(c) decision consumption and inventory:** add `consume_repo_decision(path,
      repos_root)` (design D8: answered decision whose question is
      `REPO_ASSIGNMENT_QUESTION` → `dashboard._resolve_repo_dir(answer, repos_root)` →
      `_set_fm_fields` + `decisions.resolve_decision` + `verdict: repo-inferred` note with
      `rule: decision`; unresolvable answer left untouched and returned for reporting);
      `_write_repo_inference(path, result)` appending the `verdict: repo-inferred` note and
      stamping `repo:`; give `group_queue_by_repo(repos_root=None)` the pre-pass over
      null-repo briefs (decision consumption first, then `repo_inference.infer_repo`)
      returning inferred and unresolvable lists alongside the groups; make `inventory()`
      exempt a brief from the dedup skip when `escalation_due()` is set and return no-repo
      briefs that are due in a separate `escalate_without_evaluator` list.
      **(d) evaluator wiring:** `evaluate_group()` runs `premise_check.run_premise_check()`
      per brief when `repo != NO_REPO_KEY`, appends the `format_premise_block()` text under
      each brief line, and returns `premise_by_brief`; `EVALUATOR_PROMPT_TEMPLATE` step 2b
      gains the D6 sentence instructing the evaluator to cite confirmed premise-check
      entries; `parse_verdicts(..., premise_by_brief=None, no_repo=False)` copies premise
      results onto each `Verdict` and converts a would-be `keep` to `needs-decision` with
      `REPO_ASSIGNMENT_QUESTION` when `no_repo`; add `evaluate_briefs(repo, briefs, *,
      agent, cwd, repos_root)` (design D9) chaining premise → `evaluate_group` →
      `parse_verdicts` → `apply_wip_cap_preview` → `escalate`; `cmd_evaluate()` uses it per
      group, verdicts `escalate_without_evaluator` briefs via the matrix directly, and gains
      `--repos-root` (default `~/projects`).
      **(e) reporting:** `compute_run_summary()` adds `escalations.by_reason`,
      `escalations.by_verdict`, and `repos_inferred`; `write_report()` adds `## Repos
      inferred` and `## Escalations` sections and an escalation column in the per-brief
      table; `cmd_evaluate` `--json` and human output print the same counts.
      (Requirement: Keep verdicts are bounded and escalate deterministically; Requirement:
      Apply step never closes a brief without an approved verdict; Requirement:
      Repo-grouped inventory with dedup skip; Requirement: Work-directly converts an intake
      brief into an execution brief; Requirement: Needs-decision files a pending decision
      and keeps the brief queued; Requirement: Brief repo is inferred deterministically from
      its focus; Requirement: Mechanical premise check precedes evaluation; Requirement:
      Evidence-required verdict per brief; Requirement: Verdict file and human-readable
      report)
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py
- [x] 4.2 Tests for 4.1, co-scoped with it (do not split impl from tests across separate
      orchestrator tasks — that split is what caused the original quarantine). In
      `tests/workqueue/test_queue_triage_escalation.py` (new file, reusing
      `QueueTriageTestBase`'s temp-queue shape): `triage_history` parses typed, legacy, and
      `repo-inferred` notes; `consecutive_keep_count` resets on a non-keep note;
      `is_recently_triaged` ignores `repo-inferred`; `_apply_keep` writes the note with
      `keep-count` 1 then 2 and leaves frontmatter byte-identical; preview does not write;
      `escalation_due` by keep limit, by queue age, and neither, including a policy file
      overriding both limits; every matrix row (confirmed premise → work-directly whose
      evidence passes `_work_directly_accepted`; under cap → propose-change with a kebab
      name derived from the id; over cap with candidates → fold into `candidates[0]`; over
      cap without → needs-decision; null repo → needs-decision with
      `REPO_ASSIGNMENT_QUESTION`); escalation not applied to a non-due brief; applied to a
      due `work-directly` failing the acceptance rule and to a due over-cap
      `propose-change`; `_work_directly_accepted` true on regex alone, on premise alone,
      false on neither, at both `_apply_work_directly` and `_preview_verdict`. In
      `tests/workqueue/test_queue_triage_inventory.py` (new file): null-repo brief with
      `Repo: <fixture>` is written back, gets a `repo-inferred` note, and lands in the
      fixture's group in the same `inventory()` call; ambiguous focus stays in
      `NO_REPO_KEY`; an already-inferred brief is not re-noted on a second call; due brief
      bypasses the dedup window while a non-due recently-triaged brief is still skipped;
      due null-repo brief appears in `escalate_without_evaluator`; answered
      repo-assignment decision (filed via `decisions.ask`, answered via `decisions.answer`)
      is consumed, `repo:` written, record moved to `resolved/`, and stamp cleared;
      unresolvable answer leaves brief and record untouched and is reported;
      `parse_verdicts(no_repo=True)` turns a keep, a fallback keep, and a malformed verdict
      into `needs-decision` with `REPO_ASSIGNMENT_QUESTION` and does not touch a
      `stale-close`; `premise_by_brief` lands on `Verdict.premise_check` and round-trips
      through `write_verdict_file` / `Verdict(**entry)`; the prompt block contains
      "Mechanical premise check" and the D6 sentence; `evaluate_group` (with `spawn_agent`
      patched) calls `run_premise_check` only for non-`NO_REPO_KEY` groups; `--json` summary
      and `report.md` carry matching escalation counts and `repos_inferred`. Also update the
      pre-existing `TestApplyVerdicts.test_keep_verdict_...` test in
      `tests/workqueue/test_queue_triage.py`, which asserted the pre-4.1 "keep is always a
      noop" behavior 4.1(a) replaces — this reauthoring already landed that fix on this
      branch as `test_keep_verdict_appends_a_triage_note_instead_of_being_a_noop`, so 4.2's
      worker should verify it still passes rather than re-touch it. (Requirement: Keep
      verdicts are bounded and escalate deterministically; Requirement: Work-directly
      converts an intake brief into an execution brief; Requirement: Repo-grouped inventory
      with dedup skip; Requirement: Evidence-required verdict per brief; Requirement:
      Verdict file and human-readable report; Requirement: Needs-decision files a pending
      decision and keeps the brief queued)
      files: tests/workqueue/test_queue_triage_escalation.py, tests/workqueue/test_queue_triage_inventory.py
- [x] 4.3 In `src/worktrail/router/skill_dispatch.py` (depends on 4.1/4.2):
      `evaluate_single_brief(brief_path, *, repo, agent, cwd=None, repos_root=None)` runs
      the D2 pre-pass on the one brief when `repo` is falsy (decision consumption, then
      inference with write-back) and uses the inferred repo as the group; then calls
      `queue_triage.evaluate_briefs()` instead of hand-wiring
      `evaluate_group`/`parse_verdicts`/`apply_wip_cap_preview`; a due null-repo brief is
      verdicted by the matrix without spawning. Add `--triage-repos-root` (default
      `~/projects`) threaded to it. `--apply-brief-triage` and `--confirm` semantics are
      unchanged. Tests, co-scoped, in `tests/router/test_skill_dispatch.py`
      `SingleBriefTriageTests` / `SingleBriefTriageCliTests`: `evaluate_single_brief` with
      `repo=None` on a brief whose focus names a fixture checkout writes `repo:` back and
      evaluates in that group (`evaluate_briefs` patched to capture the `repo` argument); a
      due null-repo brief returns `needs-decision` with `REPO_ASSIGNMENT_QUESTION` and never
      calls `spawn_agent`; a `keep` verdict applied via `--apply-brief-triage --confirm`
      writes the `verdict: keep` note; the existing dry-run preview test still passes
      without `--confirm`. (Requirement: Interactive pickup of an intake brief triages it)
      files: src/worktrail/router/skill_dispatch.py, tests/router/test_skill_dispatch.py

## 5. worktrail-go skill prose (independent lane)

- [x] 5.1 In `skills/worktrail-go/SKILL.md`: rewrite the Phase 2 "Intake-brief triage gate"
      per design D10 — step 1 adds `--triage-repos-root "${REPOS_ROOT:-$HOME/projects}"` and
      notes that a null-repo brief is inferred and written back; delete step 2's
      `AskUserQuestion` confirmation; step 3 always passes `--confirm`, reports the
      action-log entry, keeps the landing-outcome sentences `shared-pr-landing-pipeline`
      8.1 adds verbatim, and states that a `work-directly` entry with `status: executed`
      continues to Phase 3's `claim` action for the same `$BRIEF_ID` in this invocation
      while every other verdict stops; update the "When to Use" bullet for `worktrail-go
      BRIEF-ID` and the "Queue pickup by ID (untriaged intake brief)" example to say
      "evaluate → apply → report; work-directly continues into claim + dispatch".
      (Requirement: Interactive pickup of an intake brief triages it)
      files: skills/worktrail-go/SKILL.md
- [x] 5.2 In `tests/router/test_skill_prose_enforcement_coverage.py` (or the existing
      SKILL.md assertion test it delegates to), add an assertion that the Phase 2 intake
      gate contains no `AskUserQuestion` between `--evaluate-brief-triage` and
      `--apply-brief-triage`, that the apply block passes `--confirm`, and that it names the
      `work-directly` → Phase 3 continuation. (Requirement: Interactive pickup of an intake
      brief triages it)
      files: tests/router/test_skill_prose_enforcement_coverage.py

## 6. End-to-end regression and verification (tail; runs after every group)

- [x] 6.1 [e2e] Add `tests/workqueue/test_intake_convergence_e2e.py`: build a `git init`'d
      fixture checkout `<repos_root>/worktrail/` containing `src/worktrail/drain/drain.py`
      with the literal `no TASK-*.md found for` line and an empty `openspec/changes/`;
      write a queue brief reconstructing `20260902-080526-worktrail-drain-resume-pass-close`
      verbatim (`repo: null`, the focus from the proposal, `recommended-route: E`); patch
      `spawnlib.spawn_agent` with a stub that asserts its prompt contains a "Mechanical
      premise check" block with a confirmed entry for `no TASK-*.md found`, and returns a
      `work-directly` verdict whose evidence cites `drain.py:1494-1503` by inspection only
      (no test/command, i.e. the real 2026-09-02 evaluator output, which
      `_REPRODUCTION_EVIDENCE_RE` alone rejects); run `queue_triage.main(["evaluate",
      "--queue-dir", ..., "--repos-root", ..., "--out-dir", ...])` then `main(["apply",
      "--verdict-file", ..., "--confirm"])`; assert the brief's `repo:` equals the fixture
      path and a `verdict: repo-inferred` note names rule `a`, `verdict.json` records
      `premise_check` with a confirmed `quoted` entry naming the `no TASK-*.md found`
      fragment and a confirmed `path` entry for `src/worktrail/drain/drain.py`, the
      recorded verdict is `work-directly` with `escalation` `None` (first pass, accepted by
      the combined rule, not by escalation), the apply action-log entry is
      `stamp-frontmatter` / `executed`, the brief carries `seeded-from:
      triage:<today>:direct` and `recommended-route: F`, and a second `inventory()` call
      neither re-infers the brief nor appends a second `repo-inferred` note. Add a sibling
      test in the same file for the null-repo first pass: the same brief with its `Repo:`
      token and path removed (no rule matches) and a stub returning `keep` yields
      `needs-decision` with `REPO_ASSIGNMENT_QUESTION` and, after `apply --confirm`, an
      `awaiting-decision:` stamp and an open decision record. (Requirement: Work-directly
      converts an intake brief into an execution brief; Requirement: Brief repo is inferred
      deterministically from its focus; Requirement: Mechanical premise check precedes
      evaluation)
      files: tests/workqueue/test_intake_convergence_e2e.py
- [x] 6.2 [e2e] Run `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, and `openspec validate
      autonomous-intake-brief-convergence --strict`; confirm `tests/test_plugin_surface.py`
      still accepts every command SKILL.md names. Verification-only — no file changes
      expected.

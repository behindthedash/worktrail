# Tasks

## 1. Dependency-freshness check

- [x] 1.1 Add `src/worktrail/workqueue/dependency_freshness.py` with
      `check_dependency_freshness(repo_path) -> list[dict]`: discover tracked lockfiles via
      `git ls-files -- package-lock.json '*/package-lock.json'`, and for each root compare the
      lockfile's `packages[""]` `dependencies`/`devDependencies` pinned versions
      (`packages["node_modules/<name>"]["version"]`) against
      `<root>/node_modules/<name>/package.json`'s `version`, producing
      `{app_dir, lockfile, status: fresh|stale|unknown, mismatches: [{name, locked,
      installed}], detail}` per root (`installed: "missing"` for an absent directory;
      `unknown` for unparseable JSON or a lockfile with no `packages` map). Add
      `format_freshness_block(results) -> str` for the evaluator prompt (a "no npm package
      roots found" line when empty). Read-only: never write into the checkout. Cover it in
      `tests/workqueue/test_dependency_freshness.py` against a `git init`'d tmp repo: fresh
      root, stale version (vitest 5.0.0 locked vs 4.1.11 installed), missing dependency,
      lockfile without `packages`, nested `app/` root, no lockfile, and the rendered block.
      (Requirement: Dependency freshness is checked before reproduction)
      files: src/worktrail/workqueue/dependency_freshness.py tests/workqueue/test_dependency_freshness.py

## 2. Premise-check gate

- [x] 2.1 In `src/worktrail/workqueue/premise_check.py`, give `run_premise_check` a
      keyword-only `dependency_freshness: list[dict] | None = None` argument and, in
      `_check_command`, skip an `npm test` needle (exact or `npm test ...`) when any entry's
      status is not `fresh`: record `confirmed: false` with a detail naming the root(s) and
      mismatched packages, and still mark the command slot consumed. Other allow-listed
      runners are unaffected. Extend `tests/workqueue/test_premise_check.py`: `npm test`
      skipped with the stale detail and a following command recorded as skipped; `pytest`
      still runs with a stale root present; `npm test` runs when all roots are fresh or the
      argument is omitted. The argument's shape is the freshness-check result contract
      (entries with a `status` key and a `mismatches` list); this task needs only that
      shape, not the new module.
      (Requirement: Mechanical premise check precedes evaluation)
      files: src/worktrail/workqueue/premise_check.py tests/workqueue/test_premise_check.py

## 3. Wire into triage

- [x] 3.1 In `src/worktrail/workqueue/queue_triage.py`, have `evaluate_group` call
      `check_dependency_freshness(cwd)` once for a repo-bearing group (empty list for the
      no-repo group and the archived short-circuit), pass it into every
      `run_premise_check` call, render it into a new `{dependency_freshness}` placeholder in
      `EVALUATOR_PROMPT_TEMPLATE` under a "Dependency freshness" heading together with the
      design D3 rule (stale/unknown root output is not evidence for `stale-close`,
      `needs-update`, `work-directly`, or "no candidate fits"; prefer `keep` citing the root
      and mismatches), and return it as `dependency_freshness` on the result dict. Add a
      `dependency_freshness: list[dict[str, Any]]` field to `Verdict` and an optional
      `dependency_freshness` argument to `parse_verdicts` that copies it onto every parsed
      verdict (default `[]`); carry it through `escalate()` like `premise_check`. Update
      `tests/workqueue/test_queue_triage.py`: the prompt-template assertions gain the new
      placeholder and rule text; `evaluate_group` (with the spawn seam stubbed) returns the
      freshness list and embeds the rendered block; the no-repo group and archived
      short-circuit return `[]`; `parse_verdicts` lands the list on each `Verdict`; existing
      callers omitting the argument still get `[]`.
      depends: 1.1, 2.1
      (Requirement: Dependency freshness is checked before reproduction)
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py

## 4. Verification

- [ ] 4.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue`, then `PYTHONPATH=src pytest -q`
      and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`, and confirm
      all pass.
      depends: 3.1

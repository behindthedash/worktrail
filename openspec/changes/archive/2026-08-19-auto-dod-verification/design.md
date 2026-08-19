## Context

See proposal.md - Why. `check_dod_verification.py` today has exactly one
data path into a check: an author hand-writes `dod-checks:` frontmatter.
`check_task_file(repo, task_path)` reads frontmatter/body via
`taskformats.devkit.schema.read_task_file`, no-ops unless `status ==
"completed"`, then runs every entry in `dod-checks` through `run_check(repo,
check)`. `pre_pr_gate.py` calls `check_dod_verification.check_changed_specs`
scoped to `changed_paths(repo, policy)` (diff vs. resolved base ref) and
exits `DOD_VERIFICATION_DRIFT_EXIT` (4) on any failure. The devkit schema
(`taskformats/devkit/schema.py`) already has structured `files:` (list of
repo-relative paths) and body sections gated by fixed headings
(`## Acceptance Criteria`, `## Definition of Done (DoD)`) with `- [ ]`/`- [x]`
checkbox markers — `_all_checkboxes_checked()` already implements the
checked/unchecked scan this design reuses conceptually.

## Goals / Non-Goals

**Goals:**
- Derive checks purely from data the task file already has (`files:`,
  checkbox markers) — no new frontmatter fields, no schema change.
- Keep derivation deterministic and cheap: file-existence, `git ls-files`,
  checkbox counting, and fixed-pattern grep only. No subprocess test
  execution, no prose/NLP parsing.
- Preserve the existing diff-scoping contract in `pre_pr_gate.py` — derived
  checks must not retroactively fail PRs for tasks completed before this
  feature existed and left unchanged in the current diff.
- Give the backlog (datalena ~7241 checkboxes, GGB 61/99 files) an
  actionable, non-blocking report surface without turning a one-time
  historical audit into a permanent blocking gate.

**Non-Goals:**
- Re-executing referenced tests to confirm they currently pass. The
  full-suite `pre_pr_cmd` that `pre_pr_gate.py` already runs immediately
  after the DoD-verification check covers "tests pass" for any diff that
  isn't docs-only; duplicating that per-task would mean inventing
  per-language single-test-file invocation with no existing repo-policy hook
  to drive it (`go-policy.yaml` only has a whole-suite command).
- Parsing free-text inside Acceptance Criteria checkbox prose for file paths
  or test names (e.g. the backtick-quoted filenames visible in real GGB task
  bodies). Only the structured `files:` frontmatter array is treated as a
  reference list.
- Any change to the OpenSpec `tasks.md` checklist format — this stays
  devkit-format-specific, consistent with the parent spec's Non-Goals.
- Making `--all` audit failures fail CI by default. It is a standalone
  report command; a consuming repo may choose to wire it into its own
  non-blocking CI job later, but that wiring is not part of this change.

## Decisions

**Derivation lives in `check_task_file`, not in a new top-level function
consuming repos call directly.** `check_task_file(repo, task_path)` already
owns the "no-op unless completed" gate; adding "no-op unless dod-checks OR
derivable" there keeps one entry point. Concretely:
```python
checks = frontmatter.get("dod-checks")
if not checks:
    checks = derive_dod_checks(frontmatter, body)
    if not checks:
        return []
```
`derive_dod_checks(frontmatter, body) -> list[dict]` is a pure function (no
`repo`/filesystem access) that returns the same check-dict shape `run_check`
already consumes — so `run_check` doesn't need to know whether a check was
authored or derived.

**Three new check `type`s in `run_check`, not a special-cased derivation
runner.** Alternative considered: run derivation checks through bespoke
functions outside `run_check`. Rejected — `run_check` is already the single
place that turns a check-dict into a pass/fail, and every derived check
still needs `repo` (for path resolution and `git ls-files`), so it fits the
existing `run_check(repo, check) -> str | None` contract cleanly:
- `file_tracked`: `{"type": "file_tracked", "path": <str>}` — fails if the
  path doesn't exist under `repo`, or exists but `git ls-files --error-unmatch
  <path>` (run with `cwd=repo`) exits non-zero.
- `ac_checkboxes_complete`: `{"type": "ac_checkboxes_complete", "task_path":
  <repo-relative path to the task file itself>}` — re-reads the task file
  via `read_task_file`, reuses `schema._all_checkboxes_checked(body,
  sections=("Acceptance Criteria",))`, fails if it returns `False`. Passing
  `task_path` (rather than threading `body` through `run_check`'s existing
  two-arg signature) keeps `run_check`'s signature unchanged and lets it
  re-read fresh state deterministically, matching how `file_exists`/`grep`
  already re-read from `repo` rather than trusting caller-supplied content.
- `no_stub_markers`: `{"type": "no_stub_markers", "path": <str>}` — greps the
  path's content (once resolved to exist) against a fixed module constant
  `STUB_MARKER_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|NotImplementedError)\b")`;
  fails on any match, reporting the marker and line.

**`file_tracked` is a new type, not a change to `file_exists`'s semantics.**
Alternative considered: make `file_exists` itself also require git-tracked.
Rejected — that would silently change behavior for every existing
hand-authored `dod-checks: [{type: file_exists, ...}]` entry (e.g. checking a
gitignored build artifact intentionally), which is out of scope and would be
a breaking change to an already-shipped, documented check type.

**`derive_dod_checks` always includes the `ac_checkboxes_complete` check when
the task has a body, regardless of whether `files:` is populated.** This is
the single highest-value, zero-configuration check — it directly reproduces
the real drift pattern observed in GGB task files (e.g. `status: completed`
alongside `- [ ]` unchecked AC boxes annotated with "not executed in this
reconciliation pass"). Only `file_tracked` and `no_stub_markers` are
conditional on `files:` being non-empty.

**Audit mode is a separate function/CLI flag, not a change to
`check_changed_specs`'s diff-scoped contract.** `audit_all_specs(repo) ->
list[str]` walks `repo/docs/specs/**/TASK-*.md` (reusing
`taskformats.devkit.schema.is_task_file`) unconditionally — no git diff
involved — and calls the same `check_task_file` per file. Exposed as
`worktrail-check-dod-verification --repo R --all`, printed the same
`path: failure` format as the existing diff-scoped `main()` output but
labeled as an audit report. `pre_pr_gate.py` is not changed to call it;
audit is a manually- or CI-job-invoked tool, not part of the mandatory gate.

## Risks / Trade-offs

- **[Risk] A legitimately-completed task with an unrelated `TODO` comment
  in a listed file (e.g. a forward-looking comment unrelated to this task's
  own scope) trips `no_stub_markers` as a false positive.** → Mitigation:
  the check only inspects paths the task itself declared in `files:` — the
  task author already asserted those files are this task's own deliverable
  — and an author who disagrees with the derived verdict can always add
  explicit `dod-checks:` to opt out of derivation entirely for that task.
- **[Risk] `ac_checkboxes_complete` fires on tasks whose AC section
  legitimately mixes "verified" and "not applicable / deferred" items using
  ad hoc annotation conventions (as seen in some GGB task bodies) rather than
  ticking the box.** → Mitigation: this is exactly the drift class the
  parent spec exists to catch (a checkbox that reads as unverified is not
  the same as `status: completed`); the fix is for the task to either
  finish verification or not claim `completed`, not for the gate to special
  case prose annotations.
- **[Risk] `git ls-files` subprocess calls for every `files:` entry on every
  completed task add per-PR latency.** → Mitigation: bounded by the existing
  diff-scoping — only tasks changed in the current diff run derivation at
  all, and a single PR completes a small number of tasks.
- **[Trade-off] Audit mode's findings are not enforced anywhere by this
  change** — a consuming repo must choose to act on the `--all` report.
  Accepted per proposal.md's explicit Non-Goal: retrofitting/blocking on the
  historical backlog was already ruled out by the parent spec, and forcing
  it here would fail PRs for drift nobody introduced in that diff.

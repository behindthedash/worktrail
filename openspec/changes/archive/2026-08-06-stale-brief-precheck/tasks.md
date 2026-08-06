## 1. Core capability

- [x] 1.1 Create `src/worktrail/router/check_brief_staleness.py`. Module docstring follows
  `check_spec_collision.py` / `check_repo_freshness.py` conventions: state the one question the
  module answers, cite the motivating incident (brief `20260731-204048` delivered by
  `behindthedash/devops` PR #89 merged 2026-08-02, discovered five days later mid-dispatch), and
  state the fail-open contract explicitly. Implement, all best-effort and never raising:
  `extract_probes(text) -> {"paths": [...], "symbols": [...], "pull_requests": [...],
  "dropped": int}` (backtick-preferred extraction; a path probe is a token containing `/` **or**
  ending in a 1–10 char extension, so a bare `prevent-destructive-commands.py` qualifies; symbol
  probes require backticks; PR probes accept `PR #N`, `#N` when adjacent to `PR`/`pull`, and
  `owner/repo#N`, deduplicated by number); `resolve_base_ref(repo, base)` preferring
  `origin/<base>`, then `<base>`, then `HEAD`; and `check(repo, text, since, base=None) ->
  {"checked", "probes", "matches", "pull_requests", "warning"}`. Path probes without a separator
  are searched with a `**/` pathspec (`git log -- '**/<name>'`); symbol probes use `git log -S`.
  Every commit search is restricted with `--since=<created>`. Module-level constants own the
  per-kind probe cap and the per-subprocess timeout; when extraction exceeds a cap, keep the
  longest/most distinctive probes and report the dropped count. Distinguish `checked: false`
  (question unanswerable) from `checked: true, matches: []` (asked and clean).

- [x] 1.2 Add the `gh` lookup to `src/worktrail/router/check_brief_staleness.py` as an
  independently-degradable final step: resolve extracted PR probes and search merged PRs matching
  path probes. `gh` missing, unauthenticated, erroring, or timing out yields an empty
  `pull_requests` list plus a warning, and must never discard git evidence already collected.

- [x] 1.3 Add the `main(argv)` CLI to `src/worktrail/router/check_brief_staleness.py` mirroring
  `check_spec_collision.py`'s: `--repo` (required), `--text` / `--brief` (brief path; when given,
  read focus text and `created:` via `handoff_seed`/`brief_frontmatter` rather than reparsing
  frontmatter by hand), `--since`, `--base`, `--json`. Human-readable mode prints a short
  evidence summary or `no evidence`/`unknown: <warning>`; `--json` prints the result dict. Always
  exit 0 — this is a signal source, not a gate.

- [x] 1.4 Register `worktrail-check-brief-staleness =
  worktrail.router.check_brief_staleness:main` in `pyproject.toml`'s `[project.scripts]`, and
  bump the `version` field in the same file (this repo's `CI: Version Bump Check` fails any PR
  touching `src/worktrail/**` without a version bump, and `go:no-version-bump` is for deliberate
  deferral, not a default). Bump `.codex-plugin/plugin.json` to the same version.

## 2. Tests

- [x] 2.1 Create `tests/router/test_check_brief_staleness.py` covering extraction against every
  spec scenario: bare-filename path probe, dotted/underscored symbol probes, `PR #89` and
  `owner/repo#89` deduplicating to one entry, prose-with-no-code-tokens yielding empty probes,
  and cap truncation reporting a non-zero `dropped` while keeping the most distinctive probes.

- [x] 2.2 Extend `tests/router/test_check_brief_staleness.py` with history-search coverage using
  a temporary git repo fixture (follow `tests/router/test_check_spec_collision.py`'s existing
  fixture patterns): a commit after `created:` is reported with short SHA, date, and subject; a
  commit before `created:` is not; a commit reachable only from the remote-tracking ref is still
  found.

- [x] 2.3 Extend `tests/router/test_check_brief_staleness.py` with fail-open coverage: non-git
  path, missing/malformed `created:`, subprocess timeout, and `gh` unavailable each return
  `checked: false` (or an empty `pull_requests` with a warning, for the `gh` case) and raise
  nothing; and a searched-but-clean brief returns `checked: true` with empty `matches`. Assert
  explicitly that `check()` never raises for any of these inputs.

- [x] 2.4 Add a `TestCli` class to `tests/router/test_check_brief_staleness.py` following
  `tests/router/test_check_spec_collision.py`'s `TestCli` pattern: `--json` shape, `--brief`
  reading focus/`created:` from a real brief file, and exit code 0 on every path including
  unanswerable ones.

## 3. Procedure (Claude Code plugin surface)

- [x] 3.1 Update `skills/worktrail-go/SKILL.md` Phase 5.5 so it describes one question asked two
  ways: keep the existing route C/D spec-collision branch verbatim, and add the brief-sourced
  route E/F staleness branch that runs `worktrail-check-brief-staleness` before Phase 6. State
  the gate (brief-sourced AND route E or F), the fail-open rule, the never-auto-close rule, and
  point at the new reference file. Rename the phase heading so it no longer reads as
  collision-only.

- [x] 3.2 Create `skills/worktrail-go/references/brief-staleness-check.md` with the full
  procedure: exact command invocation, how to read `checked` vs `matches`, the
  `AskUserQuestion` operator prompt shape (options: close as already-delivered; proceed anyway),
  the `work_queue.py done ... --implementation-complete` call used only after an explicit
  close decision, and the run-record entries recording surfaced evidence plus the operator's
  decision. Cross-link it from `skills/worktrail-go/references/spec-collision-check.md`, whose
  "Gate: Route C or D only" paragraph must now say the E/F case is handled by the sibling check
  rather than simply skipped.

## 4. Validation

- [x] 4.1 [e2e] Run `PYTHONPATH=src pytest -q` and
  `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` from the worktree root;
  both must pass. `tests/test_plugin_surface.py` in particular must stay green — it enforces that
  every `worktrail-*` command named in a skill doc is a real entry point and that every
  `references/*.md` cross-link resolves, both of which this change adds.

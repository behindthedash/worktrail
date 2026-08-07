## 1. Core check module

- [ ] 1.1 Create `src/worktrail/router/check_related_brief_claims.py`: a
  `check(claimed_brief_path, picked_dir, queue_dir, agent_label=None,
  runs_dir=None)` function returning
  `{"checked": bool, "active": [...], "warning": str|None}`, mirroring
  `src/worktrail/router/check_brief_staleness.py`'s fail-open discipline
  (never raises; `checked: false` on any read failure). Read the claimed
  brief's `related:` frontmatter via
  `src/worktrail/shared/brief_frontmatter.py`'s `read_frontmatter`. For each
  related id, resolve it against `picked_dir` then `queue_dir` using the
  same resolution rules as `src/worktrail/workqueue/work_queue.py`'s
  `resolve()` (reuse that function directly, don't reimplement it). A
  related brief resolved in `picked_dir` with frontmatter `status: picked`
  is an active match; report its id, `claimed-by`, `claimed-at`, `repo`,
  and a truncated focus summary. Skip (don't abort on) an id that resolves
  to zero or ambiguously many files.
- [ ] 1.2 In the same module, add best-effort local run-record enrichment:
  when an active match's `claimed-by` equals the caller's `agent_label`
  (default: same `socket.gethostname():pid`-shaped label
  `work_queue.py`'s `_agent_label()` produces), scan `runs_dir` (default
  `~/.go/runs/<repo-name>/`, repo derived from the match's `repo:` field)
  for a `*.yaml` run record whose content references the related brief's
  id, and attach its path if found. Wrap this entirely in a broad
  try/except that silently no-ops on any failure -- it must never change
  whether a match is reported, only enrich it.
- [ ] 1.3 Add a CLI (`main(argv)`, `if __name__ == "__main__"`) mirroring
  `check_brief_staleness.py`'s: `--repo` is not needed here (no git repo
  involved), but `--brief <path>` (required), `--picked-dir`,
  `--queue-dir` (default to `work_queue.base_dir()/picked` and
  `/queue`), `--json`, and a human-readable formatter for the non-`--json`
  path. Always exit 0 (signal source for a human decision, never a gate).

## 2. Console script + packaging

- [ ] 2.1 Add `worktrail-check-related-brief-claims =
  worktrail.router.check_related_brief_claims:main` to `pyproject.toml`'s
  `[project.scripts]`, alongside the existing
  `worktrail-check-brief-staleness` entry.

## 3. Tests

- [ ] 3.1 Create `tests/router/test_check_related_brief_claims.py`
  covering, at minimum, each scenario in
  `openspec/changes/related-brief-collision-guard/specs/related-brief-collision-guard/spec.md`:
  related-id resolution (single match, zero match, ambiguous match),
  active-vs-done-vs-still-queued status determination, no-`related:`-field
  short-circuit, run-record enrichment present/absent, and every
  fail-open path (missing `picked_dir`, unreadable claimed brief).
  Use `tmp_path` fixtures for the queue directories -- do not touch the
  real `$WORK_QUEUE_DIR`.

## 4. `/go` Phase 5.5 wiring

- [ ] 4.1 Add a new `{#related-brief-collision-check}` reference section to
  `skills/worktrail-go/references/subagent-prompts.md` (or a new
  `skills/worktrail-go/references/related-brief-collision-check.md` if the
  existing file is getting unwieldy -- match whichever the two existing
  branches' docs already follow), documenting the exact
  `worktrail-check-related-brief-claims` invocation, how to read its
  result, the batched `AskUserQuestion` prompt shape, and the run-record
  entry to append on a surfaced match (mirroring
  `references/brief-staleness-check.md`'s shape).
- [ ] 4.2 Edit `skills/worktrail-go/SKILL.md`'s Phase 5.5 section: add a
  third branch after the existing "Route C/D branch" and "Route E/F
  branch" paragraphs, gated on "brief-sourced dispatch, claimed brief has
  `related:` entries, resolved route is not C/D/E/F", citing the new
  reference anchor from 4.1. Do not modify the existing two branches'
  text.

## 5. Verification

- [ ] 5.1 [e2e] Run `PYTHONPATH=src pytest -q` and
  `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`
  (golden record/replay regression) -- both green.
- [ ] 5.2 [e2e] Run `pytest tests/test_plugin_surface.py` -- confirms the new
  console script is real, `.claude-plugin/plugin.json` needs no change
  (no new skill directory added), and cross-skill anchor citations from
  4.1/4.2 resolve.

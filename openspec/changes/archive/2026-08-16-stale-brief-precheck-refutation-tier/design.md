## Context

See proposal.md - Why. The relevant current-state facts this design builds on:

- `check_brief_staleness.check(repo, text, since, base=None)`
  (`src/worktrail/router/check_brief_staleness.py:628`) is a pure probe-extraction-and-git-search
  function invoked from Phase 5.5 via the `worktrail-check-brief-staleness` CLI
  (`skills/worktrail-go/references/brief-staleness-check.md:33`). It never inspects a brief's
  frontmatter beyond `created:`/`original-created:` and has no concept of a re-runnable
  predicate.
- The checkbox-drift-sweep's predicate lives entirely in
  `taskformats/devkit/checkbox_audit.audit_repo(repo)` (`checkbox_audit.py:60`): a task file
  "drifts" when its frontmatter is `status: completed` but not every checkbox in
  `COMPLETION_AUDIT_SECTIONS` is checked. `spec_sync_sweep_checkbox_check.check_repo_checkbox_drift`
  (`spec_sync_sweep_checkbox_check.py:31`) wraps this for the fleet sweep; the sweep's captured
  hits (`path`, `unchecked_count`, `total_count`, `sections`) are rendered only as prose bullets
  by `spec_sync_sweep_checkbox_brief._render` (`spec_sync_sweep_checkbox_brief.py:49`) — the
  brief's frontmatter today carries only `drift-source: checkbox-drift-sweep`
  (`spec_sync_sweep_checkbox_brief.py:66`), with no structured, machine-parseable finding list.
- `run_record.py` has no schema for "the brief-staleness branch's outcome" beyond the existing
  `decisions` list field, appended today via `worktrail-run-record append "$RUN" decisions "..."`
  for the "proceed" outcome (`brief-staleness-check.md:149`). `cmd_append`
  (`run_record.py:359`) accepts any lowercase-snake-case field name, so a new field is possible
  but not required.
- Brief frontmatter is read via `brief_frontmatter.read_frontmatter`
  (`src/worktrail/shared/brief_frontmatter.py:68`), a full YAML parse (`yaml.safe_load`), so a
  new list-valued field (`drift-findings`) round-trips without any new parsing machinery.

## Goals / Non-Goals

**Goals:**
- Give the checkbox-drift-sweep a way to stamp a re-runnable predicate onto its own briefs.
- Give Phase 5.5 a re-check step that runs before `check_brief_staleness` and the operator
  prompt, for briefs that carry a recognized predicate, and that degrades to today's unmodified
  behavior on anything it can't answer (missing predicate, unknown `drift-source`, read error).
- Keep the carve-out registry-shaped so a second sweep can plug in its own predicate later
  without touching the Phase 5.5 branch's control flow — but implement only the
  `checkbox-drift-sweep` predicate now (no speculative second entry).

**Non-Goals:**
- No change to `check_brief_staleness.check()`'s own probe-extraction/git-search behavior — it
  is bypassed for a resolved predicate, not modified.
- No change to the prose (non-predicate) brief path, or to how the operator prompt is worded
  for briefs that do fall through.
- No attempt to make the checkbox-drift predicate re-check "smart" about partial credit —
  per-finding classification is binary (still-drifted / resolved), matching `audit_repo`'s own
  binary hit/no-hit semantics.
- No retroactive backfill of `drift-findings` onto already-filed checkbox-drift briefs sitting
  in the queue today; a brief filed before this change simply has no `drift-findings` and falls
  through to the unmodified flow (Requirement: Deterministic Staleness Predicate Is Captured On
  Sweep-Generated Briefs, Scenario: Missing structured findings is treated as no predicate).

## Decisions

**A new sibling module, not a new mode inside `check_brief_staleness.py`.**
Add `src/worktrail/router/check_brief_predicate.py` rather than growing `check()`'s signature.
`check_brief_staleness.check()` is prose-probe-shaped (text in, matches out); the predicate
re-check is frontmatter-and-registry-shaped (drift-source in, per-finding classification out).
Mixing the two would force `check()` to grow brief-parsing and registry-dispatch concerns it
doesn't otherwise have, and would make "did the predicate carve-out even run" harder to test in
isolation. The two modules share nothing but "reads a brief and a repo," which
`brief_frontmatter.read_frontmatter` and `handoff_seed.build_seed` already factor out.

Public shape:
```python
# check_brief_predicate.py
PREDICATE_RECHECKS = {"checkbox-drift-sweep": _recheck_checkbox_drift}


def recheck(repo: Path, frontmatter: Dict[str, Any]) -> Dict[str, object]:
    """Never raises. Returns:
    {"attempted": bool, "drift_source": str|None, "outcome": "no-predicate"|
     "unrecognized"|"error"|"still-true"|"resolved", "still_true": [...],
     "resolved": [...], "error": str|None}
    """
```
`attempted=False` (outcome `"no-predicate"`) when `drift-source` is absent; `outcome=
"unrecognized"` when present but not a `PREDICATE_RECHECKS` key; both are `attempted=False` so
the CLI/skill layer can treat "not attempted" as a single fall-through gate without branching on
which specific reason. `outcome="error"` covers a registered predicate whose recheck raised or
found an unreadable finding — also folds into fall-through. Only `"still-true"` and `"resolved"`
are terminal, auto-deciding outcomes.

**Per-finding classification re-derives state; it does not diff against captured counts.**
`_recheck_checkbox_drift` re-runs `checkbox_audit`'s own file-level check
(`read_task_file` + `_all_checkboxes_checked`, both already imported by `checkbox_audit.py`)
against each captured finding's `path`, not the whole-repo `audit_repo` glob — the brief already
named its scope; re-scanning the entire `docs/specs/**` tree again would risk picking up
newly-drifted files the brief never claimed and conflating "resolved" with "coincidentally also
drifted elsewhere." A finding classifies `still-true` when the file is currently `status:
completed` with an unchecked box in `COMPLETION_AUDIT_SECTIONS`, `resolved` when it is `status:
completed` with all boxes checked or no longer `status: completed`, and raises (caught by the
top-level `recheck()`, becomes `outcome="error"` for the whole brief) when the file can't be
read — per spec's file-level error requirement, one unreadable finding invalidates the whole
recheck rather than silently dropping it from consideration.

**Overall outcome is "still-true" if any finding is still-true, "resolved" only if every finding
resolves.** This mirrors `audit_repo`'s own "any unchecked box anywhere in scope counts as
drift" semantics and matches the proposal's motivating framing: the brief's underlying trigger
("this repo currently has checkbox drift in the captured scope") is still true as long as one
captured file still drifts, even if others were separately fixed.

**`drift-findings` frontmatter carries `path` only, not the captured counts.** Design considered
also stamping `unchecked_count`/`total_count`/`sections` (already computed by the sweep) for
richer run-record evidence text. Decided to include them anyway since they're free (the sweep
already has them in memory when rendering) and improve the recorded evidence's readability
without adding re-check logic — the re-check itself only ever reads `path` per finding and
ignores the rest. Each `drift-findings` entry: `{path, unchecked_count, total_count}`
(`sections` omitted — it's a list-of-strings used only for the brief body's human-readable
"which headings" detail, not needed for the recheck or its evidence line).

**Phase 5.5 skill-doc ordering: predicate re-check is a new sub-branch that runs first, not a
parameter to the existing one.** `brief-staleness-check.md` gains a step between "Gate:
brief-sourced" and "Running it" that reads the claimed brief's frontmatter, calls the new CLI
(`worktrail-recheck-brief-predicate --repo ... --brief ... --json`), and only proceeds to
today's "Running it" / `worktrail-check-brief-staleness` invocation when the new command reports
`attempted: false`. This keeps the existing probe-search step's cost (git subprocesses, `gh`
lookups) unpaid for a brief the predicate can already answer — the proposal's whole point is
that the probe search and human round-trip were unnecessary work for such a brief, not merely
that its answer should be double-checked against the predicate.

**Auto-close reuses `work_queue.py done`; auto-proceed reuses the existing post-Phase-6
`run-record append ... decisions` pattern.** No new queue-mutation or run-record primitive is
introduced. The "resolved" outcome's close note is built from the recheck's `resolved` list
(paths + a fixed "predicate re-check: checkbox-drift-sweep" tag), explicitly not from any
commit SHA or PR number, because none is computed on this path (the probe search never runs).
The "still-true" outcome's run-record entry is built from the `still_true` list the same way.

## Risks / Trade-offs

**[Risk]** A future predicate registered under `PREDICATE_RECHECKS` could be written carelessly
(e.g. no per-finding error isolation) and auto-close a brief incorrectly, with no operator
in the loop to catch it. → **Mitigation**: the spec's error-handling requirement (any unreadable
finding invalidates the whole-brief recheck) is a hard contract every registry entry must honor;
`recheck()`'s top-level `try/except` around each `PREDICATE_RECHECKS` call is a second,
module-level backstop so a raising predicate function degrades to `outcome="error"`
(fall-through) rather than crashing or silently mis-classifying.

**[Risk]** Stamping `drift-findings` onto every future checkbox-drift brief slightly grows brief
file size and duplicates data already in the body bullets. → **Mitigation**: accepted — the
duplication is small (one YAML list of short entries) and necessary, since the recheck path
must not depend on parsing the prose body back into structured data (a maintenance hazard if the
body's rendering format ever changes for readability).

**[Risk]** A brief consolidated from multiple checkbox-drift briefs (see
`stale-brief-precheck-consolidation-original-created`, if brief consolidation applies to this
sweep's briefs) could end up with a merged `drift-findings` list whose provenance is unclear. →
**Mitigation**: out of scope for this change — consolidation behavior for checkbox-drift briefs
is unchanged by this proposal; if consolidation already merges frontmatter lists today, the
recheck simply evaluates whatever `drift-findings` entries are present, same as any other list
field the consolidator merges.

## Open Questions

None — the predicate-recheck outcome vocabulary, per-finding classification rule, and
fall-through conditions are fully pinned by the spec's scenarios; nothing here would change the
approach or task breakdown if resolved later.

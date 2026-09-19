## Why

`queue_triage`'s `fold-into-change` path writes the triage verdict's `evidence`
(the evaluator's `note` field) verbatim as the new task's body:

```python
task_evidence = " ".join(v.evidence.split())
task_block = f"- [ ] {group_number}.1 {task_evidence}\n"
```

`evidence` is written to argue **why the fold belongs**, not **what to do**. The
result is a checklist item that reads as a case, with the action buried
mid-paragraph or absent. Observed 2026-09-19 in datalena PR #2975 (merged), task
12.1 of `openspec/changes/fail-closed-ci-gates/tasks.md`:

> Brief's own CONFIRMED premise check: `npx vitest run --changed origin/dev` at
> .github/workflows/qa-pipeline.yml:1709. The existing OpenSpec change
> fail-closed-ci-gates already scopes exactly this class ... It is not yet done:
> `grep -n ATTESTED_CHECK_NAMES -A8 scripts/ci/shared/attestation.py` shows only
> api-unit/api-integration/scripts-unit/security-attack-pack, no web-unit, and
> the change has 8 open tasks.

Nothing in that task says to add `web-unit` to `ATTESTED_CHECK_NAMES` and assert
`>= 1` executed test; it says the change *has not done so yet*. An implementer
picking up 12.1 has to reconstruct the instruction from the argument. This
affects every `fold-into-change` verdict fleet-wide, not just this one.

The brief's own `focus` is the one field written as a statement of the work. The
motivating brief's first sentence was already a usable instruction — *"Add a
zero-execution guard to every test-executing CI job in datalena's qa-pipeline, so
a required check can never report green having run nothing."* — while the
evidence that displaced it was pure argument.

The spec is internally inconsistent on this point today, which is how the
divergence survived: the "Fold and propose are applied as a pull request,
fail-closed" requirement says the fold appends *"the brief's focus"* to
`proposal.md`, while its own "Multi-line evidence is collapsed" scenario says
`proposal.md` carries *"the evidence verbatim"* and `tasks.md` gets
`<collapsed evidence>`. The code implements the scenario. Neither states where
the instruction comes from.

## What Changes

- The folded `tasks.md` checklist item's body is the **first sentence of the
  brief's focus**, collapsed to one line — the brief author's own statement of
  the work. The evidence no longer appears in the task body.
- The `## N. Folded from <brief-id>` group keeps a one-line rationale pointer to
  `proposal.md`'s matching section, so a `tasks.md`-only reader still knows where
  the triage evidence is without the argument displacing the instruction.
- `proposal.md`'s `## Folded from <brief-id>` section carries the brief's focus
  **and** the triage evidence, which resolves the requirement-vs-scenario
  contradiction above rather than leaving one of them wrong.
- A brief with no readable focus falls back to the collapsed evidence, exactly
  as today, so the fold can never emit an empty task.
- The task's `files:` scope is derived from path probes in the focus as well as
  the evidence, because the task is now stated from the focus and its scope must
  cover the paths that text names.

## Impact

- Affected specs: `intake-triage`
- Affected code: `src/worktrail/workqueue/queue_triage.py`
  (`_apply_fold_into_change`, `_fold_task_file_scope`)
- No change to verdict parsing, validity rules, the claim/landing pipeline, or
  any other verdict type.

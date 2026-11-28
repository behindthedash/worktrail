## Context

`/opsx:sync` is an **agent-driven** operation: the bundled
`skills/openspec-sync-specs/SKILL.md` tells a model to read a change's delta and edit the
canonical `openspec/specs/<capability>/spec.md` to apply it. Drain does not write specs itself —
it spawns that skill (`build_sync_command`, `drain.py:1010`) and then trusts the resulting diff
(`_run_sync_pending`, `drain.py:1065`): if the sync exits zero and the worktree is dirty, it
commits, pushes, and opens a PR. Nothing between the spawn and the PR reads what the model wrote.

The skill's step 4d is about *creating* a main spec, and it says to start the file with
`# <capability> Specification` because "a main spec is not a delta and `openspec validate`
requires this line". That justification does not hold: verified against openspec 1.8.0 in a
throwaway root, a `## Purpose`-first spec with no title line exits `0` from
`openspec validate <cap> --strict`, identical to a spec that carries the line. The instruction is
a stale carry-over in a hand-forked skill, and a model applying delta content to an existing spec
reads it as licence to normalize the heading shape it found.

## Goals / Non-Goals

**Goals:**

- A created main spec matches the heading shape the target repo already uses, so a repo whose
  specs begin at `## Purpose` gets no title line.
- An existing main spec is only ever edited where the delta names content; its heading is left
  alone.
- Drain refuses to land a draft that inserts a title line into a spec that already existed, and
  fails the finding instead of opening a nonconformant PR.

**Non-Goals:**

- Making drain interpret the delta or reconcile specs itself — the skill remains the writer.
- Enforcing one canonical heading shape across repos; worktrail's own specs carry the title line
  and must keep validating.
- A `git`-side or pre-PR-gate check over the whole tree — the gate is scoped to the sync draft
  drain is about to land.

## Decisions

**D1 — The skill converges on the repo, it does not impose a shape.** The create-new-spec step
must look at the other `openspec/specs/*/spec.md` files and copy their heading shape, writing a
title line only when those specs already have one (or there is none to read). A fixed title line
is right for some repos and wrong for others; the repo's own specs are the only authority on
which. `openspec validate` is not consulted for this — it accepts both shapes.

**D2 — The no-restyle rule is stated as its own instruction.** The convergence rule alone covers
new files. An existing spec is the common case for the observed defect (`dde2866` edited a spec
that already existed), so the skill states separately that an existing main spec's title line is
never added, reworded, or moved, and that only delta-named requirement/scenario content changes.

**D3 — Drain's gate keys on "added a title line to a spec that already existed".** The gate runs
in `_run_sync_pending` after the sync exits zero and before `git add -A`, over `git diff` for
`openspec/specs/*/spec.md`. It rejects when a draft adds a top-level `# ... Specification` line to
a file whose diff status is *modified* (not *added*). This is exactly the observed defect, is
diff-local (no repo-convention scan, no `base`-side read), and cannot false-positive on a repo
that creates a new titled spec — that file's status is *added*. A brand-new spec created in a
title-less repo is the skill's responsibility (D1), not the gate's; keeping the gate this narrow
avoids inventing a second, competing notion of "the repo's convention" in Python.

**D4 — The gate raises, it does not return a no-op.** A rejected draft is a remediation failure:
`_run_sync_pending` raises before commit/push/PR, the sweep's existing per-finding isolation logs
it under the `resume-sync-pending` label and continues to other findings, and the finding stays
visible on the next dashboard scan. Reusing the existing failure path means no new result key,
no new summary field, and no silent-success mode — the failure this change exists to stop is
precisely a silent success.

**D5 — One predicate beside the code that already decides landability.** The gate inspects the
worktree with a `git diff --unified=0 <base> -- <specs>` (or the equivalent `--numstat` plus
`--diff-filter`) built on the file's existing subprocess pattern, not a new helper module: a
single predicate that reads the draft diff, called from `_run_sync_pending` at the one place that
already gates the commit.

## Risks / Trade-offs

- **A legitimate restyle is refused.** A sync that genuinely intends to introduce a title line
  into an existing spec is rejected; the human resolves it on the dashboard. Accepted — that
  edit is a restyle the delta never expresses, and the referenced repo shows it is a mistake, not
  intent.
- **A brand-new spec in a title-less repo still gains a title line** if the skill's convergence
  rule is ignored by the model. This is a residual, not a regression: the skill text is the
  primary fix and the plugin-surface test pins it; the gate covers the higher-frequency existing-
  spec case.
- **The gate reads `base` in the worktree.** The remediation worktree is created fresh off the
  base branch, so the pre-sync side of `git diff` is the base tree without a network call.

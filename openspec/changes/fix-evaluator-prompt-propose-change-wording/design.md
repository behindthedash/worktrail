## Context

`EVALUATOR_PROMPT_TEMPLATE`'s Step 2a (`src/worktrail/workqueue/queue_triage.py:156-169`)
carries one sentence for both the repo-bearing and no-repo (`__none__`)
cases:

```
`propose-change` is valid only when your evidence names one of these known
repos as the `target_repo`: {known_repos}.
```

`_evaluate_group()` fills `{known_repos}` from `known_repos_str`
(`queue_triage.py:1170-1174`):

```python
known_repos = _known_repos(repos_root) if repo == NO_REPO_KEY else []
known_repos_str = (
    ", ".join(known_repos)
    if known_repos
    else ("(none found)" if repo == NO_REPO_KEY else "(not applicable)")
)
```

For a repo-bearing group this renders `{known_repos}` as literally
`(not applicable)`, so the sentence reads as a restriction with zero
qualifying values — the opposite of the actual rule. `_has_valid_target()`
(`queue_triage.py:1292-1332`) only applies the allowlist when its
`known_repos` argument is not `None`, and that argument is populated
(`known_repos_by_brief`) only for the no-repo group
(`queue_triage.py:1176-1178`); for a repo-bearing group it's `None`, so
`propose-change` is accepted for any well-formed `target_repo` there. The
spec (`openspec/specs/queue-triage/spec.md`, "Evidence-required verdict per
brief") already states the allowlist is scoped to the repo-less group only —
the code matches the spec; only the prompt text is wrong.

## Goals / Non-Goals

**Goals:**
- Make the prompt's Step 2a wording match what `_has_valid_target()` actually
  enforces, so an evaluator in a repo-bearing group is never misled into
  thinking `propose-change` is unusable there.

**Non-Goals:**
- Changing `_has_valid_target()`, `_known_repos()`, `known_repos_by_brief`,
  or any other validation logic — behavior is already correct per spec; this
  is a prompt-text fix only.
- Changing the no-repo group's wording or its `"(none found)"` case — that
  sentence is accurate as written and stays as-is.

## Decisions

### Give Step 2a two branches instead of one shared sentence

Split the single Step 2a sentence into a repo-bearing branch and a no-repo
branch, selected by `repo == NO_REPO_KEY` at format time (the same condition
`_evaluate_group()` already branches on for `known_repos` itself):

- Repo-bearing group: `propose-change`'s `target_repo` is simply this
  group's own repo (`{repo}`), stated as a fact rather than a restriction —
  there is no known-repos allowlist to satisfy.
- No-repo group: keep the existing sentence and `{known_repos}` substitution
  unchanged (`", ".join(known_repos)` or `"(none found)"`).

Implementation shape: replace the single `{known_repos}`-based sentence in
`EVALUATOR_PROMPT_TEMPLATE` with a new `{propose_target_rule}` placeholder,
and have `_evaluate_group()` compute its value with the same
`repo == NO_REPO_KEY` branch already used for `known_repos`/`known_repos_str`
— no new branching condition, just reusing the existing one to also drive
the sentence text instead of only the substituted list.

### Alternative considered: reword "(not applicable)" to something clearer

E.g. render `known_repos` as `(no restriction — target_repo may be any
repo)` instead of `(not applicable)` for the repo-bearing case, keeping one
shared sentence. Rejected: the shared sentence's grammar ("valid only when
... names one of these known repos") is inherently a restriction statement,
so any substitution still reads as *a* restriction rather than *no*
restriction — the sentence shape itself is wrong for that case, not just the
placeholder value. Splitting into two branches lets each case state its own
actual rule plainly.

## Migration

None — prompt-text change only, no data or interface migration.

## Why

`_evaluate_group()` (`src/worktrail/workqueue/queue_triage.py:1170-1181`) formats
`EVALUATOR_PROMPT_TEMPLATE`'s Step 2a sentence — `propose-change` is valid only
when your evidence names one of these known repos as the `target_repo`:
`{known_repos}` (`queue_triage.py:165-166`) — with `known_repos_str` set to
`"(not applicable)"` for every repo-bearing group (`queue_triage.py:1174`,
since `known_repos = []` there and the `else` branch only fires when
`repo != NO_REPO_KEY`). The rendered sentence for those groups reads
"`propose-change` is valid only when your evidence names one of these known
repos as the `target_repo`: (not applicable)" — which an evaluator can only
parse as "no repo qualifies, so `propose-change` is never valid here."

That reading is wrong. `_has_valid_target()` (`queue_triage.py:1292-1332`)
only enforces the known-repos allowlist when `known_repos is not None`
(line 1330), and `_evaluate_group()` only ever populates
`known_repos_by_brief` — the dict `parse_verdicts()` threads into
`_has_valid_target()` as that `known_repos` argument — for the no-repo
(`__none__`) group (`queue_triage.py:1176-1178`: `{bid: known_repos for bid
in brief_ids} if repo == NO_REPO_KEY else {}`). For a repo-bearing group the
dict is empty, so every brief's `known_repos` lookup returns `None` and
`propose-change` is accepted for any well-formed `target_repo` — matching
`openspec/specs/queue-triage/spec.md`'s "Evidence-required verdict per
brief" requirement, which only imposes the known-repos allowlist "for a
brief evaluated in the repo-less (`__none__`) group." The prompt text just
never says so: it states the restriction unconditionally and lets
`known_repos_str`'s "(not applicable)" placeholder stand in for "no
restriction," which reads as the opposite.

The practical effect: an evaluator triaging a repo-bearing group may see
"(not applicable)" and conclude `propose-change` is off the table for that
group, steering it toward `needs-decision` or `keep` for a brief that
clearly belongs in this repo and has no fold candidate — exactly the
`propose-change` case Step 2a exists to route.

None of this repo's active changes touch `EVALUATOR_PROMPT_TEMPLATE`, so
this needs its own change.

## What Changes

- `EVALUATOR_PROMPT_TEMPLATE`'s Step 2a sentence is split into two cases so
  the evaluator is never told a restriction applies to a group it doesn't
  apply to: for a repo-bearing group, the prompt states `target_repo` for
  `propose-change` is simply `{repo}` (this group's own repo), with no
  known-repos allowlist; for the no-repo group, the existing known-repos
  allowlist wording is kept as-is (including its "(none found)" case).
- No change to `_has_valid_target()`, `_known_repos()`, or
  `known_repos_by_brief` construction — the underlying validation behavior
  (already correct per spec) is unchanged; only the prompt text that
  describes it to the evaluator is fixed.

## Capabilities

### Modified Capabilities
- `queue-triage`: `Evidence-required verdict per brief` gains a requirement
  that the evaluator prompt's `propose-change`/`target_repo` wording never
  implies a known-repos restriction for a repo-bearing group, since none
  applies there.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`EVALUATOR_PROMPT_TEMPLATE`,
  `_evaluate_group()`)
- `tests/workqueue/test_queue_triage.py`

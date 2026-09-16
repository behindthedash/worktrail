## Why

Live repro from brief `20260915-102504-wake-up-sooner-has-no` (focus: "wake-up-sooner has
no `.github/dependabot.yml`"): `worktrail-skill-dispatch --evaluate-brief-triage` returned
verdict `work-directly`, confidence high, evidence citing a real reproduction command
(`python scripts/ci/dependabot/test_dependabot_config.py`) and `premise_check=[{kind: path,
needle: .github/dependabot.yml, confirmed: false, detail: path does not exist}]`.
`--apply-brief-triage` then downgraded it to `action: noop`, `status: downgraded-to-keep`
with note "evidence does not cite a test, check, or command, and no premise-check entry was
confirmed". Tracing `src/worktrail/workqueue/queue_triage.py` and
`src/worktrail/workqueue/premise_check.py` surfaces two independent defects behind that
outcome:

1. `premise_check.py`'s `_check_path()` (line 199-226) always treats `confirmed: True` as
   meaning "the path exists" and `confirmed: False` as "the path does not exist", with no
   awareness of what the brief is actually *claiming* about the path. For a presence claim
   ("see `src/foo.py:42`") that is the correct semantics. For an absence claim ("X has no
   `.github/dependabot.yml`") it is backwards: the path genuinely not existing IS the
   confirmation of the brief's claim, but `_check_path()` still reports `confirmed: False`
   for it. `_work_directly_accepted()` (`queue_triage.py:505-520`) then reads
   `any(p.get("confirmed") for p in premise_check)` generically — it has no way to know
   this `False` was actually a correct confirmation, so a correctly-verified absence-claim
   brief is treated as having no confirmed premise at all.
2. Separately, `_REPRODUCTION_EVIDENCE_RE` (`queue_triage.py:106-121`) only matches
   present-tense `reproduces? via`, not past-tense `reproduced via`. Evaluator prose citing
   a real reproduction command in past tense (a natural phrasing once the command has
   already been run) fails the text-match fallback too, so both halves of
   `_work_directly_accepted()`'s combined rule can fail on a brief that is, in fact, fully
   verified.

This likely affects every `fleet-ci-standard-drift-guard` missing-file brief (e.g. sibling
`20260915-102502-wake-up-sooner-github-workflows`), not just the one that surfaced it.

## What Changes

- `premise_check.py` gains claim-polarity awareness for `path` needles: when a path
  needle's surrounding focus text signals an absence claim (phrasing such as "has no",
  "missing", "lacks"/"lacking", "without", "no such", "does not exist"/"doesn't exist",
  "does not have"/"doesn't have", found in a short window immediately before the path
  mention), `_check_path()` reports `confirmed: True` when the path does NOT exist (the
  absence claim is confirmed) and `confirmed: False` when it does exist (the claim is
  refuted) — the inverse of the existing presence-claim semantics, which remain unchanged
  for every path needle with no absence indicator nearby.
- `_REPRODUCTION_EVIDENCE_RE` gains a past-tense alternative (`reproduced via`) alongside
  the existing present-tense `reproduces via`.
- No change to `_work_directly_accepted()`/`_apply_work_directly()` themselves: their
  generic "any confirmed premise-check entry, or reproduction-cited evidence" rule already
  does the right thing once `premise_check.py` reports polarity-correct confirmation and
  the regex recognizes past-tense phrasing.

## Capabilities

### Modified Capabilities
- `intake-triage`: `Mechanical premise check precedes evaluation` gains polarity-aware
  confirmation for an absence-claim `path` needle. `Work-directly converts an intake brief
  into an execution brief` gains scenarios covering a confirmed absence-claim premise and
  past-tense reproduction evidence (the underlying combined-rule text is unchanged; only new
  scenarios are added).

## Impact

- `src/worktrail/workqueue/premise_check.py` (`Needle`, `_extract_path_needles`,
  `_check_path`, `run_premise_check`)
- `src/worktrail/workqueue/queue_triage.py` (`_REPRODUCTION_EVIDENCE_RE`)
- `tests/workqueue/test_premise_check.py`
- `tests/workqueue/test_queue_triage.py`

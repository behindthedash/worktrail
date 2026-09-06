## Context

`spawn_agent()` returns a `SpawnResult` with no success/failure channel. Its three give-up
paths (`spawnlib.py:1208`, `:1287`, `:1318`) return `finish(last_raw)` -- the last raw stream,
which for a provider usage cap is the CLI's error text. Two brief-lifecycle consumers store
that text as a result: the triage evaluator (`raw_text` -> `parse_verdicts()` -> fail-open
`keep`) and handoff capture's slug summariser. Neither has any way to know it happened.

The failure is already classified at the give-up point: `agent_capacity.classify_failure()`
runs one screen earlier and returns `billing` for `usage limit`/`session limit`/`weekly limit`
wording. The information exists; it is simply not carried out of the function.

## Goals / Non-Goals

**Goals**
- One signal on `SpawnResult` that distinguishes "the model answered" from "we gave up".
- No fabricated triage verdict, triage note, or `keep-count` increment for a brief no model read.
- A non-zero, distinguishable exit at both entrypoints so an unattended caller stops instead of
  applying.
- Fix both consumer call sites, since one root cause produced both observed defects.

**Non-Goals**
- Changing capacity gating, cooldowns, retry budgets, or hop order.
- Automatically re-running the evaluator once capacity returns; the brief simply stays queued
  and untouched, and the next scheduled `evaluate` picks it up.
- Auditing every `spawn_agent` caller. The orchestrator's callers already have their own
  report-back contract (an unparseable report-back is journaled `retryable`); only these two
  callers convert worker text directly into a stored record.

## Decisions

### D1: The signal lives on `SpawnResult`, not in an exception

`spawn_agent()` deliberately hands the last raw output back to the caller so a caller with its
own report-back contract (the orchestrator) can inspect it. Raising instead would change that
contract for every existing caller. Two additive fields with safe defaults --
`exhausted: bool = False`, `failure_class: str = ""` -- leave every current caller reading
`result.text` exactly as it does today, while giving a caller that cares an explicit branch.

`finish()` grows a keyword-only `exhausted`/`failure_class`; the success return at `:1220`
passes neither. `failure_class` carries `agent_capacity.classify_failure()`'s existing
vocabulary (`billing`, `auth`, `transport`, ...) rather than a new `capacity` token -- the
brief's suggested `failure_class=capacity` names the *concept*; `billing` is this codebase's
established spelling for a provider usage cap and is what the gate record already stores.
Introducing a parallel vocabulary would mean two names for one state.

### D2: The evaluator raises, the CLI translates

`evaluate_group()` returns a group dict, `evaluate_briefs()` returns `list[Verdict]`. An
exhausted spawn has no honest value in either shape: an empty verdict list is
indistinguishable from "the evaluator returned nothing identifiable", which is a *different*
condition with a different exit code and a different operator action. So `evaluate_group()`
sets `"exhausted": True` / `"failure_class"` on the group dict with `raw_text=""` (the error
stream is not the group's output and must not be stored as it), and `evaluate_briefs()` raises
`EvaluatorUnavailable(repo, brief_ids, failure_class)` before `parse_verdicts()` is reached.

Raising from `evaluate_briefs()` rather than returning a sentinel means every caller must
handle it explicitly -- there is no path where an exhausted group silently produces zero
verdicts and is mistaken for a group whose briefs were all skipped.

### D3: Exit 2 for the single-brief CLI, partial-success for the batch

`--evaluate-brief-triage` evaluates exactly one brief; exhaustion means the whole invocation
produced nothing. It prints `null` and exits **2** with `blocked_no_capacity: <detail>` on
stderr -- reusing verbatim the shape `--routing`'s `NoExecutionTarget` branch already uses for
"no capacity, nothing was attempted". Exit **1** keeps its existing, narrower meaning: the
evaluator ran and emitted no identifiable verdict for this brief id.

`queue-triage evaluate` fans out over many groups, and one gated group must not discard
another group's completed work. It catches `EvaluatorUnavailable` per group, omits that
group's briefs from the verdict file entirely, counts it in a new `groups_unevaluated` field
(JSON and text summary alike), and returns a non-zero exit when that count is non-zero so an
unattended runner does not read the run as clean.

### D4: The apply path fails closed on a verdictless payload

With D2/D3 the evaluator never emits a fabricated verdict, so the apply path should never see
one -- but `--apply-brief-triage` takes JSON from the caller's shell, and the observed failure
was exactly a caller piping the evaluate step's output into the apply step unconditionally.
`Verdict(**json.loads("null"))` raises `TypeError` today. Instead: a payload that is `null`,
not an object, or whose `verdict` is null/empty returns an `error` action-log entry (the same
shape `_apply_keep()`'s "brief not found" case returns) and exits 1, appending no triage note
and incrementing no `keep-count`. This is the guard that makes the fix robust against a caller
that ignores the exit code, which is precisely what happened.

### D5: Handoff capture already has the right fallback

`_semantic_slug_summary()` already returns `None` on any exception and the caller already falls
back to `fallback_slugify()`. The fix is one branch -- return `None` when `result.exhausted` --
so an exhausted summariser produces a deterministic focus-derived slug instead of a filename
made of the provider's error message.

## Risks / Trade-offs

- **A `SpawnResult` field two callers read.** Every other caller ignores it, so the flag can
  drift out of correctness unnoticed. Mitigated by testing the flag at the spawnlib level
  (each give-up path sets it, the success path does not) rather than only through the
  consumers.
- **`billing` reads oddly for a usage cap on a subscription plan.** It is the existing gate
  vocabulary; renaming it is a larger, separate change and would churn persisted gate records.
- **A gated group's briefs are re-evaluated from scratch on the next run.** Accepted: an
  evaluation that never happened has nothing to resume, and the brief is left byte-identical.

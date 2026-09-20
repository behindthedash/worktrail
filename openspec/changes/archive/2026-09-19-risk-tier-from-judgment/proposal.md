## Why

`classify_risk()` tiers on keyword presence alone. That tier becomes the
`go:risk-*` label, and `auto-merge.yml` merges `low`/`medium` with no human, so
the table is the last thing standing between an unattended merge and production.

Measured 2026-09-19 on a 24-item adversarial probe set (now
`tests/fixtures/risk_probes.json`):

| | keyword table | judgment |
|---|---|---|
| exact tier | 8/24 | 20/24 |
| auto-merge gate decision | 10/24 | 24/24 |
| **unsafe auto-merges** (truth gated, rated mergeable) | **7** | **0** |
| false human gates (truth mergeable, rated gated) | 7 | 0 |

Both error directions are live:

- **Unsafe.** Production data deletion, an auth fail-open, refund
  re-submission and "make a required check non-blocking" were all rated
  mergeable, because none of them contains a listed word. Verified again live
  during implementation: *"Add a maintenance job that deletes every row in the
  events table older than the retention window, running against production
  nightly"* scores `low` with **no labels at all**.
- **False gates.** Seven trivial doc/test changes were rated `critical` for
  containing `billing`, `secrets` or `truncate`. This direction is already a
  confirmed production incident: brief `20260910-090241` (devops PR #366) was
  hand-merged because a dependency named `better-auth` scored `high:authz`.

Every judgment tier error was off by one, and none crossed the auto-merge
boundary — the only boundary that decides whether a human looks.

## What Changes

- New `router/risk_judgment.py`: one request carrying a 4-level blast-radius
  Score plus four red-line Nouls (`irreversible_data_loss`,
  `weakens_access_control`, `moves_money`, `disables_a_safeguard`), and a
  **pure** `compose_tier()` that maps them onto the existing `RISK_ORDER`. The
  policy stays in code, reviewable and regression-testable offline; the
  service is asked for observations, never for the tier.
- Red lines are a **floor**, not an assignment: a hard red line forces
  `critical`, a weakened safeguard forces `high`, and neither can lower a
  higher blast-radius baseline. A change can read as "ordinary" and still
  destroy data — that is exactly what the red lines are for.
- `classify_risk(text, *, judgment=False)` keeps its signature and mapping and
  gains the second backend. The keyword table remains the default and the
  fallback.
- **Fail safe, always.** `judge_risk()` returns `None` on a missing
  `TYPESAFE_API_KEY`, any HTTP/transport error, a timeout, an unparseable body,
  or an answer set missing a field — and the keyword table is used, which errs
  toward over-gating. CI needs no key and no network.
- **`classify()` stays pure by default.** `risk_judgment_enabled` defaults to
  `False`, so `classifier_coverage`'s replay and every test are unchanged and
  deterministic. Only `main()` — which already does a live `gh` lookup —
  turns it on, with `--no-risk-judgment` to opt out. Route selection never
  consults it; only `risk`/`risk_signals` change.
- The probe set lands as a repo fixture with **recorded answers**
  (`tests/fixtures/risk_probe_answers.json`), so the tier mapping is
  regression-testable with no key and no network.

## Impact

- Affected specs: `pr-risk-tiering` (new)
- Affected code: `src/worktrail/router/risk_judgment.py` (new),
  `src/worktrail/router/classify.py`
- No change to route selection, `RISK_ORDER`, the `go:risk-*` label mapping,
  `automerge_eligible()`, or `pre_pr_gate`'s docs-only risk cap.

## Open questions answered

The brief asked whether an external call is acceptable in the risk path at PR
time, or must be opt-in per repo. It is **optional by construction**: absent a
key the judgment path is never entered, and the behaviour is exactly today's.
No per-repo policy key is added, because there is nothing to disable on a
machine that has no key — adding one would be configurability for a scenario
the fallback already covers.

The brief also asked whether the probe set needs operator review before becoming
a pinned fixture. It is pinned with its provenance stated in its own `_meta`:
deliberately adversarial, measuring the capability gap and **not** a real-world
base rate, so the pass rates it produces are not accuracy figures for either
backend on live briefs.

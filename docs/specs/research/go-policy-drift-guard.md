# Discovery — automated go-policy.yaml / CI drift guard

Route A (idea-discovery) note. Source brief:
`20260731-171931-build-an-automated-go-policy`. Upstream evidence brief:
`20260731-080113-workspace-wide-audit-for-pre` (done).

Status: **discovery only — no implementation.** Gate
`no_implementation_without_approval` applies.

## Problem

`docs/specs/go-policy.yaml` justifies each repo's pre-PR gate in freeform
rationale comments. Those comments encode *claims about repository reality*:

| Claim class | Example (verbatim from a real policy file) |
|---|---|
| absence | `This repo has NO CI workflows, so this local gate is the only automated check` |
| absence | `No test suite in this repo; npm run lint is tsc --noEmit` |
| mirror | `Mirrors the "Lint, Test & Build" job in .github/workflows/ci.yml` |
| reference | `Also enforced in CI: .github/workflows/ci.yml` |

Reality moves — tests get added, CI gets added — and the comments do not. The
gate's stated justification silently becomes false. In the worst case the
divergence is not cosmetic: **real test files exist that nothing ever runs.**

This failure mode has now surfaced twice, both times discoverable only by
manual `git log` archaeology:

1. **devops PR #66** — a stale "no test suite" comment justified
   `pre_pr_cmd: skip` for over a week after PRs #64/#65 added a real 10+14-test
   suite.
2. **The 16-repo manual audit** (brief `20260731-080113`) — found 4 more
   instances by hand-grepping every repo in the workspace.

Nothing re-runs that comparison automatically. A third recurrence is the
expected outcome of doing nothing.

### Who benefits

The workspace operator (one person, 17 repos) and every agent session that
reads `go-policy.yaml`'s rationale to decide whether the configured gate is
adequate. An agent that trusts "no test suite in this repo" will not go looking
for the suite that now exists.

### Smallest complete outcome

A passive detector that, per repo, flags:

- **(a)** test files that neither `pre_pr_cmd` nor any CI workflow ever runs; and
- **(b)** prose absence-claims contradicted by filesystem reality.

### What would make this unsuccessful

False positives. A detector that flags healthy repos gets ignored within a
week, and is then indistinguishable from having built nothing. Precision
matters more than recall here — this is an advisory nudge competing for
attention on a dashboard, not a blocking gate.

## Evidence: the core signal works

A throwaway probe implementing signal (a) + (b) was run against all 15 repos
under `~/projects/` that have a `go-policy.yaml`. Heuristic: does the repo
contain git-tracked test files, and does `pre_pr_cmd`/`integrate_smoke_cmd` or
any `.github/workflows/*.yml` invoke a recognised test runner (`pytest`,
`jest`, `vitest`, `node --test`, `go test`, `npm test`, …)?

```
repo                             tests  wf  gate   ci  flagged
behindthedash                        3   4 False False  [!] orphaned-tests + stale-claim
briankudera                          0   2 False False
continuum                            3   3  True True
datalena                           722  23  True True
devops                               4   5  True True
feedback-capture                     7   1  True True
gracefully-giving-back             219   6  True True
kudera-consulting                    0   2 False False
kudera-shared-db                     0   0 False False
mailbox-service                      3   2  True True
note-forth                           1   1  True True
pullhook                            28   4  True True
roost-radar                          1   1 False False  [!] orphaned-tests + stale-claim
when-truth-becomes-optional          0   6 False False
worktrail                          117   4  True True
```

**Result: exactly the two functional gaps the manual audit found, and nothing
else.**

- `behindthedash` — 3 orphaned pytest files under `ci/scripts/release_notes/`
- `roost-radar` — orphaned `firestore.rules.test.ts`

Zero false positives across the other 13 repos, including the two extremes
(datalena at 722 test files, `kudera-shared-db` at zero tests and zero
workflows). The two *doc-only* drift cases the audit found
(`kudera-consulting`, `pullhook`) correctly no longer flag — both were fixed
during the audit session (PR #7, PR #60), which is itself a useful negative
control: the prose check tracks the file's current state, not a stale memory
of it.

Probe script (not shipped, reproduce-only):
`scratchpad/probe_drift.py` from run `go-20260731-172620`.

## Scope: three check classes, very different confidence

**Class 1 — orphaned tests (structural, comment-independent).** Needs no prose
parsing at all: glob test files, check whether any test runner appears in the
gate command or CI. This caught both *functional* gaps. Highest value, lowest
false-positive risk. **Build this first.**

**Class 2 — prose absence-claims (fragile).** Regex over freeform English
("no test suite", "NO CI workflows", "no linter") contradicted by reality —
test files, workflow files, and lint configuration respectively. Caught the two
*doc-only* cases — zero functional risk, cosmetic accuracy only. Worth having,
but must be a narrow high-precision phrase set, advisory only.

**Class 3 — stale ruleset job names.** The audit checked every
`.github/rulesets/*.json` `required_status_checks` entry across 13+ ruleset
files against real workflow job ids and found **zero** stale references —
clean workspace-wide. Building for it now is speculative. **Defer.**

*Forward-looking:* the comments that were *fixed* now embed **mirror-claims**
("Mirrors the `Lint, Test & Build` job in `ci.yml`"). That is Class 3's problem
relocated into policy prose — a job name that could later be renamed. Cheap to
add once Class 1 exists; not worth its own effort now.

## Candidate homes

### Option A — vendored CI workflow per repo (the brief's first suggestion)

Sibling to the existing `rulesets_drift_guard.yml` pattern.

- **Pro:** runs automatically on PR; can block.
- **Con — decisive:** the repos that actually drift are the ones that *cannot
  host it*. `behindthedash` and `roost-radar`, both flagged, have no CI
  workflow running tests at all. Additionally, only 4 repos have the rulesets
  guard pattern to copy, each copy needs its own Python deps, and
  policy-adjacent copy-paste across repos is *literally the failure mode*
  `policy_selfcheck.py` exists to detect.

### Option B — worktrail selfcheck module (**recommended**)

Worktrail already has an established passive-detector family:

| Module | Detects | Surfaced via |
|---|---|---|
| `router/policy_selfcheck.py` | cross-repo copy-paste contamination in `go-policy.yaml` | `dashboard.py` repo rows (`policy_findings`) |
| `router/automerge_selfcheck.py` | auto-merge workflows not gating on `go:no-automerge` | `dashboard.py` repo rows (`automerge_findings`) |
| `router/dashboard_selfcheck.py` | (in flight — OpenSpec change `dashboard-selfcheck`) | same |

The shape is settled and repeatable: `check_repo()` → `sweep()` → `main()`,
`{signal, detail}` findings, exit 1 when flagged, `discover_repo_names()`
reused from `policy_selfcheck`, a `worktrail-*-selfcheck` console script in
`pyproject.toml [project.scripts]`, tests mirroring
`tests/router/test_policy_selfcheck.py`, and a one-line dashboard nudge.

- **Pro:** one `pip install worktrail` covers all 17 repos *including the
  CI-less ones*; zero per-repo vendoring; proven delivery surface; degrades
  silently on import failure like every other optional dashboard import.
- **Con:** surfaces only when someone runs `/go` — a nudge, not a hard gate.
  That is the deliberate posture of the whole family ("passive detector, not a
  gate … never blocks").

### Option C — extend `policy_selfcheck.py` in place

- **Pro:** one module for "things wrong with go-policy.yaml"; dashboard wiring
  already exists.
- **Con:** that module's stated scope is specifically *cross-repo
  contamination*, and its inputs are the policy text alone. Drift-vs-reality
  needs to glob test files and read workflow YAML — a different concern with
  different inputs. Mixing them muddies both docstrings and both signal sets.

**Recommendation: Option B**, a new sibling module reusing `policy_selfcheck`'s
helpers. Class 1 first, Class 2 as a narrow phrase set, Class 3 deferred.

## Risks and unknowns

1. **Reachability is undecidable in general.** `pre_pr_cmd` is an arbitrary
   shell string; knowing whether `npm test` reaches
   `firestore.rules.test.ts` requires resolving `package.json` scripts, and in
   the limit, running it. The probe sidesteps this with an honest weaker
   question — *does the gate invoke any test runner at all?* — which is
   sufficient for both real cases (both gates are lint+build only). This
   under-detects a repo whose gate runs *some* tests but not a given orphaned
   file. Accepting that under-detection is what keeps precision at 100%.
2. **Test-file globbing needs a conservative allowlist** and hard ignores for
   `node_modules`, `.venv`, `dist`, `.next`, `vendor`, `__pycache__`.
   Restricting to git-tracked files (as the probe does) removes a whole class
   of stray-artifact noise.
3. **Multi-language repos** where the gate covers one language's tests and
   another language's tests are orphaned — the runner-presence heuristic
   returns `True` and the orphan is missed. Known gap; a per-language refinement
   is possible later.
4. **Class 2 phrase drift** — comments are prose, so the phrase set will always
   be partial. It should never be the only signal for a repo.
5. **Dashboard attention budget.** Three selfcheck nudge lines already compete
   for the same space. If the count grows further, they likely want collapsing
   into one "repo hygiene" line with a drill-down.

## Decision

**Taken 2026-07-31 (run `go-20260731-172620`): Option B, plus the pre-PR gate
warning.** Build the sibling selfcheck module (Class 1 + narrow Class 2,
dashboard-surfaced) *and* additionally surface the Class 1 signal in
`pre_pr_gate.py` as a non-blocking `WARNING` at PR time.

The trade-off was accepted explicitly: this steps outside the family's
passive-only posture, on the grounds that two of the four audit findings were
functional gaps that shipped for weeks, and the `/go` dashboard is only read
when someone happens to run `/go`. The concession that keeps it honest is that
the warning has **no exit code of its own** and cannot block a PR, and any
exception inside the detector is swallowed — a detector must never be able to
break the gate it advises.

Class 3 (stale ruleset job names) remains deferred: zero findings workspace-wide.

### What shipped

| Artifact | Role |
|---|---|
| `src/worktrail/router/policy_drift_selfcheck.py` | `check_repo()` / `sweep()` / `main()`, plus `orphaned_test_paths()` as the cheap entry point for the gate. Four signals: `orphaned-tests`, `stale-claim-no-tests`, `stale-claim-no-lint`, `stale-claim-no-ci` |
| `pyproject.toml` | `worktrail-policy-drift-selfcheck` console script |
| `src/worktrail/router/dashboard.py` | `drift_findings` on each repo row + a `🚩 Policy drift` nudge line |
| `src/worktrail/router/pre_pr_gate.py` | non-blocking `WARNING` before the gate command runs |
| `tests/router/test_policy_drift_selfcheck.py` | 23 tests + 5 subtests |
| `tests/router/test_pre_pr_gate.py` | 5 added tests, incl. "failing gate is not rescued" and "detector failure cannot break the gate" |
| `tests/router/test_dashboard.py` | 1 added row+render integration test |

Implementation notes worth keeping:

- File enumeration is `git ls-files`, not globbing. One subprocess instead of a
  walk, and gitignored build output and `node_modules` are excluded for free
  rather than via a maintained ignore list.
- Prose claims are matched against **comment lines only**. Scanning the whole
  file would let a command that happens to contain "no test" read as an
  authored claim — there is a regression test for exactly that.
- The detector degrades to "no findings" when `git ls-files` fails (e.g. a
  fixture directory with a fake `.git`), which is why it could be added without
  disturbing any existing dashboard test.

Verified against the live workspace: flags exactly `behindthedash` and
`roost-radar`, the two repos the manual audit found, and nothing else.

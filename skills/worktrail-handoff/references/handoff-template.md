# Handoff brief — format and example

Write each brief in the shape below. Rules:

- Frontmatter fields are literal; set `repo`/`remote` to `null` when not working in a repo.
- `status:` starts at `queued`; `work_queue.py` advances it (`picked` on claim, `done` on
  completion). Don't set it past `queued` by hand.
- `blocked-by:` is optional. List the IDs of prerequisite briefs that must be `done` before
  this brief is actionable. `work_queue.py list` surfaces blocked briefs in a separate section;
  `claim` succeeds but emits a `warnings` key so the agent can see the dependency. Omit
  the field entirely when there are no prerequisites. **Hard, gating dependency** — a brief
  with unsatisfied `blocked-by` entries cannot be considered ready.
- `related:` is optional. List the IDs of briefs that touch the same surface — **non-blocking
  awareness links**, not dependencies. `work_queue.py list` surfaces related IDs in each brief
  object; `claim` ignores them entirely (they never gate, block, or warn). Unlike `blocked-by`,
  `related` is purely informational: reading the linked briefs is advisory, not required. The
  field is **symmetric** — if brief A lists B, B also lists A (both sides are maintained by
  `work_queue.py link`). A pair that is already a `blocked-by` relationship in either direction
  MUST NOT also be a `related` link; if both ever appear on the same file, `blocked-by` wins
  and the `related` entry is deduped/dropped. Omit the field entirely when a brief has no
  related neighbours — no empty stub required.
- `recommended-route:` is optional, but the create workflow's Step 2.5 stamps it automatically
  by running `classify.py` against the focus text — a single GO v2 route letter (`A`–`J`)
  naming the route the brief is expected to take (e.g. `F` for a defect, `I` when the cause is
  still unknown). `sdd-workflow handoff[:id]` feeds it to its classifier as a strong (not
  absolute) signal, and `/go auto`'s unattended runs need it to avoid halting on an ambiguous
  pick with no one to ask; the consuming run re-verifies against actual repo state and may
  reroute. Only omit it when Step 2.5 degrades (classify.py absent/erroring, or a low-confidence
  ambiguous result) or the capturing agent has direct evidence classify.py can't see.
- `implementation-intent:` is optional and controls the Route-C transition for a concrete brief:
  `requested` continues into implementation after a clean spec, `planning-only` intentionally
  stops after planning, and `unknown` requires one decision. Missing values are treated as
  `unknown`; a Route-C brief must never be silently marked done.
- `change-kind:` is optional. Use `new`, `delta`, or `bugfix` only when the capturing agent
  has strong evidence about how SDD should treat the brief. `new` hints toward a new spec,
  `delta` hints toward `specs.change-spec --type=delta`, and `bugfix` hints toward
  `specs.change-spec --type=bugfix`. It is a hint, not a final decision; the consuming
  `sdd-workflow` run re-checks the current repo state.
- `target-spec:` is optional. Use a `docs/specs/<spec-folder>` folder name when the brief
  likely changes an existing spec. It helps the deterministic handoff matcher find the
  current spec but does not force the final route if the repo has drifted.
- `next-check-after:` is optional. An ISO-8601 date (`YYYY-MM-DD`). Set it when a recheck
  finds the brief still legitimately blocked on an external, out-of-band event (an upstream
  package release, a third-party API change) and nothing about the situation will change
  before then — pick a forward date matched to how fast that upstream moves (e.g. `+7d` for a
  fast-moving package, `+14d`/`+30d` for a slow one). `work_queue.py list`/`/go`'s picker treat
  the brief as **not yet due** until that date passes: still visible, but excluded from
  auto-pick and the claimable picker options, so it stops consuming a full recheck session for
  zero new information. Omit the field entirely when there is no known external blocker, or
  set it via `work_queue.py release <id> --next-check-after <date>` when releasing after a
  recheck.
- `released-at:` is set automatically by `work_queue.py release` (never by hand) — an
  ISO-8601 timestamp of the release. For 20 minutes after, `work_queue.py list`/`/go`'s
  picker and auto-pick treat the brief as **recently released**: still visible and
  claimable, but not preferred, so a session doesn't immediately re-pick a brief another
  live session just released moments ago (a claim/release race, or a considered
  not-yet-actionable judgment) and repeat the same investigation.
- `watch:` is optional and independent of `next-check-after:` — a brief may set either, both, or
  neither. A YAML list of watch entries in the form `<registry>:<package>@<version>` (e.g.
  `npm:left-pad@1.3.0`), one per upstream package to track; `npm` and `pypi` are the only
  supported registries. Each entry's `@<version>` is the recorded version last observed.
  `dist_tag_watch.py` queries each entry's registry, and when the published version has moved
  past the recorded one, advances the recorded version in place. Setting `watch:` together with
  `next-check-after:` lets an unattended cron/`/loop`-scheduled run clear the backoff
  automatically once the watched package updates, instead of waiting out the calendar date.
- Reference commits, PRs, and file paths rather than reproducing their content.
- Mark each open item by type: **Decision needed**, **Needs investigation**, or **Blocked on**.
- Keep the whole brief under ~150 lines. Dense > verbose.

A complete, filled-out example:

```markdown
---
id: 20260531-141200-auth-middleware-error-handling
created: 2026-05-31T14:12:00-05:00
focus: Surface the real failure reason in the auth middleware instead of swallowing it
repo: /home/briank/projects/acme-api
remote: https://github.com/acme/acme-api
base-branch: main
status: queued
suggested-skills:
  - devkit.fix-debugging
recommended-route: F   # optional; GO v2 route letter A-J, omit when unsure
implementation-intent: requested  # optional: requested|planning-only|unknown
change-kind: bugfix     # optional; one of new|delta|bugfix, omit when unsure
target-spec: 003-auth   # optional; docs/specs folder slug, omit when unknown
# blocked-by:        # optional; omit when there are no prerequisites
#   - 20260531-130000-some-prereq
related:             # optional; omit when there are no related briefs
  - 20260531-120000-auth-token-expiry-investigation
  - 20260530-093000-jwt-library-upgrade
---

## Focus

The auth middleware catches every token-validation error and returns 401 with no logging,
so expired vs. malformed vs. revoked tokens are indistinguishable in production. Branch on
the error type and log the reason (without leaking token contents) so failures are
diagnosable. Surfaced while building the /reports endpoint, where a valid token 401'd.

## Discovery context

- Found while implementing /reports (PR #214) — a known-valid token returned 401.
- `src/middleware/auth.ts:42` wraps `verifyToken()` in a try/catch that discards `err`.
- The catch emits no log line; it returns `res.status(401).end()`.
- Hypothesis (unverified): clock skew makes `exp` failures look like malformed tokens.

## Suggested approach

1. In `src/middleware/auth.ts:42`, branch on the jwt error name (TokenExpiredError vs.
   JsonWebTokenError) and log it at warn level — no token payload.
2. Add a regression test per error class in `test/auth.middleware.test.ts`.
3. Reproduce against PR #214 before closing.

## Key artifacts

| Artifact | Location |
|---|---|
| Originating PR | https://github.com/acme/acme-api/pull/214 |
| Offending code | src/middleware/auth.ts:42 |
| Handoff brief | (this file) |

## Open questions / blockers

- **Needs investigation** — is the clock-skew hypothesis real? Check server NTP + token `iat`.
- **Decision needed** — should revoked tokens return 401 or 403? Product call.

## Suggested skills

- `devkit.fix-debugging` — systematic root-cause for the swallowed error.
```

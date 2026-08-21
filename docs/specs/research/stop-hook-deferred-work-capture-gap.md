# Discovery: a second Stop-hook check for uncaptured brief-named deferrals

Source: work-queue brief `20260821-140736-add-second-narrow-check-to`.
Route A (idea discovery) — this note is discovery only; no change created, no code written.

## Problem framing

**User/business problem.** `hooks/suggest_next_step.py` fires once per session (when
substantive work happened) and injects a single prompt-based check: the EXCEPTIONAL-VALUE
gate, which asks the agent to judge whether *its own new, creative idea* clears a high bar
before capturing a handoff. That gate is deliberately narrow and works as intended for its
job — it keeps routine follow-up work from spamming the queue.

But a different kind of item falls through the same hook with no check at all: a deferral
the *author already named explicitly* in this session's own run record or PR ("keep this
advisory for a PR or two", "out of scope for this PR", "once calibrated, promote to
required"). That's not a new idea needing a judgment call about exceptional value — it's
already-decided, concretely-scoped follow-through the session itself is the authoritative
source for. Motivating incident: brief `20260820-024527` named a two-phase rollout in its
own suggested approach; the EXCEPTIONAL-VALUE gate correctly excluded it as routine (not a
step-change), and no other mechanism caught that a real, self-named deferral was never
captured as a handoff.

**Who benefits.** The operator and any future agent session that treats the work queue as
the source of truth for outstanding work — a self-named deferral that never becomes a brief
is invisible to `/go`, dashboards, and drain.

**Observable behavior to improve.** At session Stop, in addition to today's
judgment-based EXCEPTIONAL-VALUE gate, something should notice when this session's own
run record or PR explicitly named deferred work and no handoff exists that covers it —
and only then.

**Smallest complete outcome.** One additive, narrow check that (1) finds deferral language
the session itself stated, (2) checks whether a matching handoff already exists, (3) flags
only the unmatched case. It must not touch or loosen the EXCEPTIONAL-VALUE gate.

**What would make it commercially unsuccessful.** Either failure direction kills trust in
the mechanism: too many false positives (nagging on legitimate, already-adjudicated
exclusions) trains the operator to ignore or mute it; false negatives on real deferrals
just recreate the exact gap this brief exists to close. The brief itself flags this as an
open, unsolved risk — confirmed below, with a concrete collision found in the actual
schema.

## Prior art check

Ran `worktrail-overlap-check` against both `docs/specs/` and `openspec/`. No existing spec
matches actor+capability+primary domain for "detect self-named deferral language at
session-stop and cross-check the work queue." Nearest neighbors by subject matter —
`human-decision-queue`, `duplicate-brief-detection`, `related-brief-collision-guard`,
`stale-brief-precheck` — all operate on a claimed brief's own text against other briefs or
against merged code; none reads a *live session's* run record/PR to catch something never
turned into a brief at all. This is a new capability, not an extension.

## What the hook actually is today (grounded read of `hooks/suggest_next_step.py`)

Important, non-obvious finding: the hook is **not** a scanner. Its entire job is: read
`session_id`/`transcript_path` from stdin, grep the transcript once for evidence of
substantive work (`Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool_use, or a bash command
containing `git commit`/`gh pr create`/`gh pr merge`/`git push`), and — once per session,
gated by a sentinel file — print a fixed `INSTRUCTION` block as a `decision: block` reason.
All of the actual judgment (offer next-step ideas, apply the EXCEPTIONAL-VALUE gate, decide
whether to run `worktrail-handoff`) happens in the *calling agent's own reasoning* after
reading that instruction text, not in Python. The hook holds no dependency on YAML, the
run-record format, or the work queue today; it only ever touches the transcript file. It
also has no `gh`/network call and must stay fast and fail-open (bare `except Exception:
return 0`), and it never runs when `CC_HEADLESS=1`.

This matters directly for scope: a "scan the run record's deferred_work field and/or PR
body" check as literally described is a materially different kind of component than what
exists — deterministic, stateful, cross-referencing — not a tweak to the existing regex/gate
text.

## The unsolved mechanical problem: finding *this session's* run record

The hook receives only `session_id` and `transcript_path` — no run-record path, no repo,
no PR number. Nothing in `run_record.py`'s schema stores a Claude session id today (checked
`cmd_start`'s full field list — no `session_id` key). So "read the run record's
deferred_work field" first requires answering "which run record(s) did this session touch?"
Three candidate approaches, in ascending cost:

1. **Grep the transcript for run-record paths.** Every `/go`/sdd-workflow dispatch in this
   session echoes its `$RUN` path (`~/.worktrail/runs/<repo>/go-*.yaml`) in bash
   commands/output — visible in this very exploration's own transcript. The hook already
   reads the whole transcript once for `entry_has_work`; extracting `~/.worktrail/runs/.*\.yaml`
   path literals from the same pass is a same-shape, no-new-dependency extension. Weakest
   part: brittle if a session never happens to print the literal path (e.g., pure-headless
   dispatch, which is out of scope anyway since `CC_HEADLESS=1` already returns early).
2. **Match by repo + recency.** List run records under the resolved repo's run dir with
   `started_at`/`updated_at` inside this session's time window. No new state, but a fuzzier
   signal (concurrent sessions in the same repo would collide) — the exact ambiguity
   `concurrent-go-dispatch-brief-claim-race.md` already documents run records being fragile
   to.
3. **Add a `session_id` field to the run record schema**, threaded through from `/go`'s
   Phase 6 `start` call. Deterministic and robust, but the largest footprint: a schema
   change plus new plumbing through `worktrail-go/SKILL.md`'s dispatch contract and
   `sdd-workflow`'s Phase 6 — the kind of change this brief's own framing ("narrow,
   additive") argues against reaching for first.

(1) is the cheapest fit for "narrow"; (3) is the only one that's not probabilistic.

## A concrete false-positive collision already in the schema

The run record's own `scope_review` mechanism (Phase 8, `worktrail-go/SKILL.md`) requires
recording each requested outcome as `complete`, `blocked`, or **`out-of-scope`** — and an
`out-of-scope` item is legitimate only with a reason beginning `different purpose:` or `user
approved:`. That is, `"out of scope"` — one of the exact example phrases this brief names as
deferral language — is *already* the vocabulary of a different, already-adjudicated,
code-structured decision elsewhere in the same run record. A naive text-pattern scan over
the whole record (or a PR body that quotes it) would flag correctly-excluded scope-review
items as missed deferrals. Any implementation needs to scope its scan to genuinely
free-text deferral prose (the `deferred_work` list's own entries, or PR-body prose outside
a structured scope-review section) — not just grep the word "scope" anywhere.

## Two candidate shapes for the check itself

**A — Prompt-only, agent-judged (cheapest).** Add a paragraph to `INSTRUCTION` asking the
agent to itself notice, from what it already knows about the session, whether it stated a
deferral with no corresponding handoff, and capture it if so. Same category of mechanism as
today's EXCEPTIONAL-VALUE gate (agent judgment, not code). Minimal diff — one string edit,
no new dependencies, no run-record/session-id discovery problem to solve. Weakness: this is
structurally the same kind of check that already let the motivating incident through once
(the agent was following that hook's own instructions and correctly judged the item
*not* exceptional — a sibling prompt-only check asks the same agent, in the same moment, to
make a second judgment call, with no independent signal forcing the catch).

**B — Mechanical, deterministic (what the brief's wording literally asks for).** The hook
(or a script it shells out to, following the existing precedent of
`check_brief_staleness.py`'s bounded-probe-extraction-and-search pattern) resolves this
session's run record(s) (see options above), extracts `deferred_work` entries and, if a
`pull_request` URL is set, that PR's body; matches a curated deferral-phrase set scoped away
from the `scope_review` vocabulary; and cross-checks any match against `queue/` + `picked/`
brief content the same way `check_related_brief_collision.py` already does. Only prints the
block instruction when something matches with no covering brief found. Strictly stronger
guarantee than (A) — doesn't rely on the agent noticing — at the cost of real engineering:
run-record discovery, a phrase list that needs the false-positive tuning the brief itself
already flags as an open risk, and (if PR-body scanning is kept) a `gh pr view` call this
hook has never needed before, which adds a network dependency to something that must stay
fast and fail-open.

## Open questions / unknowns

- Is PR-body scanning worth the added `gh` dependency and latency, or should v1 scope to
  `deferred_work` only (in-repo, no network) and treat PR-body scanning as a later
  extension?
- Which run-record-discovery approach (transcript-grep vs. schema field) is acceptable —
  this is the single biggest scope/robustness tradeoff in the whole idea.
- Where does the deferral-phrase list live and get tuned — hardcoded in the check, or a
  policy-configurable list per `go-policy.yaml` (mirroring how other narrow checks in this
  repo, e.g. `check_brief_staleness.py`'s probes, are tuned)?
- Does a false-negative-friendly v1 (narrow phrase list, `deferred_work`-only, transcript-
  grep discovery) ship first with tuning as explicit deferred work of *this* change — or
  does the brief's own "expect false positives/negatives, plan to tune it" argue for landing
  it behind a non-blocking/advisory posture (print, don't block) for its first iteration?

## Recommendation

The idea is real and the gap is confirmed by grounded reading of the current hook — it has
zero mechanism for this today. Recommend **Route C (feature planning)** next, scoped to
Approach B narrowed to: `deferred_work`-only (no PR-body/`gh` dependency in v1),
transcript-grep run-record discovery, and a deferral-phrase list explicitly scoped away from
`scope_review` vocabulary — with the four open questions above resolved as part of that
spec's design, not deferred further. Do not fold this into the EXCEPTIONAL-VALUE gate text.

## Context

The interactive intake-triage gate has two actors' worth of state living in one file. Step 1
(`skills/worktrail-go/SKILL.md`, Phase 2) runs `worktrail-skill-dispatch
--evaluate-brief-triage` with stdout redirected to `${TMPDIR:-/tmp}/triage-verdict-${BRIEF_ID}.json`;
step 2 re-derives the same path and runs `--apply-brief-triage-file` on it. Because the path
depends on nothing but the brief id, a scheduled queue-triage one-shot also running
`worktrail-go BRIEF-ID` (the same skill text, headless) shares it. Two live incidents on
2026-10-02 (brief `20261002-203443-triage-verdict-temp-file-race`) showed both halves of the
hazard: a cross-dispatch overwrite where the loser's apply could consume the winner's verdict
for the same brief, and an interleaved write followed by a removal under the reader, leaving a
session holding a stale verdict against a brief another triager had already rewritten.

The apply side (`skill_dispatch.py`'s `--apply-brief-triage-file` branch) reads whatever path it
is given, validated only by the brief-ownership guard (`_guarded_brief_path`, raising
`BriefOwned`/`BriefMissing`) -- which protects the *brief* but says nothing about the verdict
file or about the brief's content having changed since evaluation.

See `proposal.md` for the full motivation. This document records the decisions behind the two
guards and the refusal taxonomy.

## Goals / Non-Goals

**Goals:**

- Two concurrent triages of the same brief can never read, overwrite, or consume each other's
  verdict file.
- A verdict is applied only if it was computed against the brief content that is still on disk
  at apply time; every other outcome refuses loudly and names the remedy.
- Keep the gate's two-step shape (separate Bash calls, no shell state carried between them)
  and its "the verdict JSON is never re-typed" property intact.

**Non-Goals:**

- The batch/scheduled `queue_triage evaluate` → `apply` pipeline. It writes a run-scoped
  `<out_dir>/verdict.json` (no shared path), and its per-verdict ownership/`_owned_error`
  guards are separate machinery. Threading the digest requirement through `apply_verdicts()`
  would change scheduled-run semantics well beyond the observed failure.
- Locking. A `flock` cannot span the gate's two Bash calls (each is its own process), a
  host-local lock is unsound when the queue dir is shared across hosts (the claim mechanism's
  own reason for being rename-based), and the digest check removes the need: staleness is a
  property of content, not of who holds a lock.
- Enforcing anything about the path *shape* in code. The tool never sees the writer's path
  (the shell redirects stdout), and the apply cannot know the caller's dispatch id without a
  new flag -- the dispatch-scoped path is a gate-prose rule, pinned by the prose-enforcement
  test, not a runtime check.

## Decisions

### The verdict path is scoped to the dispatch: brief id plus `$INVOCATION_CONTEXT_DISPATCH_ID`

`${TMPDIR:-/tmp}/triage-verdict-${BRIEF_ID}-${INVOCATION_CONTEXT_DISPATCH_ID}.json`. The
dispatch id is `go-<16 hex>`, generated fresh per `/go` invocation
(`invocation_context._generate_dispatch_id()`) and documented as "the stable identity for this
one `/go` invocation" -- stable across both gate steps, distinct across two concurrent
dispatches, exactly the property the verdict file needs. It is also the identity the claim
guard already uses (`--by` on every claim), so the verdict file now carries the same scoping
the claim does.

Alternatives rejected:

- **`mktemp`** (the skill's other temp files): the two steps are separate Bash calls, so a
  random path chosen in step 1 would have to be carried in agent context and substituted into
  step 2. The dispatch id preserves the archived file-form design's property that the path is
  *re-derived* deterministically in both steps, from values the skill already holds for the
  whole session -- a shorter path from "hold this string" to a typo.
- **`$$`/pid**: differs between the two Bash calls by construction.
- **A run-scoped directory under `worktrail_home()`**: the same re-derivation problem as
  `mktemp` (which run?), plus a new state root to clean up; `/tmp` with a unique tail is
  already the skill's convention.

### Provenance is a whole-file SHA-256 digest recorded at evaluation, verified at apply

`evaluate_single_brief()` captures `sha256(brief bytes)` after the linked-decision /
repo-inference pre-pass and immediately before the verdict is produced (the evaluator spawn, or
the escalation matrix when no evaluator runs), and stamps it on the returned verdict as
`brief_digest`. `apply_single_brief_verdict()` re-computes the digest of the re-resolved
brief after the ownership guard and refuses on mismatch.

- **Whole file, not just `focus:`.** What the evaluator saw includes the focus text, but a
  concurrent *apply* also appends `## Triage` notes, stamps `seeded-from:`/`recommended-route:`,
  and rewrites `focus:`; `consecutive_keep_count()` reads the file (the keep streak is
  escalation input). Any of those changing between the two steps invalidates the verdict. A
  focus-only digest would miss the note/streak mutations. Over-invalidation is the safe
  direction: a benign third-party stamp costs a re-run of the evaluate step, not a wrong apply.
- **Captured before evaluation, not after.** Capturing after the evaluator returns would
  record the *post*-evaluation content; a brief mutated during the (minutes-long) evaluation
  would still match at apply time and the stale verdict would sail through. Only a
  pre-evaluation digest catches mutations during the evaluation window.
- **Stamped on the `Verdict` object, not a side channel.** The digest must ride the JSON the
  tool prints (the skill redirects stdout; the tool never sees a path). `Verdict` gains
  `brief_digest: str | None = None`; the batch pipeline never sets it, its `verdict.json`
  entries gain a `null` key, and `--apply-brief-triage <json>` payloads without the field
  behave exactly as before. Rejected: returning `(verdict, digest)` from
  `evaluate_single_brief()` -- a signature change with ~15 test call sites and no benefit; and
  injecting the key only at the print site -- which cannot work, because by print time the
  function has returned and the pre-evaluation digest is gone.

### Refusals are `blocked_*` exit-2 lines, behind the ownership guard

`blocked_verdict_stale: <brief-id> (brief content changed since evaluation)` and
`blocked_verdict_unattributed: <path>` join the gate's existing exit-2 family (null on stdout,
one line on stderr, nothing applied): both are preconditions under which the agent must not
proceed, with a nameable remedy (re-run the evaluate step), not malformed input. The malformed
family (unreadable file, invalid JSON, null/non-object/no-verdict payload) keeps its exit-1
`status: error` entries and precedence -- validation happens before provenance.

Ordering inside the apply: `_guarded_brief_path` (missing/owned) first, then the staleness
check, then `resolve_duplicate_targets()`/`apply_verdicts()`. A brief claimed by another
claimant refuses as `blocked_brief_owned` even when its content also changed; "it is theirs" is
the more actionable message. The staleness check runs for previews too, so a preview never
shows an action the confirmed apply would refuse.

### A confirmed file-form apply consumes the verdict file

After a successful read and JSON parse, `--confirm` removes the file. Rationale:

- A *retried* step 2 (agent retry after a transient failure) would otherwise re-apply the same
  verdict: for `keep` that appends a second `## Triage` note and inflates `keep-count` (an
  escalation input), and for a verdict whose brief is unchanged the digest check alone cannot
  catch it -- the content still matches.
- A file that survives its apply is also what let the incident's second writer interleave into
  a path the reader had already used.
- Previews keep the file (the documented preview→confirm flow reuses it), and a file that
  could not be read or parsed is left in place for inspection -- a torn file is evidence.

## Risks / Trade-offs

- [Older installed skill text writes the un-scoped path] → the *code* side still protects:
  the new evaluate prints the digest regardless of which skill text invoked it, so a
  mis-scoped file from an old text still gets the staleness guard, and the path collision
  persists only until the skill refresh propagates (a running session needs a restart).
- [A hand-made or third-party caller passes a digest-less file] → refused with
  `blocked_verdict_unattributed`; the documented producer (`--evaluate-brief-triage`) always
  stamps one.
- [The digest refuses a verdict whose brief was edited harmlessly] → accepted; the refusal is
  loud, names the cause, and the remedy is re-running the evaluate step against the current
  content -- the same remedy every other blocked_* line in the gate points to.
- [A verdict file is left in `/tmp` when the flow aborts between steps] → one small file per
  dispatch, unique by construction; consumed on the next confirmed apply, and never a
  collision source.
- [A same-dispatch retry of step 1 rewrites the file] → intended: the file is scoped to the
  dispatch, and step 1 always re-evaluates; the rewrite is the retry working.

## Migration Plan

1. Land the code (field, digest stamp, refusals, consumption) and the gate-prose + prose-test
   updates together -- the spec's gate scenario pins both, and the batch pipeline is untouched
   on both sides.
2. Rollback is reverting the commit: the `Verdict` field defaults to `None`, so a reverted
   apply reads no digest and behaves exactly as before; no state migrates.

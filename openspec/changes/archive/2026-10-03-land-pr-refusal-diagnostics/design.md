## Context

See proposal.md — Why. The constraint that shapes every decision below is that two sibling
helpers in the same module already solved this exact problem in two different ways:

- `_ensure_compile_markers` returns `(refused_step, detail)` today.
- `_run_record_main` captures a module `main()`'s stdout and stderr under
  `redirect_stdout`/`redirect_stderr`, keeps a string `SystemExit` code as the detail, and
  otherwise falls back to the captured stderr.

The two broken helpers sit on either side of those precedents: `_run_preflight_and_labels`
returns a two-element tuple with no detail slot at all, and `_commit_pending` returns a bare
step name. The fix is therefore mostly a matter of conforming to the module's own established
shapes rather than inventing a new one.

A second constraint is external: `refused_step: "dirty_tree"` is a documented contract that
`tests/router/test_documented_land_pr_invocations.py` exists to protect — that test was added
because a caller lost a round trip to a bare `refused_step: dirty_tree` with no way to tell
what to do next. The value must not change; the missing diagnostic is the gap it left open.

Verified on base `36af09a1` against the real function (not a mock): a worktree whose preflight
denies produces `("preflight", [])` with the refusal reason on **stderr** (353 chars) and an
empty stdout. This is why the capture must cover both streams.

## Goals / Non-Goals

**Goals:**

- Every refusal path in `land_pr()` populates `LandOutcome.detail` with text that identifies
  the cause, so a caller can act on the refusal without re-running and inspecting stderr.
- The preflight capture preserves the gate's output whether the gate wrote it to stdout or
  stderr, and whether it returned a code or raised `SystemExit`.
- No change to `refused_step` values, exit codes, JSON shape, or any push/PR behavior.

**Non-Goals:**

- Reworking `_run_record_main`'s own `SystemExit(int)` fallback, which drops captured stderr
  when the module exits with an integer code. It is the same defect family and was found
  during this work, but it is a different function with a different caller contract
  (`_ensure_run_record` reads its stdout); it is captured as a separate brief rather than
  folded in here.
- Changing the `push_ambiguous`/`pr_create`/`pr_update` `ceiling` paths, which already carry
  details and are not refusals.
- Any change to the `dirty_tree` step name or the `--commit-message` invocation contract.

## Decisions

**D1 — `_run_preflight_and_labels` returns a three-element tuple
`(refused_step, labels, detail)`, mirroring `_ensure_compile_markers`.**
Alternative considered: a small frozen dataclass result. Rejected — it would make this one
helper the module's only non-tuple multi-value return, and the two call sites would still need
updating either way. The tuple keeps the call sites symmetric with the compile-marker site
immediately above it in `land_pr()`. `detail` is `None` on success, so the caller ignores it on
the pass path exactly as it ignores `_ensure_compile_markers`'s.

**D2 — A private capture helper runs `preflight.main(argv)` in-process under
`redirect_stdout`/`redirect_stderr`, modelled on `_run_record_main`.**
It returns `(exit_code, detail)`: a string `SystemExit` code becomes the detail (that is how
argparse reports an invalid `--risk`), otherwise the detail is the captured stderr, or the
captured stdout when stderr is empty, or `""`. Rationale: this is the module's own established
pattern for in-process module `main()` calls, so the in-process capture story stays one
concept. Alternatives rejected: a subprocess invocation (would duplicate argv construction and
change in-process semantics the tests rely on); stdout-only capture (proven empty for the most
common denial by the base-`36af09a1` reproduction).

**D3 — `_commit_pending` keeps `refused_step="dirty_tree"` and adds a cause-specific detail.**
The four causes — failed `git status`, no `commit_message` supplied, failed `git add`, failed
`git commit` — keep one step name and are separated by detail text; the three git failures quote
the failing subcommand's `stderr` (falling back to `stdout`), matching how `_push` already
composes its refusal detail. Alternatives considered: (a) four distinct step names
(`dirty_tree_status_failed`, …). Rejected — it changes a documented, test-guarded
`refused_step` value and asks every consumer to learn four names where the detail text answers
the same question. (b) A `ceiling` for the git-failure causes. Rejected — no remote mutation
has occurred, so `refused` remains the honest outcome; only its diagnostic was missing.
The clean-tree path and the commit path keep their current behavior byte-for-byte.

**D4 — `land_pr()` threads the detail into `LandOutcome(detail=...)` at both refusal sites,
and nowhere else.** The route, compile-marker, no-branch, and push refusal sites already do
this; after the change, every `refused` return in `land_pr()` populates `detail`.

## Risks / Trade-offs

- [The preflight capture changes what the gate's output does during a landing — it is no longer
  streamed to the caller's terminal] → The gate's output is still surfaced, now in the refused
  outcome's `detail`, which is the contract the spec requires; a successful run's stdout is
  captured and discarded, and the run's own JSON verdict remains in the detail only on the
  failure path where it is useful.
- [Adding a tuple element silently breaks callers that unpack two values] → All four test-module
  call sites are updated in the same change; `land_pr()`'s own call site is updated;
  `rg`-verified there is no other production caller. Direct unit tests that unpack two values
  fail loudly with `ValueError` rather than silently, so a missed site cannot pass unnoticed.
- [The `detail` for a denial is the gate's pretty-printed verdict JSON, which is verbose] →
  Accepted; a diagnostic that is too long is recoverable, an empty one is not, and the same
  verbosity already appears in the compile-marker detail.
- [Preserving `SystemExit(int)` handling could diverge from `_run_record_main`'s] → Deliberate:
  the helper follows `_run_record_main`'s *shape*, not its `SystemExit(int)` fallback, whose
  stderr-dropping is the separate defect recorded in the non-goals.

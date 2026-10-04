## Context

See proposal.md - Why. The defect sits in `_run_record_main` (`src/worktrail/router/land_pr.py`
:817-843), the in-process wrapper all of the landing pipeline's run-record interaction goes
through (`start`, `set`, `append`, `scope-review`, `finish`): it runs
`run_record_module.main(argv)` under `redirect_stdout`/`redirect_stderr` and returns
`(exit_code, stdout, detail)`. Its third element is what `_finish_or_checkpoint`
(`land_pr.py:1269`) hands back as `(ok, detail)`, and that `detail` lands verbatim in
`LandOutcome(detail=...)` at the four `"... but run record could not be completed"` ceiling
returns (`:1484`, `:1968`, `:2034`, `:2066`).

The sibling `_preflight_main` (`land_pr.py:409-437`) already solves the identical capture
problem with the full fallback chain: a string `SystemExit` code becomes the detail, else the
captured stderr, else the captured stdout, else `""`. Its docstring states the rationale
directly: the gate writes the common denial reason to stderr, "but a gate is free to write its
failure to stdout, and a denial must carry it either way." `land-pr-refusal-diagnostics`
(#1411) built that helper by mirroring `_run_record_main`'s capture shape and extending it to
stdout -- but did not update `_run_record_main` itself, whose docstring still promises only
"whatever was written to stderr".

## Goals / Non-Goals

**Goals:**

- `_run_record_main`'s `detail` carries the run-record tool's own failure message whichever
  stream the tool wrote it to, and never surfaces empty when a message was printed.
- Zero change to the tuple's other elements and zero change to any caller: every existing
  call site, outcome classification, and `merge_result` string stays as it is.

**Non-Goals:**

- Changing `run_record` itself -- which stream it writes failure messages to is the tool's
  own convention (its commands print JSON to stdout by design).
- Changing what a failed finish *does*: ceiling classification, `failed_recoverable`, exit
  statuses, and the `"... but run record could not be completed"` merge results are untouched;
  only the detail's content changes.
- The string-`SystemExit` precedence. `raise SystemExit("msg")` never prints its message to
  any stream when caught in-process, so `str(exc.code)` is the *only* carrier and must stay
  authoritative.
- Other in-process wrappers (`_push`, `_commit_pending`); #1411 closed their detail capture.

## Decisions

- **One expression: adopt the sibling's exact chain.** The final return becomes
  `return exit_code, out.getvalue(), detail or err.getvalue().strip() or out.getvalue().strip()`
  -- character-for-character the composition `_preflight_main` returns. Alternatives:
  (a) patching only inside the `isinstance(exc.code, int)` except arm -- rejected: a plain
  non-`SystemExit` non-zero return (`main()` returning 1) reaches the same final expression
  with the same stderr-only hole, so an arm-local fix leaves half the defect in place;
  (b) restructuring into per-branch detail assignments -- rejected: diverges from the accepted
  sibling shape for no behavioral difference.
- **The stdout element stays untouched.** The tuple's second element remains the raw,
  unstripped captured stdout, because `_ensure_run_record` (`land_pr.py:846`) parses `start`'s
  JSON path line from it. Only the third element's composition changes, and only by appending
  one `or` clause.
- **Precedence stays string code, then stderr, then stdout.** stderr is preferred over stdout
  so a tool that warns on stderr while reporting on stdout keeps today's detail; this matches
  the sibling exactly and keeps the diff to one expression.

## Risks / Trade-offs

- A run_record invocation that prints its normal JSON payload to stdout *and* exits non-zero
  now returns that payload as `detail` instead of `""`. This is the intended direction -- the
  payload is the tool's own message that the requirement demands -- callers read `detail` only
  on a non-zero exit, and none parse its format.
- A tool that writes both streams gets only stderr in the detail (stdout is the second
  fallback, not concatenated). Same posture as the sibling; concatenating would change the
  detail's shape for the currently-working stderr case.

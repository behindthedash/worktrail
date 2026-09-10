## Context

The `-X ours` defect class has shipped twice (PR #414, and the same risk class
in `live.py`'s squash-merged-dependency carry) and been fixed twice. Both fixes
left a comment at the call site explaining why the biased strategy must not be
used. Comments are not enforcement: they bind only the reader who happens to be
editing that exact line, and not at all a new merge call site added elsewhere.

The source brief asked for "a CI guardrail grep". The literal reading —
a `grep -rn` step in a workflow — does not work here, because the strongest
existing statements of the prohibition are themselves prose containing the
forbidden string. A naive grep over `src/` fails immediately on
`integrate.py:682`, `integrate.py:1409` and `live.py:2273`, and the only ways
out are to delete the explanations or to hand-maintain a line-number allowlist
that rots on the next edit.

## Goals / Non-Goals

**Goals:**
- Fail the build if a biased merge strategy option is passed to git anywhere in
  `src/worktrail/`.
- Keep the existing explanatory comments and docstrings — which must name
  `-X ours` to explain why it is avoided — passing unmodified.
- Ship as part of an already-required check, so it blocks merges from day one.

**Non-Goals:**
- Detecting biased resolution achieved some other way (a hand-written conflict
  resolver, `git checkout --ours`, a custom merge driver). Those are legitimate
  in some places — `integrate.py`'s `_union_init` and `live.py`'s
  `_union_merge_checklist` are exactly that, deliberately — and are not the
  defect class.
- Retroactively auditing merge call sites. The baseline was audited when the
  two fixes landed and is clean.

## Decisions

### Decision 1: A pytest structural guard, not a workflow grep step

Implement as `tests/test_no_biased_merge_strategy.py`, in the shape of the
existing `tests/test_no_bare_head_ctx_default.py` — the repo's established
pattern for "this defect class must not recur" enforcement.

- It runs inside `pytest -q`, which is already a step of the required
  `CI: Lint, Test & Build` check, so it is merge-blocking without touching
  `.github/workflows/` or the branch ruleset's `required_status_checks`.
- It runs locally under a bare `pytest`, so the failure arrives before the push
  rather than after.
- Being Python, it can parse instead of pattern-match, which is what makes
  Decision 2 possible.

**Alternative rejected:** a `grep` step in `ci.yml`. It cannot distinguish the
comment that forbids the flag from the call that uses it, and would need an
allowlist of the three documenting sites.

### Decision 2: Scan executable string literals via `ast`, excluding docstrings

Parse each `src/worktrail/**/*.py` with `ast` and inspect only `ast.Constant`
string nodes, skipping the module/class/function docstring position. Comments
are absent from the AST entirely, so they are free. This makes the guard
precise about the thing that actually matters — a string that can reach git's
argv — and leaves every current explanation of the prohibition untouched.

Flagged literal shapes (matched case-sensitively against the whole literal, so
prose containing the words is not flagged):

- exactly `-X` (the separated form, as in `_git(wt, "merge", "-X", "ours", ...)`)
- `-Xours` / `-Xtheirs` (the joined form)
- anything starting `--strategy-option` (the long form, joined or separated)
- `-s ours` / `--strategy=ours` / `--strategy ours` and the `theirs` variants
  (`ours`/`theirs` as a top-level merge *strategy*, not just an option)

A bare `"ours"` / `"theirs"` literal is deliberately NOT flagged on its own:
`_union_init(ours, theirs)` and `_git(iw, "show", f":2:{path}")`-style conflict
handling use those words legitimately, and flagging them would make the guard
noisy enough to be disabled.

### Decision 3: No allowlist or opt-out

The guard has no skip comment, no per-file exemption list, and no environment
override. A biased merge strategy has no known correct use in this codebase; if
one is ever found, editing this test (and stating the reason in its message)
should be a deliberate, reviewed act, not a one-line `# noqa`-style bypass at
the call site. This mirrors `test_no_bare_head_ctx_default.py`, which also has
no escape hatch.

### Decision 4: Scan all of `src/worktrail/`, not just `orchestrator/`

Both incidents were in `orchestrator/`, and that is where git merges are
invoked today. But the scan is cheap, the false-positive rate under Decision 2
is zero on the current tree, and a merge call added to another subsystem later
is exactly the case a guardrail exists for. Scanning the whole package costs
nothing and removes the "the guard did not cover that directory" failure mode.

## Risks / Trade-offs

- **A dynamically built option evades the scan** (`"-X" + choice`, or an option
  read from config). Accepted: the guard targets the literal form both
  incidents took. A dynamically assembled biased-merge option is a
  deliberate act, not the accidental recurrence being guarded against.
- **A file that fails to parse** would be silently skipped if the test swallowed
  `SyntaxError`. It must not — an unparseable file under `src/worktrail/` fails
  the test, since `ruff`/`pytest` would be failing anyway.

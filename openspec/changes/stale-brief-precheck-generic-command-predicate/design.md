## Context

See proposal.md — Why. The mechanics that constrain the approach:

- `src/worktrail/router/check_brief_predicate.py` today holds one registry,
  `PREDICATE_RECHECKS: Dict[str, Callable[[Path, List[dict]], Dict[str, List[str]]]]`, keyed by
  `drift-source`, with one entry (`checkbox-drift-sweep` → `_recheck_checkbox_drift`).
  `recheck(repo, frontmatter)` never raises: every unhandled condition degrades to a
  non-terminal outcome (`no-predicate`, `unrecognized`, `error`) and Phase 5.5 falls through to
  the probe-based flow.
- Its result dict is the JSON contract consumed by `skills/worktrail-go/references/
  brief-staleness-check.md` through the `worktrail-recheck-brief-predicate --json` console
  script. Fields: `attempted`, `drift_source`, `outcome`, `still_true`, `resolved`, `error`.
- `format_still_true_evidence` / `format_resolved_closure_note` turn that result into the two
  exact strings the skill doc passes to `worktrail-run-record append ... decisions` and
  `worktrail-work-queue done --note`.
- `work_queue.done()` gained a closure evidence gate in PR #601:
  `_reverification_claim_missing_evidence` rejects a note matching
  `_REVERIFICATION_CLAIM_RE` (`disproven|re-?verified|no longer flags?|...`) unless it also
  matches `_EVIDENCE_MARKER_RE` — a fenced code block, or a line beginning `$ `, `command:`,
  `output:`, or `detector output:`. Any note this change generates has to satisfy that gate on
  its own terms, not by dodging the claim regex.
- The motivating external producer, `behindthedash/devops`'s
  `scripts/fleet-workflow-hygiene-guard.py`, already imports
  `worktrail.shared.brief_frontmatter` and writes briefs directly into `~/work-queue/queue/`.
  It currently stamps no `drift-source` at all; it is a separate repo and a separate PR.

## Goals / Non-Goals

**Goals:**

- One deterministic dispatch order in `recheck()`, with the existing `checkbox-drift-sweep` path
  byte-for-byte unchanged in behavior.
- A predicate contract an out-of-process, out-of-repo, any-language detector can satisfy.
- Evidence strings that are self-verifying — the reader can re-run what the automation ran.
- Every new failure mode collapses onto the one existing safe outcome (`error` → fall through to
  the human-in-the-loop flow).

**Non-Goals:**

- No sandboxing, allowlisting, or privilege reduction of the executed command (see Risks — the
  work queue is already a trusted local artifact).
- No change to the `worktrail-recheck-brief-predicate` CLI surface (`--repo`, `--brief`,
  `--json`) — the skill doc's invocation is untouched.
- No capture-side helper in this repo for external sweeps: the contract is documented, not
  wrapped. Worktrail's own sweeps (`spec_sync_sweep_checkbox_brief.py`) keep using the named
  predicate they already have.

## Decisions

### Decision 1: `predicate-kind: command` selects the mechanism; `drift-source` keeps naming the sweep

The originating request sketched a reserved `drift-source: verify-cmd-sweep` value. Rejected,
because `drift-source` is already load-bearing as *sweep identity*: `spec_sync_sweep_dedup.py`
keys brief dedup on `(repo, drift-source)`, and every evidence/closure string this module emits
prints it (`"Predicate re-check (checkbox-drift-sweep) found ..."`). Collapsing every
command-based sweep onto one shared literal would make two different sweeps' briefs dedup
against each other and would make the recorded evidence say `verify-cmd-sweep` where it should
say which sweep filed the brief.

So the mechanism gets its own frontmatter field. Dispatch order in `recheck()`:

1. `PREDICATE_RECHECKS.get(drift_source)` — a named predicate wins if one is registered.
2. else `frontmatter.get("predicate-kind") == "command"` → `_recheck_command_predicate`.
3. else `outcome="unrecognized"`, unchanged.

Alternative considered and rejected: dispatch by *shape* (any brief whose findings all carry a
`predicate-cmd`). Implicit activation — a brief could start executing commands because of a
field it happened to carry. An explicit declaration is one extra field for the same result.

`PREDICATE_RECHECKS` stays a single registry rather than growing a sibling: step 2 is a
documented fallback inside `recheck()`, not a second lookup table to keep in sync.

### Decision 2: Classify on exit status only, with a fixed `0 = still true` polarity

Parsing a foreign detector's output text is not something this codebase can do correctly for
detectors it does not own, and a misread that lands on "resolved" silently closes live work.
Exit status is the one channel every language and every detector already agrees on.

Polarity is fixed and encoded in the field name: `predicate-cmd` answers "does the predicate —
the condition that generated this brief — still hold?", exiting `0` for yes. This is `grep -q`
and `test` semantics, which is what most such commands literally will be. The alternative — a
per-finding `still-true-exit-codes` mapping — is configurability for a scenario no current
requirement has, and every additional knob is another way for a sweep author to get the polarity
backwards.

Everything that is not `0` or `1` — `2` from a detector's own argument error, `127` from a
missing executable, `124`/timeout, a signal — is `error`, not `resolved`. The asymmetry is
deliberate: `error` costs one operator prompt, `resolved` costs a wrongly-closed brief.

### Decision 3: The `recheck()` result grows an additive `evidence` list

`still_true` / `resolved` stay `List[str]` of the findings' `path` values, so the existing
formatters, the JSON contract, and the skill doc's table all keep working and the checkbox path
is untouched. The transcript rides along in a new optional key:

```python
{"still_true": [...], "resolved": [...],
 "evidence": [{"path": ..., "command": "<shlex-joined>", "exit": 0, "output": "<truncated>"}]}
```

A recheck function may return `evidence`; `recheck()` passes it through, defaulting to `[]`.
`_recheck_checkbox_drift` returns no `evidence` and keeps its current signature and body.
Formatters append a transcript section only when `evidence` is non-empty — so the
checkbox-drift strings are unchanged, which matters because
`tests/router/test_check_brief_predicate.py` asserts on them verbatim.

Requiring `path` on every finding (already required by the spec for the checkbox case, now
generalized) is what lets `still_true`/`resolved` stay uniform `List[str]` across both predicate
kinds.

### Decision 4: The transcript is formatted to satisfy PR #601's gate by construction

`_EVIDENCE_MARKER_RE` matches a line beginning `command:` or `output:` (MULTILINE). The
transcript block is therefore rendered as, per finding:

```
command: python3 .../fleet-workflow-hygiene-guard.py --check-one <file>
exit: 1
output: <first N chars, newlines collapsed>
```

This is not a formatting coincidence to be rediscovered later — a test asserts
`work_queue._reverification_claim_missing_evidence(note) is False` for a generated
command-predicate closure note, so a future edit to either side breaks a test rather than
silently starting to reject auto-closures at dispatch time. `work_queue` is imported into that
test only; `check_brief_predicate` does not import `work_queue` (the dependency is on the
*format*, not the module).

Output is captured with stdout and stderr merged and truncated per finding (a detector's own
verbose log is not the evidence; the command and its status are). Truncation is marked, so a
reader never mistakes a clipped excerpt for the whole output.

### Decision 5: Execution bounds, and where each one lands

| Bound | Value | On violation |
|---|---|---|
| Argv shape | non-empty `list[str]` | `error` (never shell-split) |
| Shell | `shell=False`, always | n/a |
| cwd | the `repo` argument `recheck()` already receives | n/a |
| Per-command timeout | 30s | `error` |
| Commands per brief | 20, checked before executing any | `error` |
| Captured output | merged stdout+stderr, truncated per finding | n/a |

The per-brief cap is checked *before* the first command runs, so an oversized brief costs zero
executions rather than 20. 30s and 20 mirror the existing bounded-cost posture the spec already
takes for probes (`Requirement: Probe Count Is Bounded`) and what
`brief-staleness-check.md` already claims about this step's cost — that section needs updating,
since "spawns no subprocess" stops being true.

`env` is inherited unchanged: an external detector legitimately needs `PATH` and often
`PYTHONPATH` (devops's guard imports `worktrail.shared.brief_frontmatter`).

### Decision 6: A malformed command-kind brief is an error, not "unrecognized"

`predicate-kind: command` with a missing/malformed `predicate-cmd` on some finding is a
*miscaptured* brief, not an unrecognized one. Both outcomes fall through identically at the
skill-doc level, but `error` carries an `error` string into the JSON, which is the difference
between a debuggable capture bug and a silent no-op. `unrecognized` stays reserved for "nothing
here claims to be re-checkable."

## Risks / Trade-offs

- **Executing a command stored in a data file is a real trust boundary.** → Mitigation is scope,
  not sandboxing: `$WORK_QUEUE_DIR` is a private, single-machine, push-only-backed directory
  written by the user's own sweeps and agents, and its contents are already trusted to name
  repos, branches, and routes that drive automated dispatch. The guards that do apply are the
  ones that stop *accidental* damage: no shell, argv-only, bounded time, bounded count, and the
  executed command recorded verbatim in the outcome even on the error path, so anything that ran
  is auditable. Stated as an accepted assumption rather than mitigated further — an allowlist of
  executables was considered and rejected as brittle (it would have to name a path in another
  repo) without meaningfully changing the trust model.
- **Polarity inversion by a sweep author** (`predicate-cmd` that exits 0 when *fixed*) would
  invert every classification for that sweep, and the "resolved" direction auto-closes briefs.
  → Mitigation: the field name states the polarity, the spec states it normatively, the skill
  doc's capture-side contract states it with a worked example, and the closure note shows the
  command and its exit status, so a wrong closure is visible in the brief's own history rather
  than being invisible prose. Not fully preventable from this side of the contract.
- **A detector that is slow per-file** turns a 20-finding brief into up to 10 minutes of
  dispatch latency in the worst case. → Mitigation: the per-brief cap; and the realistic shape
  is one command per file taking well under a second. If this ever bites, the fix is a total
  wall-clock budget, not a bigger per-command timeout.
- **The gate-compatibility coupling to `work_queue.py` is a format contract across two modules.**
  → Mitigation: the test named in Decision 4 fails on either side's drift, which is the only
  place the coupling is enforceable without making one module import the other.
- **`brief-staleness-check.md`'s cost claim becomes wrong** ("adds no comparable cost: it spawns
  no subprocess"). → Mitigation: that line is in the change's task scope, not left to be noticed
  later.

## Migration Plan

No migration. The new frontmatter fields are additive and optional; a brief that carries neither
`predicate-kind` nor a registered `drift-source` behaves exactly as it does today. Rollback is
reverting the PR — no persisted state is written by this change.

Ordering with the downstream `devops` change is unconstrained: worktrail ships the reader first,
and briefs stamped with `predicate-kind: command` before the reader is installed simply fall
through to the probe-based flow, which is the pre-existing behavior.

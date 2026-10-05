## MODIFIED Requirements

### Requirement: Interactive apply reads the verdict from a file
`worktrail-skill-dispatch` SHALL accept `--apply-brief-triage-file <path>` as an alternative
to `--apply-brief-triage <json>`. It SHALL read the verdict JSON object from the named file and
apply (or, without `--confirm`, preview) it through the same validation and apply path as the
inline form, honouring `--confirm`, `--triage-agent`, and `--triage-repos-root` identically.
The two flags SHALL be mutually exclusive. When the file is missing, unreadable, or does not
contain valid JSON, the command SHALL print a `status: error` action-log entry whose `error`
names the path and exit 1 rather than raising. A file containing `null`, a non-object, or an
object without a `verdict` field SHALL produce the same `status: error` entry the inline form
produces for that payload. With `--confirm`, the file form SHALL consume the verdict file:
once its contents have been read and parsed, the file SHALL be removed before the command
exits, so one verdict file can be applied at most once -- a later apply of the same path fails
closed with the same unreadable-file entry. A preview without `--confirm` SHALL leave the file
in place, and a file that could not be read or parsed SHALL be left in place.

The `worktrail-go` intake-brief triage gate SHALL NOT carry the verdict JSON between its
evaluate and apply steps as a shell variable or as a re-typed argv string, and SHALL NOT
derive a verdict-file path from the brief id alone. The evaluate step SHALL write the
evaluator's stdout to a verdict file whose path is scoped to this dispatch -- the brief id
plus the invocation's dispatch id (`$INVOCATION_CONTEXT_DISPATCH_ID`), e.g.
`${TMPDIR:-/tmp}/triage-verdict-${BRIEF_ID}-${INVOCATION_CONTEXT_DISPATCH_ID}.json` -- and the
apply step SHALL pass the same dispatch-scoped path via `--apply-brief-triage-file` together
with `--confirm`, so two concurrent triages of the same brief never share one file.

#### Scenario: Verdict file is applied with confirm
- **WHEN** a file contains the JSON object printed by `--evaluate-brief-triage` for a `keep`
  verdict and `--apply-brief-triage-file <path> --confirm` is run
- **THEN** the brief gains the same `verdict: keep` triage note the inline
  `--apply-brief-triage` form writes, and the printed action-log entry matches it

#### Scenario: Verdict file is previewed without confirm
- **WHEN** `--apply-brief-triage-file <path>` is run without `--confirm`
- **THEN** the action-log entry is a preview, the brief is unchanged, and the file remains on
  disk, exactly as for the inline form

#### Scenario: Missing verdict file fails closed
- **WHEN** `--apply-brief-triage-file /nonexistent/verdict.json --confirm` is run
- **THEN** the command prints a `status: error` entry whose `error` names the path and exits 1
  without applying anything

#### Scenario: Null verdict file fails like the inline form
- **WHEN** the verdict file contains `null` (the evaluator's no-verdict output)
- **THEN** the command prints the same `status: error` entry with error
  `payload is null -- no verdict to apply` as `--apply-brief-triage null` and exits 1

#### Scenario: Both forms together are rejected
- **WHEN** `--apply-brief-triage <json>` and `--apply-brief-triage-file <path>` are both given
- **THEN** argument parsing fails with a usage error before anything is applied

#### Scenario: A confirmed apply consumes the verdict file
- **WHEN** `--apply-brief-triage-file <path> --confirm` has run against a readable verdict file
- **THEN** the file no longer exists, and running the same command again fails closed with the
  unreadable-file entry naming the path

#### Scenario: Gate carries only the verdict path between steps
- **WHEN** the Phase 2 intake-brief triage gate text of `skills/worktrail-go/SKILL.md` is read
- **THEN** its evaluate step redirects stdout to a verdict file whose path names both the brief
  id and `$INVOCATION_CONTEXT_DISPATCH_ID`, its apply step passes that same path via
  `--apply-brief-triage-file` with `--confirm`, no `VERDICT_JSON=$(` capture or
  `--apply-brief-triage "$VERDICT_JSON"` argv form remains, and no verdict path derived from
  the brief id alone remains

## ADDED Requirements

### Requirement: A verdict is applied only against the brief state it was evaluated from
`worktrail-skill-dispatch` SHALL record, on the verdict printed by `--evaluate-brief-triage`, a
`brief_digest` -- the SHA-256 hex digest of the brief file's bytes as read for evaluation,
captured after the linked-decision and repo-inference pre-pass and immediately before that
brief's evaluation (the evaluator spawn, or the escalation matrix when no evaluator runs) -- so
the verdict is attributable to the exact brief content it was decided from. The single-brief
apply SHALL verify that digest against the named brief's current content before applying or
previewing, behind the existing brief-ownership guard; on a mismatch it SHALL print `null` on
stdout, write `blocked_verdict_stale: <brief-id> (brief content changed since evaluation)` to
stderr, and exit 2 without applying or previewing anything. The file form SHALL require the
field: a payload whose `brief_digest` is absent or empty SHALL print `null` on stdout, write
`blocked_verdict_unattributed: <path>` to stderr, and exit 2 without applying anything. The
inline `--apply-brief-triage` form SHALL verify the digest when the payload carries one and
otherwise behave exactly as it did before this requirement. The `worktrail-go` gate SHALL
document both refusals as cases that do not proceed to apply, whose remedy is re-running the
evaluate step against the brief's current content.

#### Scenario: A verdict for an unchanged brief applies
- **WHEN** the verdict file this dispatch's evaluate step wrote is applied and the brief's
  content has not changed since evaluation
- **THEN** the verdict applies exactly as before this requirement, and the action-log entry is
  the same as the inline form's

#### Scenario: A concurrent rewrite refuses the stale verdict
- **WHEN** another triage of the same brief rewrites it -- for example stamps execution
  provenance or rewrites its focus -- between this dispatch's evaluate and apply steps
- **THEN** the apply prints `null`, writes
  `blocked_verdict_stale: <brief-id> (brief content changed since evaluation)` to stderr, exits
  2, and leaves the brief as the other triage left it

#### Scenario: A stale digest is refused on preview too
- **WHEN** `--apply-brief-triage-file <path>` without `--confirm` names a verdict whose brief
  content changed since evaluation
- **THEN** the command prints `null` and the `blocked_verdict_stale` line instead of a preview
  entry, and applies nothing

#### Scenario: Ownership refusal keeps precedence
- **WHEN** the brief has since moved to `picked/` under another claimant and its content also
  changed
- **THEN** the apply exits 2 with the existing `blocked_brief_owned` line, not the staleness
  line

#### Scenario: A file-form payload without provenance is refused
- **WHEN** `--apply-brief-triage-file <path> --confirm` is given a verdict object that carries
  no `brief_digest`
- **THEN** the command prints `null`, writes `blocked_verdict_unattributed: <path>` to stderr,
  exits 2, and applies nothing

#### Scenario: An inline payload without provenance is unchanged
- **WHEN** `--apply-brief-triage <json>` is given a verdict object that carries no
  `brief_digest`
- **THEN** it applies exactly as it did before this requirement

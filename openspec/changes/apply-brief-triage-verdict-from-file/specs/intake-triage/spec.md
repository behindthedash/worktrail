## ADDED Requirements

### Requirement: Interactive apply reads the verdict from a file
`worktrail-skill-dispatch` SHALL accept `--apply-brief-triage-file <path>` as an alternative
to `--apply-brief-triage <json>`. It SHALL read the verdict JSON object from the named file and
apply (or, without `--confirm`, preview) it through the same validation and apply path as the
inline form, honouring `--confirm`, `--triage-agent`, and `--triage-repos-root` identically.
The two flags SHALL be mutually exclusive. When the file is missing, unreadable, or does not
contain valid JSON, the command SHALL print a `status: error` action-log entry whose `error`
names the path and exit 1 rather than raising. A file containing `null`, a non-object, or an
object without a `verdict` field SHALL produce the same `status: error` entry the inline form
produces for that payload.

The `worktrail-go` intake-brief triage gate SHALL NOT carry the verdict JSON between its
evaluate and apply steps as a shell variable or as a re-typed argv string. The evaluate step
SHALL write the evaluator's stdout to a verdict file whose path is derived from the brief id
alone, and the apply step SHALL pass that path via `--apply-brief-triage-file` together with
`--confirm`.

#### Scenario: Verdict file is applied with confirm
- **WHEN** a file contains the JSON object printed by `--evaluate-brief-triage` for a `keep`
  verdict and `--apply-brief-triage-file <path> --confirm` is run
- **THEN** the brief gains the same `verdict: keep` triage note the inline
  `--apply-brief-triage` form writes, and the printed action-log entry matches it

#### Scenario: Verdict file is previewed without confirm
- **WHEN** `--apply-brief-triage-file <path>` is run without `--confirm`
- **THEN** the action-log entry is a preview and the brief is unchanged, exactly as for the
  inline form

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

#### Scenario: Gate carries only the verdict path between steps
- **WHEN** the Phase 2 intake-brief triage gate text of `skills/worktrail-go/SKILL.md` is read
- **THEN** its evaluate step redirects stdout to a verdict file, its apply step passes
  `--apply-brief-triage-file` with `--confirm`, and no `VERDICT_JSON=$(` capture or
  `--apply-brief-triage "$VERDICT_JSON"` argv form remains

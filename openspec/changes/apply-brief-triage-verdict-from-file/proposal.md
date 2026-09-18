## Why

The `worktrail-go BRIEF-ID` intake-brief triage gate (`skills/worktrail-go/SKILL.md`, Phase 2)
is written as two separate Bash blocks: step 1 captures the evaluator's verdict into a shell
variable (`VERDICT_JSON=$(worktrail-skill-dispatch --evaluate-brief-triage ...)`), and step 2
passes it back as an argv string (`--apply-brief-triage "$VERDICT_JSON"`). Each block runs in
its own Bash tool call, so the variable never survives from step 1 to step 2; the agent has to
re-type a multi-line JSON object (with nested quotes, backslashes, and multi-line `evidence`)
into a shell command line. That re-typing is where the flow breaks: the JSON reaches
`json.loads(parsed.apply_brief_triage)` (`src/worktrail/router/skill_dispatch.py`) mangled by
shell quoting and the apply step fails or, worse, applies a verdict whose `evidence` no longer
matches what the evaluator produced. `--apply-brief-triage` accepts nothing but the raw argv
string -- there is no file or stdin alternative. Source: work-queue brief
`20260917-165015-triage-verdict-shell-quoting-breakage`.

## What Changes

- `worktrail-skill-dispatch` gains `--apply-brief-triage-file VERDICT_PATH`: it reads the
  verdict JSON object from that file and then follows exactly the same validation and apply
  path as `--apply-brief-triage` (null / non-object / verdictless payloads produce the same
  `status: error` entry and exit 1; `--confirm`, `--triage-agent`, `--triage-repos-root` apply
  unchanged). The two flags are mutually exclusive. A missing or unreadable file, or a file
  whose contents are not valid JSON, produces a `status: error` entry naming the path and
  exits 1 instead of raising.
- The Phase 2 intake-brief triage gate in `skills/worktrail-go/SKILL.md` no longer captures
  the verdict into a shell variable. Step 1 redirects the evaluator's stdout to a verdict file
  whose path is derived from the brief id alone, so the path (not the JSON) is the only thing
  carried between Bash calls; step 2 passes that path via `--apply-brief-triage-file`. The
  exit-2 / exit-1 handling is restated in terms of the file's contents.
- `--apply-brief-triage VERDICT_JSON` keeps working unchanged for callers that already hold
  the JSON in-process.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `intake-triage`: the interactive apply step accepts its verdict from a file, and the
  documented `worktrail-go` gate uses that form.

## Impact

- `src/worktrail/router/skill_dispatch.py` — new `--apply-brief-triage-file` flag sharing the
  existing apply branch.
- `skills/worktrail-go/SKILL.md` — Phase 2 intake-brief triage gate, steps 1 and 2.
- `tests/router/test_skill_dispatch.py` — file-flag scenarios.
- `tests/router/test_skill_prose_enforcement_coverage.py` — the Phase 2 gate assertions now
  prove the file form is used (and that `--confirm` still accompanies it).

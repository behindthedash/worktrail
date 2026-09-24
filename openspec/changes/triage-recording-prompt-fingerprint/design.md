## Context

The live-smoke harness records an evaluator answer and the exact fixture focus, but its `_meta`
block currently identifies only the agent and capture date. The evaluator prompt template is a
module-level source string; rendering it per run adds repository, brief, and freshness data that
is intentionally variable and is not the prompt revision this recording needs to identify.

## Goals / Non-Goals

**Goals:**

- Make every newly produced recording identify the stable evaluator prompt template that guided
  the answer.
- Make the committed recording's offline replay fail visibly when its prompt provenance no longer
  matches the current template.

**Non-Goals:**

- Fingerprint per-run rendered prompt inputs, evaluator model versions, tools, or repository
  state.
- Make a live evaluator call part of CI or automatically re-record an answer after a mismatch.

## Decisions

- **Fingerprint the unrendered evaluator prompt template with SHA-256.** Its canonical UTF-8
  source text is the stable instruction surface whose edits can affect the evaluator's answer;
  a SHA-256 lowercase hexadecimal digest is deterministic, available in the standard library,
  and avoids adding a dependency. Fingerprinting the fully rendered prompt would make ordinary
  brief and environment data look like a prompt revision; storing only a source line number or
  a hand-maintained version would be brittle or easy to forget to update.
- **Expose one shared fingerprint value from the smoke harness and use it for recording and
  replay.** This keeps production metadata and test expectations on the same definition while
  still letting the replay assert a fixture's literal provenance. Duplicating the hash expression
  in the test would risk tests and recording code drifting together incorrectly.
- **Fail offline on a missing or mismatched fixture field.** A legacy fixture without provenance
  cannot establish which prompt created it, and accepting it would preserve the blind spot. The
  failure asks an operator to deliberately regenerate the recording or update the deterministic
  provenance after verifying the template did not change.

## Risks / Trade-offs

- **Any prompt wording edit fails replay until provenance is refreshed** → This is intentional
  visibility; the failure remains local and does not spend model tokens.
- **A digest is not human-readable** → Keep the existing description, agent, and capture date as
  readable context; the digest is a precise equality token rather than an operator explanation.

## Migration Plan

Add the fingerprint field to the committed recording using the current template's deterministic
value, then enable the replay assertion in the same change. No runtime data migration or rollback
is required: reverting the code and fixture together restores the prior recording format.

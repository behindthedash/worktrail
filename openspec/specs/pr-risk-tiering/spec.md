# pr-risk-tiering Specification

## Purpose
TBD - created by archiving change risk-tier-from-judgment. Update Purpose after archive.
## Requirements
### Requirement: Risk tier has a keyword backend and a judgment backend over one mapping
The system SHALL expose one risk-tier function whose result is always a tier
from the existing `low`/`medium`/`high`/`critical` order plus tier-prefixed
labels, backed by either a pure keyword table over the request prose or a
single remote judgment about what the change does. The keyword table SHALL be
the default backend and SHALL remain reachable with no network and no
credential. The judgment SHALL be used only when the caller asks for it AND a
credential is configured.

#### Scenario: Default backend is the keyword table
- **WHEN** the risk-tier function is called without asking for the judgment
- **THEN** it returns the keyword table's tier and labels, and makes no network
  call of any kind

#### Scenario: Judgment backend is used when asked for and available
- **WHEN** the caller asks for the judgment and a credential is configured
- **THEN** the returned tier and labels come from the judgment, in the same
  `<tier>:<label>` shape the keyword table produces

#### Scenario: Labels name which backend produced them
- **WHEN** the judgment produces a tier
- **THEN** its labels name the blast-radius baseline and each crossed red line
  (for example `medium:blast-radius`, `critical:moves-money`), so a reader can
  tell a judged tier from a keyword tier without a second field

### Requirement: The judgment observes, the code decides the tier
The system SHALL ask the judgment service only for a blast-radius score over
four ordered, described levels and for four independent red-line
values — irreversible data loss, weakened access control, money movement, and a
disabled safeguard — in a single request, and SHALL compose the tier from those
values in pure, offline code. The service SHALL NOT be asked for a tier, a
label, or a merge decision.

#### Scenario: Blast radius sets the baseline tier
- **WHEN** no red line is crossed
- **THEN** the tier is the blast-radius score rounded onto the four tiers and
  clamped to that range

#### Scenario: A hard red line forces critical
- **WHEN** irreversible data loss, weakened access control, or money movement
  is at or above the red-line threshold
- **THEN** the tier is `critical`, whatever the blast-radius score said

#### Scenario: A disabled safeguard forces high
- **WHEN** only the disabled-safeguard red line is crossed
- **THEN** the tier is at least `high` — serious enough that a human looks,
  while the change itself is still revertible

#### Scenario: A red line never lowers a higher baseline
- **WHEN** the blast-radius score already yields `critical` and only the
  safeguard red line is crossed
- **THEN** the tier stays `critical`, because a red line is a floor and not an
  assignment

#### Scenario: A value below the threshold is not a red line
- **WHEN** a red-line value is below the threshold
- **THEN** it contributes no label and does not raise the tier

### Requirement: The judgment fails safe into the keyword table
The system SHALL treat every failure of the judgment path as "unavailable" and
use the keyword table's result instead, never raising and never returning a
tier derived from a partial answer. The failures covered SHALL include at
minimum: no credential configured, an HTTP error, a transport error, a timeout,
a body that does not parse, and an answer set missing the score or any red-line
value.

#### Scenario: No credential configured
- **WHEN** no credential is configured and the caller asks for the judgment
- **THEN** no request is made and the keyword table's tier is returned

#### Scenario: The service errors or times out
- **WHEN** the request fails with an HTTP error, a transport error, or a timeout
- **THEN** the keyword table's tier is returned

#### Scenario: A field is missing from the answer
- **WHEN** the response parses but omits the blast-radius score or one
  red-line value
- **THEN** the keyword table's tier is returned, rather than a tier composed
  from the missing field read as zero

### Requirement: Route classification stays pure and deterministic by default
The route classification entry point SHALL NOT enable the judgment unless its
caller explicitly asks, so that replaying it over a corpus is reproducible,
free, and offline. Enabling the judgment SHALL change only the risk tier and
its labels, never the selected route, its confidence, or its reason.

#### Scenario: Classification makes no network call by default
- **WHEN** the classification entry point is called without asking for the
  judgment
- **THEN** the judgment is never invoked, matching the existing rule that live
  lookups happen only in the command-line entry point

#### Scenario: Coverage replay is unaffected
- **WHEN** the classifier coverage audit replays classification over a corpus
- **THEN** every result is produced from the pure keyword path, so the agreed
  count is an exact reproducible number

#### Scenario: The command-line entry point opts in and can opt out
- **WHEN** the classifier is run from the command line
- **THEN** the judgment is enabled by default and a flag disables it, leaving
  the keyword table as the only backend

### Requirement: The tier mapping is regression-testable without the service
The system SHALL carry the adversarial probe set and a recorded answer for
every probe as repository fixtures, so the composition from answers to tiers
is exercised offline. The probe fixture SHALL state that it is adversarial and
measures a capability gap rather than a real-world base rate.

#### Scenario: Recorded answers replay offline
- **WHEN** the test suite runs with no credential and no network
- **THEN** every probe's tier is composed from its recorded answer and asserted

#### Scenario: The keyword table's failure on the probe set is pinned
- **WHEN** the keyword table is scored against the probe set
- **THEN** its unsafe-auto-merge and false-gate counts are asserted, so a later
  change to the table that fixes them fails the test and forces the judgment's
  justification to be re-argued rather than silently inherited


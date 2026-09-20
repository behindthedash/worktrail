# brief-relatedness-judgment Specification

## Purpose
TBD - created by archiving change brief-relatedness-judgment. Update Purpose after archive.
## Requirements
### Requirement: Relatedness is decided by reading the pair, not by counting shared words
The system SHALL be able to decide whether two queued briefs belong in one
cluster by asking two independent judgments about the pair — whether they
describe the same underlying work, and whether doing them separately would
cause rework — and SHALL form an edge when **either** is at or above the
judgment threshold. Requiring both SHALL NOT be the rule, because two distinct
pieces of work that must land together is exactly the case the second question
exists for.

#### Scenario: Paraphrased pair the lexical rule cannot see
- **WHEN** two briefs describe identical work in different vocabulary, scoring
  far below `OVERLAP_THRESHOLD` on token overlap, and the judgment rates them
  same-work above the threshold
- **THEN** an edge is formed between them

#### Scenario: High-overlap pair the judgment rates unrelated
- **WHEN** a pair's token overlap is at or above `OVERLAP_THRESHOLD` but the
  judgment rates both questions below the threshold
- **THEN** the pair carries no focus match, so the lexical coincidence alone
  does not cluster them

#### Scenario: Distinct work that must land together
- **WHEN** the judgment rates a pair low on same-work but at or above the
  threshold on should-cluster
- **THEN** an edge is formed

### Requirement: The lexical stage becomes a bounded prefilter
The system SHALL use the token-overlap coefficient to choose and rank which
pairs are offered to the judgment, at a floor well below `OVERLAP_THRESHOLD`
so that paraphrased pairs are reachable, and SHALL judge at most a fixed
number of pairs per run, ranked by overlap descending. Every pair beyond that
cap SHALL keep the lexical decision unchanged.

#### Scenario: A pair below the prefilter floor is never offered
- **WHEN** two briefs share almost no tokens at all
- **THEN** no request is made for that pair and it keeps the lexical decision

#### Scenario: The per-run cap bounds a large queue
- **WHEN** the number of candidate pairs exceeds the per-run cap
- **THEN** exactly the cap's worth of highest-overlap pairs are judged and the
  remainder keep the lexical decision

#### Scenario: Repo scoping and the thin-brief abstention are unchanged
- **WHEN** a candidate pair spans two different repos, or either brief carries
  fewer than the minimum focus tokens
- **THEN** the pair is not offered to the judgment, matching the preconditions
  the lexical signal already applies

### Requirement: The judgment replaces the focus signal and nothing else
For a judged pair, the system SHALL replace the lexical focus match with the
judgment's verdict — a judgment-named match when an edge is formed and no
focus match at all when it is not — and SHALL leave every structural match on
that pair (duplicate-slug, same-target-spec, related-link) untouched, since
those are facts about the briefs rather than a reading of their text.

#### Scenario: A structural match survives a negative verdict
- **WHEN** a judged pair also carries a same-target-spec match and the
  judgment forms no edge
- **THEN** the same-target-spec match remains and still connects the pair

#### Scenario: The match names which rule decided
- **WHEN** an edge comes from the judgment rather than the threshold
- **THEN** its match type is distinguishable from the lexical one, so a reader
  and the telemetry log can tell the two apart

### Requirement: Relatedness judgment fails safe and is never required
The system SHALL treat every failure of the judgment path as "unavailable" and
keep the lexical decision for the affected pairs, and SHALL make no request at
all when no credential is configured. The first unavailable answer in a run
SHALL stop that run's judging rather than retrying per pair.

#### Scenario: No credential configured
- **WHEN** no credential is configured
- **THEN** no request is made and every pair keeps the lexical decision,
  exactly as before this capability existed

#### Scenario: The service fails partway through a run
- **WHEN** the judgment is unavailable for a pair after earlier pairs were
  judged successfully
- **THEN** judging stops for that run and the remaining pairs keep the lexical
  decision, so the surfaced clusters do not depend on where the outage began

#### Scenario: A malformed answer is not a verdict
- **WHEN** a response parses but omits either judgment value
- **THEN** the pair is treated as unavailable rather than judged from the
  missing value read as zero

### Requirement: Judged pairs are recorded for later tuning
The system SHALL record every judged pair — its members, the lexical overlap
that ranked it, both judgment values, and whether an edge was formed — to the
existing cluster telemetry log, so the threshold and the prefilter floor can be
retuned against real pairs without paying for inference again. Recording SHALL
be best-effort and SHALL never affect the edges already decided.

#### Scenario: A judged pair is logged with both values
- **WHEN** a pair is judged
- **THEN** a record carrying both judgment values, the overlap and the verdict
  is appended to the cluster telemetry log

#### Scenario: A failed write changes nothing
- **WHEN** the telemetry log cannot be written
- **THEN** the judged edges are unaffected and no error reaches the caller


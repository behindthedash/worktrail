## MODIFIED Requirements

### Requirement: Focus-Overlap Threshold
The system SHALL form a `focus-overlap` Signal Match between two
repo-eligible briefs (per the requirement above) when their token overlap
coefficient (intersection size / smaller brief's token-set size) is >=
`OVERLAP_THRESHOLD` (0.45) -- unless the pair was decided by the relatedness
judgment, in which case the judgment's verdict replaces this match entirely
(see the `brief-relatedness-judgment` capability). The threshold remains the
whole decision for every pair the judgment did not decide, including every
pair when the judgment is unavailable.

#### Scenario: Below-threshold overlap forms no edge
- **WHEN** two repo-eligible briefs have a focus-overlap coefficient of
  0.40 and the pair was not judged
- **THEN** no `focus-overlap` Signal Match is formed

#### Scenario: Judged pair does not also carry a lexical focus match
- **WHEN** a pair whose overlap coefficient is >= `OVERLAP_THRESHOLD` is
  decided by the relatedness judgment
- **THEN** the pair carries the judgment's match or no focus match at all,
  never both, so one pair can never be counted as two focus signals

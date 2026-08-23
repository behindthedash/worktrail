## MODIFIED Requirements

### Requirement: History Search Is Bounded By The Brief's Capture Time

The system SHALL search the repository's base-branch history for changes matching each probe,
restricted to commits authored at or after a **search boundary** computed as the brief's
**anchor timestamp** minus a fixed, documented grace period (`RACE_GRACE_SECONDS`). The anchor
timestamp SHALL be selected by preference order: the brief's `released-at:` frontmatter field
when present, else its `original-created:` frontmatter field when present, else its `created:`
frontmatter field. `released-at:` is stamped whenever a brief is released back to the queue
(including after a recheck) and, when present, records the most recent time the brief was
looked at — reading it in preference to `original-created:`/`created:` prevents a repeatedly
rechecked brief's own already-cited, already-resolved history from re-surfacing as staleness
evidence on every subsequent recheck. `original-created:` remains the anchor for a
consolidated brief that has not yet been released post-consolidation, distinct from its own
`created:` field (which records the time of consolidation, not original capture) — reading
`original-created:` in preference to `created:` prevents a consolidated brief's later
consolidation time from masking work that already landed against one of its source briefs
before consolidation happened. The grace period exists to catch a delivering commit that lands
on the base branch moments before the brief describing the same work is captured (or, for a
released brief, re-released), in the same session — a same-session race that an exact-
timestamp boundary would otherwise miss entirely. Path probes SHALL be searched by path;
symbol probes (including CLI-flag-shaped probes) SHALL be searched **both** by
change-in-occurrence-count (`git log -S`) and by commit message (`git log --grep`), since a
commit that moved, reverted, or merely described the work can name the symbol in its subject
without changing its occurrence count. Each reported match SHALL carry the kind of search that
found it, and a commit found by more than one search for the same probe SHALL be reported once.
The base branch SHALL be resolved preferring the remote-tracking ref when one exists, so the
search sees work that landed upstream but has not been merged into the local checkout.

#### Scenario: A commit landing after capture is reported as evidence
- **WHEN** a brief was captured on 2026-07-31 and a commit touching one of its path probes
  landed on the base branch on 2026-08-02
- **THEN** that commit is reported as a match, carrying its short SHA, commit date, and subject

#### Scenario: A commit landing moments before capture is reported as evidence
- **WHEN** a brief's `created:` timestamp is `T`, and a commit touching one of its probes
  landed on the base branch at `T` minus 56 seconds — well inside the grace period
- **THEN** that commit is reported as a match, not silently excluded

#### Scenario: A commit predating capture is not evidence
- **WHEN** the only commit touching a probe landed before `T` minus `RACE_GRACE_SECONDS`, the
  grace-widened search boundary
- **THEN** it is not reported as a match, because work that far outside the search boundary
  cannot be what the brief was filed against

#### Scenario: A commit naming a symbol only in its message is found
- **WHEN** a commit's diff does not change a symbol probe's occurrence count but its subject
  names that symbol
- **THEN** the commit is reported as a match, distinguished from an occurrence-count match by
  its recorded search kind

#### Scenario: A commit found by both searches is reported once
- **WHEN** a commit both changes a symbol probe's occurrence count and names it in its subject
- **THEN** exactly one match is reported for that commit and probe

#### Scenario: Remote-tracking ref is preferred over the local branch
- **WHEN** the local base branch is behind its remote-tracking ref and the delivering commit
  exists only on the remote-tracking ref
- **THEN** the search still finds that commit

#### Scenario: A consolidated brief's original-created predates its created and bounds the search
- **WHEN** a consolidated brief's `created:` frontmatter records the time it was consolidated,
  its `original-created:` frontmatter records an earlier timestamp `T0` from its earliest
  source brief, it carries no `released-at:` field, and a commit touching one of its probes
  landed on the base branch at `T0` plus one day but before the brief's `created:` timestamp
- **THEN** that commit is reported as a match, because the search boundary is computed from
  `original-created:` (`T0`), not from the later `created:` timestamp

#### Scenario: A non-consolidated, never-released brief is bounded by created as before
- **WHEN** a brief carries a `created:` frontmatter field and no `original-created:` or
  `released-at:` field
- **THEN** the search boundary is computed from `created:` exactly as before this requirement
  was modified

#### Scenario: A rechecked brief's released-at excludes history already accounted for
- **WHEN** a brief's `created:` timestamp is `T0`, it was released back to the queue after a
  prior recheck with `released-at:` timestamp `T1` (well after `T0`), and a commit touching
  one of its probes landed on the base branch between `T0` and `T1` minus `RACE_GRACE_SECONDS`
- **THEN** that commit is NOT reported as a match, because the search boundary is computed
  from `released-at:` (`T1`), not from the brief's original `created:` (`T0`) — the commit
  landed before the brief's most recent recheck and was already accounted for at that time

#### Scenario: released-at takes precedence over original-created when both are present
- **WHEN** a brief carries both `original-created:` (`T0`, from consolidation) and
  `released-at:` (`T1`, from a post-consolidation recheck, later than `T0`)
- **THEN** the search boundary is computed from `released-at:` (`T1`), not from
  `original-created:` (`T0`)

---
id: TASK-006
title: "End-to-End Testing for URL Shortener"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: e2e
dependencies: [TASK-003, TASK-004, TASK-005]
files: [test/links.e2e.spec.ts]
reqs: []
ac-mapping: [AC-2, AC-3, AC-4]
---

# TASK-006: End-to-End Testing for URL Shortener

**Functional Description**: Validate the full create-then-resolve journey across
all implemented components.

## Acceptance Criteria
- [ ] Create a link, then resolve its code back to the original URL. (AC-2, AC-3)
- [ ] Codes are unique across repeated creates. (AC-4)
- [ ] Unknown code -> 404. (AC-3)

## Implementation Details
**Files to Create**:
- `test/links.e2e.spec.ts` - end-to-end happy path + edge cases

## Definition of Done
- [ ] e2e suite passes against the assembled feature.

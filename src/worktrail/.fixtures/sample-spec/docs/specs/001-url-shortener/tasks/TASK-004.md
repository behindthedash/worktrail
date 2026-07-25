---
id: TASK-004
title: "Shorten controller (POST /links)"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: impl
dependencies: [TASK-003]
files: [src/links/shorten.controller.ts]
reqs: [REQ-002]
ac-mapping: [AC-2]
---

# TASK-004: Shorten controller (POST /links)

**Functional Description**: HTTP endpoint that validates input and calls the
shorten service.

## Acceptance Criteria
- [ ] `POST /links` with a valid URL returns 201 + the short code. (AC-2)

## Implementation Details
**Files to Create**:
- `src/links/shorten.controller.ts` - POST /links (uses shorten service)

## Definition of Done
- [ ] Integration test: valid URL -> 201; invalid -> 400.

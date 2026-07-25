---
id: TASK-003
title: "Shorten service"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: impl
dependencies: [TASK-001, TASK-002]
files: [src/links/shorten.service.ts]
reqs: [REQ-002]
ac-mapping: [AC-2]
---

# TASK-003: Shorten service

**Functional Description**: Generate a unique short code for a valid URL and
persist the mapping.

## Acceptance Criteria
- [ ] `shorten(url)` returns a unique code and stores `{code, url}`. (AC-2)

## Implementation Details
**Files to Create**:
- `src/links/shorten.service.ts` - shorten logic (uses schema + config)

## Definition of Done
- [ ] Unit tests cover code generation + persistence.

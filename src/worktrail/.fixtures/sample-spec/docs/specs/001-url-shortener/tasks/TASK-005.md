---
id: TASK-005
title: "Resolve service + redirect route"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: impl
dependencies: [TASK-001, TASK-002]
files: [src/links/resolve.service.ts]
reqs: [REQ-003]
ac-mapping: [AC-3]
---

# TASK-005: Resolve service + redirect route

**Functional Description**: Resolve a short code to its stored URL; unknown code
yields a 404.

## Acceptance Criteria
- [ ] `resolve(code)` returns the URL for a known code. (AC-3)
- [ ] Unknown code -> 404. (AC-3)

## Implementation Details
**Files to Create**:
- `src/links/resolve.service.ts` - resolve logic + GET /:code

## Definition of Done
- [ ] Unit + integration tests cover hit and miss (404).

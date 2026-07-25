---
id: TASK-001
title: "Short link schema + shared types"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: impl
dependencies: []
files: [src/db/schema.ts]
reqs: [REQ-001]
ac-mapping: [AC-1]
---

# TASK-001: Short link schema + shared types

**Functional Description**: Persistence schema for short links and the shared
types other services import. Foundation for the feature.

## Acceptance Criteria
- [ ] A `links` model persists `{ code, url, createdAt }`. (AC-1)

## Implementation Details
**Files to Create**:
- `src/db/schema.ts` - link schema + exported shared types

## Definition of Done
- [ ] Schema compiles and shared types are exported for the services.

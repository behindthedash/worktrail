---
id: TASK-002
title: "App config + module wiring"
spec: docs/specs/001-url-shortener/2026-05-29--url-shortener.md
lang: typescript
status: pending
kind: impl
dependencies: []
files: [src/config/app.config.ts]
reqs: [REQ-001]
ac-mapping: [AC-1]
---

# TASK-002: App config + module wiring

**Functional Description**: Config foundation (base URL, short-code length) and
module wiring the services depend on.

## Acceptance Criteria
- [ ] Config exposes `baseUrl` and `codeLength`. (AC-1)

## Implementation Details
**Files to Create**:
- `src/config/app.config.ts` - typed config + module provider

## Definition of Done
- [ ] Config is injectable and consumed by the link services.

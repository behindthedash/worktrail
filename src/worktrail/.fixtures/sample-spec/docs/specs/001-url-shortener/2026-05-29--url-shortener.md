# Feature Specification: URL Shortener

**ID**: 001-url-shortener
**Created**: 2026-05-29
**Language**: typescript (NestJS)

## Summary

A minimal URL shortener: create short codes for long URLs and resolve them, on a
small shared persistence + config foundation.

## Requirements

- REQ-001: Persistence + config foundation for short links.
- REQ-002: Create a short link (generate a unique code, store the mapping).
- REQ-003: Resolve a short code back to its URL (404 on unknown).

## Acceptance Criteria

- AC-1 [IMP] A link record persists `{ code, url, createdAt }`. (REQ-001)
- AC-2 [IMP] Creating a link returns a unique short code for a valid URL. (REQ-002)
- AC-3 [IMP] Resolving a known code returns its URL; unknown code -> 404. (REQ-003)
- AC-4 [SEF] Short codes are unique. (natural consequence of AC-2)

## Notes

Fixture spec for the parallel-orchestrator record/replay golden. Intentionally
small but structurally real: a shared foundation (schema + config) that two
independent feature slices (shorten, resolve) build on, plus e2e + cleanup.

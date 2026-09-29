## Context

`prepare_codex_child_environment` is the shared boundary used by skill
dispatch and the direct orchestrator Codex worker path. Its automatic selector
currently accepts a writable inherited `CODEX_HOME`; authentication inheritance
then deliberately rejects the identical parent and child to avoid replacing
the real `auth.json` with a self-link. See proposal.md for the incident
motivation and the nested-codex-home-isolation spec for the behavior contract.

## Goals / Non-Goals

**Goals:**

- Separate automatic child-home selection from the inherited parent home.
- Preserve verified, symlink-based parent authentication inheritance and the
  shared preparation boundary used by both launch paths.
- Cover both the selector/preparation behavior and the direct worker launch
  environment without invoking real Codex or reading credentials.

**Non-Goals:**

- Changing the authentication-file format, copying credentials, or weakening
  symlink and permission checks.
- Replacing explicit `--codex-home` or `WORKTRAIL_CODEX_HOME` choices.
- Adding a new user-facing configuration surface or changing API-pool
  provisioning, which already supplies an explicit child home.

## Decisions

- **Automatic selection always uses a Worktrail-owned child location, not a
  writable inherited home.** The inherited `CODEX_HOME` is an input source for
  parent authentication only. This removes the implicit alias that triggers
  the self-link guard. Retaining a writable inherited home was considered, but
  it makes successful dispatch depend on whether auth inheritance is requested
  and contradicts child-home isolation.
- **Automatic selection proves parent/child path distinction before auth
  inheritance.** If the normal automatic location resolves to the parent,
  selection must choose a distinct persistent child location. Raising at the
  later auth-link guard would preserve credential safety but leaves the common
  automatic path unusable; silently accepting the same path risks credential
  destruction.
- **Explicit paths remain fail-closed.** An override continues to choose the
  child path exactly. Existing writability, symlink, and identical-home guards
  report unsafe explicit input rather than silently substituting a different
  directory, which keeps operator configuration predictable.
- **Keep the behavior in the shared preparation helper.** Both the CLI skill
  adapter and `spawnlib` already call it. Fixing only the adapter would leave
  direct orchestrator workers vulnerable to the same startup failure, while a
  second implementation would drift. Tests will observe the adapter’s prepared
  environment and the actual environment handed to the worker subprocess.

## Risks / Trade-offs

- **An existing automatic child home can contain stale local state** → retain
  the established persistent Worktrail-home behavior and refresh only the
  Worktrail-managed skill links; authentication continues to be a verified
  link to the parent, never a copied token.
- **A caller may have relied on writable inherited state** → explicit
  `--codex-home` and `WORKTRAIL_CODEX_HOME` remain the documented opt-in for a
  caller-selected home, with existing fail-closed validation.
- **Tests could accidentally exercise a real login or credential file** → use
  temporary directories and mocked process calls; assert path identity and
  environment values only.

## Migration Plan

No data migration is required. Existing automatic invocations begin using the
isolated Worktrail child location; callers needing a particular child state can
continue to provide an explicit override. Reverting the code restores the old
automatic selection behavior without changing persisted credential contents.

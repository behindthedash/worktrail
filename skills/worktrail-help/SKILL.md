---
name: worktrail-help
description: >
  Show the Worktrail front-door commands and accepted positional arguments.
  Use when the user asks how to invoke worktrail-go, wants the available repo,
  spec, brief, route, or auto forms, or invokes worktrail-go with help.
allowed-tools: Read
---

# Worktrail command help

Display this concise command reference. Do not start work, claim a brief, or
run the dashboard merely because help was requested.

## Client command names

Use the command exposed by the host client:

| Client | Front door |
|---|---|
| Claude Code | `/worktrail-go` |
| Codex | `$worktrail:worktrail-go` |

The help skill itself is available as `/worktrail-help` in Claude Code or
`$worktrail:worktrail-help` in Codex.

## Accepted forms

```text
<front-door>                         dashboard and interactive picker
<front-door> help                    show this reference
<front-door> <brief-id>              claim or resume a queued handoff
<front-door> <repo>                  show active work for a repository
<front-door> auto                    auto-pick one ranked queue brief
<front-door> <repo> auto             auto-pick one brief for that repository
<front-door> drain [max-items] [repo] drain multiple items in fresh contexts
<front-door> fix <request>            classify and route a defect/request
<front-door> implement spec <id>      execute a specification
<front-door> <repo> implement spec <id>
<front-door> route:<A-J>              force a route
<front-door> <repo> route:<A-J> <id>  force a route for a repo/spec
```

`<brief-id>` is a Worktrail queue handoff identifier, such as
`20260726-140500`. A specification identifier is deterministic when used with
`implement spec <id>` or an explicit repository and route. A bare spec name
may instead be interpreted through the dashboard picker.

`auto` runs one queue item. `drain` runs multiple items with fresh contexts.
The standalone drain skill is intentionally not user-facing.

`handoff:<id>` remains an internal/documented compatibility spelling, but the
portable front-door spelling is the bare positional `<brief-id>`.

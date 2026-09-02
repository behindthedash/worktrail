---
name: worktrail-help
description: >
  Show the Worktrail front-door commands and accepted positional arguments.
  Use when the user asks how to invoke worktrail-go, wants the available
  handoff, spec, pr, repo, or route forms, invokes worktrail-go with help or
  with a bare noun, or asks how to answer a filed product decision
  (worktrail-decision).
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

This block is generated from the parser's own form registry
(`worktrail-go-parse --forms`) and pinned by
`tests/router/test_parse_invocation.py`, so it cannot drift from what the front
door actually accepts. Regenerate it rather than editing it by hand.

```text
<front-door>                                     dashboard and interactive picker
<front-door> help [topic]                        show this reference
<front-door> <repo>                              show active work for a repository
<front-door> handoff new <focus>                 capture a new handoff brief
<front-door> handoff list                        list queued handoff briefs
<front-door> handoff start <id>                  claim or resume a queued handoff
<front-door> handoff auto                        auto-pick one ranked queue brief
<front-door> handoff drain [max-items] [repo]    drain multiple items in fresh contexts
<front-door> spec new <request>                  plan a new feature
<front-door> spec implement <id>                 execute a specification
<front-door> spec continue [<id>]                resume in-flight work
<front-door> spec fix <request>                  defect repair (Route F)
<front-door> spec explore <idea>                 idea discovery (Route A)
<front-door> spec route <A-J> [<id>]             force a route
<front-door> pr fix                              PR / CI repair
<front-door> decision list                       list open product decisions awaiting an answer
<front-door> decision answer <decision-id>       answer a filed decision interactively
<front-door> <brief-id>                          claim or resume a queued handoff (same as handoff start)
<front-door> <free text>                       * classify and route any other request

* not a parsed form -- reaches the route classifier as free text
Every <noun> <verb> form takes an optional leading <repo> to scope it, e.g. <front-door> <repo> spec implement <id>.

Compatibility spellings, still accepted and read as the form shown:
<front-door> auto                      = <front-door> handoff auto
<front-door> drain [max-items] [repo]  = <front-door> handoff drain [max-items] [repo]
<front-door> handoff:<id>              = <front-door> handoff start <id>
<front-door> new <request>             = <front-door> spec new <request>
<front-door> implement [spec] <id>     = <front-door> spec implement <id>
<front-door> continue [<id>]           = <front-door> spec continue [<id>]
<front-door> fix <request>             = <front-door> spec fix <request>
<front-door> brainstorm <idea>         = <front-door> spec explore <idea>
<front-door> spec brainstorm <idea>    = <front-door> spec explore <idea>
<front-door> route:<A-J> [<id>]        = <front-door> spec route <A-J> [<id>]
<front-door> pr                        = <front-door> pr fix
```

The grammar is `<front-door> [<repo>] <noun> <verb> [args]` with four nouns:
`handoff` (the work queue), `spec` (spec-driven work), `pr` (an open pull
request), and `decision` (the human-decision queue). Forms are listed in the
order the parser checks them, so an earlier line wins over a later one. A noun typed without a recognised verb shows this
reference for that noun instead of being routed as free text.

Two bare shortcuts sit outside the noun-verb shape on purpose: `<brief-id>`
(the same as `handoff start <id>`) and `<repo>`. `<brief-id>` is a Worktrail
queue handoff identifier, such as `20260726-140500`. A specification
identifier is deterministic when used with `spec implement <id>` or
`spec route <A-J> <id>`. A bare spec name may instead be interpreted through
the dashboard picker.

`handoff auto` runs one queue item. `handoff drain` runs multiple items with
fresh contexts. The standalone drain skill is intentionally not user-facing.

The compatibility spellings are permanent, not deprecated: nothing that worked
before the noun-verb grammar stops working. `handoff:<id>` in particular stays
the internal dispatch spelling between `worktrail-go` and its executor; the
front-door spelling is `handoff start <id>` or the bare `<brief-id>`.

## Answering a filed decision

An unattended run that hits a genuine product decision files a record instead
of guessing, then blocks the brief until you answer. `<front-door> decision
list` shows what is open, and `<front-door> decision answer <decision-id>`
walks you through that record's Question/Background/Options interactively and
records your answer. The bare `<front-door>` dashboard surfaces the same open
decisions as a picker category. Full interactive procedure:
`../worktrail-go/references/answer-decision.md`.

For non-interactive use (e.g. scripting an answer), the underlying CLI is
still available directly:

```text
worktrail-decision list                         everything open/answered/resolved
worktrail-decision answer <id> --answer "..."    record your answer
```

The blocked brief unblocks automatically once you answer; the next drain pass
picks it back up. Full filing/resuming procedure:
`../worktrail-go/references/decision-queue.md`.

# Spawnlib fallback-hop argv leak

Date: 2026-08-25  
Handoff: `20260825-095114-fix-spawnlib-spawn-agent-fallback`  
Recommended next route: **F — Defect Repair**

## Business outcome

When the preferred headless agent is capacity-gated, Worktrail must launch the
selected fallback with argv valid for that fallback CLI. This keeps unattended
task dispatch progressing instead of turning a recoverable provider-capacity
event into an immediate CLI usage failure.

## Verified observations

1. `LiveSpawn.__call__` derives `_LEAN_WORKER_FLAGS` only when its initially
   resolved agent is Claude. Those flags contain Claude-only options including
   `--strict-mcp-config`, `--tools`, and `--setting-sources`.
2. `LiveSpawn` passes those flags through `spawn_claude_p` to
   `spawn_agent(..., agent="claude", extra_args=...)` together with the fallback
   chain.
3. `spawn_agent` checks persisted capacity before its first subprocess launch.
   If Claude is gated, it replaces the local `agent` and `model` variables with
   the first ungated fallback.
4. The initial `build_cmd` call occurs after that selection but receives the
   original, caller-supplied `extra_args` unchanged.
5. A focused reproduction that gates Claude, selects Codex, and supplies the
   live Claude lean flags captured this command:

   ```text
   codex exec --json -s danger-full-access --model gpt-5.4-mini \
     --output-last-message <tempfile> --strict-mcp-config --tools Read Bash \
     --setting-sources project,local prompt
   ```

   The three options after the output file are not Codex CLI options.
6. The later session-limit fallback path in the same function already rebuilds
   the fallback command with `extra_args=None` and documents that
   `extra_args` are primary-agent-specific. Its regression test asserts that a
   Claude-only `--append-system-prompt` argument does not reach OpenCode.
7. Existing persisted-capacity fallback coverage proves that the fallback is
   selected and runs, but does not inspect its argv. Consequently the current
   tests pass while allowing the leak.

## Unknowns

- Whether future callers will supply non-Claude, agent-specific options that
  need preservation when the selected agent remains the primary.
- Whether Worktrail should eventually represent extra arguments as a per-agent
  mapping rather than a flat sequence. That is not required for this repair.

## Hypotheses and validation

### H1: the child environment leaks across the capacity hop

Rejected. `build_child_env(agent)` runs after capacity selection and receives
the selected fallback agent. The existing session-limit path also rebuilds it
on subsequent hops.

### H2: `build_cmd` adds the Claude flags itself

Rejected. `_with_default_setting_sources` adds a default only when its `agent`
argument is Claude. The invalid options in the Codex reproduction came from
the flat `extra_args` passed into `spawn_agent`.

### H3: initial capacity-hop selection fails to re-filter primary-agent argv

Confirmed. The selected agent changes before the first `build_cmd`, but
`extra_args` does not. The captured command directly demonstrates the data
flow and matches the reported Codex/OpenCode usage failures.

## Confirmed root cause

`spawn_agent` treats `extra_args` as though they remain valid after persisted
capacity selection changes the serving agent. The API accepts one flat
sequence derived for the primary agent, and the initial fallback path forwards
that sequence to `build_cmd` for a different CLI. The session-limit hop has the
correct boundary behavior (`extra_args=None`), but the pre-launch capacity hop
does not apply the same rule.

## Recommended Route F repair

Keep the repair inside `spawnlib.spawn_agent`:

1. Preserve caller `extra_args` only when the persisted-capacity selection
   keeps the originally requested agent.
2. Pass no primary-specific `extra_args` when selection starts on a fallback
   hop, matching the established session-limit-hop behavior.
3. Add focused regression tests for a gated Claude primary with Codex and
   OpenCode fallbacks. Capture the actual subprocess argv and assert Claude-only
   flags are absent, while a non-gated Claude primary still receives them.
4. Retain the existing per-hop child-environment rebuild and effort translation;
   neither participates in this defect.

This is a small, localized defect with a confirmed mechanism, so Route F can
proceed without a broader design change.

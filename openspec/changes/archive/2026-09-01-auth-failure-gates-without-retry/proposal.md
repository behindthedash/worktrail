## Why

A capacity-gated provider whose failure is an auth failure (HTTP 401, consumed
ChatGPT refresh token, "log out and sign in again") was still retried per spawn:
`spawn_agent` burned its full retry budget (3 attempts, 5s+10s backoff) before
classifying and gating the cell, and the `auth` cooldown was one hour, so a
multi-hour run re-burned that budget every hour. Live 2026-09-01: codex-sub's
refresh token was consumed and every review spawn across two runs paid this
cost before hopping to claude-sub (brief 20260901-175101).

## What Changes

- An auth-class infra failure gates the served cell on the first attempt and
  hops to the next cell in the row; no retry, no backoff.
- The `auth` default cooldown becomes one day (matching `model_unavailable`);
  operators clear it with `worktrail-agent-capacity clear` once re-authenticated.
- `classify_failure` recognises codex's consumed-refresh-token wording.

## Capabilities

### Modified Capabilities

- `model-tier-routing`: adds the auth-class gating requirement.

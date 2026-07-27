# Internal drain procedure

This procedure is reached only through `worktrail-go drain`; it is not a separate
user-facing skill.

## Arguments

```text
worktrail-go drain
worktrail-go drain <max-items>
worktrail-go drain <max-items> <repo>
worktrail-go drain dry-run
```

Resolve the agent CLI once using the same invocation-context precedence as the
main Go flow. Then run the installed console script in the background:

```bash
worktrail-drain \
  --agent "$INVOCATION_CONTEXT_AGENT" \
  ${MAX_ITEMS:+--max-items "$MAX_ITEMS"} \
  ${ARG_REPO:+--go-repo "$ARG_REPO"}
```

Only pass `--permission-arg --dangerously-skip-permissions` when the user has
explicitly opted into unattended permission bypasses. Otherwise state that
one-shots use default permission prompts and may stall on unapproved tools.

`dry-run` previews the first decision without launching a worker. Relay each
iteration outcome and the final `drain stop: ...` reason. A brief that is blocked
or fails remains in `picked/`; do not release another session's claim.

The console script launches one fresh-context `worktrail-go auto` process per
item. Do not implement draining by looping `worktrail-go auto` in the current
conversation.

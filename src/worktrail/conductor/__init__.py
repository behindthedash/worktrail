"""The conductor: one warm process per change, above the per-task fan-out.

Design: `docs/design/conductor-lanes.md` §4.4. Phases are `compile -> plan ->
dispatch -> observe -> integrate -> archive`; only `compile` lives here so far.
The rest is still `worktrail.orchestrator`.
"""

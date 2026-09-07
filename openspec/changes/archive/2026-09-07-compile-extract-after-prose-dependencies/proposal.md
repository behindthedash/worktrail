## Why

`compile.py` already turns an authored prose dependency sentence into a real plan edge, but only for one phrasing. `_DEPENDS_ON_RE` (`src/worktrail/conductor/compile.py:143`) is `r"\bdepends?\s+on\b"` — the "depends on 1.2" family and nothing else.

The same module's `PROMPT` (around `src/worktrail/conductor/compile.py:320`) tells the model something wider: a task's own text stating `"depends on 1.2", "after 2.1", or the same in prose` is a real ordering constraint. So `after 2.1` is documented to the model as an authored dependency, yet has no deterministic extraction path. On the no-model compile path — every task declaring file scope, so no model runs at all — an `after 2.1` edge is simply lost, and two file-disjoint tasks fan out into the same frontier despite the author having ordered them. On the model path the edge survives only if the model happens to honor the prompt, which is exactly the fragility the deterministic union exists to remove.

## What Changes

- Extend deterministic prose-reference extraction in `compile.py` to the `after <ids>` phrasing, using the same id-shaped-token scan, separator handling, `Task` label handling and self-reference drop already used for `depends on`.
- Scan a task's text for every dependency phrase it contains rather than only the first, so a task stating both phrasings contributes both sets of ids.
- Treat an unresolvable identifier differently per phrasing: `depends on` keeps today's compile problem, while an `after` reference that matches no task in the change is ignored silently, because `after` is ordinary English ("after the migration runs in step 3") and a hard problem there would fail compiles on prose that states no dependency at all.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `compile-prose-dependency-references`: broadened from the single `depends on` phrasing to the set of authored dependency phrasings the compile prompt already names, with a per-phrasing rule for an identifier that resolves to no task.

## Impact

- Modifies prose-reference extraction in `src/worktrail/conductor/compile.py` (`_DEPENDS_ON_RE` and `extract_prose_dep_refs`, plus the unresolvable-reference reporting in `prose_dep_edges`).
- Extends coverage in `tests/conductor/test_compile.py`.
- Additive to the compiled plan: this change can only add edges, so no plan becomes more parallel than it is today.
- No change to `tasks.md` authoring rules, `files` inference, `PROMPT`, or `runplan.py`'s edge-dropping safety rule.

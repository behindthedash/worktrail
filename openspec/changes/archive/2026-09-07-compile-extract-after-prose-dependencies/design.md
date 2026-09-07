## Context

Prose dependency extraction is deterministic and additive: it reads a task's authored text, collects id-shaped tokens after a dependency phrase, and unions them into the compiled `deps` on both compile paths. Today the only phrase it recognises is `depends on`. The compile prompt already names a second one, `after <id>`, so the model and the deterministic pass disagree about what counts as an authored dependency.

## Goals / Non-Goals

- Goal: recognise `after <ids>` with exactly the token/separator/label semantics `depends on` already has.
- Goal: keep the change additive — never remove an edge, never add a self-edge.
- Non-goal: open-ended natural-language dependency parsing. Only the phrasings the prompt names are in scope.

## Decisions

### Two phrases, one scan

`extract_prose_dep_refs` keeps its existing token scan and gains a second trigger pattern. A task's text is scanned for every dependency phrase occurrence, not just the first: `"; after 1.1. Also depends on 2.3."` is one task with two authored references, and dropping either would lose an edge the author wrote. Ids stay de-duplicated and in authored order.

### `after` does not raise an unresolvable-reference problem

`depends on` is a near-unambiguous marker: an author writing it is stating a dependency, so an identifier that matches no task is a typo worth failing compile over, and that behaviour stays.

`after` is not. It appears in ordinary task prose — "run the sweep after 30 seconds", "assert the file is gone after 2 retries" — where the digit-carrying token that follows is not a task id at all. The existing digit guard is what keeps `after the parser lands` from producing a reference, but it does not help here: `30` and `2` are id-shaped. Raising a compile problem for those would fail compiles on changes that state no dependency whatsoever, which is strictly worse than the status quo.

So an `after` reference resolves against the change's task ids and is used when it matches; when it does not match, it is dropped silently rather than reported. The asymmetry is deliberate: the `depends on` path trades a false-positive risk for typo detection because its trigger phrase earns that, and the `after` path does not.

The union stays additive either way, so the worst case for `after` is an edge that was already implied by authored order (`after 2.1` on task 2.2), never a lost or spurious ordering constraint between unrelated tasks — a matched identifier is by construction a real task in the change.

## Risks / Trade-offs

- A sentence like "delete the temp file after 1.1 completes" adds an edge to 1.1 that the author arguably meant as narration. Accepted: the edge is real (the sentence describes an ordering), and an extra edge only serialises, never breaks.
- Silent drops mean an `after 9.9` typo goes unnoticed. Accepted per the decision above; `depends on` remains the phrasing to use when a hard reference check is wanted.

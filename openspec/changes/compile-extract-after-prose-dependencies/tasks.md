## 1. Recognise the "after" Phrasing

- [x] 1.1 In `src/worktrail/conductor/compile.py`, extend deterministic prose-reference extraction so `extract_prose_dep_refs` recognises the `after <ids>` phrasing alongside `depends on <ids>` — same id-shaped-token scan, separator set, `Task` label handling, trailing-period strip and self-reference drop — and scan every dependency phrase occurrence in the text rather than only the first, keeping ids de-duplicated and in authored order; in `prose_dep_edges`, resolve identifiers as today but drop an unmatched identifier that came from an `after` reference silently while keeping the existing compile problem for an unmatched `depends on` identifier; cover the new extraction cases (bare `after`, comma-joined ids after `after`, both phrasings in one text, self-reference, non-id token), the additive union on both the no-model and model compile paths for a file-disjoint `after` pair, and the per-phrasing unresolvable-identifier behaviour in `tests/conductor/test_compile.py`. (Requirement: Authored prose dependency references become plan edges) (Requirement: An unresolvable prose dependency reference is reported)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends on 1.1.

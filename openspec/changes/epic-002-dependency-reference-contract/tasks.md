## 1. Failing Regression Coverage

- [ ] 1.1 Add focused failing API and CLI tests in `tests/workqueue/test_create_handoff.py` for accepted full IDs/prefixes, rejected blank and comma-joined values, actionable repeated-flag error text, valid ordered repeated flags, and no queue directory or brief after rejection. (Requirement: A blocked-by value represents exactly one dependency reference) (Requirement: CLI callers receive actionable repeated-flag guidance) (Requirement: Repeated blocked-by arguments preserve dependency list structure)
- [ ] 1.2 Add a focused regression test in `tests/workqueue/test_create_handoff.py` proving creation does not rewrite an existing malformed queue brief. (Requirement: Producer validation does not reinterpret existing queue data)

## 2. Producer Validation

- [ ] 2.1 Implement and apply single-reference `blocked_by` validation in `src/worktrail/workqueue/create_handoff.py` before any queue filesystem mutation, preserving trimmed valid values in order and raising actionable `ValueError` failures shared by API and CLI callers.

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_create_handoff.py` and confirm the focused regression suite passes.
- [ ] 3.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass.

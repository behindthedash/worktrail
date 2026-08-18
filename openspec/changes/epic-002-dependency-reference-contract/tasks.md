## 1. Producer Validation and Regression Coverage

- [x] 1.1 Add focused failing API and CLI tests in `tests/workqueue/test_create_handoff.py`, then implement and apply single-reference `blocked_by` validation in `src/worktrail/workqueue/create_handoff.py` before any queue filesystem mutation so the focused suite finishes green. Cover accepted full IDs/prefixes, rejected blank and comma-joined values, actionable repeated-flag error text, valid ordered repeated flags, no queue directory or brief after rejection, and proof that creation does not rewrite an existing malformed queue brief. Preserve trimmed valid values in order and share actionable `ValueError` failures across API and CLI callers. (Requirement: A blocked-by value represents exactly one dependency reference) (Requirement: CLI callers receive actionable repeated-flag guidance) (Requirement: Repeated blocked-by arguments preserve dependency list structure) (Requirement: Producer validation does not reinterpret existing queue data)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_create_handoff.py` and confirm the focused regression suite passes.
- [ ] 2.2 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` and confirm both repository gates pass.

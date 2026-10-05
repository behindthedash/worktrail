## 1. OpenSpec file-declaration parser

- [ ] 1.1 Validate `files:` tokens as path-like repository paths, retain only valid tokens, and report rejected tokens without changing the parser's tolerant continuation behavior. (Requirement: Inline file-scope tokens are path-like repository paths) (Requirement: Invalid inline file-scope tokens warn tolerantly)
  files: src/worktrail/taskformats/openspec/schema.py tests/taskformats/openspec/test_openspec_schema.py

## 2. Verification

- [ ] 2.1 [e2e] Run the focused OpenSpec schema tests and the full test suite to verify valid declarations still seed scope while prose and unsafe paths do not.

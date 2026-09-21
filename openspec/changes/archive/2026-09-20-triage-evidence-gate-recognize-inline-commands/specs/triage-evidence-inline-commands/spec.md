## ADDED Requirements

### Requirement: An inline interpreter reproduction counts as reproduction evidence
The `work-directly` evidence check SHALL accept evidence that cites an inline interpreter
reproduction -- `python`, `python3`, or `py` followed by a `-c` or `-m` flag -- as citing a
command, exactly as it accepts `pytest`, `make <target>`, or "reproduced via" phrasing. The
interpreter name alone SHALL NOT qualify: the flag is required, so prose naming Python without
running it is still not evidence. Apply and its no-confirm preview SHALL agree, since both read
the same rule.

#### Scenario: an inline python3 -c reproduction is accepted
- **WHEN** an evaluator returns `work-directly` whose evidence quotes `python3 -c "from
  worktrail.router.dashboard import _resolve_repo_dir; _resolve_repo_dir('x'*300, None)" ->
  OSError Errno 36` and whose `premise_check` has no confirmed entry
- **THEN** the verdict is applied and the brief is stamped `seeded-from:
  triage:<run-date>:direct`, not downgraded to `keep`

#### Scenario: a module invocation is accepted
- **WHEN** the evidence cites `python -m worktrail.orchestrator.orchestrate check`
- **THEN** the evidence is accepted as citing a command

#### Scenario: bare prose about Python is not evidence
- **WHEN** the evidence says only "the python code path here looks wrong" and the
  `premise_check` has no confirmed entry
- **THEN** the verdict is downgraded to `keep` with the existing downgrade note

#### Scenario: preview agrees with apply
- **WHEN** a verdict accepted on inline-command evidence is previewed without `--confirm`
- **THEN** the preview reports it as applying, not as a downgrade

### Requirement: A bare-basename path needle resolves anywhere in the checkout
When a `path` needle contains no `/` and does not exist at the repo root, the premise check
SHALL look the basename up across the checkout's tracked files and confirm the needle when it
is found, reporting the resolved path in its detail. When several tracked files carry that
basename the needle SHALL still confirm, with a detail naming the match count and one example
path. When no tracked file carries it, the existing unconfirmed `path does not exist` outcome
SHALL be retained. A needle that already contains a `/` SHALL be resolved exactly as it is
today, relative to the repo root.

#### Scenario: a bare filename under src/ confirms
- **WHEN** the needle is `queue_triage.py` and the checkout tracks
  `src/worktrail/workqueue/queue_triage.py`
- **THEN** the row confirms and its detail names the resolved path

#### Scenario: an ambiguous basename still confirms
- **WHEN** the needle is `__init__.py` and many tracked files carry that basename
- **THEN** the row confirms and its detail names the match count and one example path

#### Scenario: an unknown basename still refutes
- **WHEN** the needle is `not_a_real_module.py` and no tracked file carries that basename
- **THEN** the row is unconfirmed with the existing "path does not exist" detail

#### Scenario: a rooted path needle is unaffected
- **WHEN** the needle is `src/worktrail/workqueue/queue_triage.py:194`
- **THEN** it is resolved relative to the repo root as before, including its line-count
  refinement, with no basename lookup

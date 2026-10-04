## MODIFIED Requirements

### Requirement: A worker environment can be supplied from a declared profile without storing values

The routing table SHALL accept an `env_profiles` section mapping a profile name to
`{from, keys, expect?}`, where `from` names a JSON file containing an `env` object, `keys` lists
the key names to copy from that object, and `expect` optionally maps a key name to the literal
value it must equal. A profile SHALL NOT store any value.

A target MAY name a profile through `auth.profile`. Before launching a worker the system SHALL
resolve that profile, assert every `expect` entry against the file, and copy every `keys` entry
into the child environment — before the harness/pool branch, so the claude `subscription` lane's
key removal still applies. It SHALL do so using the same resolved routing table the cell was
selected from, without re-reading policy.

When the resolved table cannot supply the named profile, the system SHALL distinguish two cases.
A populated `env_profiles` table that does not declare the profile is an operator-config error
naming the target, the profile and the routing file. A resolved table that carries no
`env_profiles` at all is a resolver/caller fault: the error SHALL name the target, the profile
and the resolved routing source, SHALL attribute the failure to the resolved table, and SHALL NOT
instruct the operator to add an entry to the routing file; when the raw policy declares the named
profile, the error SHALL say so.

The system SHALL raise an operator-config error naming the target, the profile, the resolved file
and the offending key when the file is missing or unreadable, is not valid JSON, has no `env`
object, omits a declared key, has an empty or non-string value for one, or fails an `expect`
assertion. An error message SHALL NOT contain a value for any key not named in `expect`. The
system SHALL NOT launch a process when any of these fail.

`keys` and `expect` SHALL be independent: a key MAY be asserted without being copied, and copied
without being asserted.

#### Scenario: Declared keys reach the child environment

- **WHEN** a target declares `auth.profile: deepseek` and `env_profiles.deepseek` names a
  readable file with `keys: [ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN]`
- **THEN** the child environment SHALL contain both keys with the file's values

#### Scenario: A configured endpoint is proven before launch

- **WHEN** `env_profiles.deepseek.expect` asserts `ANTHROPIC_BASE_URL` and the file holds a
  different value
- **THEN** the spawn SHALL raise an operator-config error naming the key, the expected value and
  the file's actual value, before any process is launched

#### Scenario: A missing key fails loud

- **WHEN** a profile lists a key under `keys` that the source file's `env` object does not
  contain
- **THEN** the spawn SHALL raise an operator-config error naming the key and the file, before
  any process is launched

#### Scenario: A secret outside expect is never printed

- **WHEN** a profile's source file holds a credential under a key not named in `expect` and any
  resolution failure occurs
- **THEN** the raised error's message SHALL NOT contain that value

#### Scenario: An undeclared profile is a configuration error

- **WHEN** a target names an `auth.profile` that a populated resolved `env_profiles` table does
  not declare
- **THEN** the spawn SHALL raise an operator-config error naming the target, the profile and the
  routing file, before any process is launched

#### Scenario: A resolved table that lost env_profiles is a resolver/caller fault

- **WHEN** the routing file declares `env_profiles` including the profile a target's
  `auth.profile` names, but the resolved routing table carries no `env_profiles` at all
- **THEN** the spawn SHALL raise an operator-config error naming the target, the profile and the
  resolved routing source, SHALL attribute the failure to the resolved table (a resolver/caller
  fault) rather than to the routing file, SHALL say that the routing file does declare the
  profile, and SHALL NOT instruct the operator to add an entry to the routing file — all before
  any process is launched

#### Scenario: A profile applies to any harness

- **WHEN** a non-claude target declares `auth.profile`
- **THEN** its declared keys SHALL still be copied into the child environment

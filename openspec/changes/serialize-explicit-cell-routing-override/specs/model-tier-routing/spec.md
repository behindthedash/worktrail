## ADDED Requirements

### Requirement: Concurrent explicit overrides are isolated to their spawn's thread
The temporary routing file an explicit model or effort override writes SHALL be scoped to the
calling thread, never to the process environment. `explicit_cell_override()` SHALL NOT read,
write, or restore `WORKTRAIL_ROUTING_FILE` (or its legacy `GO_ROUTING_FILE` synonym) in
`os.environ`. When an override is active, `resolved_routing_file_path()` SHALL return that
thread's throwaway file ahead of the environment override and the default routing file, so
`load_policy()` -- and every cell resolution derived from it, including `spawn_agent(tier=
"explicit", ...)` -- observes the override for that thread and no other. Entering a nested
override in the same thread SHALL restore the enclosing override's file on exit, and leaving the
outermost override SHALL restore the operator's resolved routing file. The throwaway file SHALL
remain unique per override invocation.

#### Scenario: Overlapping overrides do not clobber each other
- **WHEN** two threads each enter an explicit override for different targets and models and
  both remain inside their blocks while resolving routing
- **THEN** each thread's `resolved_routing_file_path()` names its own throwaway file, each
  thread's `load_policy()` resolves that thread's `tier="explicit"` cell to its own model, and
  neither thread observes the other's file

#### Scenario: A thread outside an override sees operator routing
- **WHEN** a thread that is not inside an override resolves routing while a sibling thread's
  override is active
- **THEN** it resolves the operator's routing file, not the sibling's throwaway file

#### Scenario: The process environment is never mutated
- **WHEN** an explicit override is active
- **THEN** `os.environ` carries the same `WORKTRAIL_ROUTING_FILE` value it did before the block
  (unset when it was unset), and after the block `resolved_routing_file_path()` again returns
  the operator's routing file

#### Scenario: Nested overrides restore the enclosing override
- **WHEN** an explicit override is entered inside another explicit override in the same thread
- **THEN** the inner block resolves the inner throwaway file, and after the inner block exits the
  outer block again resolves its own throwaway file

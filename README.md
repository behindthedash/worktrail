# worktrail

Spec-format-agnostic task orchestration: parallel git-worktree fan-out execution, a deterministic
route classifier, and a work-queue handoff system — extracted from the `developer-kit` Claude
Code plugin marketplace so they can be consumed by any harness and paired with any spec/task
format via the `TaskSource` adapter interface.

See [AGENTS.md](AGENTS.md) for architecture, origin, and development workflow.

Worktrail understands DevKit task files, OpenSpec changes, and GitHub Spec Kit feature tasks
(`.specify/specs/<feature>/tasks.md`) through the `TaskSource` adapter interface.

The Claude plugin also includes a once-per-session Stop hook, with OpenCode `session.idle`
parity, that asks for an exceptional next-step idea after substantive work and captures it
through `worktrail-handoff` when appropriate.

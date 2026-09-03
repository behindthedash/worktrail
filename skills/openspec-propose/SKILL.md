---
name: openspec-propose
description: Propose a new change with all artifacts generated in one step. Use when the user wants to quickly describe what they want to build and get a complete proposal with design, specs, and tasks ready for implementation.
allowed-tools: Bash(openspec:*)
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.6.0"
---

Propose a new change - create the change and generate all artifacts in one step.

I'll create a change with artifacts:
- proposal.md (what & why)
- design.md (how)
- tasks.md (implementation steps)

When ready to implement, do NOT run /opsx:apply — worktrail's own orchestrator
replaces it (running apply would execute the change a second time). Continue
per the calling worktrail-sdd-workflow pipeline instead.

---

**Store selection:** If the user names a store (a store is a standalone OpenSpec repo registered on this machine) or the work lives in one, run `openspec store list --json` to discover registered store ids, then pass `--store <id>` on the commands that read or write specs and changes (`new change`, `status`, `instructions`, `list`, `show`, `validate`, `archive`, `doctor`, `context`). Other commands do not take the flag. Hints printed by commands already carry the flag; keep it on follow-ups. Without a store, commands act on the nearest local `openspec/` root.

**Input**: The user's request should include a change name (kebab-case) OR a description of what they want to build.

**Steps**

1. **If no clear input provided, ask what they want to build**

   Use the **AskUserQuestion tool** (open-ended, no preset options) to ask:
   > "What change do you want to work on? Describe what you want to build or fix."

   From their description, derive a kebab-case name (e.g., "add user authentication" → `add-user-auth`).

   **IMPORTANT**: Do NOT proceed without understanding what the user wants to build.

2. **Create the change directory**
   ```bash
   openspec new change "<name>"
   ```
   This creates a scaffolded change in the planning home resolved by the CLI with `.openspec.yaml`.

3. **Get the artifact build order**
   ```bash
   openspec status --change "<name>" --json
   ```
   Parse the JSON to get:
   - `applyRequires`: array of artifact IDs needed before implementation (e.g., `["tasks"]`)
   - `artifacts`: list of all artifacts with their status and dependencies
   - `planningHome`, `changeRoot`, `artifactPaths`, and `actionContext`: path and scope context. Use these instead of assuming repo-local paths.

4. **Create artifacts in sequence until apply-ready**

   Use the **TodoWrite tool** to track progress through the artifacts.

   Loop through artifacts in dependency order (artifacts with no pending dependencies first):

   a. **For each artifact that is `ready` (dependencies satisfied)**:
      - Get instructions:
        ```bash
        openspec instructions <artifact-id> --change "<name>" --json
        ```
      - The instructions JSON includes:
        - `context`: Project background (constraints for you - do NOT include in output)
        - `rules`: Artifact-specific rules (constraints for you - do NOT include in output)
        - `template`: The structure to use for your output file
        - `instruction`: Schema-specific guidance for this artifact type
        - `resolvedOutputPath`: Resolved path or pattern to write the artifact
        - `dependencies`: Completed artifacts to read for context
      - Read any completed dependency files for context
      - Create the artifact file using `template` as the structure and write it to `resolvedOutputPath`
      - Apply `context` and `rules` as constraints - but do NOT copy them into the file
      - **If `<artifact-id>` is `tasks`**, worktrail's own orchestrator (not OpenSpec) compiles
        `tasks.md` into a per-task file-scope plan afterward (`worktrail-compile`) — get it right
        on the first pass instead of relying on that later compile step to catch it. OpenSpec's
        checklist schema carries no dedicated field for file scope, but `worktrail-compile`
        recognizes an optional indented `files:` continuation line immediately under a task line
        as an inline declaration of that task's create-or-modify paths:
        ```
        - [ ] 2.3 Add the `files:` parser to `parse_tasks_md` (Requirement: Inline file-scope declaration parsing)
          files: src/worktrail/taskformats/openspec/tasks_md.py tests/taskformats/test_tasks_md.py
        ```
        This is opt-in per task, not required on every task — list only the paths the task will
        create or modify (never paths it merely reads). Where you're confident of a task's exact
        file scope, declaring it here is worth doing: a `tasks.md` where every task carries a
        `files:` declaration compiles into a RunPlan without a model call at all. Tasks that omit
        it still compile fine — the compile step infers their file scope from context, same as
        today.
        - **Requirement coverage**: for every requirement declared in this change's
          `specs/**/spec.md` (`### Requirement: <Name>` under `## ADDED Requirements` /
          `## MODIFIED Requirements`), make the exact requirement name appear somewhere in
          `tasks.md` — append `(Requirement: <exact title>)` to the task line that implements it.
        - **File-less tasks**: a task that creates or modifies no file (pure end-to-end
          verification, or pure cleanup with no diff) needs a leading tail-kind tag or
          `worktrail-compile` rejects it for having no file scope: `[e2e]` for verification-only
          tasks, `[cleanup]` for cleanup-only tasks — e.g. `- [ ] 3.1 [e2e] Run the full test
          suite and confirm it passes.` Any other kind tag, including `[docs]`, does NOT exempt a
          task from file scope — a `[docs]`-tagged task still needs a real file (the doc it
          updates).
        - **Per-phase hot-file ownership bias**: when a file recurs across more than one `##`
          phase in `tasks.md`, bias decomposition so it is owned by at most one task per phase —
          do not let two tasks within the same phase both list it in their file scope. A hot file
          that already recurs across phases just from the shape of the work is fine; the bias
          only applies to collapsing multiple same-phase tasks onto it.
        - **Per-phase file split for additive hot files**: if the hot file is additive/composable
          (entries appended to a registry, table, or list rather than a single section rewritten
          in place), don't rely on ownership bias alone — split it into separate per-phase files,
          each owned by the one task in that phase, and add a single later task that composes them
          into the final combined file. This turns what would be a cross-phase collision into
          independent per-phase file scopes plus one explicit composition step.
        - **Collision-serialization preserved for unavoidable same-file edits**: neither bias
          above eliminates every same-file collision by construction — some hot files still end
          up owned by more than one task in the same phase. That's fine: `worktrail-compile`'s
          grouping-time shared-file lane folding (serializing tasks that collide on a file into
          the same lane) still applies unchanged and remains the backstop. This guidance is a
          decomposition-time reduction of how often that backstop has to fire, not a replacement
          for it.
        - **Task sizing**: one implementation task per module per phase, sized for roughly
          20-60 minutes of work — this is a coarser grain than the file-scope guidance above,
          which governs how a task's *files* are chosen, not how many tasks a phase has. Fold
          consecutive same-file steps into one task with sub-bullets rather than a dependent
          chain of tasks.
        - **Tests co-scoped with implementation**: an implementation task's `files:` MUST
          include every existing test file asserting behavior the task changes, plus any new
          test file it adds. Never split a task's implementation and its tests into separate
          tasks.
        - **`review: skip` for mechanical tasks**: a mechanical or docs-only task (a config key,
          a prose edit, a single constant) carries an indented `review: skip` continuation line,
          the same way `files:` is declared above. A task producing executable behavior never
          carries `review: skip`.
      - Show brief progress: "Created <artifact-id>"

   b. **Continue until all `applyRequires` artifacts are complete**
      - After creating each artifact, re-run `openspec status --change "<name>" --json`
      - Check if every artifact ID in `applyRequires` has `status: "done"` in the artifacts array
      - Stop when all `applyRequires` artifacts are done

   c. **If an artifact requires user input** (unclear context):
      - Use **AskUserQuestion tool** to clarify
      - Then continue with creation

5. **Show final status**
   ```bash
   openspec status --change "<name>"
   ```

**Output**

After completing all artifacts, summarize:
- Change name and location
- List of artifacts created with brief descriptions
- What's ready: "All artifacts created! Ready for implementation."
- Prompt: within a worktrail pipeline, continue per the calling
  worktrail-sdd-workflow procedure — do NOT run `/opsx:apply` (worktrail's
  orchestrator replaces it). Standalone use only: ask the user whether to
  implement now.

**Artifact Creation Guidelines**

- Follow the `instruction` field from `openspec instructions` for each artifact type
- The schema defines what each artifact should contain - follow it
- Read dependency artifacts for context before creating new ones
- Use `template` as the structure for your output file - fill in its sections
- **IMPORTANT**: `context` and `rules` are constraints for YOU, not content for the file
  - Do NOT copy `<context>`, `<rules>`, `<project_context>` blocks into the artifact
  - These guide what you write, but should never appear in the output

**Guardrails**
- Create ALL artifacts needed for implementation (as defined by schema's `apply.requires`)
- Always read dependency artifacts before creating a new one
- If context is critically unclear, ask the user - but prefer making reasonable decisions to keep momentum
- If a change with that name already exists, ask if user wants to continue it or create a new one
- Verify each artifact file exists after writing before proceeding to next
- Before showing final status, re-check `tasks.md` against the requirement-coverage,
  file-less-task, task-sizing, test-co-scoping, and `review: skip` rules above — every declared
  requirement's exact name must appear somewhere in the file, every task with no file changes
  must carry `[e2e]` or `[cleanup]`, every implementation task is sized per module per phase with
  its changed tests co-scoped in `files:`, and every mechanical/docs-only task carries
  `review: skip`

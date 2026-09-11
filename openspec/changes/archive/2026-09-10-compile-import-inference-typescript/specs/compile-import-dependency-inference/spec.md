## MODIFIED Requirements

### Requirement: Imports between tasks' declared files become plan edges

The compile step SHALL, for each non-tail task, scan the imports of every declared file that
is a supported source file present on disk under the repository, resolve each import to a
repo-relative path, and where that path is declared in the file scope of a different task in
the same change, include that task's id in the importing task's compiled `deps`. Supported
source files are Python (`.py`) and TypeScript/JavaScript (`.ts`, `.tsx`, `.mts`, `.cts`,
`.js`, `.jsx`, `.mjs`, `.cjs`). This SHALL hold whether or not the two tasks declare any
file in common. The union SHALL be additive only: no dependency edge present before the
union is removed by it, and no self-edge is ever added.

#### Scenario: A consumer of a sibling task's module is ordered behind it

- **WHEN** task B declares a Python file that imports a module whose path task A declares,
  and A's and B's declared files share no path
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: A TypeScript consumer of a sibling task's module is ordered behind it

- **WHEN** task B declares `src/app/page.tsx` containing `import { fmt } from "./lib/format"`
  and task A declares `src/app/lib/format.ts`, and A's and B's declared files share no path
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: Direction follows the import, not authored order

- **WHEN** task 1.1 declares a file that imports a module task 2.1 declares, and no
  dependency edge already leads from 2.1 back to 1.1
- **THEN** the compiled plan records 2.1 in 1.1's dependencies

#### Scenario: Files that import nothing of each other are unaffected

- **WHEN** no declared file of any task imports a module declared by another task
- **THEN** every task's compiled dependencies are exactly what the compile step produced
  without this rule

#### Scenario: A file importing its own task's other file adds no edge

- **WHEN** a task declares two files and one imports the other
- **THEN** no edge is added for that import

### Requirement: Inference never introduces a cycle and never fails a compile

An inferred edge that would make a task depend, directly or transitively, on a task that
already depends on it SHALL be skipped, and the skip SHALL be recorded as a compile warning
naming both tasks. A declared file that is missing from disk, is not a supported source
file, or does not parse or decode SHALL contribute no edges and SHALL NOT raise or produce a
compile problem.

#### Scenario: A mutual import keeps the plan acyclic

- **WHEN** task A's file imports task B's file and task B's file imports task A's file
- **THEN** the compiled plan records exactly one edge between them, the plan remains acyclic,
  and a warning names both tasks

#### Scenario: A mutual TypeScript import keeps the plan acyclic

- **WHEN** task A's `.ts` file imports task B's `.ts` file and task B's file imports task A's
- **THEN** the compiled plan records exactly one edge between them, the plan remains acyclic,
  and a warning names both tasks

#### Scenario: A not-yet-created file contributes nothing

- **WHEN** a task declares a path that does not exist on disk
- **THEN** that path is skipped and the compile proceeds without a problem

#### Scenario: An unparseable file contributes nothing

- **WHEN** a task declares a Python file that raises a syntax error when parsed
- **THEN** that file is skipped and the compile proceeds without a problem

#### Scenario: An unsupported file type contributes nothing

- **WHEN** a task declares a file whose suffix is neither Python nor TypeScript/JavaScript,
  such as a Markdown file containing import-like text
- **THEN** that file is skipped and the compile proceeds without a problem

## ADDED Requirements

### Requirement: Relative TypeScript and JavaScript specifiers are resolved to paths

For a declared TypeScript or JavaScript file, the compile step SHALL collect the
string-literal module specifier of each ES-module `import ... from` declaration,
side-effect `import` declaration, `export ... from` declaration, dynamic `import(...)`
call, and CommonJS `require(...)` call. A relative specifier (beginning with `./` or `../`)
SHALL be resolved against the importing file's directory by trying, in order and stopping
at the first existing file: the specifier as written; the specifier with each supported
extension appended (`.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.d.ts`);
for a specifier ending in `.js`, `.jsx`, `.mjs`, or `.cjs`, the same path with that
extension replaced by `.ts`, `.tsx`, `.mts`, or `.cts` respectively; and the specifier as a
directory containing an `index` file with each supported extension. A bare specifier (a
package name or scoped package name) and a path-alias specifier SHALL be ignored, and a
specifier that resolves to no file under the repository SHALL be ignored. Matching against
other tasks' declared scope SHALL be by repo-relative path equality.

#### Scenario: An extensionless relative import resolves to a .ts file

- **WHEN** task B's file `src/a/use.ts` contains `import { x } from "../lib/thing"` and
  task A declares `src/lib/thing.ts`
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: A .js-suffixed specifier resolves to its .ts source

- **WHEN** task B's file contains `import { x } from "./thing.js"`, no `thing.js` exists
  beside it, and task A declares the sibling `thing.ts`
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: A directory specifier resolves to its index file

- **WHEN** task B's file contains `import { x } from "./widgets"` and task A declares
  `src/widgets/index.tsx` beside it, with no `widgets.*` file present
- **THEN** the compiled plan records A in B's dependencies

#### Scenario: export-from, dynamic import, and require are all seen

- **WHEN** task B's file references task A's declared file via `export * from "./m"`,
  `await import("./m")`, or `const m = require("./m")`
- **THEN** in each case the compiled plan records A in B's dependencies

#### Scenario: A bare package specifier adds nothing

- **WHEN** a task's file contains `import React from "react"` or
  `import x from "@scope/pkg/sub"`
- **THEN** no edge is added and no problem or warning is reported for that import

#### Scenario: A path-alias specifier adds nothing

- **WHEN** task B's file contains `import { x } from "@/lib/thing"` and task A declares
  `src/lib/thing.ts`
- **THEN** no edge is added between A and B and no problem or warning is reported

#### Scenario: A relative specifier that escapes the repository adds nothing

- **WHEN** a task's file at the repository root contains `import x from "../outside"` and a
  file exists at that location outside the repository
- **THEN** no edge is added and the compile proceeds without a problem

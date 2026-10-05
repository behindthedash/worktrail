## Context

Twelve source sites cite a root-relative `contracts/*.md` path: eleven under
`src/worktrail/` (`orchestrator/coordinator.py`, `orchestrator/dispatch.py`,
`orchestrator/live.py` x4, `router/spec_sync_sweep_brief.py`,
`router/spec_sync_sweep_checkbox_brief.py`, `router/run_record.py`,
`taskformats/devkit/source.py`, `workqueue/dist_tag_watch.py`) and one under `tests/`
(`orchestrator/test_precheck.py`). None of the cited documents exists in this repository:
they are developer-kit path remnants, inherited whole at the Phase 1a extraction
(`git log -S 'contracts/'` on a citing file bottoms out at extraction commit `f1d81211`;
`git log --all --diff-filter=D -- 'contracts/*'` is empty). Two sites wrap the filename
across a comment continuation (`coordinator.py:117`, `live.py:1383`), so a per-line match
cannot see them as citations. The surrounding text at nearly every site already states
the behavior the pointer claims to document.

Three `contracts/` uses are *not* this defect and must survive: `dispatch.py:667`'s worker
prompt string listing what a spec folder contains (`spec.md + data-model.md + contracts/`),
`classify_handoff.py:79`'s `contracts/*.md` glob over a spec folder, and
`drain/summary_contract.py:9`'s real fixture path
`.fixtures/contracts/nightly-drain-summary-v1.json`. All three are spec-folder-relative
or fixture-relative; none cites a repository-root document.

The class has a regression precedent: the `run_record.py` citation was dropped by the
archived `active-conflicts-staleness-reconciliation` change, which explicitly chose
"update the docstring's JSON-shape description in place rather than authoring that file",
and commit `dd54135d` re-added the identical dangling sentence four commits later. Nothing
in the suite can see that.

## Goals / Non-Goals

**Goals:**

- No source cites a root-relative `contracts/` document that does not exist, and every
  former site still explains the behavior it documents.
- The class cannot silently return: a structural guard fails the suite on a new
  root-relative citation -- wrapped or not -- whose target does not resolve.
- The three legitimate `contracts/` forms are untouched, and no repository-root
  `contracts/` directory is introduced.

**Non-Goals:**

- Authoring the missing documents or creating a repository-root `contracts/` tree.
- Repointing the citations at OpenSpec specs. Related specs exist
  (`task-source-dependency-validation`, `same-repo-run-concurrency-contract`,
  `stacked-worktree-conflict-resolution`, ...), but none *carries* the cited contract's
  content -- the field contract, the prompt-stacking note, the precheck report shape -- so
  a repoint would trade one inaccurate pointer for another.
- A general documentation-reference checker (the `docs/*.md` population includes fixture
  strings and spec-relative forms with no single resolution rule), and the sibling dangling
  `docs/specs/research/*.md` citations.
- Any behavior, CLI, or config change.

## Decisions

### D1: Drop or state in place; never author a repo-root `contracts/` tree

Alternatives considered:

- **Restore the documents.** The cited content was never in this repo, so "restoring"
  means authoring six new contract docs from scratch, inventing a repository-root docs
  convention (a seventh doc home next to `openspec/specs/`, `docs/design/`, and module
  docstrings) purely to satisfy comments. The archived precedent already rejected this for
  the one site it owned.
- **Repoint at the nearest OpenSpec spec.** See Non-Goals: a pointer is only an
  improvement if the target states the contract, and none does; several sites would then
  read as if a spec owned a contract it does not mention.
- **Drop or state in place.** Chosen. Where the prose already states the rule
  (`coordinator.py`'s `external_deps_ok` precompute note, `dispatch.py`'s worker-context
  note, the two brief-module validation-convention clauses, the devkit resolver's field
  list) the parenthetical goes; where the sentence was the pointer
  (`run_record.py`'s "prints a JSON array (see ...)") the description is corrected to the
  output the command actually prints.

### D2: The guard is existence-aware and scoped to root-relative `contracts/` citations

The guard reports a citation only when the named path does not exist at the repository
root, rather than forbidding the citation form outright. The invariant is "no pointer to a
nonexistent document"; an existence check states exactly that, cannot misfire if the repo
ever legitimately grows a `contracts/` document, and still catches the observed regression
shape (a copied `contracts/x.md` sentence) because no such document exists today.

Scope is the `contracts/` class only, and only the root-relative form: a citation starts
at a token-start `contracts/` (not preceded by a path character) and carries a Markdown
filename. Prefixed forms are excluded because their resolution root is not the repository
(a spec folder for `docs/specs/<id>/contracts/x.md`, the citing file's directory for
`../contracts/x.md`), and the two prefixed sites this cleanup touches are judged by hand.
A flat zero-citation prohibition was rejected as over-broad (it would forbid a future real
document and misstate the invariant); a general `docs/*.md` checker was rejected because
that population is dominated by fixture-shaped strings with no clean rule.

### D3: Wrapped citations count, and the guard reads raw source text

Two of the twelve sites break mid-filename across a comment continuation, so the guard
normalizes a comment-continuation wrap inside a citation before matching rather than
grepping line by line; the fixture suite pins a wrapped citation as a detected case. Raw
text (not AST/docstrings-only) is deliberate: citations also appear inside prompt-building
string literals (`dispatch.py`'s worker brief), which are the most consequential place for
a worker to be told to read a nonexistent file.

### D4: The run-record entry is corrected, not just stripped

`run_record.py`'s `active-conflicts` entry says the command "prints a JSON array"; the
command prints `_active_conflicts()`'s `{"live": [...], "stale": [...]}` partition (its own
docstring says so). Dropping the dead pointer alone would leave the sentence wrong, so the
entry is rewritten to the printed shape -- the same in-place correction the archived
precedent performed, re-applied after its regression.

### D5: Guard placement and shape follow the existing structural-guard precedent

`tests/test_no_dangling_contracts_doc_refs.py`, modeled on
`tests/test_no_bare_head_ctx_default.py`: a module-level checker callable (so fixtures can
point it at a temporary root), a live assertion over the real repository, a docstring
naming the defect class and its regression history, and a failure message that names the
remedies. The live assertion fails loudly if either scan tree is missing, so the guard can
never pass by scanning nothing.

## Risks / Trade-offs

- [A contributor cites a bare `contracts/x.md` as a *spec-folder* example in a docstring
  and the guard flags it] -> the failure message names the two remedies: make the example
  unambiguous with its prefix (`docs/specs/<id>/contracts/x.md`) or cite the owning spec.
- [The six missing documents' content is genuinely wanted someday] -> then it gets written
  as an OpenSpec spec or a `docs/` page, which is where this repo keeps contracts, and the
  citations that need it can point there.
- [A prefixed dangling citation is reintroduced] -> the guard does not see it (documented,
  deliberate limit: prefixed forms have no single resolution root); the `contracts/` class
  the brief evidence identified is fully covered.
- [The same defect class exists for other doc paths] -> recorded as out of scope in the
  proposal; the guard generalizes later if that class earns its own rule.

## Migration Plan

None: comment and docstring text plus one new test module. Rollback is reverting the
commit; no persisted state, no interface.

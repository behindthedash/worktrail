## Context

See proposal.md for the stall and the two shipped copies of the procedure. What the change
has to respect, inspected in this checkout:

- The two files are twins with deliberate drift: `commands/opsx/archive.md` is OpenSpec
  1.6.0's generated output, and `skills/openspec-archive-change/SKILL.md` is this repo's
  lightly-edited variant (`AGENTS.md`). They share the three confirmations but word them
  differently — the skill says "Use **AskUserQuestion tool** to confirm user wants to
  proceed"; the command says "Prompt user for confirmation to continue". Any structural
  check over both files has to tolerate that split (see Decisions).
- The procedure's own step 5 is `mkdir -p` + `mv`, not `openspec archive`; the delta-spec
  merge is step 4's agent-driven sync. So the defaults this change documents must preserve
  step 4's outcome, not assume a CLI will supply it.
- `drain/drain.py`'s `_run_openspec_archive` (`openspec archive -y <change-id>`) and
  `build_sync_command` (`/opsx:sync`) are already the unattended half of this problem. They
  are the evidence that the non-interactive path is understood in this codebase; neither is
  what stalls, so neither is in scope.
- `$AUTO_MODE=true` is a held session variable in `skills/worktrail-go/SKILL.md` (the `auto`
  argument, spec 017), not an exported environment variable. Every existing fallback in the
  go/sdd-workflow surface keys on it, and the go close-out runs `/opsx:archive` in the same
  session that holds it.

## Goals / Non-Goals

**Goals:**

- The bundled archive procedure completes unattended: no `AskUserQuestion` on a path a
  headless run can reach, and no stall — a documented default or a loud refusal at every
  confirming step.
- Fail closed where guessing is destructive: an unattended run with no change name stops
  rather than choosing one.
- Leave the interactive path byte-for-byte equivalent in behaviour.
- Make the fallback structurally enforced, not prose the next edit can drop.

**Non-Goals:**

- Reworking the procedure onto the CLI (`openspec archive -y`). That is a semantics
  question (which OpenSpec version's archive behaviour this surface adopts, and whether it
  keeps step 4's assessment at all), not the interactivity stall. Drain's CLI path already
  exists and is untouched.
- The other bundled OpenSpec skills (`propose`, `explore`, `update-change`, `sync-specs`).
  Their asks are reachable only in interactive flows — `sync-specs` is dispatched with an
  explicit change id by `build_sync_command`, so its selection prompt never fires. Extending
  the same contract to them is a follow-up if one is ever shown to strand.
- `router/skill_dispatch.py` itself. The routing is correct and its `build_command`
  write-path docstring (line 629) is cited as evidence of the failure class, not as a site
  to change.
- Drain's `_run_openspec_archive` refusal semantics (unchecked tasks hard-refuse there but
  only warn here, per the upstream guardrail "Don't block archive on warnings"). The
  divergence predates this change and this change does not touch it.

## Decisions

- **Key "unattended" on two signals, not one.** `$AUTO_MODE` is `true` **or** the
  `AskUserQuestion` tool is unavailable to the session. `$AUTO_MODE` alone would fix the
  worktrail drain path and leave a bare headless `/opsx:archive` (`claude -p` against the
  plugin, no go session holding the variable) still stalling; tool-availability alone would
  be invisible to a reader grepping for the repo's existing marker. Both are cheap to state
  and neither has a false positive that matters — an interactive session has the tool and is
  not in auto mode, so it prompts exactly as before.

- **One greppable marker per site, defined once per file.** Every confirming step carries
  `**Unattended: no ask.**` with its default, mirroring the `{#auto-mode-ask-fallbacks}`
  shared-contract-plus-per-site-annotation shape the go surface already uses. A marker the
  test can find is what makes the invariant enforceable; prose that only reads correctly to
  a human is what the next edit silently breaks.

- **Step 1 fails closed; steps 2–4 take the documented default.** The asymmetry is
  deliberate. Steps 2–4 are confirmations of a known change — the worst case of proceeding
  is an archive with a warning, which the procedure already tolerates interactively
  ("Don't block archive on warnings"). Step 1 is *selection*: proceeding means picking a
  change the caller did not name, and the change that gets picked could be another run's
  in-flight work. The interactive path already forbids guessing here ("Do NOT guess or
  auto-select a change"); unattended promotes that from a prompt to an error.

- **Step 4's unattended default is the recommended option, not "archive without syncing".**
  The go close-out documents `/opsx:archive` as performing the merge, and step 5 only moves
  the directory — so in this procedure the sync is the only thing that lands the delta. A
  headless run that silently skipped it would leave `openspec/specs/` un-updated while the
  change looks archived, which is a worse outcome than the stall this change removes.

- **The structural test keys on a closed prompt vocabulary, not on a single literal.** The
  two files word their confirmations differently (see Context), so the check matches a small
  set of cue strings — `AskUserQuestion` (the skill's wording at steps 1–3), `Prompt options`
  (step 4, both files) — and requires the marker in the same numbered step. This is the same
  closed-vocabulary approach `test_skill_prose_enforcement_coverage.py` landed on after the
  paragraph-pairing prototype scored near-zero recall; a new prompt phrasing is caught by
  adding its cue, and the test's failure names the file and step either way.

- **Split on numbered steps, not `##` sections.** The archive docs have no `##` headings in
  the procedure body (`**Steps**` is a bold line), so `_h2_sections` would collapse the whole
  file into one block and the invariant would be vacuous. Splitting on the `^\d+\. ` list
  markers gives the granularity the three ask sites actually live at.

- **The test lives in `tests/test_plugin_surface.py`.** That is where the bundle's structural
  invariants already are (`test_opsx_apply_is_never_dispatched`, the frontmatter and manifest
  checks), and it already holds `REPO_ROOT`. The command file is not under `skills/`, so the
  test carries its own path pair rather than reusing `_skill_docs()`.

## Risks / Trade-offs

- [The bundled text diverges further from upstream OpenSpec's generated output] → the repo
  already edits this text deliberately (AGENTS.md) and regenerates from `openspec init` only
  when it chooses to; an unattended carve-out is the same kind of edit as the existing
  redirects to worktrail's pipeline. A future regeneration re-loses it, exactly as it would
  re-lose the redirects.
- [An unattended step 4 invokes the sync skill through the `Task` tool] → unchanged by this
  change: step 4 already specifies that path, and on a host without `Task` the sync fails as
  it already would while the procedure still proceeds to archive. This change does not make
  the sync newly conditional on anything.
- [`$AUTO_MODE` reads as a worktrail-specific marker inside a skill that also ships
  standalone] → the tool-availability half of the condition covers the standalone case;
  `$AUTO_MODE` is stated as an additional affirmative signal, not the only one.
- [The marker becomes ritual text a future edit pastes without meaning] → the test can only
  check the marker's presence. The defaults it accompanies are the substantive part and are
  pinned by the spec's scenarios; a pasted marker with a wrong default would still read
  wrong to a reviewer, which is the same exposure every other prose invariant here carries.

## Migration Plan

None. Two documentation edits and one test; no configuration, journal schema, or data
migration. Rollback is a revert, and the interactive behaviour the revert restores is the
pre-change behaviour.

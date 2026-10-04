## 1. The unattended contract, in both shipped copies

- [ ] 1.1 In `skills/openspec-archive-change/SKILL.md` and `commands/opsx/archive.md`, add
      one block that defines what makes a run unattended — `$AUTO_MODE` is `true`, or the
      `AskUserQuestion` tool is unavailable to the session (a headless one-shot registers no
      such tool) — and states that an unattended run takes each site's documented default and
      records what it skipped in the summary's warnings instead of prompting. Then annotate
      every confirming step in both files with the marker `**Unattended: no ask.**` and its
      default:
      - step 1 (no change name given): stop with an error naming the required `<change-id>`,
        archive nothing, and never auto-select — the interactive path's existing "do NOT
        guess or auto-select" rule, promoted from a prompt to a refusal;
      - step 2 (incomplete artifacts): list them in the summary's warnings and proceed;
      - step 3 (incomplete tasks): report the incomplete-task count and proceed;
      - step 4 (delta-spec sync): take the option the step marks recommended — "Sync now"
        when changes are needed, "Archive now" when already synced — and archive regardless
        of the sync's outcome, as the step already does after a choice;
      - step 6 (summary): name every confirmation skipped and the default taken, so an
        unavailable-prompt run is visible in its own output.
      In the Guardrails section of each file, extend the "always prompt for change selection
      if not provided" rule with the same carve-out, since it is the one guardrail that
      contradicts step 1's fallback head-on. Leave the interactive wording of every ask
      intact — the skill's "Use **AskUserQuestion tool** to confirm user wants to proceed"
      and the command's "Prompt user for confirmation to continue" both stay as they are,
      with the fallback alongside them. Do not renumber the steps, and do not change step 5's
      archive mechanics.
      (Requirements: The bundled archive procedure never blocks on an interactive
      confirmation; An unattended archive refuses a change it was not told to archive.)
      files: skills/openspec-archive-change/SKILL.md, commands/opsx/archive.md

## 2. The structural pin

- [ ] 2.1 Extend `tests/test_plugin_surface.py` with a test over both archive files
      (`skills/openspec-archive-change/SKILL.md`, `commands/opsx/archive.md`) that asserts,
      per file: the unattended condition is defined (a fixed phrase from the contract block,
      e.g. the marker and the condition sentence); every numbered step whose text carries a
      prompt cue from a closed vocabulary — `AskUserQuestion` for steps 1–3 of the skill,
      `Prompt options` for step 4 of both files — also carries `Unattended: no ask.` in the
      same step; and the Guardrails change-selection rule carries the carve-out. Split the
      file body on its `^\d+\. ` list markers rather than on `##` headings (the procedure
      body has none, so an h2 split collapses it to one block and the invariant would be
      vacuous). Assert the closed cue vocabulary and the marker string exactly, so a step
      reworded to drop its fallback — or a new unguarded prompt phrasing — fails with the
      file and step named. Keep the helper's file list explicit rather than reusing
      `_skill_docs()`, which walks `skills/` only and would miss `commands/opsx/`.
      (Requirements: Every confirming step's fallback is pinned structurally.)
      depends: 1.1
      files: tests/test_plugin_surface.py

## 3. Verification

- [ ] 3.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q tests/test_plugin_surface.py`,
      then the full `PYTHONPATH=src python3.14 -m pytest -q`,
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`,
      `python3.14 scripts/ci/ruff_pinned.py check .`,
      `python3.14 scripts/ci/ruff_pinned.py format --check .` and
      `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then confirm the pin actually
      bites: temporarily strip the `Unattended: no ask.` marker from one step of
      `commands/opsx/archive.md`, observe the new test fail naming that file and step,
      and restore the marker. Finally run
      `openspec validate headless-opsx-archive-noninteractive --strict` and
      `worktrail-compile openspec/changes/headless-opsx-archive-noninteractive`.

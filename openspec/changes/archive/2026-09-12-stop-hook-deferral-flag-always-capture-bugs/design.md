## Context

See `proposal.md` (Why) for the two faults. Current state of `hooks/suggest_next_step.py`:

- **`INSTRUCTION`** is one constant string. `main()` always emits it first. Two optional blocks
  can be appended after it: `build_deferred_work_block` and `build_dedup_gate_block`. Every
  byte-identity test compares output against `hook.INSTRUCTION` itself, not a copy of the text.
  Only `test_instruction_is_worktrail_native_and_value_gated` pins literal phrases.
- **`durable_artifact_paths_from_entry`** handles Bash commands in two steps:
  1. Treat the command as a write if its lowercased text contains any `BASH_WRITE_MARKERS`
     substring (`">"`, `"cp "`, `"rm "`, and so on).
  2. If it does, run `DURABLE_ARTIFACT_PATH_RE.findall` over the whole command.

  Both steps are too broad. The first matches `2>/dev/null`. The second picks up quoted
  arguments and the operands of unrelated commands.
- **`worktrail-check-durable-artifact-capture-gate`** turns each `--touched-path` it receives into
  a hit. Fixing detection in the hook is therefore enough; the checker does not change.
- **Constraints:**
  - The only runtime dependency is `pyyaml`.
  - The hook must fail open and must never raise.
  - v1.0 fixes-only: no new flags, CLIs, or capabilities.

## Goals / Non-Goals

**Goals:**
- Make capturing unfixed verified defects a mandatory, ungated step of the base instruction.
- Make the Bash write detector report exactly the durable paths a command writes. Existing
  positive detection must keep working: `test_scan_transcript_collects_touched_durable_paths_from_bash_write_markers`
  must pass unchanged.

**Non-Goals:**
- Detecting defects mechanically from the transcript. The hook has no reliable signal for this,
  and it would be a new feature. The mandate remains agent-judged instruction text.
- Parsing arbitrary shell. Writes hidden behind wrappers go unreported, as they do today:
  `python -c "open(...,'w')"`, `bash -c '...'`, `xargs`, `find -exec`, `sudo`.
- Changing the checker CLI, the deferred-work block, or the PR-ledger guard.

## Decisions

### D1. Defect capture is a numbered step inside `INSTRUCTION`, not an appended block

The instruction's post-completion audit gets a new first step. Draft wording, which the
implementer may refine as long as the phrases pinned by task 1.1 survive:

> 1) DEFECTS — mandatory, not value-gated. For every verified defect, bug, or regression you
> discovered this session and did not fix (reproduced or directly evidenced by code, logs, or test
> output — not a hypothesis), capture one brief per distinct defect: run
> `worktrail-handoff --focus "<defect>" --json` for each and report every filename. The
> EXCEPTIONAL-VALUE gate below does not apply to defects. A defect inside the current request is
> not a handoff: fix it now per the completion audit above.

Changes to the existing steps:
- The idea-offering and EXCEPTIONAL-VALUE steps are renumbered.
- The gate paragraph and its "Do NOT capture routine polish..." exclusions are explicitly scoped
  to forward-looking ideas.
- The closing line becomes: if no idea clears the gate **and no defect brief was captured**, say
  'No handoff captured; no exceptional next step identified.'
- "Capture exactly that one" still refers only to the idea. There remains at most one idea brief
  and any number of defect briefs.

Why in-scope defects are excluded: the completion audit already says an incomplete in-scope item
"is not a follow-up idea: finish it now". Workspace doctrine also forbids handing off items named
in the current request. The defect step covers bugs *discovered* outside the request, which is the
observed failure. It does not create a way to hand off required work.

**Alternative rejected:** an appended "DEFECTS" block, built the same way as the deferred-work
block. Those blocks fire conditionally on a mechanical signal. No defect signal exists, so such a
block would have to be emitted every time. That makes it base instruction text anyway, and
splitting it off would only obscure the byte-identity baseline.

### D2. The DEDUP GATE block narrows its suppression

`build_dedup_gate_block` replaces "Do NOT auto-capture a handoff brief here." with a sentence that
limits suppression to the follow-up the listed artifacts already track. A second sentence says the
gate never suppresses capturing a distinct verified defect those artifacts do not track, and that
each such defect is still captured as the base instruction requires.

These phrases stay: "Do NOT auto-capture", "suggestion-only line naming the resume command",
`` `worktrail-go <brief-id>` ``, and `## Dedup justification`. The existing hit test asserts
them. The justification escape hatch still applies only to tracked work. An untracked defect
needs no dedup justification.

### D3. Bash write targets come from a quote-aware, per-command token walk

A private helper takes the command string and returns the paths it writes.
`durable_artifact_paths_from_entry` keeps only the ones that match `DURABLE_ARTIFACT_PATH_RE`.
Steps:

1. **Remove heredoc bodies.** For each `<<`/`<<-` delimiter (quoted or bare), drop the text from
   the end of that line through the terminating delimiter line. The redirect on the heredoc line
   itself (`cat > openspec/changes/x/proposal.md <<'EOF'`) stays. Body text often contains
   unbalanced apostrophes, and it can mention paths that are not written.
2. **Tokenize** with `shlex.shlex(command, posix=True, punctuation_chars=True)`. An unescaped
   newline must separate commands. By default `shlex` treats newline as whitespace, which merges
   `mkdir a` and a following `rm docs/specs/x` into one command. That was verified during
   proposal.
3. **Split into simple commands** at control operators (`|`, `||`, `&`, `&&`, `;`, `(`, `)`,
   newline).
4. **Handle redirects.** For each redirect operator token (one containing `>`):
   - If it is immediately preceded by a word made only of digits, that word is its fd prefix and
     not an operand.
   - The next word is the target, except in two cases, where there is no write:
     - The operator ends in `&` and the target is digits or `-`, which is fd duplication, e.g.
       `2>&1`, `>&2`.
     - The target is `/dev/null`.
   - Otherwise the target is a written path.
   - Input redirects (`<`, `<<`, `<<<`) consume their word and write nothing.
   - Every redirect operator and its word are removed from the operand list.
5. **Find the verb.** Skip leading `NAME=value` assignment words. Treat `git mv` and `git rm` as
   `mv` and `rm`, which keeps coverage that today's `"mv "`/`"rm "` substring match gave
   incidentally.
6. **Get written operands.** Collect non-option operands, meaning words not starting with `-`,
   plus everything after `--`. Then apply this table:

| Verb | Written paths |
|---|---|
| `tee` | all operands |
| `cp` | last operand |
| `mv` | all operands (last is the destination; the others are removed sources) |
| `rm`, `touch`, `mkdir` | all operands |
| `sed` with `-i`/`-i<suffix>`/`--in-place` | operands, minus the first operand when no `-e`/`--expression`/`-f`/`--file` was given (that operand is the script) |
| `patch` | first operand (the file being patched) |

`BASH_WRITE_MARKERS` is removed because nothing uses it any more. It has no references outside
the hook; a repo-root `rg` confirmed this during proposal and task 1.1 re-checks it. `WORK_BASH_MARKERS`
and the substantive-work check stay as they are.

**Alternatives rejected:**
- **Strip `N>/dev/null` and `N>&M` with a regex, then keep substring matching and harvesting.**
  This fixes the first reproduced command but not the second fault: a real write elsewhere in the
  command still collects every durable path it mentions.
- **`bashlex` or another shell-parser dependency.** It would add a runtime dependency to a
  fail-open hook during a fixes-only release, which is too much for this change.
- **Marking only `mv` destinations,** as the proposal's example list suggests. A move removes its
  source from the durable tree, the same as `rm`. The governing rule is "the path(s) it actually
  writes", and the source is written.

### D4. An unparseable command contributes no paths

If `shlex` raises (for example "No closing quotation") or the walk hits an unexpected shape, that
command contributes `[]`, and the helper never raises.

Failing toward "no hit" matches the hook's existing boundary, where every checker failure leaves
the instruction unchanged. It also avoids the cost this change removes: a false hit suppresses
capture, and after D1 that could include suppressing a defect's handoff. A missed hit costs at
most a duplicate brief, and the capture-time overlap warning still catches those.

## Risks / Trade-offs

- **Writes behind wrappers (`sudo`, `xargs`, `bash -c`, interpreter one-liners) are not
  reported**, so the dedup gate may not fire. → Mitigation: this is under-reporting of the kind
  that already exists for interpreter writes, and the capture-time overlap warning still flags
  overlap with a spec slug or change name.
- **A `>` inside quotes (for example `echo 'a > docs/specs/x'`) can come out of `shlex` as a
  token that looks like a real redirect.** → Mitigation: this is rare, fails toward a single
  spurious hit, and is no worse than today.
- **`cp -t DIR src...` reports `src` as the destination.** → Accepted: rare in agent transcripts.
  Adding `-t` parsing would be speculative.
- **Agents may over-capture suspected defects.** → Mitigation: the instruction requires "verified
  (reproduced or directly evidenced), not a hypothesis", and the dedup gate still stops
  duplicates of tracked follow-ups.
- **The longer instruction costs a few more prompt tokens per wrap-up.** → Accepted.

## Migration Plan

No data or config migration. The merge propagates through the existing plugin-refresh path. A
running Claude Code session keeps the old instruction text until it restarts. Rollback is a
revert of the PR. The version bump, if any, goes in a separate `chore: bump Worktrail` commit, as
repository convention requires.

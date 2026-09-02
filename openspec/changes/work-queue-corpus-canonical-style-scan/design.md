## Context

See `proposal.md` for motivation and this change's capability spec for normative behavior.

`is_canonical_style()` (`src/worktrail/shared/brief_frontmatter.py:68`) already answers the exact
question a scan needs — does a brief's on-disk frontmatter block match what `serialize_frontmatter`
would produce for the same parsed values — but it is only ever called on content this repo's own
writer just produced, never against a file already sitting in `queue/` or `picked/`. There is no
existing corpus-iteration helper to build on: `list_queue()` walks `queue/` for the claim workflow
and returns brief summaries, not raw frontmatter blocks, and `backfill_focus_style.py`'s `preview`
walks the corpus but is scoped to the single `focus:` key and exists to propose a rewrite, not to
report a standing violation.

`$WORK_QUEUE_DIR` (default `~/work-queue`) is explicitly never repo state (see `AGENTS.md`'s
`workqueue/` description), so this repo's own CI cannot scan a live corpus — there is nothing
checked out for a GitHub Actions job to read. `worktrail-dist-tag-watch` already established the
pattern for this situation: a console script that scans the live corpus, with zero workflow-file
wiring in this repo (confirmed: no `dist-tag-watch` reference anywhere under `.github/workflows/`),
left for whoever operates a given `$WORK_QUEUE_DIR` to schedule externally (cron, launchd, a CI job
in a different repo that has access).

## Goals / Non-Goals

**Goals:**

- Make non-canonical frontmatter in the live corpus visible without requiring an operator to
  inspect YAML or diff files by hand.
- Reuse `is_canonical_style()` exactly as the two writers already use it, so the scan can never
  disagree with what a write-time check would have caught.
- Separate "wrong style" from "broken" — a file that fails to parse at all needs a different fix
  (likely hand repair) than one that parses correctly but wasn't written through
  `serialize_frontmatter`.
- Ship a scriptable contract (JSON + exit code) so the tool composes into whatever scheduling
  mechanism an operator already has, matching `worktrail-dist-tag-watch`'s shape.

**Non-Goals:**

- Fixing, rewriting, or normalizing any file — that is `backfill_focus_style.py`'s job for the
  historical backlog, and each writer's own post-write self-check for new writes.
- Adding a GitHub Actions workflow — `$WORK_QUEUE_DIR` is not reachable from this repo's CI.
- A normalize-on-write watcher process — the brief that seeded this change named that as an
  alternative option; a read-only scan was chosen because it can run against a corpus written by
  tools this repo does not control (external Codex/OpenCode-side writers) without requiring those
  tools to run alongside a watcher, and because a report-only tool carries none of a watcher's risk
  of mutating a brief mid-edit by another process.
- Changing `is_canonical_style()`, `serialize_frontmatter()`, or either existing writer.

## Decisions

### A new read-only scan module, not an extension of `list_queue()` or `backfill_focus_style.py`

`list_queue()` returns brief summaries for the claim workflow; teaching it to also carry a
whole-frontmatter canonical-style verdict would mean every `list` call (including ones on the
claim hot path) pays for a full re-serialize-and-compare of every brief, for a question only an
operator auditing corpus health asks. `backfill_focus_style.py`'s preview is scoped to `focus:`
specifically because that key's free-text content was the one field affected by the historical
gap the backfill closes; broadening it to whole-frontmatter canonical style would change what an
existing, already-shipped tool's preview means. A new module
(`src/worktrail/workqueue/check_corpus_style.py`) keeps the concerns and their tests separate,
following the same one-tool-per-concern layout as `dist_tag_watch.py` and the `backfill_*` scripts.

### Classify each finding as `style-mismatch` or `malformed`, never silently merge them

`is_canonical_style()` returns `False` for both a file whose frontmatter parses but isn't styled
canonically, and one whose frontmatter block is missing or fails to parse — collapsing both into
one "non-canonical" bucket would point an operator at `serialize_frontmatter`/`backfill_focus_style`
for a file that actually needs hand repair instead. The scan tells the two apart the same way
`is_canonical_style()` already does internally, via the existing lenient `split_frontmatter()`: a
file whose parsed frontmatter comes back empty (no `---`-fenced block, or YAML that fails to parse
or isn't a mapping) is reported `malformed`; a file with a non-empty parsed frontmatter that still
fails `is_canonical_style()` is reported `style-mismatch`.

### Ship as a console script, not a GitHub Actions workflow

Following `worktrail-dist-tag-watch`'s precedent, this change adds `worktrail-check-corpus-canonical-style`
to `[project.scripts]` and nothing under `.github/workflows/`. `--json` output and a nonzero exit
code on any finding are the only scheduling contract this repo owns; wiring an actual cron/launchd
entry or a separate CI job against a specific `$WORK_QUEUE_DIR` is an operator concern outside this
repo, exactly as it already is for the dist-tag watcher.

## Risks / Trade-offs

- [Shipping a scan with no wired schedule means it can go unrun indefinitely] → Same trade-off
  `worktrail-dist-tag-watch` already accepted; the alternative (owning an operator's crontab from
  inside this repo) is out of scope and not something the repo can verify in CI anyway.
- [A brief with a real but legitimately empty frontmatter block would be misclassified `malformed`]
  → No writer in this repo ever produces an empty frontmatter block (every brief carries at least
  `status`), so this is already indistinguishable from a broken file in practice; nothing in this
  change makes that worse.
- [A large corpus makes a full scan slow] → Each file is read and parsed once (`split_frontmatter`
  and `is_canonical_style` both re-derive the same parse, but only ever from the one string already
  read into memory, never a second disk read); ~1400 briefs at the corpus's current size is not a
  scale where this matters, and nothing here changes the claim/list hot path's cost.

## Migration Plan

Purely additive: a new module, a new console script, no changes to existing behavior or stored
files. Nothing to migrate or roll back beyond removing the script entry.

## Context

`audit_repo` classifies a reviewed-PASSED task whose commit is not an ancestor of base by
running, in order: `shippable_files` (policy exclusion), `content_delivered_via_rewrite`
(per-file byte or >=90% added-line match), `identifiers_survive_elsewhere` (definition names
anywhere in the base tree), else `confirmed_dropped`. All three treat "path absent on base" as
evidence *against* delivery. The two categories from the 2026-09-17 re-verification are cases
where "path absent on base" is the *correct* state.

## Goals / Non-Goals

- Goals: stop re-reporting pure-deletion tasks and restructure-orphaned paths as
  `confirmed_dropped`; keep both in their own named buckets so a reader can still audit them;
  keep the default invocation's behaviour identical except for the deletion filter, which
  needs no configuration because the evidence is in the task's own diff.
- Non-Goals: proving that a restructure preserved behaviour (schema/model equivalence); a
  per-repo config file for restructure globs (the flat `policy.yaml` subset does not carry
  nested lists, and one CLI flag per invocation is enough for a periodic audit); changing
  `unverifiable` handling.

## Decisions

- **Deletion is detected from the task's diff, not inferred from base.** `touched_files`
  gains a status-aware sibling that reads `git diff-tree --name-status` so the classifier
  knows which paths the commit deleted (`D`) versus added/modified. A deleted path is
  "delivered" iff `_blob_at(base_ref, path)` is `None`. A deleted path that base still has is
  *not* delivered — the deletion did not land — and stays on the existing path.
- **Pure deletion gets its own bucket; mixed diffs reuse the rewrite bucket.** If every
  shippable file is a deletion and all are absent on base, the record goes to
  `content_delivered_via_deletion`. If the diff also adds content, the per-file check in
  `content_delivered_via_rewrite` treats deleted-and-absent as `True` for those paths and the
  existing byte/line rule for the rest, so the task lands in `content_delivered_via_rewrite`
  as before. This keeps one per-file predicate rather than two parallel pipelines.
- **Restructure filter is opt-in via `--restructured-path GLOB` (repeatable).** Globs are
  matched with `fnmatch` against the repo-relative path. The filter applies only to paths
  that are absent on base; a matched path that still exists with different content is not
  excused. A task is `superseded_by_restructure` when, after the rewrite and identifier
  checks both fail, every shippable file is either glob-matched-and-absent or individually
  content-verified. Globs apply to every `--repo` in the invocation; run one invocation per
  repo when the globs differ.
- **Classification order.** never-shipped → pure deletion → rewrite → reorg → restructure →
  confirmed dropped. Restructure runs last because it is the weakest evidence (a path
  pattern, not content), mirroring how reorg already runs after rewrite.
- **Exit code unchanged.** Only `confirmed_dropped` drives exit 1.

## Risks / Trade-offs

- A too-broad glob (e.g. `*`) would silence real drops among absent paths. Mitigation: the
  bucket is named and printed in the summary, the flag is per-invocation and off by default,
  and a matched path that still exists on base is never excused.
- A task whose only work was deleting a file that a later commit re-added will now be
  reported `confirmed_dropped` (base has the path), which is arguably correct: the deletion
  did not hold.

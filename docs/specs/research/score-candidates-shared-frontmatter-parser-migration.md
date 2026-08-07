# Investigation: can `score_candidates.py` drop its hand-rolled YAML parser for `shared.brief_frontmatter`?

**Triggered by:** work-queue brief `20260807-121604`, a follow-up to
`20260807-114939` (see
`docs/specs/research/score-candidates-block-scalar-focus-parsing-miss.md`),
which fixed one instance of this parser class (`focus: |-` block scalars)
but flagged the underlying duplication as an ongoing risk.

## Question

`score_candidates.py` maintains its own regex-based frontmatter parser
(`_parse_fm`/`_read_brief`) instead of the shared, PyYAML-backed
`worktrail.shared.brief_frontmatter.read_frontmatter()`/`split_frontmatter()`
that `work_queue.py` already uses. Is it safe to replace it — specifically,
is there a circular-import risk between `shared/brief_frontmatter.py` and
`workqueue/score_candidates.py`, and does the shared module preserve the
exact behavior `score_candidates.py`'s callers depend on?

## Verified Observations

- `src/worktrail/shared/brief_frontmatter.py` imports only `pathlib`,
  `typing`, and `yaml` (lines 6-9). It has zero imports from `worktrail.workqueue`
  or `worktrail.router`. `src/worktrail/shared/__init__.py` is empty.
- `grep -rln "brief_frontmatter" src/` shows 13 existing importers across
  `workqueue/` and `router/` (including `workqueue/work_queue.py:92` and
  `workqueue/create_handoff.py`), none of which `shared/brief_frontmatter.py`
  imports back — confirming it is a leaf module with no path back to
  `workqueue/score_candidates.py`.
- `workqueue/create_handoff.py:18` already does `from . import
  score_candidates` (a direct Python import, not a subprocess call) while
  also importing `brief_frontmatter` itself — i.e. a module that imports both
  already coexists without incident, which is consistent with, though not a
  substitute for, the leaf-module check above.
- **Behavioral difference found:** `score_candidates._read_brief()` returns
  `(None, None)` specifically on `OSError` (unreadable file), and callers
  branch on that (`if new_fm is None: return {"auto_link": [], "confirm":
  []}` in `score_candidates()`; `if cand_fm is None: continue` in the scoring
  loop; `if fm is None: return {"batch": []}` in `batch_candidates()`).
  `shared.brief_frontmatter.read_frontmatter()` catches the same `OSError`
  but returns `{}`, not `None` — collapsing "file unreadable" and "file
  readable but has no/malformed frontmatter" into the same empty-dict result.
  A naive drop-in swap of `read_frontmatter()` for `_parse_fm()` would lose
  the `is None` distinction the three call sites above rely on.
- `_read_brief()`'s body-splitting (`content[m.end():]` after its own
  `_FM_RE` match) and `shared.brief_frontmatter.split_frontmatter()`'s body
  slice (`content[offset:]` after `_find_frontmatter_block`) both return the
  text following the closing `---` fence; `split_frontmatter` additionally
  tolerates a UTF-8 BOM and a closing fence with no trailing newline, which
  `_FM_RE` (anchored `^---\r?\n(.*?)\n---\r?\n`) does not.
- `_parse_fm()` does not handle YAML flow-style lists (`[a, b]`), certain
  quoted-string edge cases, or anchors — confirmed by inspection of its
  line-oriented regex parser (lines 64-119: plain scalar / null / quoted /
  block-sequence / block-scalar cases only, no flow-collection handling).
  `shared.brief_frontmatter` delegates to `yaml.safe_load`, which handles all
  of these by construction; this is the class of bug the brief anticipates
  beyond the already-fixed block-scalar case.
- `tests/workqueue/test_score_candidates.py`'s `TestBlockScalarFocusParsing`
  class (lines 571-600) calls `sc._parse_fm()` directly — these tests are
  coupled to the internal function this fix removes and cannot survive
  unchanged; `TestBatchModeBlockScalarFocusRegression` (lines 603-643)
  exercises the same regression through the public `batch_candidates()` API
  and is unaffected by the internal swap.
- `pyproject.toml` lists `pyyaml>=6.0` as a hard dependency (already required
  by `shared/brief_frontmatter.py`), so the module docstring's stated reason
  for staying stdlib-only ("no yaml dependency") no longer reflects the
  repo's actual dependency graph.

## Confirmed Root Cause

Not a defect investigation — a safety-of-refactor question. Confirmed:

1. **No circular-import risk.** `shared/brief_frontmatter.py` is a leaf
   module with no dependency on `workqueue/` or `router/`; importing it from
   `score_candidates.py` cannot create a cycle.
2. **Not a mechanical drop-in.** The refactor must preserve `_read_brief()`'s
   `(None, None)`-on-`OSError` contract explicitly (by reading the file with
   `Path.read_text()` inside `_read_brief()` and calling
   `shared.brief_frontmatter.split_frontmatter()` on the content, rather than
   calling `read_frontmatter()` directly) — otherwise the three "abort on
   unreadable file" call sites silently degrade into "treat as brief with no
   frontmatter," which for `score_candidates()`/`batch_candidates()` happens
   to compute the same empty result today (empty tokens → zero overlap
   scores below threshold) but is not the same guarantee and would silently
   diverge if either function's scoring logic changes later.

## Fix (Route F, same run)

Replace `_read_brief()`'s call to the local `_parse_fm()` with
`worktrail.shared.brief_frontmatter.split_frontmatter()`, keeping the
existing `try/except OSError: return None, None` wrapper so the public
contract is unchanged. Delete `_parse_fm`, `_FM_RE`, and `_BLOCK_SCALAR_RE`
(now unused). Update the module docstring's stale "stdlib-only; no yaml
dependency" claim. Remove `TestBlockScalarFocusParsing` (tests the deleted
internal function) and add equivalent + new coverage through `_read_brief`/
`batch_candidates` for the block-scalar case plus a flow-style-list and a
quoted-string case, to prove the class of bug this migration eliminates.

## Recommended next route

None — continuing directly into Route F in this run per the Route I
playbook (root cause confirmed, fix small and clearly in scope of this
repo's own `workqueue` module).

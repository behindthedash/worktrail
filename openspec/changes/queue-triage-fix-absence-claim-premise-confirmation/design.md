## Context

See `proposal.md - Why` for the live incident. Two call sites, already traced against the
current worktree:

- `premise_check.py`'s `_check_path()` (line 199-226): for a `path` needle, returns
  `{"confirmed": exists, ...}` (with a line-count refinement when the needle carries
  `:LINE`). It has no notion of what the brief is claiming about the path — presence or
  absence — so it always reports existence-as-confirmed.
- `_extract_path_needles()` (line 100-106): builds each `path` `Needle` purely from
  `extract_probes(focus)["paths"]`, with no surrounding-text inspection at all.
- `queue_triage.py`'s `_REPRODUCTION_EVIDENCE_RE` (line 106-121): matches
  `\breproduces?\s+via\b` — present tense only.
- `queue_triage.py`'s `_work_directly_accepted()` (line 505-520): `any(p.get("confirmed")
  for p in (v.premise_check or []))` — generic over needle kind, no changes needed here;
  it already does the right thing once `confirmed` itself carries the correct meaning.

## Goals / Non-Goals

**Goals:**
- A path needle whose surrounding focus text asserts the path's absence reports
  `confirmed: True` exactly when the path is, in fact, absent.
- Every existing presence-claim path needle (the overwhelming majority — a brief citing
  `src/foo.py:42` or "see `src/foo.py`") keeps its current confirmation semantics
  unchanged.
- Evaluator prose citing a reproduction command in past tense is recognized identically to
  present tense.

**Non-Goals:**
- No change to `_work_directly_accepted()`/`_apply_work_directly()`'s combined-rule logic —
  the generic "any confirmed entry" check is already correct once the entries it reads are
  correct.
- No general natural-language claim-polarity classifier. The absence-indicator vocabulary is
  a small, closed list matched against a short text window immediately before the path
  mention — the same scope as the two real absence-claim briefs this fix was verified
  against (`20260915-102504-wake-up-sooner-has-no`,
  `20260915-102502-wake-up-sooner-github-workflows`), not a speculative broader NLP
  pass.
- No change to `quoted` or `command` needle confirmation semantics — only `path` needles
  carry a presence/absence distinction; a quoted string or a command either matched/ran or
  it didn't, there is no "absence claim" reading for those kinds.
- No change to the keep-limit/queue-age escalation matrix in `escalate()` — it already
  consumes `premise_check` via the same generic `confirmed` field this change fixes.

## Decisions

### 1. Absence-indicator detection: a bounded text window before the path mention

```python
_ABSENCE_INDICATOR_RE = re.compile(
    r"\bhas\s+no\b|\bmissing\b|\blacks?\b|\blacking\b|\bwithout\b|\bno\s+such\b"
    r"|\bdoes(?:n't|\s+not)\s+(?:have|exist)\b|\bdoesn't\s+exist\b",
    re.IGNORECASE,
)
_ABSENCE_WINDOW = 40
```

40 characters immediately before the path's match index in `focus`, case-insensitive. This
comfortably covers the live incident's exact phrasing ("wake-up-sooner has no
`.github/dependabot.yml`" — "has no " sits 7 characters before the path) without scanning
the whole focus text, which would risk an absence indicator elsewhere in a long brief
(describing an unrelated missing thing) flipping polarity for an unrelated path mention
later in the same text.

### 2. `Needle` gains a `polarity: str = "presence"` field

Added as the last field of the frozen dataclass (after `kind`, `needle`, `line`) so every
existing 3-positional-arg `Needle(...)` construction (quoted/command needles, and any path
needle with no absence indicator nearby) is unaffected and defaults to `"presence"` —
today's behavior. `_extract_path_needles()` computes polarity per-path from the window
check above; `_extract_quoted_needles()`/`_extract_command_needles()` are untouched and
never set it.

### 3. `_check_path()` takes polarity and flips only the absence branch

```python
def _check_path(
    repo_path: Path, needle: str, polarity: str = "presence"
) -> dict[str, Any]:
    ...
    exists = target.exists()
    if polarity == "absence":
        if not exists:
            return {
                "confirmed": True,
                "detail": f"absence confirmed: path does not exist: {candidate}",
            }
        return {
            "confirmed": False,
            "detail": f"absence claim refuted: path exists: {candidate}",
        }
    # unchanged presence-claim branches below (not-exists / line-count / exists)
```

An absence claim never carries a meaningful `:LINE` suffix (a brief cannot cite a line
number inside a file it says doesn't exist), so the line-count refinement stays scoped to
the presence branch only — no behavior to define for "absence claim with a line number".

### 4. `_REPRODUCTION_EVIDENCE_RE`: add the past-tense alternative

```python
r"|\breproduces?\s+via\b"

r"|\breproduced\s+via\b"
```

A one-line addition to the existing alternation; `confirmed via` is already tense-neutral
and needs no change.

## Migration

None — behavioral fix, no data or interface migration. `format_premise_block()`'s
CONFIRMED/UNCONFIRMED rendering is unaffected in shape; for an absence-claim path needle its
label now correctly reads CONFIRMED when the evaluator's own claim was right, instead of
always reading UNCONFIRMED for that needle kind regardless of which way the brief's claim
actually ran.

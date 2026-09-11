#!/usr/bin/env python3
"""Regression test closing the "-R <base_slug> silently missing on a new gh
pr call" failure mode: PR #1157 found `open_or_update_pull_request()`'s `gh
pr edit` call was missing `-R <base_slug>` -- only the function's own
docstring documented the contract that every `gh pr view`/`create`/`edit`
call in it must carry `-R <base_slug>` for a fork-remote checkout (where
`gh` would otherwise resolve the ambiguous branch name/PR number against the
wrong repo). Nothing structurally stopped a *future* new `gh pr` call in
this function from reintroducing the same silent gap.

This mirrors `test_pr_creation_callsite_enforcement_coverage.py`'s AST-walk
pattern, scoped to this one function:

1. `extract_gh_pr_arg_sites()` AST-walks `open_or_update_pull_request()` for
   every `gh pr view`/`edit`/`create` argument list it builds -- both a
   standalone `name = ["pr", verb, ...]` list (`view_args`, `edit_args`,
   `cmd`) and an inline call whose result is assigned (`verify = _gh(...,
   "pr", "view", ...)`, `number_result = _gh(..., "pr", "view", ...)`) --
   keyed by the assigned variable name, since every gh-pr call site in this
   function is exactly one of those two shapes.
2. `test_every_callsite_is_reviewed` asserts the discovered set exactly
   equals `KNOWN_SITES`. A new `gh pr view`/`edit`/`create` call added to
   this function fails this test immediately, forcing a human to classify it
   (`requires_base_slug` or `url_identified`) and add a proof below.
3. `SITE_PROOFS` registers one proof per site: a `requires_base_slug` site
   must show an `if base_slug:` block appending `["-R", base_slug]` to that
   same variable (`_proves_guarded_by_base_slug`); a `url_identified` site
   is exempt because `gh` resolves a full PR URL without `-R` -- proven by
   asserting its identifier argument is one of the known URL-producing
   variables (`pr_url`, `out`), never a bare branch name or PR number
   (`_proves_identified_by_url`).
"""

import ast
import inspect
import textwrap
import unittest

from worktrail.router import land_pr

FUNC = land_pr.open_or_update_pull_request


def _string_constants(nodes) -> list[str]:
    return [
        n.value
        for n in nodes
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _pr_verb(literals: list[str]) -> str | None:
    """literals is the ordered list of string constants found in a list/call
    args list. Returns the gh-pr verb ("view"/"edit"/"create") if a "pr"
    literal is immediately followed by one of those verbs (an optional
    leading "gh" is tolerated), else None."""
    for i in range(len(literals) - 1):
        if literals[i] == "pr" and literals[i + 1] in ("view", "edit", "create"):
            return literals[i + 1]
    return None


def _iter_bodies(node: ast.AST):
    """Every statement list anywhere in `node`'s subtree -- function bodies,
    if/else bodies, etc. -- so callers can scan for adjacent-statement
    patterns regardless of nesting depth."""
    for _field, value in ast.iter_fields(node):
        items = value if isinstance(value, list) else [value]
        if items and all(isinstance(i, ast.stmt) for i in items):
            yield items
        for item in items:
            if isinstance(item, ast.AST):
                yield from _iter_bodies(item)


def extract_gh_pr_arg_sites() -> dict[str, tuple[str, ast.AST]]:
    """{variable_name: (verb, assign_node)} for every gh-pr call site
    `open_or_update_pull_request()` constructs, whether the argv is built as
    a standalone list literal or as inline positional args to a call."""
    source = textwrap.dedent(inspect.getsource(FUNC))
    tree = ast.parse(source)
    sites: dict[str, tuple[str, ast.AST]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        value = node.value
        if isinstance(value, ast.List):
            verb = _pr_verb(_string_constants(value.elts))
        elif isinstance(value, ast.Call):
            verb = _pr_verb(_string_constants(value.args))
        else:
            continue
        if verb is not None:
            sites[name] = (verb, node)
    return sites


def _proves_guarded_by_base_slug(var_name: str) -> None:
    """`var_name = [...]` must be immediately followed by
    `if base_slug: var_name += ["-R", base_slug]` (or an equivalent
    `.extend`/`.append` call) in the same statement block -- the actual
    shape `view_args`, `edit_args`, and `cmd` each use today."""
    source = textwrap.dedent(inspect.getsource(FUNC))
    tree = ast.parse(source)
    for body in _iter_bodies(tree):
        for i, stmt in enumerate(body[:-1]):
            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == var_name
            ):
                continue
            guard = body[i + 1]
            if not (
                isinstance(guard, ast.If)
                and isinstance(guard.test, ast.Name)
                and guard.test.id == "base_slug"
            ):
                continue
            for inner in guard.body:
                augmented = None
                if (
                    isinstance(inner, ast.AugAssign)
                    and isinstance(inner.target, ast.Name)
                    and inner.target.id == var_name
                ):
                    augmented = inner.value
                elif (
                    isinstance(inner, ast.Expr)
                    and isinstance(inner.value, ast.Call)
                    and isinstance(inner.value.func, ast.Attribute)
                    and inner.value.func.attr in ("extend", "append")
                    and isinstance(inner.value.func.value, ast.Name)
                    and inner.value.func.value.id == var_name
                ):
                    call_args = inner.value.args
                    augmented = call_args[0] if call_args else None
                if augmented is None:
                    continue
                lits = _string_constants(ast.walk(augmented))
                names = [n.id for n in ast.walk(augmented) if isinstance(n, ast.Name)]
                if "-R" in lits and "base_slug" in names:
                    return
    raise AssertionError(
        f"{var_name!r} is not immediately guarded by an "
        f'`if base_slug: {var_name} += ["-R", base_slug]`-shaped block -- '
        "this is exactly the silent gap PR #1157 fixed for the pr-edit call"
    )


KNOWN_URL_IDENTIFIER_VARS = frozenset({"pr_url", "out"})


def _proves_identified_by_url(var_name: str, verb: str) -> None:
    """`var_name`'s gh call must identify the PR by a full URL (one of
    `KNOWN_URL_IDENTIFIER_VARS`), not a bare branch name or PR number --
    only a URL-identified call is exempt from needing `-R <base_slug>`,
    since `gh` can resolve a full URL's target repo on its own."""
    source = textwrap.dedent(inspect.getsource(FUNC))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == var_name
            and isinstance(node.value, ast.Call)
        ):
            continue
        args = node.value.args
        for i, a in enumerate(args):
            if isinstance(a, ast.Constant) and a.value == verb and i + 1 < len(args):
                identifier = args[i + 1]
                if (
                    isinstance(identifier, ast.Name)
                    and identifier.id in KNOWN_URL_IDENTIFIER_VARS
                ):
                    return
                raise AssertionError(
                    f"{var_name!r}'s gh pr {verb} call no longer identifies "
                    f"the PR by a known URL variable ({sorted(KNOWN_URL_IDENTIFIER_VARS)}) "
                    "-- it may now need -R <base_slug> like the other call sites"
                )
    raise AssertionError(f"could not locate {var_name!r}'s assignment to re-check")


# variable_name -> (verb, classification); "requires_base_slug" sites are
# proved via _proves_guarded_by_base_slug, "url_identified" sites via
# _proves_identified_by_url. Every site extract_gh_pr_arg_sites() finds MUST
# have an entry here.
SITE_CLASSIFICATION: dict[str, tuple[str, str]] = {
    "view_args": ("view", "requires_base_slug"),
    "edit_args": ("edit", "requires_base_slug"),
    "cmd": ("create", "requires_base_slug"),
    "verify": ("view", "url_identified"),
    "number_result": ("view", "url_identified"),
}

KNOWN_SITES = set(SITE_CLASSIFICATION)


class TestLandPrBaseSlugEnforcementCoverage(unittest.TestCase):
    def test_every_callsite_is_reviewed(self):
        found = extract_gh_pr_arg_sites()
        self.assertTrue(
            found,
            "extraction found no gh pr view/edit/create call sites -- "
            "open_or_update_pull_request()'s call shapes may have moved/renamed",
        )
        found_names = set(found)
        unreviewed = found_names - KNOWN_SITES
        self.assertFalse(
            unreviewed,
            f"new gh pr call site(s) {sorted(unreviewed)} found with no "
            "registered classification in SITE_CLASSIFICATION -- decide "
            "whether it needs -R <base_slug> (requires_base_slug) or is "
            "identified by a full PR URL (url_identified), then add a proof",
        )
        stale = KNOWN_SITES - found_names
        self.assertFalse(
            stale,
            f"SITE_CLASSIFICATION registers {sorted(stale)}, which no longer "
            "constructs a gh pr call -- remove the stale entry",
        )
        for name, (verb, _classification) in SITE_CLASSIFICATION.items():
            self.assertEqual(
                found[name][0],
                verb,
                f"{name!r} now issues gh pr {found[name][0]!r}, expected {verb!r} "
                "-- update SITE_CLASSIFICATION",
            )

    def test_registered_sites_actually_enforce(self):
        for name, (verb, classification) in SITE_CLASSIFICATION.items():
            with self.subTest(site=name):
                if classification == "requires_base_slug":
                    _proves_guarded_by_base_slug(name)
                elif classification == "url_identified":
                    _proves_identified_by_url(name, verb)
                else:
                    raise AssertionError(f"unknown classification {classification!r}")


if __name__ == "__main__":
    unittest.main()

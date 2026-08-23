"""Vendored copy of devops's canonical `scripts/ci/dependabot/{test_dependabot_config.py,requirements.txt}`.

`test_dependabot_config.py` guards against a silent Dependabot-Updates
failure: a `.github/dependabot.yml` entry whose `directory` (or
`directories`) has no manifest file GitHub's ecosystem updater actually
recognizes, so Dependabot silently stops opening update PRs for that entry
with no error surfaced anywhere in the repo's own CI. worktrail-repo-init
scaffolds this pair of files into a target repo verbatim -- as string
constants here rather than a packaged data file, matching how `repo_init.py`
already vendors `_AUTOMERGE_WORKFLOW` as an inline template rather than
reading it off disk, so the CLI has no runtime dependency on package-data
resolution.

Source of truth: behindthedash/devops, `scripts/test_dependabot_config.py`
(PR #306). Update these constants by hand if that script changes
upstream -- there is no automated sync back to devops.
"""
from __future__ import annotations

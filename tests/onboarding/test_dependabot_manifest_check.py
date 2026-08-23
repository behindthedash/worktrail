"""Scaffold for testing the vendored Dependabot manifest-check script.

`DEPENDABOT_MANIFEST_CHECK_PY` lives only as a string constant in
`dependabot_manifest_check_template.py` (see that module's docstring) --
there is no on-disk `.py` file in this repo to import directly. Each test
writes the constant to a tmp file and loads it via
`importlib.util.spec_from_file_location` (design D6) so the tests exercise
the actual vendored source, not a copy of its logic.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from worktrail.onboarding.dependabot_manifest_check_template import (
    DEPENDABOT_MANIFEST_CHECK_PY,
)


def load_check_module(tmp_path: Path):
    """Write `DEPENDABOT_MANIFEST_CHECK_PY` to `tmp_path` and import it as a module."""
    script_path = tmp_path / "test_dependabot_config.py"
    script_path.write_text(DEPENDABOT_MANIFEST_CHECK_PY)
    spec = importlib.util.spec_from_file_location("dependabot_manifest_check", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_repo(tmp_path: Path, dependabot_yml=None, manifests=()) -> Path:
    """Build a synthetic repo tree under `tmp_path`.

    `dependabot_yml`, when given, is written to `.github/dependabot.yml`; a
    `dict`/`list` is YAML-dumped, a `str` is written verbatim (for malformed-YAML
    cases). `manifests` is an iterable of repo-relative file paths to create
    (parent directories included), each with empty contents.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    if dependabot_yml is not None:
        github_dir = repo / ".github"
        github_dir.mkdir()
        content = (
            dependabot_yml
            if isinstance(dependabot_yml, str)
            else yaml.safe_dump(dependabot_yml)
        )
        (github_dir / "dependabot.yml").write_text(content)
    for relative_path in manifests:
        manifest_path = repo / relative_path
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("")
    return repo


def test_load_check_module_exposes_expected_api(tmp_path):
    module = load_check_module(tmp_path)
    assert hasattr(module, "main")
    assert hasattr(module, "iter_checkable_entries")
    assert "pip" in module.ECOSYSTEM_MANIFEST_GLOBS
    assert "npm" in module.ECOSYSTEM_MANIFEST_GLOBS


def test_build_repo_helper_creates_expected_tree(tmp_path):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "pip", "directory": "/"}],
        },
        manifests=["pyproject.toml", "services/api/package.json"],
    )
    assert (repo / ".github" / "dependabot.yml").is_file()
    assert (repo / "pyproject.toml").is_file()
    assert (repo / "services" / "api" / "package.json").is_file()


def test_build_repo_helper_omits_dependabot_yml_when_not_given(tmp_path):
    repo = build_repo(tmp_path)
    assert not (repo / ".github").exists()


def test_pip_root_with_pyproject_toml_passes(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "pip", "directory": "/"}],
        },
        manifests=["pyproject.toml"],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        "in sync: 1 checkable updates entry have a manifest"
    )


def test_pip_root_with_only_requirements_dev_txt_glob_match_passes(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "pip", "directory": "/"}],
        },
        manifests=["requirements-dev.txt"],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        "in sync: 1 checkable updates entry have a manifest"
    )


def test_npm_subdirectory_with_package_json_passes(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [
                {"package-ecosystem": "npm", "directory": "/services/api"}
            ],
        },
        manifests=["services/api/package.json"],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        "in sync: 1 checkable updates entry have a manifest"
    )


def test_several_entries_all_valid_passes(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [
                {"package-ecosystem": "pip", "directory": "/"},
                {"package-ecosystem": "npm", "directory": "/services/api"},
                {"package-ecosystem": "pip", "directory": "/tools"},
            ],
        },
        manifests=[
            "pyproject.toml",
            "services/api/package.json",
            "tools/requirements.txt",
        ],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        "in sync: 3 checkable updates entries have a manifest"
    )


def test_pip_directory_with_no_matching_manifest_fails(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "pip", "directory": "/tools"}],
        },
        manifests=["tools/README.md"],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == (
        "no manifest for ecosystem='pip' directory='/tools'"
    )


def test_directory_that_does_not_exist_fails(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "pip", "directory": "/missing"}],
        },
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == (
        "no manifest for ecosystem='pip' directory='/missing'"
    )


def test_npm_root_with_package_json_nested_one_level_down_fails(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [{"package-ecosystem": "npm", "directory": "/"}],
        },
        manifests=["services/package.json"],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == (
        "no manifest for ecosystem='npm' directory='/'"
    )


def test_three_entries_one_broken_names_only_the_broken_one(tmp_path, capsys):
    repo = build_repo(
        tmp_path,
        dependabot_yml={
            "version": 2,
            "updates": [
                {"package-ecosystem": "pip", "directory": "/"},
                {"package-ecosystem": "npm", "directory": "/services/api"},
                {"package-ecosystem": "pip", "directory": "/tools"},
            ],
        },
        manifests=[
            "pyproject.toml",
            "services/api/package.json",
        ],
    )
    module = load_check_module(tmp_path)

    exit_code = module.main(["--repo", str(repo)])

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == (
        "no manifest for ecosystem='pip' directory='/tools'"
    )

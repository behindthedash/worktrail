from __future__ import annotations

import pytest

from worktrail.learning.notes import load_learned_notes
from worktrail.learning.paths import retro_memory_path


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "home"))
    r = tmp_path / "repo"
    r.mkdir()
    return r


def _write(repo, data):
    path = retro_memory_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def test_absent_file(repo):
    assert load_learned_notes(repo) is None


def test_missing_section(repo):
    _write(repo, "# Memory\n\n## Other\n- a\n")
    assert load_learned_notes(repo) is None


def test_empty_section(repo):
    _write(repo, "## Notes for workers\n\n## Next\n- a\n")
    assert load_learned_notes(repo) is None


def test_section_ends_at_next_heading(repo):
    _write(repo, "# M\n## Notes for workers\n- one\n- two\n## Later\n- three\n")
    assert load_learned_notes(repo) == "- one\n- two"


def test_caps_at_20_bullets(repo):
    body = "".join(f"- note {i}\n" for i in range(25))
    _write(repo, "## Notes for workers\n" + body)
    notes = load_learned_notes(repo)
    assert notes.count("- note") == 20
    assert notes.splitlines()[-1] == "- note 19"


def test_char_cap_truncates_between_bullets(repo):
    bullets = [f"- {i} " + "x" * 900 for i in range(6)]
    _write(repo, "## Notes for workers\n" + "\n".join(bullets) + "\n")
    notes = load_learned_notes(repo)
    assert len(notes) <= 4000
    assert notes == "\n".join(bullets[:4])


def test_undecodable_bytes(repo):
    _write(repo, b"## Notes for workers\n- \xff\xfe bad\n")
    assert load_learned_notes(repo) is None

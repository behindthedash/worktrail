from worktrail.orchestrator import live


def test_recover_subcommand_does_not_resolve_default_model(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        "worktrail.orchestrator.recover.recover",
        lambda repo, spec, groups, tasks, **kwargs: 0,
    )
    monkeypatch.setattr(
        live,
        "_default_model_for_agent",
        lambda agent: (_ for _ in ()).throw(AssertionError()),
    )

    assert (
        live.main(
            [
                "recover",
                "--repo",
                str(tmp_path),
                "--spec",
                "openspec/x",
                "--tasks",
                "1.1",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == ""


def test_recover_subcommand_no_selector_refuses(tmp_path, capsys):
    assert live.main(["recover", "--repo", str(tmp_path), "--spec", "openspec/x"]) == 1
    assert "name at least one --group or --tasks" in capsys.readouterr().out


def test_recover_dry_run_wording_promises_no_merge_or_write(
    monkeypatch, tmp_path, capsys
):
    called = {}

    def recover(repo, spec, groups, tasks, **kwargs):
        called.update(kwargs)
        print("recover: dry-run; no merge or journal write performed")
        return 0

    monkeypatch.setattr("worktrail.orchestrator.recover.recover", recover)
    assert (
        live.main(
            [
                "recover",
                "--repo",
                str(tmp_path),
                "--spec",
                "openspec/x",
                "--tasks",
                "1.1",
                "--base",
                "main",
                "--remote",
                "upstream",
                "--dry-run",
            ]
        )
        == 0
    )
    assert called["dry_run"] is True
    assert "no merge or journal write" in capsys.readouterr().out

import pytest

from reviewbench.cli import main


def test_live_run_requires_execute_before_authentication(monkeypatch, capsys):
    def blocked(*args):
        pytest.fail("Live credentials should not be requested")

    monkeypatch.setattr("reviewbench.cli._live_client", blocked)
    assert main(["run", "--manifest", "benchmark/fixtures/pagination/campaign.toml"]) == 1
    assert "requires --execute" in capsys.readouterr().err


def test_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    assert "reviewbench" in capsys.readouterr().out

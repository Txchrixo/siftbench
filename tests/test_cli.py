import pytest

from reviewbench.cli import main


def test_help(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    assert "reviewbench" in capsys.readouterr().out

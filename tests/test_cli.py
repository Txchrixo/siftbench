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


def test_attempt_selection_must_name_pairs_from_the_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("reviewbench.cli._live_client", lambda _: pytest.fail("Invalid selections must fail before GitHub"))
    selection = tmp_path / "attempts.json"
    for content in ('["not-a-pair"]', "[]", '{"pair": 1}'):
        selection.write_text(content, encoding="utf-8")
        assert main(["--state-dir", str(tmp_path / "state"), "run", "--manifest", "benchmark/fixtures/pagination/campaign.toml",
                     "--execute", "--attempts", str(selection)]) == 1
        assert "--attempts must list unique pair IDs" in capsys.readouterr().err


def test_malformed_input_files_are_reported_without_a_traceback(tmp_path, capsys):
    limits = tmp_path / "limits.json"
    limits.write_text('{"unknown-reviewer": {}}', encoding="utf-8")
    assert main(["plan", "--manifest", "benchmark/fixtures/pagination/campaign.toml", "--limits", str(limits)]) == 1
    assert "outside the manifest" in capsys.readouterr().err


def test_json_output_is_utf8_whatever_the_console_code_page(monkeypatch):
    import io

    from reviewbench.cli import _print_json

    raw = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    _print_json({"title": "✨ Add show_warning_types"})
    assert raw.getvalue() == '{"title":"✨ Add show_warning_types"}\n'.encode()

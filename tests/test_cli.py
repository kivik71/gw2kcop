import pytest

from gw2kcop import __version__
from gw2kcop.cli import NOT_IMPLEMENTED, NOT_IMPLEMENTED_EXIT, main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: gw2kcop" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(NOT_IMPLEMENTED))
def test_subcommands_fail_closed_until_implemented(command, capsys):
    assert main([command]) == NOT_IMPLEMENTED_EXIT
    assert "not implemented" in capsys.readouterr().err


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit) as exc:
        main(["unknown"])
    assert exc.value.code == 2


def test_anonymize_missing_file_hints_at_imported_copy(tmp_path, capsys):
    folder = tmp_path / "OWNA-TEST"
    (folder / "_zaimportowane").mkdir(parents=True)
    (folder / "_zaimportowane" / "export.csv").write_text("a;b\n1;2\n", encoding="utf-8")
    args = ["anonymize", str(folder / "export.csv"), "--mapping-dir", str(tmp_path / "map"),
            "--output", str(tmp_path / "out")]
    assert main(args) == 1
    err = capsys.readouterr().err
    assert "not found" in err and "_zaimportowane" in err and "Traceback" not in err


def test_anonymize_missing_file_without_copy(tmp_path, capsys):
    assert main(["anonymize", str(tmp_path / "none.csv"), "--check", "--mapping-dir", str(tmp_path)]) == 1
    assert "not found" in capsys.readouterr().err

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

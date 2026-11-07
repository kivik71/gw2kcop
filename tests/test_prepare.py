import os
import stat

import pytest

from gw2kcop.accounts import parse_accounts
from gw2kcop.cli import main
from gw2kcop.prepare import UNASSIGNED_FOLDER, prepare, validate_month

ACCOUNTS_CSV = (
    "account_id,institution,owner,export_identifier,name,account_type,currency,active,import_method\n"
    "ANN-BROKER-PLN,demo_broker,ann,,Ann broker,brokerage,PLN,TRUE,parser\n"
    "ANN-BANK-EUR,demo_bank,ann,,Ann bank,fx,EUR,TRUE,manual\n"
    "BOB-OLD-PLN,demo_bank,bob,,Bob closed,current,PLN,FALSE,manual\n"
)
ACCOUNTS = parse_accounts(ACCOUNTS_CSV)


def names(path):
    return sorted(p.name for p in path.iterdir())


@pytest.mark.parametrize("value", ["2026-11", "1999-01", "2030-12"])
def test_valid_month(value):
    assert validate_month(value) == value


@pytest.mark.parametrize("value", ["2026-13", "2026-1", "26-11", "2026/11", "../x", ""])
def test_invalid_month(value):
    with pytest.raises(ValueError):
        validate_month(value)


def test_creates_month_folders(tmp_path):
    result = prepare(tmp_path, ACCOUNTS, "2026-11")
    assert names(tmp_path / "2026-11") == ["ANN-BANK-EUR", "ANN-BROKER-PLN", UNASSIGNED_FOLDER]
    assert len(result.created) == 4  # month + 2 accounts + unassigned


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_new_folders_are_private(tmp_path):
    prepare(tmp_path, ACCOUNTS, "2026-11")
    mode = stat.S_IMODE((tmp_path / "2026-11" / "ANN-BROKER-PLN").stat().st_mode)
    assert mode == 0o700


def test_idempotent_and_keeps_files(tmp_path):
    prepare(tmp_path, ACCOUNTS, "2026-11")
    sample = tmp_path / "2026-11" / "ANN-BROKER-PLN" / "export.txt"
    sample.write_text("keep me", encoding="utf-8")
    result = prepare(tmp_path, ACCOUNTS, "2026-11")
    assert result.created == []
    assert sample.read_text(encoding="utf-8") == "keep me"


def test_start_folder(tmp_path):
    prepare(tmp_path, ACCOUNTS, None, start=True)
    assert names(tmp_path / "_start") == ["ANN-BANK-EUR", "ANN-BROKER-PLN"]


def test_month_and_start_are_exclusive(tmp_path):
    with pytest.raises(ValueError):
        prepare(tmp_path, ACCOUNTS, "2026-11", start=True)
    with pytest.raises(ValueError):
        prepare(tmp_path, ACCOUNTS, None, start=False)


def test_missing_exports_dir(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        prepare(tmp_path / "nope", ACCOUNTS, "2026-11")


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_refuses_symlinked_account_folder(tmp_path):
    (tmp_path / "2026-11").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "2026-11" / "ANN-BROKER-PLN").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symbolic link"):
        prepare(tmp_path, ACCOUNTS, "2026-11")


def _write_config(tmp_path):
    exports = tmp_path / "Eksporty"
    data = tmp_path / "Dane"
    exports.mkdir()
    data.mkdir()
    (data / "accounts.csv").write_text(ACCOUNTS_CSV, encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'timezone = "Europe/Warsaw"\n[paths]\nexports = "{exports.as_posix()}"\ndata = "{data.as_posix()}"\n',
        encoding="utf-8",
    )
    return config, exports


def test_cli_prepare_end_to_end(tmp_path, capsys):
    config, exports = _write_config(tmp_path)
    assert main(["--config", str(config), "prepare", "2026-11"]) == 0
    assert "4 folder(s) created" in capsys.readouterr().out
    assert (exports / "2026-11" / "ANN-BROKER-PLN").is_dir()


def test_cli_prepare_bad_month(tmp_path, capsys):
    config, _ = _write_config(tmp_path)
    assert main(["--config", str(config), "prepare", "2026-13"]) == 1
    assert "2026-11" in capsys.readouterr().err


def test_cli_config_init_and_show(tmp_path, capsys):
    path = tmp_path / "new" / "config.toml"
    assert main(["--config", str(path), "config", "init"]) == 0
    assert path.is_file()
    assert main(["--config", str(path), "config", "show"]) == 0
    assert "Europe/Warsaw" in capsys.readouterr().out


def test_cli_missing_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "none.toml"), "prepare", "2026-11"]) == 1
    assert "config init" in capsys.readouterr().err

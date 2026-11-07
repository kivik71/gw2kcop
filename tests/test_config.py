from pathlib import Path

import pytest

from gw2kcop import config as cfg


def test_template_parses():
    conf = cfg.parse_config(cfg.TEMPLATE, Path("config.toml"))
    assert conf.timezone.key == "Europe/Warsaw"
    assert conf.exports_dir == Path("~/Finanse/Eksporty").expanduser()
    assert conf.accounts_file == Path("~/Finanse/Dane/accounts.csv").expanduser()


def test_accounts_defaults_to_data_dir():
    text = 'timezone = "UTC"\n[paths]\nexports = "/x/e"\ndata = "/x/d"\n'
    conf = cfg.parse_config(text, Path("c.toml"))
    assert conf.accounts_file == Path("/x/d/accounts.csv")
    assert conf.timezone.key == "UTC"


@pytest.mark.parametrize(
    "text, message",
    [
        ("timezone = 'Mars/Base'\n[paths]\nexports='/e'\ndata='/d'\n", "unknown timezone"),
        ("timezone = 'UTC'\n", "missing [paths]"),
        ("[paths]\nexports='/e'\n", "paths.data is required"),
        ("[paths]\nexports='/e'\ndata='/d'\nexprots='/x'\n", "unknown keys in [paths]"),
        ("tiemzone='UTC'\n[paths]\nexports='/e'\ndata='/d'\n", "unknown keys"),
        ("[paths]\nexports=''\ndata='/d'\n", "non-empty"),
        ("not toml ===", "invalid TOML"),
    ],
)
def test_invalid_config(text, message):
    with pytest.raises(cfg.ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        cfg.parse_config(text, Path("c.toml"))


def test_resolve_order(monkeypatch, tmp_path):
    monkeypatch.setenv(cfg.ENV_VAR, str(tmp_path / "env.toml"))
    assert cfg.resolve_config_path("cli.toml") == Path("cli.toml")
    assert cfg.resolve_config_path(None) == tmp_path / "env.toml"
    monkeypatch.delenv(cfg.ENV_VAR)
    assert cfg.resolve_config_path(None).name == "config.toml"


def test_write_template_never_overwrites(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    assert cfg.write_template(path) is True
    path.write_text("# mine\n", encoding="utf-8")
    assert cfg.write_template(path) is False
    assert path.read_text(encoding="utf-8") == "# mine\n"


def test_missing_file_explains_init(tmp_path):
    with pytest.raises(cfg.ConfigError, match="gw2kcop config init"):
        cfg.load_config(tmp_path / "none.toml")

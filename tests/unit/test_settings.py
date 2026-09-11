"""Configuration loading and precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from velox_ui.settings import SettingsError, load_settings


def test_defaults_produce_a_working_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.port == 8080
    assert settings.is_sqlite
    assert settings.secret_key, "a secret key must be generated on first run"


def test_secret_key_is_persisted_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    first = load_settings().secret_key
    assert (tmp_path / "secret.key").is_file()
    assert load_settings().secret_key == first, (
        "a regenerated key would orphan stored credentials"
    )


def test_environment_overrides_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "velox.toml"
    config.write_text("port = 1234\n[auth]\naccess_token_ttl_s = 111\n", encoding="utf-8")
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VELOX_PORT", "4321")
    settings = load_settings(config)
    assert settings.port == 4321, "the environment must win over the file"
    assert settings.auth.access_token_ttl_s == 111, "file values survive where unset"


def test_environment_strings_are_coerced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VELOX_DB_ECHO", "true")
    monkeypatch.setenv("VELOX_CORS_ORIGINS", "http://a.example, http://b.example")
    settings = load_settings()
    assert settings.db.echo is True
    assert settings.cors_origins == ("http://a.example", "http://b.example")


def test_invalid_value_names_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VELOX_PORT", "not-a-number")
    with pytest.raises(SettingsError, match="port"):
        load_settings()


def test_unknown_key_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "velox.toml"
    config.write_text("prot = 8080\n", encoding="utf-8")
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    with pytest.raises(SettingsError):
        load_settings(config)


def test_missing_explicit_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not found"):
        load_settings(tmp_path / "absent.toml")

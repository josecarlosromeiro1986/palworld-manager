import stat
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import AppEnvironment, Settings
from app.palworld_settings.definitions import SETTING_DEFINITIONS_BY_KEY
from app.palworld_settings.ini import (
    IniParseError,
    SettingValueError,
    parse_ini,
    parse_setting_value,
    serialize_setting_value,
)
from app.palworld_settings.storage import (
    FakePalworldSettingsStorage,
    FilePalworldSettingsStorage,
    PalworldSettingsStorageError,
    SettingsStorageErrorKind,
    create_palworld_settings_storage,
)


def test_conservative_parser_changes_only_selected_known_values() -> None:
    original = (
        "; comentário preservado\n"
        "[/Script/Pal.PalGameWorldSettings]\n"
        'OptionSettings=( ServerName = "Antigo, servidor" ,'
        'FutureSetting=(Mode="Preserve,Me",Level=2), ExpRate = 1.000000 )\n'
        "[Outra.Secao]\nValor=continua\n"
    )
    parsed = parse_ini(original)
    server_name = SETTING_DEFINITIONS_BY_KEY["ServerName"]

    rendered = parsed.render(
        {
            "ServerName": serialize_setting_value(server_name, 'Novo "Servidor"'),
            "ExpRate": "2.5",
        }
    )

    assert "; comentário preservado\n[/Script/Pal.PalGameWorldSettings]\n" in rendered
    assert 'ServerName = "Novo \\"Servidor\\"" ' in rendered
    assert 'FutureSetting=(Mode="Preserve,Me",Level=2)' in rendered
    assert " ExpRate = 2.5 " in rendered
    assert rendered.endswith("\n[Outra.Secao]\nValor=continua\n")


@pytest.mark.parametrize(
    "content",
    [
        'OptionSettings=(ServerName="sem seção")',
        '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName="aberto"',
        (
            "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(A=1)\n"
            "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(B=2)"
        ),
    ],
)
def test_parser_rejects_ambiguous_or_malformed_structure(content: str) -> None:
    with pytest.raises(IniParseError):
        parse_ini(content)


def test_typed_values_use_only_documented_limits() -> None:
    base_limit = SETTING_DEFINITIONS_BY_KEY["BaseCampMaxNumInGuild"]
    sync_distance = SETTING_DEFINITIONS_BY_KEY["ServerReplicatePawnCullDistance"]
    log_format = SETTING_DEFINITIONS_BY_KEY["LogFormatType"]

    assert parse_setting_value(base_limit, "10") == "10"
    assert parse_setting_value(sync_distance, "5000.0") == "5000.0"
    assert parse_setting_value(log_format, "Json") == "Json"
    with pytest.raises(SettingValueError, match="menor ou igual"):
        parse_setting_value(base_limit, "11")
    with pytest.raises(SettingValueError, match="opção inválida"):
        parse_setting_value(log_format, "Xml")


def test_real_storage_creates_exact_backup_before_atomic_replace(tmp_path: Path) -> None:
    settings_path = tmp_path / "PalWorldSettings.ini"
    original = (
        "[/Script/Pal.PalGameWorldSettings]\n"
        'OptionSettings=(ServerName="Original",FutureSetting=Keep)\n'
    )
    updated = original.replace('ServerName="Original"', 'ServerName="Atualizado"')
    settings_path.write_text(original, encoding="utf-8")
    storage = FilePalworldSettingsStorage(
        settings_path,
        clock=lambda: datetime(2026, 8, 14, 18, 30, tzinfo=UTC),
    )
    stored = storage.read()

    result = storage.write(expected_version=stored.version, content=updated)

    assert settings_path.read_text(encoding="utf-8") == updated
    backup_path = tmp_path / result.backup_name
    assert backup_path.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert "20260814T183000000000Z" in result.backup_name


def test_real_storage_rejects_stale_version_without_creating_backup(tmp_path: Path) -> None:
    settings_path = tmp_path / "PalWorldSettings.ini"
    settings_path.write_text("conteúdo atual", encoding="utf-8")
    storage = FilePalworldSettingsStorage(settings_path)

    with pytest.raises(PalworldSettingsStorageError) as error:
        storage.write(expected_version="0" * 64, content="novo")

    assert error.value.kind is SettingsStorageErrorKind.CONFLICT
    assert list(tmp_path.glob("*.backup-*")) == []


def test_real_storage_rejects_symlink_target(tmp_path: Path) -> None:
    real_path = tmp_path / "real.ini"
    link_path = tmp_path / "PalWorldSettings.ini"
    real_path.write_text("não tocar", encoding="utf-8")
    link_path.symlink_to(real_path)
    storage = FilePalworldSettingsStorage(link_path)

    with pytest.raises(PalworldSettingsStorageError) as error:
        storage.read()

    assert error.value.kind is SettingsStorageErrorKind.INVALID_FILE
    assert real_path.read_text(encoding="utf-8") == "não tocar"


def test_storage_factory_uses_fake_outside_production(tmp_path: Path) -> None:
    development = create_palworld_settings_storage(Settings(environment=AppEnvironment.DEVELOPMENT))
    production = create_palworld_settings_storage(
        Settings(
            environment=AppEnvironment.PRODUCTION,
            manager_database=tmp_path / "manager.db",
            palworld_settings=tmp_path / "PalWorldSettings.ini",
            app_host=ip_address("127.0.0.1"),
            palworld_rest_username=SecretStr("usuario-ficticio"),
            palworld_rest_password=SecretStr("senha-ficticia"),
        )
    )

    assert isinstance(development, FakePalworldSettingsStorage)
    assert isinstance(production, FilePalworldSettingsStorage)

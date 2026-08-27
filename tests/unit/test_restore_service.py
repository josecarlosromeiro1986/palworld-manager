import json
import sqlite3
import stat
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from app.backups.service import LocalBackupService
from app.backups.source import SAFE_MANAGER_SETTING_DEFAULTS
from app.config import AppEnvironment, Settings
from app.db.models import BackupRecord
from app.palworld_settings.storage import (
    FakePalworldSettingsStorage,
    SettingsWriteResult,
    content_version,
)
from app.restores.service import (
    FakeRestoreTarget,
    FilesystemRestoreTarget,
    LocalRestoreService,
    PreparedRestore,
    RestoreValidationError,
    create_restore_target,
    merge_generic_ini,
    merge_palworld_settings,
)


def _remote_record() -> BackupRecord:
    filename = f"palworld-manager-backup-20260814T120000000000Z-j000123-{'a' * 32}.tar.gz"
    return BackupRecord(
        filename=filename,
        location="DRIVE",
        status="VALID",
        sha256="b" * 64,
        size_bytes=16,
        storage_path=filename,
    )


def test_remote_restore_rejects_download_outside_controlled_staging(tmp_path: Path) -> None:
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"external-content")
    service = LocalRestoreService(
        manager_database=tmp_path / "manager.db",
        backup_service=Mock(spec=LocalBackupService),
        target=FakeRestoreTarget(FakePalworldSettingsStorage()),
    )

    with pytest.raises(RestoreValidationError) as captured:
        service.prepare_remote(_remote_record(), outside, job_id=123)

    assert captured.value.category == "REMOTE_TEMP_INVALID"


def test_remote_restore_rejects_symlinked_download_in_controlled_staging(tmp_path: Path) -> None:
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"external-content")
    temporary = tmp_path / "tmp/drive/job-000123-test"
    temporary.mkdir(parents=True)
    linked = temporary / "source.tar.gz"
    linked.symlink_to(outside)
    service = LocalRestoreService(
        manager_database=tmp_path / "manager.db",
        backup_service=Mock(spec=LocalBackupService),
        target=FakeRestoreTarget(FakePalworldSettingsStorage()),
    )

    with pytest.raises(RestoreValidationError) as captured:
        service.prepare_remote(_remote_record(), linked, job_id=123)

    assert captured.value.category == "REMOTE_TEMP_INVALID"


def test_restore_rejects_insufficient_staging_space_before_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.restores.service.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=4),
    )

    with pytest.raises(RestoreValidationError) as captured:
        LocalRestoreService._ensure_staging_space(tmp_path, 5)

    assert captured.value.category == "DISK_SPACE_INSUFFICIENT"


def test_manager_snapshot_integrity_check_does_not_create_sqlite_sidecars(
    tmp_path: Path,
) -> None:
    manager = tmp_path / "payload/manager"
    manager.mkdir(parents=True)
    database = manager / "manager.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('persistido')")
        connection.commit()
    finally:
        connection.close()
    (manager / "settings.json").write_text(
        json.dumps(SAFE_MANAGER_SETTING_DEFAULTS),
        encoding="utf-8",
    )
    expected = {"manager.db", "settings.json"}

    LocalRestoreService._validate_manager_disaster_recovery_payload(tmp_path / "payload")

    assert {path.name for path in manager.iterdir()} == expected


def test_palworld_settings_merge_restores_safe_fields_and_preserves_secrets_and_unknowns() -> None:
    current = (
        "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=("
        'ServerName="Atual",AdminPassword="fake-current-admin",'
        'ServerPassword="fake-current-server",FuturePassword="fake-future",'
        'FutureSetting=(Mode="Keep,Me"))\n'
    )
    backup = (
        "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=("
        'ServerName="Restaurado",AdminPassword="",ServerPassword="",'
        'FuturePassword="",FutureSetting=(Mode="Old"))\n'
    )

    merged = merge_palworld_settings(backup, current)

    assert 'ServerName="Restaurado"' in merged
    assert 'AdminPassword="fake-current-admin"' in merged
    assert 'ServerPassword="fake-current-server"' in merged
    assert 'FuturePassword="fake-future"' in merged
    assert 'FutureSetting=(Mode="Keep,Me")' in merged


@pytest.mark.parametrize(
    "current",
    [
        "invalid",
        "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(RESTAPIEnabled=maybe)",
        '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName="A",ServerName="B")',
        '[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(ServerName="unterminated)',
    ],
)
def test_palworld_settings_merge_rejects_invalid_or_ambiguous_current_file(current: str) -> None:
    backup = (
        "[/Script/Pal.PalGameWorldSettings]\n"
        'OptionSettings=(ServerName="Restaurado",AdminPassword="")\n'
    )

    with pytest.raises(RestoreValidationError):
        merge_palworld_settings(backup, current)


def test_generic_ini_merge_preserves_sensitive_current_values() -> None:
    backup = '[Server]\nName=Old\nApiToken=""\n'
    current = '[Server]\nName=Current\nApiToken="fake-current-token"\n'

    merged = merge_generic_ini(backup, current)

    assert "Name=Old" in merged
    assert 'ApiToken="fake-current-token"' in merged


def test_generic_ini_merge_rejects_missing_sensitive_current_value() -> None:
    with pytest.raises(RestoreValidationError):
        merge_generic_ini('[Server]\nApiToken=""\n', "[Server]\nName=Current\n")


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_restore_target_is_integral_fake_outside_production(
    tmp_path: Path, environment: AppEnvironment
) -> None:
    target = create_restore_target(
        Settings(
            environment=environment,
            manager_database=tmp_path / "manager.db",
        )
    )

    assert isinstance(target, FakeRestoreTarget)
    assert isinstance(target.storage, FakePalworldSettingsStorage)


def test_restore_target_uses_filesystem_only_in_production(tmp_path: Path) -> None:
    target = create_restore_target(
        Settings(
            environment=AppEnvironment.PRODUCTION,
            palworld_dir=tmp_path / "palworld",
            palworld_settings=tmp_path / "palworld/PalWorldSettings.ini",
            manager_database=tmp_path / "manager.db",
            palworld_rest_username=SecretStr("fake-user"),
            palworld_rest_password=SecretStr("fake-password"),
            app_host=ip_address("127.0.0.1"),
        )
    )

    assert isinstance(target, FilesystemRestoreTarget)


def test_filesystem_target_replaces_world_with_minimal_permissions_and_no_manager_state(
    tmp_path: Path,
) -> None:
    palworld = tmp_path / "palworld"
    world = palworld / "Pal/Saved/SaveGames"
    config = palworld / "Pal/Saved/Config/LinuxServer"
    world.mkdir(parents=True)
    config.mkdir(parents=True)
    (world / "old.sav").write_bytes(b"old")
    current_settings = (
        "[/Script/Pal.PalGameWorldSettings]\n"
        'OptionSettings=(ServerName="Atual",AdminPassword="fake-current")\n'
    )
    settings_path = config / "PalWorldSettings.ini"
    settings_path.write_text(current_settings, encoding="utf-8")
    restored_world = tmp_path / "prepared/world"
    players = restored_world / "world-id/Players"
    players.mkdir(parents=True)
    (restored_world / "world-id/Level.sav").write_bytes(b"restored")
    (restored_world / "world-id/LevelMeta.sav").write_bytes(b"meta")
    (players / "player.sav").write_bytes(b"opaque-player")
    storage = FakePalworldSettingsStorage(current_settings)
    target = FilesystemRestoreTarget(
        Settings(
            environment=AppEnvironment.DEVELOPMENT,
            palworld_dir=palworld,
            palworld_settings=settings_path,
            manager_database=tmp_path / "manager.db",
        ),
        storage,
    )
    prepared = PreparedRestore(
        working_directory=tmp_path / "prepared",
        world_directory=restored_world,
        palworld_settings=current_settings.replace("Atual", "Restaurado"),
        palworld_settings_version=content_version(current_settings.encode()),
        game_user_settings=None,
        payload_size_bytes=24,
    )

    target.ensure_available_space(prepared.payload_size_bytes)
    target.apply(prepared, job_id=42)

    restored_level = world / "world-id/Level.sav"
    restored_player = world / "world-id/Players/player.sav"
    assert restored_level.read_bytes() == b"restored"
    assert restored_player.read_bytes() == b"opaque-player"
    assert not (world / "old.sav").exists()
    assert stat.S_IMODE(restored_level.stat().st_mode) == 0o660
    assert stat.S_IMODE(restored_player.parent.stat().st_mode) == 0o770
    assert storage.content == prepared.palworld_settings
    assert list(world.parent.glob(".SaveGames.palworld-manager-restore-*")) == []


def test_filesystem_target_rejects_symlinked_world_before_applying(
    tmp_path: Path,
) -> None:
    palworld = tmp_path / "palworld"
    save_parent = palworld / "Pal/Saved"
    outside = tmp_path / "outside"
    save_parent.mkdir(parents=True)
    outside.mkdir()
    (save_parent / "SaveGames").symlink_to(outside, target_is_directory=True)
    settings_path = palworld / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("unused", encoding="utf-8")
    target = FilesystemRestoreTarget(
        Settings(
            environment=AppEnvironment.DEVELOPMENT,
            palworld_dir=palworld,
            palworld_settings=settings_path,
            manager_database=tmp_path / "manager.db",
        ),
        FakePalworldSettingsStorage(),
    )

    with pytest.raises(RestoreValidationError):
        target.ensure_available_space(1)


def test_filesystem_target_rejects_settings_outside_managed_palworld_root(
    tmp_path: Path,
) -> None:
    palworld = tmp_path / "palworld"
    world = palworld / "Pal/Saved/SaveGames"
    world.mkdir(parents=True)
    outside_settings = tmp_path / "outside/PalWorldSettings.ini"
    outside_settings.parent.mkdir()
    outside_settings.write_text("unused", encoding="utf-8")
    target = FilesystemRestoreTarget(
        Settings(
            environment=AppEnvironment.DEVELOPMENT,
            palworld_dir=palworld,
            palworld_settings=outside_settings,
            manager_database=tmp_path / "manager.db",
        ),
        FakePalworldSettingsStorage(),
    )

    with pytest.raises(RestoreValidationError):
        target.read_palworld_settings()


def test_filesystem_target_does_not_roll_back_partial_apply(
    tmp_path: Path,
) -> None:
    class FailingStorage(FakePalworldSettingsStorage):
        def write(self, *, expected_version: str, content: str) -> SettingsWriteResult:
            del expected_version, content
            raise RuntimeError("falha simulada sem conteúdo sensível")

    palworld = tmp_path / "palworld"
    world = palworld / "Pal/Saved/SaveGames"
    world.mkdir(parents=True)
    (world / "old.sav").write_bytes(b"old")
    settings_path = palworld / "Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("unused", encoding="utf-8")
    restored_world = tmp_path / "prepared/world/world-id"
    restored_world.mkdir(parents=True)
    (restored_world / "Level.sav").write_bytes(b"new")
    target = FilesystemRestoreTarget(
        Settings(
            environment=AppEnvironment.DEVELOPMENT,
            palworld_dir=palworld,
            palworld_settings=settings_path,
            manager_database=tmp_path / "manager.db",
        ),
        FailingStorage(),
    )
    prepared = PreparedRestore(
        working_directory=tmp_path / "prepared",
        world_directory=restored_world.parent,
        palworld_settings="unused",
        palworld_settings_version="version",
        game_user_settings=None,
        payload_size_bytes=3,
    )

    with pytest.raises(RuntimeError):
        target.apply(prepared, job_id=43)

    assert (world / "world-id/Level.sav").read_bytes() == b"new"
    previous = world.parent / ".SaveGames.palworld-manager-restore-j000043-previous"
    assert (previous / "old.sav").read_bytes() == b"old"

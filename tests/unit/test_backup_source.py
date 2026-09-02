import sqlite3
from ipaddress import ip_address
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.backups.source import (
    BackupSourceError,
    FilesystemBackupPayloadSource,
    snapshot_database,
)
from app.config import AppEnvironment, Settings
from app.integrations.palworld_rest import FakePalworldRestClient


def _production_source(
    tmp_path: Path,
) -> tuple[FilesystemBackupPayloadSource, FakePalworldRestClient]:
    root = tmp_path / "palworld"
    world = root / "Pal/Saved/SaveGames/0/world-id"
    (world / "Players").mkdir(parents=True)
    (world / "Level.sav").write_bytes(b"level")
    (world / "LevelMeta.sav").write_bytes(b"meta")
    (world / "Players/player.sav").write_bytes(b"opaque-player")
    (world / "backup").mkdir()
    (world / "backup/old.sav").write_bytes(b"excluded")
    (world / "secrets.env").write_text("PROHIBIDO", encoding="utf-8")
    config = root / "Pal/Saved/Config/LinuxServer"
    config.mkdir(parents=True)
    settings_path = config / "PalWorldSettings.ini"
    settings_path.write_text(
        "[/Script/Pal.PalGameWorldSettings]\n"
        'OptionSettings=(ServerName="Teste",AdminPassword="segredo-admin",'
        'ServerPassword="segredo-server",UnknownToken="segredo-token")\n',
        encoding="utf-8",
    )
    (config / "GameUserSettings.ini").write_text(
        "DedicatedServerName=world-id\nApiToken=segredo-relacionado\n",
        encoding="utf-8",
    )
    rest = FakePalworldRestClient()
    settings = Settings(
        environment=AppEnvironment.PRODUCTION,
        palworld_dir=root,
        palworld_settings=settings_path,
        manager_database=tmp_path / "manager.db",
        app_host=ip_address("127.0.0.1"),
        palworld_rest_username=SecretStr("usuario"),
        palworld_rest_password=SecretStr("senha"),
    )
    return FilesystemBackupPayloadSource(settings, rest), rest


def test_filesystem_source_saves_then_copies_full_world_without_secrets(
    tmp_path: Path,
) -> None:
    source, rest = _production_source(tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()

    source.request_safe_save()
    source.stage_palworld_payload(payload)

    assert rest.save_requests == 1
    assert (payload / "world/0/world-id/Level.sav").read_bytes() == b"level"
    assert (payload / "world/0/world-id/Players/player.sav").read_bytes() == b"opaque-player"
    assert not (payload / "world/0/world-id/backup").exists()
    assert not (payload / "world/0/world-id/secrets.env").exists()
    combined = "".join(path.read_text(errors="ignore") for path in (payload / "config").iterdir())
    assert "segredo" not in combined
    assert 'AdminPassword=""' in combined


def test_filesystem_source_rejects_world_symlink(tmp_path: Path) -> None:
    source, _rest = _production_source(tmp_path)
    outside = tmp_path / "outside.sav"
    outside.write_bytes(b"outside")
    link = tmp_path / "palworld/Pal/Saved/SaveGames/0/world-id/Players/link.sav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("ambiente não permite criar symlink")
    payload = tmp_path / "payload"
    payload.mkdir()

    with pytest.raises(BackupSourceError, match=r"link simbólico|entrada não regular"):
        source.stage_palworld_payload(payload)


def test_sqlite_snapshot_is_consistent_with_active_wal(tmp_path: Path) -> None:
    source = tmp_path / "manager.db"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    connection.execute("INSERT INTO values_table VALUES ('persistido')")
    connection.commit()
    target = tmp_path / "snapshot.db"

    snapshot_database(source, target)

    snapshot = sqlite3.connect(target)
    try:
        assert snapshot.execute("SELECT value FROM values_table").fetchone() == ("persistido",)
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        snapshot.close()
        connection.close()

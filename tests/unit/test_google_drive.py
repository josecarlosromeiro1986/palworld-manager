import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from app.backups.manifest import sha256_file
from app.integrations.google_drive import (
    FakeGoogleDriveStorage,
    GoogleDriveCancelled,
    GoogleDriveError,
    RcloneGoogleDriveStorage,
)


class RecordingRunner:
    def __init__(self, handler: Callable[[tuple[str, ...]], str]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        del timeout_seconds, cancel_requested
        call = tuple(arguments)
        self.calls.append(call)
        return self.handler(call)


def test_rclone_parses_quota_and_uses_fixed_remote_namespace() -> None:
    runner = RecordingRunner(
        lambda call: (
            json.dumps({"total": 1000, "used": 400, "free": 600, "trashed": 25})
            if "about" in call
            else ""
        )
    )
    storage = RcloneGoogleDriveStorage(
        Path("/usr/bin/rclone"),
        "palworld-manager",
        runner=runner,
    )

    quota = storage.quota()

    assert quota.total_bytes == 1000
    assert quota.free_bytes == 600
    assert runner.calls == [
        (
            "/usr/bin/rclone",
            "--ask-password=false",
            "about",
            "palworld-manager:",
            "--json",
        )
    ]


def test_rclone_rejects_invalid_quota_and_unsafe_remote_filename() -> None:
    runner = RecordingRunner(lambda _call: "{}")
    storage = RcloneGoogleDriveStorage(Path("/usr/bin/rclone"), "drive", runner=runner)

    with pytest.raises(GoogleDriveError, match="quota incompleta"):
        storage.quota()
    with pytest.raises(GoogleDriveError, match="nome de backup remoto inválido"):
        storage.delete("../arquivo-do-usuario.tar.gz")

    assert len(runner.calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX são validadas em produção")
def test_production_rclone_requires_regular_private_config(tmp_path: Path) -> None:
    executable = tmp_path / "rclone"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "rclone.conf"
    config.write_text("[palworld-manager]\ntype = drive\n", encoding="utf-8")
    config.chmod(0o600)

    RcloneGoogleDriveStorage(executable, "palworld-manager", config_path=config)

    config.chmod(0o640)
    with pytest.raises(GoogleDriveError, match="grupo/outros"):
        RcloneGoogleDriveStorage(executable, "palworld-manager", config_path=config)


def test_interrupted_cleanup_removes_only_owned_temporary_upload() -> None:
    owned = ".palworld-manager-upload-j000123-" + "a" * 32 + ".partial"
    other_job = ".palworld-manager-upload-j000999-" + "b" * 32 + ".partial"
    deleted: list[str] = []

    def handler(call: tuple[str, ...]) -> str:
        if "lsjson" in call:
            return json.dumps(
                [
                    {
                        "Name": owned,
                        "Size": 1,
                        "Hashes": {"SHA-256": "1" * 64},
                    },
                    {
                        "Name": other_job,
                        "Size": 1,
                        "Hashes": {"SHA-256": "2" * 64},
                    },
                    {
                        "Name": "arquivo-do-usuario.txt",
                        "Size": 1,
                        "Hashes": {"SHA-256": "3" * 64},
                    },
                ]
            )
        if "deletefile" in call:
            deleted.append(call[3])
        return ""

    storage = RcloneGoogleDriveStorage(
        Path("/usr/bin/rclone"), "drive", runner=RecordingRunner(handler)
    )

    assert storage.cleanup_interrupted_uploads((123,)) == 1
    assert deleted == [f"drive:Palworld Manager/Backups/{owned}"]


def test_fake_drive_upload_download_delete_and_cancel_are_complete(tmp_path: Path) -> None:
    filename = "palworld-manager-backup-20260814T120000000000Z-j000123-" + "a" * 32 + ".tar.gz"
    source = tmp_path / filename
    source.write_bytes(b"backup-validado")
    digest = sha256_file(source)
    storage = FakeGoogleDriveStorage(total_bytes=1024)

    uploaded = storage.upload(
        source,
        filename,
        job_id=123,
        expected_sha256=digest,
        cancel_requested=lambda: False,
    )
    target = tmp_path / "download.partial"
    storage.download(
        filename,
        target,
        expected_sha256=digest,
        cancel_requested=lambda: False,
    )

    assert uploaded.sha256 == digest
    assert target.read_bytes() == source.read_bytes()
    assert storage.quota().used_bytes == source.stat().st_size
    storage.delete(filename)
    assert not storage.contains(filename)

    with pytest.raises(GoogleDriveCancelled):
        storage.upload(
            source,
            filename,
            job_id=123,
            expected_sha256=digest,
            cancel_requested=lambda: True,
        )

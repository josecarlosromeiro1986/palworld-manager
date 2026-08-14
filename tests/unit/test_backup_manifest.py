import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.backups.manifest import (
    BackupValidationError,
    ManifestFile,
    build_manifest,
    payload_manifest_files,
    validate_archive,
    validate_archive_path,
)


def test_manifest_is_deterministic_and_lists_each_payload_file(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    (payload / "world" / "Players").mkdir(parents=True)
    (payload / "world" / "Level.sav").write_bytes(b"level")
    (payload / "world" / "Players" / "player.sav").write_bytes(b"player")

    files = payload_manifest_files(payload)
    first = build_manifest(
        backup_id="0" * 32,
        created_at_utc="2026-08-14T07:00:00Z",
        trigger="MANUAL",
        files=files,
    )
    second = build_manifest(
        backup_id="0" * 32,
        created_at_utc="2026-08-14T07:00:00Z",
        trigger="MANUAL",
        files=files,
    )
    parsed = json.loads(first)

    assert first == second
    assert [item["path"] for item in parsed["files"]] == [
        "world/Level.sav",
        "world/Players/player.sav",
    ]
    assert "manifest.json" not in {item["path"] for item in parsed["files"]}
    assert all(len(item["sha256"]) == 64 for item in parsed["files"])


@pytest.mark.parametrize(
    "path",
    ["../escape", "/absolute", "C:\\absolute", "C:/absolute", "a/../b"],
)
def test_archive_paths_reject_traversal_and_absolute_values(path: str) -> None:
    with pytest.raises(BackupValidationError):
        validate_archive_path(path)


def test_archive_validation_rejects_symlinks(tmp_path: Path) -> None:
    archive_path = tmp_path / "invalid.tar.gz"
    manifest = build_manifest(
        backup_id="0" * 32,
        created_at_utc="2026-08-14T07:00:00Z",
        trigger="MANUAL",
        files=(),
    )
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        link = tarfile.TarInfo("world/Level.sav")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)

    with pytest.raises(BackupValidationError, match="não regular"):
        validate_archive(archive_path)


def test_archive_validation_rejects_corrupted_payload_and_invalid_gzip(tmp_path: Path) -> None:
    archive_path = tmp_path / "corrupted.tar.gz"
    expected = b"expected"
    actual = b"corruptd"
    manifest = build_manifest(
        backup_id="0" * 32,
        created_at_utc="2026-08-14T07:00:00Z",
        trigger="MANUAL",
        files=(
            ManifestFile(
                "world/Level.sav",
                len(expected),
                hashlib.sha256(expected).hexdigest(),
            ),
        ),
    )
    with tarfile.open(archive_path, "w:gz") as archive:
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest)
        archive.addfile(manifest_info, io.BytesIO(manifest))
        payload_info = tarfile.TarInfo("world/Level.sav")
        payload_info.size = len(actual)
        archive.addfile(payload_info, io.BytesIO(actual))

    with pytest.raises(BackupValidationError, match="hash"):
        validate_archive(archive_path)

    invalid = tmp_path / "invalid.tar.gz"
    invalid.write_bytes(b"not-a-gzip")
    with pytest.raises(BackupValidationError, match=r"tar\.gz"):
        validate_archive(invalid)

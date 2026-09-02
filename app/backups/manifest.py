import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final, cast

MANIFEST_SCHEMA_VERSION: Final = 1
MANIFEST_FILENAME: Final = "manifest.json"
MAX_MANIFEST_BYTES: Final = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS: Final = 100_000
MAX_ARCHIVE_PATH_BYTES: Final = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES: Final = 128 * 1024**3


class BackupValidationError(RuntimeError):
    """O artefato de backup não satisfaz o contrato de integridade."""


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveSummary:
    member_count: int
    payload_size_bytes: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def payload_manifest_files(payload_root: Path) -> tuple[ManifestFile, ...]:
    files: list[ManifestFile] = []
    for path in sorted(payload_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise BackupValidationError("o payload contém link simbólico")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BackupValidationError("o payload contém entrada não regular")
        relative = path.relative_to(payload_root).as_posix()
        validate_archive_path(relative)
        stat = path.stat()
        files.append(ManifestFile(relative, stat.st_size, sha256_file(path)))
    if not files:
        raise BackupValidationError("o payload do backup está vazio")
    return tuple(files)


def build_manifest(
    *,
    backup_id: str,
    created_at_utc: str,
    trigger: str,
    files: tuple[ManifestFile, ...],
) -> bytes:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at_utc": created_at_utc,
        "metadata": {
            "format": "tar.gz",
            "kind": "local",
            "trigger": trigger.lower(),
        },
        "files": [
            {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in files
        ],
    }
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def validate_archive(archive_path: Path) -> dict[str, object]:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            _members, manifest, expected, _summary = _validated_structure(archive)
            for item in expected:
                member = archive.getmember(item.path)
                if member.size != item.size_bytes:
                    raise BackupValidationError("tamanho de arquivo difere do manifest")
                payload = archive.extractfile(member)
                if payload is None:
                    raise BackupValidationError("arquivo do payload não pode ser lido")
                digest = hashlib.sha256()
                while block := payload.read(1024 * 1024):
                    digest.update(block)
                if digest.hexdigest() != item.sha256:
                    raise BackupValidationError("hash de arquivo difere do manifest")
            return manifest
    except (
        OSError,
        ValueError,
        RecursionError,
        tarfile.TarError,
    ) as error:
        raise BackupValidationError("o tar.gz é inválido") from error


def inspect_archive(archive_path: Path) -> ArchiveSummary:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            _members, _manifest, _expected, summary = _validated_structure(archive)
            return summary
    except (
        OSError,
        ValueError,
        RecursionError,
        tarfile.TarError,
    ) as error:
        raise BackupValidationError("o tar.gz é inválido") from error


def validate_archive_path(value: str) -> None:
    try:
        encoded_path = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise BackupValidationError("path de backup inválido") from error
    path = PurePosixPath(value)
    if (
        not value
        or len(encoded_path) > MAX_ARCHIVE_PATH_BYTES
        or "\\" in value
        or path.is_absolute()
        or value != path.as_posix()
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BackupValidationError("path de backup inválido")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupValidationError("path de backup inválido")


def _validated_structure(
    archive: tarfile.TarFile,
) -> tuple[
    tuple[tarfile.TarInfo, ...],
    dict[str, object],
    tuple[ManifestFile, ...],
    ArchiveSummary,
]:
    members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    declared_payload_size = 0
    for member in archive:
        if len(members) >= MAX_ARCHIVE_MEMBERS:
            raise BackupValidationError("o arquivo excede o limite de entradas")
        validate_archive_path(member.name)
        if not member.isfile() or member.size < 0:
            raise BackupValidationError("o arquivo contém entrada não regular")
        if not members and member.name != MANIFEST_FILENAME:
            raise BackupValidationError("manifest.json deve ser a primeira entrada")
        if member.name in names:
            raise BackupValidationError("o arquivo contém paths duplicados")
        names.add(member.name)
        if member.name == MANIFEST_FILENAME:
            if member.size > MAX_MANIFEST_BYTES:
                raise BackupValidationError("manifest.json excede o limite permitido")
        else:
            declared_payload_size += member.size
            if declared_payload_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise BackupValidationError("o payload excede o limite permitido")
        members.append(member)
    if not members:
        raise BackupValidationError("manifest.json deve ser a primeira entrada")

    manifest_member = members[0]
    stream = archive.extractfile(manifest_member)
    if stream is None:
        raise BackupValidationError("manifest.json não pode ser lido")
    manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise BackupValidationError("manifest.json excede o limite permitido")
    manifest_value = json.loads(manifest_bytes)
    expected = _parse_manifest(manifest_value)
    if names != {MANIFEST_FILENAME, *(item.path for item in expected)}:
        raise BackupValidationError("conteúdo do arquivo difere do manifest")
    by_name = {member.name: member for member in members}
    payload_size = 0
    for item in expected:
        member = by_name[item.path]
        if member.size != item.size_bytes:
            raise BackupValidationError("tamanho de arquivo difere do manifest")
        payload_size += item.size_bytes
        if payload_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise BackupValidationError("o payload excede o limite permitido")
    manifest = cast(dict[str, object], manifest_value)
    return (
        tuple(members),
        manifest,
        expected,
        ArchiveSummary(member_count=len(members), payload_size_bytes=payload_size),
    )


def _parse_manifest(payload: object) -> tuple[ManifestFile, ...]:
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise BackupValidationError("versão do manifest inválida")
    backup_id = payload.get("backup_id")
    created_at = payload.get("created_at_utc")
    metadata = payload.get("metadata")
    raw_files = payload.get("files")
    if not isinstance(backup_id, str) or len(backup_id) != 32:
        raise BackupValidationError("identificador do manifest inválido")
    try:
        int(backup_id, 16)
    except ValueError as error:
        raise BackupValidationError("identificador do manifest inválido") from error
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise BackupValidationError("timestamp do manifest inválido")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupValidationError("timestamp do manifest inválido") from error
    if not isinstance(metadata, dict) or set(metadata) != {"format", "kind", "trigger"}:
        raise BackupValidationError("metadados do manifest inválidos")
    if metadata.get("format") != "tar.gz" or metadata.get("kind") != "local":
        raise BackupValidationError("metadados do manifest inválidos")
    if metadata.get("trigger") not in {"manual", "automatic"}:
        raise BackupValidationError("metadados do manifest inválidos")
    if not isinstance(raw_files, list):
        raise BackupValidationError("lista do manifest inválida")
    parsed: list[ManifestFile] = []
    for raw in raw_files:
        if not isinstance(raw, dict) or set(raw) != {"path", "size_bytes", "sha256"}:
            raise BackupValidationError("entrada do manifest inválida")
        path = raw.get("path")
        size = raw.get("size_bytes")
        digest = raw.get("sha256")
        if not isinstance(path, str):
            raise BackupValidationError("path do manifest inválido")
        validate_archive_path(path)
        if path == MANIFEST_FILENAME:
            raise BackupValidationError("manifest não pode listar a si próprio")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BackupValidationError("tamanho do manifest inválido")
        if not isinstance(digest, str) or len(digest) != 64:
            raise BackupValidationError("hash do manifest inválido")
        try:
            int(digest, 16)
        except ValueError as error:
            raise BackupValidationError("hash do manifest inválido") from error
        parsed.append(ManifestFile(path, size, digest))
    if tuple(item.path for item in parsed) != tuple(sorted(item.path for item in parsed)):
        raise BackupValidationError("lista do manifest não é determinística")
    if len({item.path for item in parsed}) != len(parsed):
        raise BackupValidationError("lista do manifest contém paths duplicados")
    return tuple(parsed)

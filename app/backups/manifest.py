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


class BackupValidationError(RuntimeError):
    """O artefato de backup não satisfaz o contrato de integridade."""


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    size_bytes: int
    sha256: str


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
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise BackupValidationError("o arquivo contém paths duplicados")
            for member in members:
                validate_archive_path(member.name)
                if not member.isfile():
                    raise BackupValidationError("o arquivo contém entrada não regular")
            if MANIFEST_FILENAME not in names:
                raise BackupValidationError("manifest.json ausente")
            manifest_member = archive.getmember(MANIFEST_FILENAME)
            if manifest_member.size > MAX_MANIFEST_BYTES:
                raise BackupValidationError("manifest.json excede o limite permitido")
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise BackupValidationError("manifest.json não pode ser lido")
            manifest = json.loads(stream.read(MAX_MANIFEST_BYTES + 1))
            expected = _parse_manifest(manifest)
            if set(names) != {MANIFEST_FILENAME, *(item.path for item in expected)}:
                raise BackupValidationError("conteúdo do arquivo difere do manifest")
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
            return cast(dict[str, object], manifest)
    except (OSError, tarfile.TarError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BackupValidationError("o tar.gz é inválido") from error


def validate_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or value != path.as_posix()
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BackupValidationError("path de backup inválido")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupValidationError("path de backup inválido")


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

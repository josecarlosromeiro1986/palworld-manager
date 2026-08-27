import json
import os
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import uuid4

from app.backups.manifest import sha256_file
from app.backups.service import BACKUP_FILENAME_PATTERN
from app.config import AppEnvironment, Settings
from app.system.commands import (
    rclone_subprocess_environment,
    sanitized_subprocess_environment,
)

DRIVE_NAMESPACE: Final = "Palworld Manager/Backups"
REMOTE_TEMP_PATTERN: Final = (
    r"^\.palworld-manager-upload-j(?P<job_id>\d{6,})-[0-9a-f]{32}\.partial$"
)
MAX_RCLONE_OUTPUT_BYTES: Final = 1024 * 1024


class GoogleDriveError(RuntimeError):
    """Falha segura da integração com Google Drive."""


class GoogleDriveCancelled(GoogleDriveError):
    """Transferência cancelada em um ponto seguro."""


@dataclass(frozen=True, slots=True)
class DriveQuota:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    trashed_bytes: int


@dataclass(frozen=True, slots=True)
class DriveFile:
    filename: str
    size_bytes: int
    sha256: str


CancelCheck = Callable[[], bool]


class GoogleDriveStorage(Protocol):
    def quota(self) -> DriveQuota: ...

    def list_files(self) -> tuple[DriveFile, ...]: ...

    def upload(
        self,
        source: Path,
        filename: str,
        *,
        job_id: int,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> DriveFile: ...

    def download(
        self,
        filename: str,
        target: Path,
        *,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> None: ...

    def delete(self, filename: str) -> None: ...

    def cleanup_interrupted_uploads(self, job_ids: tuple[int, ...]) -> int: ...


class CommandRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancel_requested: CancelCheck | None = None,
    ) -> str: ...


class SubprocessCommandRunner:
    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = dict(
            sanitized_subprocess_environment() if environment is None else environment
        )

    def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancel_requested: CancelCheck | None = None,
    ) -> str:
        if timeout_seconds <= 0:
            raise ValueError("timeout inválido")
        process = subprocess.Popen(
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=self._environment,
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancel_requested is not None and cancel_requested():
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise GoogleDriveCancelled("transferência cancelada")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                raise GoogleDriveError("rclone excedeu o tempo limite")
            try:
                stdout, _stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise GoogleDriveError("rclone não concluiu a operação")
        if len(stdout.encode("utf-8")) > MAX_RCLONE_OUTPUT_BYTES:
            raise GoogleDriveError("resposta do rclone excedeu o limite")
        return stdout


class RcloneGoogleDriveStorage:
    def __init__(
        self,
        executable: Path,
        remote: str,
        *,
        runner: CommandRunner | None = None,
        config_path: Path | None = None,
    ) -> None:
        if not executable.is_absolute():
            raise ValueError("o executável rclone deve usar path absoluto")
        if not remote or any(character in remote for character in ":/\\\r\n"):
            raise ValueError("remote rclone inválido")
        self._executable = (
            executable if runner is not None else _validated_rclone_executable(executable)
        )
        self._remote = remote
        if runner is not None:
            self._runner = runner
        else:
            if config_path is None:
                raise ValueError("RCLONE_CONFIG é obrigatório para o adapter de produção")
            validated_config = _validated_rclone_config(config_path)
            self._runner = SubprocessCommandRunner(rclone_subprocess_environment(validated_config))

    def quota(self) -> DriveQuota:
        payload = self._json(
            ("about", self._remote_root, "--json"),
            timeout_seconds=30,
        )
        if not isinstance(payload, dict):
            raise GoogleDriveError("resposta de quota inválida")
        try:
            total = _non_negative_int(payload["total"])
            used = _non_negative_int(payload["used"])
            free = _non_negative_int(payload["free"])
            trashed = _non_negative_int(payload.get("trashed", 0))
        except (KeyError, TypeError, ValueError) as error:
            raise GoogleDriveError("resposta de quota incompleta") from error
        return DriveQuota(total, used, free, trashed)

    def list_files(self) -> tuple[DriveFile, ...]:
        self._run(("mkdir", self._namespace), timeout_seconds=30)
        payload = self._json(
            (
                "lsjson",
                self._namespace,
                "--files-only",
                "--max-depth",
                "1",
                "--no-mimetype",
                "--hash",
                "--hash-type",
                "SHA-256",
            ),
            timeout_seconds=30,
        )
        if not isinstance(payload, list):
            raise GoogleDriveError("listagem remota inválida")
        files: list[DriveFile] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise GoogleDriveError("item remoto inválido")
            name = raw.get("Name")
            size = raw.get("Size")
            sha256 = _rclone_sha256(raw.get("Hashes"))
            if not isinstance(name, str) or not isinstance(sha256, str):
                continue
            try:
                parsed_size = _non_negative_int(size)
            except (TypeError, ValueError) as error:
                raise GoogleDriveError("tamanho remoto inválido") from error
            files.append(DriveFile(name, parsed_size, sha256))
        return tuple(sorted(files, key=lambda item: item.filename))

    def upload(
        self,
        source: Path,
        filename: str,
        *,
        job_id: int,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> DriveFile:
        self._validate_filename(filename)
        digest = _sha256(expected_sha256)
        if source.is_symlink() or not source.is_file() or sha256_file(source) != digest:
            raise GoogleDriveError("artefato local inválido")
        self._run(("mkdir", self._namespace), timeout_seconds=30)
        if self._stat(filename) is not None:
            raise GoogleDriveError("o backup remoto já existe")
        temporary_name = f".palworld-manager-upload-j{job_id:06d}-{uuid4().hex}.partial"
        try:
            self._run(
                (
                    "copyto",
                    str(source),
                    self._path(temporary_name),
                    "--checksum",
                    "--immutable",
                    "--no-traverse",
                ),
                timeout_seconds=3600,
                cancel_requested=cancel_requested,
            )
            temporary = self._stat(temporary_name)
            if (
                temporary is None
                or temporary.sha256 != digest
                or temporary.size_bytes != source.stat().st_size
            ):
                raise GoogleDriveError("integridade remota divergente")
            if cancel_requested():
                raise GoogleDriveCancelled("transferência cancelada")
            self._run(
                (
                    "moveto",
                    self._path(temporary_name),
                    self._path(filename),
                    "--immutable",
                ),
                timeout_seconds=60,
            )
            final = self._stat(filename)
            if final is None or final.sha256 != digest or final.size_bytes != source.stat().st_size:
                raise GoogleDriveError("integridade remota divergente")
            return final
        except Exception:
            self._delete_exact(temporary_name, ignore_missing=True)
            raise

    def download(
        self,
        filename: str,
        target: Path,
        *,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> None:
        self._validate_filename(filename)
        if target.exists() or target.is_symlink():
            raise GoogleDriveError("destino temporário inválido")
        self._run(
            (
                "copyto",
                self._path(filename),
                str(target),
                "--checksum",
                "--immutable",
                "--no-traverse",
            ),
            timeout_seconds=3600,
            cancel_requested=cancel_requested,
        )
        if target.is_symlink() or not target.is_file():
            raise GoogleDriveError("download remoto inválido")
        if sha256_file(target) != _sha256(expected_sha256):
            raise GoogleDriveError("SHA-256 do download não confere")

    def delete(self, filename: str) -> None:
        self._validate_filename(filename)
        self._delete_exact(filename, ignore_missing=False)

    def cleanup_interrupted_uploads(self, job_ids: tuple[int, ...]) -> int:
        import re

        expected = set(job_ids)
        removed = 0
        for item in self.list_files():
            match = re.fullmatch(REMOTE_TEMP_PATTERN, item.filename)
            if match is None or int(match.group("job_id")) not in expected:
                continue
            self._delete_exact(item.filename, ignore_missing=True)
            removed += 1
        return removed

    @property
    def _remote_root(self) -> str:
        return f"{self._remote}:"

    @property
    def _namespace(self) -> str:
        return f"{self._remote}:{DRIVE_NAMESPACE}"

    def _path(self, filename: str) -> str:
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise GoogleDriveError("nome remoto inválido")
        return f"{self._namespace}/{filename}"

    def _stat(self, filename: str) -> DriveFile | None:
        try:
            payload = self._json(
                (
                    "lsjson",
                    self._path(filename),
                    "--stat",
                    "--no-mimetype",
                    "--hash",
                    "--hash-type",
                    "SHA-256",
                ),
                timeout_seconds=30,
            )
        except GoogleDriveError:
            return None
        if not isinstance(payload, dict):
            raise GoogleDriveError("metadado remoto inválido")
        sha256 = _rclone_sha256(payload.get("Hashes"))
        name = payload.get("Name")
        if not isinstance(name, str) or not isinstance(sha256, str):
            raise GoogleDriveError("hash remoto indisponível")
        return DriveFile(name, _non_negative_int(payload.get("Size")), sha256)

    def _delete_exact(self, filename: str, *, ignore_missing: bool) -> None:
        try:
            self._run(
                ("deletefile", self._path(filename), "--drive-use-trash=false"),
                timeout_seconds=60,
            )
        except GoogleDriveError:
            if not ignore_missing:
                raise

    def _json(self, arguments: Sequence[str], *, timeout_seconds: float) -> object:
        output = self._run(arguments, timeout_seconds=timeout_seconds)
        try:
            return cast(object, json.loads(output))
        except (json.JSONDecodeError, UnicodeError) as error:
            raise GoogleDriveError("resposta JSON inválida do rclone") from error

    def _run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        cancel_requested: CancelCheck | None = None,
    ) -> str:
        return self._runner.run(
            (str(self._executable), "--ask-password=false", *arguments),
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )

    @staticmethod
    def _validate_filename(filename: str) -> None:
        if BACKUP_FILENAME_PATTERN.fullmatch(filename) is None:
            raise GoogleDriveError("nome de backup remoto inválido")


class FakeGoogleDriveStorage:
    def __init__(self, *, total_bytes: int = 10 * 1024**3) -> None:
        self._total_bytes = total_bytes
        self._files: dict[str, bytes] = {}
        self._failure: str | None = None

    def set_failure(self, category: str | None) -> None:
        self._failure = category

    def set_total_bytes(self, total_bytes: int) -> None:
        if total_bytes < 0:
            raise ValueError("quota fake inválida")
        self._total_bytes = total_bytes

    def seed(self, filename: str, content: bytes) -> None:
        RcloneGoogleDriveStorage._validate_filename(filename)
        self._files[filename] = content

    def seed_unmanaged(self, filename: str, content: bytes) -> None:
        if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError("nome fake inválido")
        self._files[filename] = content

    def contains(self, filename: str) -> bool:
        return filename in self._files

    def quota(self) -> DriveQuota:
        self._check_failure()
        used = sum(len(content) for content in self._files.values())
        return DriveQuota(self._total_bytes, used, max(0, self._total_bytes - used), 0)

    def list_files(self) -> tuple[DriveFile, ...]:
        self._check_failure()
        return tuple(
            DriveFile(name, len(content), _bytes_sha256(content))
            for name, content in sorted(self._files.items())
        )

    def upload(
        self,
        source: Path,
        filename: str,
        *,
        job_id: int,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> DriveFile:
        del job_id
        self._check_failure()
        RcloneGoogleDriveStorage._validate_filename(filename)
        if cancel_requested():
            raise GoogleDriveCancelled("transferência cancelada")
        content = source.read_bytes()
        if _bytes_sha256(content) != _sha256(expected_sha256):
            raise GoogleDriveError("artefato local inválido")
        if filename in self._files:
            raise GoogleDriveError("o backup remoto já existe")
        if len(content) > self.quota().free_bytes:
            raise GoogleDriveError("quota remota insuficiente")
        self._files[filename] = content
        return DriveFile(filename, len(content), _bytes_sha256(content))

    def download(
        self,
        filename: str,
        target: Path,
        *,
        expected_sha256: str,
        cancel_requested: CancelCheck,
    ) -> None:
        self._check_failure()
        RcloneGoogleDriveStorage._validate_filename(filename)
        if cancel_requested():
            raise GoogleDriveCancelled("transferência cancelada")
        try:
            content = self._files[filename]
        except KeyError as error:
            raise GoogleDriveError("backup remoto indisponível") from error
        if _bytes_sha256(content) != _sha256(expected_sha256):
            raise GoogleDriveError("SHA-256 do download não confere")
        target.write_bytes(content)

    def delete(self, filename: str) -> None:
        self._check_failure()
        RcloneGoogleDriveStorage._validate_filename(filename)
        try:
            del self._files[filename]
        except KeyError as error:
            raise GoogleDriveError("backup remoto indisponível") from error

    def cleanup_interrupted_uploads(self, job_ids: tuple[int, ...]) -> int:
        del job_ids
        return 0

    def _check_failure(self) -> None:
        if self._failure is not None:
            raise GoogleDriveError("Google Drive indisponível")


def create_google_drive_storage(settings: Settings) -> GoogleDriveStorage:
    if settings.environment is AppEnvironment.PRODUCTION:
        return RcloneGoogleDriveStorage(
            settings.rclone,
            settings.rclone_remote,
            config_path=settings.rclone_config,
        )
    return FakeGoogleDriveStorage()


def _sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise GoogleDriveError("SHA-256 inválido")
    return normalized


def _rclone_sha256(hashes: object) -> str | None:
    if not isinstance(hashes, dict):
        return None
    candidates: list[str] = []
    for key in ("SHA-256", "sha256"):
        if key not in hashes:
            continue
        value = hashes[key]
        if not isinstance(value, str):
            raise GoogleDriveError("hash remoto inválido")
        candidates.append(_sha256(value))
    if not candidates:
        return None
    if len(set(candidates)) != 1:
        raise GoogleDriveError("hash remoto ambíguo")
    return candidates[0]


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("inteiro não negativo obrigatório")
    return value


def _bytes_sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _validated_rclone_executable(path: Path) -> Path:
    validated = _validated_regular_path(path, label="RCLONE", writable=False)
    if not os.access(validated, os.X_OK):
        raise GoogleDriveError("RCLONE não é um executável regular permitido")
    return validated


def _validated_rclone_config(path: Path) -> Path:
    validated = _validated_regular_path(path, label="RCLONE_CONFIG", writable=True)
    metadata = validated.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise GoogleDriveError(
            "RCLONE_CONFIG deve pertencer ao processo e não permitir acesso de grupo/outros"
        )
    return validated


def _validated_regular_path(path: Path, *, label: str, writable: bool) -> Path:
    if not path.is_absolute():
        raise GoogleDriveError(f"{label} deve usar path absoluto")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise GoogleDriveError(f"{label} contém link simbólico")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise GoogleDriveError(f"{label} não está disponível") from error
    if path.is_symlink() or not resolved.is_file():
        raise GoogleDriveError(f"{label} não é um arquivo regular permitido")
    access = os.R_OK | (os.W_OK if writable else 0)
    if not os.access(resolved, access):
        raise GoogleDriveError(f"{label} não possui acesso mínimo necessário")
    return resolved

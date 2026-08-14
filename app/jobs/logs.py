import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

JOB_LOG_RETENTION_DAYS = 90
MAX_JOB_LOG_READ_BYTES = 256 * 1024
MAX_JOB_LOG_LINES = 50
SAFE_KIND_PATTERN = re.compile(r"^[A-Z0-9_]{1,100}$")


class JobLogStore(Protocol):
    def create(self, job_id: int, kind: str, *, occurred_at: datetime | None = None) -> str: ...

    def append(
        self,
        log_path: str,
        message: str,
        *,
        occurred_at: datetime | None = None,
    ) -> None: ...

    def tail(self, log_path: str | None) -> tuple[str, ...]: ...

    def prune(self, *, now: datetime | None = None) -> int: ...


class FileJobLogStore:
    def __init__(self, manager_data_directory: Path) -> None:
        if not manager_data_directory.is_absolute():
            raise ValueError("o diretório de dados do Manager deve ser absoluto")
        self._data_directory = manager_data_directory.resolve()
        self._jobs_directory = self._data_directory / "jobs"

    def create(self, job_id: int, kind: str, *, occurred_at: datetime | None = None) -> str:
        if job_id <= 0 or SAFE_KIND_PATTERN.fullmatch(kind) is None:
            raise ValueError("identidade de job inválida para criação do log")
        timestamp = occurred_at or datetime.now(UTC)
        relative = (
            Path("jobs")
            / f"{timestamp.year:04d}"
            / (f"{kind.lower().replace('_', '-')}-{job_id:06d}.log")
        )
        target = self._resolve(relative)
        target.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o640)
        os.close(descriptor)
        self.append(relative.as_posix(), "Job adquirido pelo worker.", occurred_at=timestamp)
        return relative.as_posix()

    def append(
        self,
        log_path: str,
        message: str,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        if "\r" in message or "\n" in message:
            raise ValueError("a mensagem do log deve ocupar uma única linha")
        target = self._resolve(Path(log_path))
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags)
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(f"{timestamp} {message}\n")

    def tail(self, log_path: str | None) -> tuple[str, ...]:
        if log_path is None:
            return ()
        target = self._resolve(Path(log_path))
        if target.is_symlink() or not target.is_file():
            return ()
        with target.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(size - MAX_JOB_LOG_READ_BYTES, 0))
            content = stream.read(MAX_JOB_LOG_READ_BYTES)
        text = content.decode("utf-8", errors="replace")
        return tuple(text.splitlines()[-MAX_JOB_LOG_LINES:])

    def prune(self, *, now: datetime | None = None) -> int:
        if not self._jobs_directory.exists():
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=JOB_LOG_RETENTION_DAYS)
        removed = 0
        for path in self._jobs_directory.glob("[0-9][0-9][0-9][0-9]/*.log"):
            if path.is_symlink() or not path.is_file():
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if modified_at < cutoff:
                path.unlink()
                removed += 1
        return removed

    def _resolve(self, relative: Path) -> Path:
        if relative.is_absolute() or relative.suffix != ".log":
            raise ValueError("referência de log inválida")
        target = (self._data_directory / relative).resolve(strict=False)
        if not target.is_relative_to(self._jobs_directory):
            raise ValueError("referência de log fora da área administrada")
        return target


class MemoryJobLogStore:
    def __init__(self) -> None:
        self.entries: dict[str, list[str]] = {}

    def create(self, job_id: int, kind: str, *, occurred_at: datetime | None = None) -> str:
        timestamp = occurred_at or datetime.now(UTC)
        path = f"jobs/{timestamp.year:04d}/{kind.lower().replace('_', '-')}-{job_id:06d}.log"
        self.entries[path] = []
        self.append(path, "Job adquirido pelo worker.", occurred_at=timestamp)
        return path

    def append(
        self,
        log_path: str,
        message: str,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        self.entries.setdefault(log_path, []).append(f"{timestamp} {message}")

    def tail(self, log_path: str | None) -> tuple[str, ...]:
        if log_path is None:
            return ()
        return tuple(self.entries.get(log_path, ())[-MAX_JOB_LOG_LINES:])

    def prune(self, *, now: datetime | None = None) -> int:
        del now
        return 0


def create_job_log_store(manager_database: Path) -> FileJobLogStore:
    return FileJobLogStore(manager_database.parent)

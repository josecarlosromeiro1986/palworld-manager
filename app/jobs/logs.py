import os
import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

JOB_LOG_RETENTION_DAYS = 90
MAX_JOB_LOG_READ_BYTES = 256 * 1024
MAX_JOB_LOG_LINES = 50
SAFE_KIND_PATTERN = re.compile(r"^[A-Z0-9_]{1,100}$")
SAFE_YEAR_PATTERN = re.compile(r"^[0-9]{4}$")
SAFE_LOG_FILENAME_PATTERN = re.compile(r"^[a-z0-9-]{1,120}-[0-9]{6}\.log$")


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
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(target, flags, 0o640)
        self._write_descriptor(
            descriptor,
            "Job adquirido pelo worker.",
            occurred_at=timestamp,
            ensure_line_boundary=True,
        )
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
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(target, flags)
        self._write_descriptor(descriptor, message, occurred_at=occurred_at)

    @staticmethod
    def _write_descriptor(
        descriptor: int,
        message: str,
        *,
        occurred_at: datetime | None,
        ensure_line_boundary: bool = False,
    ) -> None:
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("o log do job não é um arquivo regular")
            if ensure_line_boundary and metadata.st_size > 0:
                os.lseek(stream.fileno(), -1, os.SEEK_END)
                if os.read(stream.fileno(), 1) != b"\n":
                    stream.write("\n")
            stream.write(f"{timestamp} {message}\n")

    def tail(self, log_path: str | None) -> tuple[str, ...]:
        if log_path is None:
            return ()
        target = self._resolve(Path(log_path))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError:
            return ()
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return ()
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(size - MAX_JOB_LOG_READ_BYTES, 0))
            content = stream.read(MAX_JOB_LOG_READ_BYTES)
        text = content.decode("utf-8", errors="replace")
        return tuple(text.splitlines()[-MAX_JOB_LOG_LINES:])

    def prune(self, *, now: datetime | None = None) -> int:
        if self._jobs_directory.is_symlink() or not self._jobs_directory.is_dir():
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=JOB_LOG_RETENTION_DAYS)
        removed = 0
        for year_directory in self._jobs_directory.iterdir():
            if (
                year_directory.is_symlink()
                or not year_directory.is_dir()
                or SAFE_YEAR_PATTERN.fullmatch(year_directory.name) is None
            ):
                continue
            for path in year_directory.glob("*.log"):
                try:
                    metadata = path.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                modified_at = datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
                if modified_at < cutoff:
                    path.unlink()
                    removed += 1
        return removed

    def _resolve(self, relative: Path) -> Path:
        parts = relative.parts
        if (
            relative.is_absolute()
            or len(parts) != 3
            or parts[0] != "jobs"
            or SAFE_YEAR_PATTERN.fullmatch(parts[1]) is None
            or SAFE_LOG_FILENAME_PATTERN.fullmatch(parts[2]) is None
        ):
            raise ValueError("referência de log inválida")
        current = self._data_directory
        for part in parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError("referência de log contém link simbólico")
        return self._data_directory / relative


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

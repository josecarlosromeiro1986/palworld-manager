import json
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import IO, Protocol

from app.config import SERVICE_NAME_PATTERN, AppEnvironment, Settings
from app.system.commands import sanitized_subprocess_environment

JOURNALCTL_PATH = "/usr/bin/journalctl"
JOURNAL_QUERY_TIMEOUT_SECONDS = 10.0
JOURNAL_HEARTBEAT_SECONDS = 15.0
ALLOWED_HISTORY_LIMITS = frozenset({100, 500, 1000})
CURSOR_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9:;_=+.,@-]{1,1024}$")
SERVICE_NAME_REGEX = re.compile(SERVICE_NAME_PATTERN)
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|token|cookie|webhook|credential|api[_-]?key)"
    r"\b(\s*[:=]\s*)([^\s,;]+)"
)
AUTHORIZATION_PATTERN = re.compile(r"(?i)\b(authorization)\b(\s*[:=]\s*)[^\r\n,;]+")
URL_CREDENTIALS_PATTERN = re.compile(r"(?i)(https?://)[^\s/:@]+:[^\s/@]+@")
NON_CRITICAL_OPERATIONAL_PATTERNS = (
    re.compile(
        r"\baccess-control-expose-headers\s*:\s*[^\r\n]*\bx-sentry-error\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmain process exited\b[^\r\n]*\b(?:status|code)\s*[=:]\s*143(?:\b|/)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:status|code)\s*[=:]\s*143(?:\b|/)[^\r\n]*"
        r"\b(?:sigterm|main process exited|code=exited)\b",
        re.IGNORECASE,
    ),
)
CRITICAL_OPERATIONAL_PATTERN = re.compile(
    r"\b(?:fatal(?:\s+error)?|critical error|erro cr[ií]tico|crash(?:ed)?|panic|"
    r"segmentation fault|core dumped|out of memory|unhandled exception|assertion failed)\b"
    r"|fatalerror"
    r"|\bfailed to (?:load|save|initialize|start|open|read|write)\b",
    re.IGNORECASE,
)


class LogCategory(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    CONNECTION = "CONNECTION"
    SYSTEM = "SYSTEM"
    NORMAL = "NORMAL"


class PalworldLogError(RuntimeError):
    """O journal não produziu uma resposta segura e utilizável."""


@dataclass(frozen=True, slots=True)
class LogEntry:
    cursor: str
    occurred_at: datetime
    message: str
    category: LogCategory
    priority: int | None = None


class PalworldLogSource(Protocol):
    def history(self, limit: int) -> list[LogEntry]: ...

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]: ...


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]: ...


class StreamProcess(Protocol):
    stdout: IO[str] | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class StreamOpener(Protocol):
    def __call__(self, command: Sequence[str]) -> StreamProcess: ...


class LogRedactor:
    def __init__(self, sensitive_values: Sequence[str] = ()) -> None:
        self._sensitive_values = tuple(
            sorted({value for value in sensitive_values if value}, key=len, reverse=True)
        )

    def redact(self, message: str) -> str:
        redacted = AUTHORIZATION_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[SEGREDO PROTEGIDO]",
            message,
        )
        redacted = URL_CREDENTIALS_PATTERN.sub(
            lambda match: f"{match.group(1)}[SEGREDO PROTEGIDO]@",
            redacted,
        )
        redacted = SENSITIVE_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}{match.group(2)}[SEGREDO PROTEGIDO]",
            redacted,
        )
        for value in self._sensitive_values:
            redacted = redacted.replace(value, "[SEGREDO PROTEGIDO]")
        return redacted


def validate_history_limit(limit: int) -> int:
    if isinstance(limit, bool) or limit not in ALLOWED_HISTORY_LIMITS:
        raise ValueError("quantidade de linhas inválida")
    return limit


def validate_cursor(cursor: str | None) -> str | None:
    if cursor is None or cursor == "":
        return None
    if CURSOR_PATTERN.fullmatch(cursor) is None:
        raise ValueError("cursor de journal inválido")
    return cursor


def classify_log(message: str, priority: int | None = None) -> LogCategory:
    normalized = message.casefold()
    if priority is not None and priority <= 3:
        return LogCategory.ERROR
    if re.search(r"\b(error|fatal|exception|crash|failed)\b", normalized):
        return LogCategory.ERROR
    if priority == 4 or re.search(r"\b(warn|warning|atenção)\b", normalized):
        return LogCategory.WARNING
    if re.search(
        r"\b(player|client|connect(?:ed|ion)?|disconnect(?:ed|ion)?|join(?:ed)?|left)\b",
        normalized,
    ):
        return LogCategory.CONNECTION
    if re.search(r"\b(systemd|service|server|steam|shutdown|startup|world)\b", normalized):
        return LogCategory.SYSTEM
    return LogCategory.NORMAL


def is_critical_log(entry: LogEntry) -> bool:
    """Classifica falhas operacionais sem reutilizar a categoria visual da UI."""
    if any(pattern.search(entry.message) for pattern in NON_CRITICAL_OPERATIONAL_PATTERNS):
        return False
    if entry.priority is not None and entry.priority <= 3:
        return True
    return CRITICAL_OPERATIONAL_PATTERN.search(entry.message) is not None


def parse_journal_entry(raw_line: str, redactor: LogRedactor) -> LogEntry | None:
    try:
        payload = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    cursor = payload.get("__CURSOR")
    timestamp = payload.get("__REALTIME_TIMESTAMP")
    message_value = payload.get("MESSAGE")
    if not isinstance(cursor, str) or not isinstance(timestamp, str):
        return None
    try:
        validated_cursor = validate_cursor(cursor)
        microseconds = int(timestamp)
    except (TypeError, ValueError):
        return None
    if validated_cursor is None or microseconds < 0:
        return None

    if isinstance(message_value, str):
        message = message_value
    elif isinstance(message_value, list) and all(
        isinstance(value, int) and 0 <= value <= 255 for value in message_value
    ):
        message = bytes(message_value).decode("utf-8", errors="replace")
    else:
        return None

    priority_value = payload.get("PRIORITY")
    try:
        priority = int(priority_value) if priority_value is not None else None
    except (TypeError, ValueError):
        priority = None
    safe_message = redactor.redact(message)
    return LogEntry(
        cursor=validated_cursor,
        occurred_at=datetime.fromtimestamp(microseconds / 1_000_000, tz=UTC),
        message=safe_message,
        category=classify_log(safe_message, priority),
        priority=priority,
    )


def _run_command(
    command: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=sanitized_subprocess_environment(),
    )


def _open_stream(command: Sequence[str]) -> StreamProcess:
    return subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=sanitized_subprocess_environment(),
    )


class JournalctlPalworldLogSource:
    def __init__(
        self,
        service_name: str,
        *,
        redactor: LogRedactor | None = None,
        runner: CommandRunner = _run_command,
        stream_opener: StreamOpener = _open_stream,
        query_timeout_seconds: float = JOURNAL_QUERY_TIMEOUT_SECONDS,
        heartbeat_seconds: float = JOURNAL_HEARTBEAT_SECONDS,
    ) -> None:
        if SERVICE_NAME_REGEX.fullmatch(service_name) is None:
            raise ValueError("nome de serviço systemd inválido")
        if query_timeout_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("timeouts do journal devem ser positivos")
        self._service_name = service_name
        self._redactor = redactor or LogRedactor()
        self._runner = runner
        self._stream_opener = stream_opener
        self._query_timeout_seconds = query_timeout_seconds
        self._heartbeat_seconds = heartbeat_seconds

    def history(self, limit: int) -> list[LogEntry]:
        validated_limit = validate_history_limit(limit)
        command = (*self._base_command(), "--lines", str(validated_limit))
        try:
            result = self._runner(command, timeout_seconds=self._query_timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PalworldLogError("Não foi possível consultar os logs do Palworld.") from error
        if result.returncode != 0:
            raise PalworldLogError("Não foi possível consultar os logs do Palworld.")
        return self._parse_lines(result.stdout.splitlines())

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        cursor = validate_cursor(after_cursor)
        command = (*self._base_command(), "--follow", "--lines", "0")
        if cursor is not None:
            command = (*command, f"--after-cursor={cursor}")
        try:
            process = self._stream_opener(command)
        except OSError as error:
            raise PalworldLogError("Não foi possível acompanhar os logs do Palworld.") from error
        if process.stdout is None:
            self._stop_process(process)
            raise PalworldLogError("Não foi possível acompanhar os logs do Palworld.")

        pending: queue.Queue[str | None] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for raw_line in process.stdout:
                pending.put(raw_line)
            pending.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True, name="palworld-journal-reader")
        reader.start()
        try:
            while True:
                try:
                    raw_line = pending.get(timeout=self._heartbeat_seconds)
                except queue.Empty:
                    yield None
                    continue
                if raw_line is None:
                    break
                entry = parse_journal_entry(raw_line, self._redactor)
                if entry is not None:
                    yield entry
        finally:
            self._stop_process(process)

    def _base_command(self) -> tuple[str, ...]:
        return (
            JOURNALCTL_PATH,
            "--unit",
            self._service_name,
            "--output",
            "json",
            "--output-fields",
            "MESSAGE,PRIORITY",
            "--no-pager",
            "--quiet",
        )

    def _parse_lines(self, raw_lines: Sequence[str]) -> list[LogEntry]:
        entries: list[LogEntry] = []
        for raw_line in raw_lines:
            entry = parse_journal_entry(raw_line, self._redactor)
            if entry is not None:
                entries.append(entry)
        return entries

    @staticmethod
    def _stop_process(process: StreamProcess) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


class FakePalworldLogSource:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 1.0,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("intervalo do fake não pode ser negativo")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval_seconds = interval_seconds
        start = self._clock() - timedelta(seconds=4)
        messages = (
            ("Servidor Palworld iniciado pelo ambiente simulado.", LogCategory.SYSTEM),
            ("World save carregado com sucesso.", LogCategory.SYSTEM),
            ("Player Testador connected to the server.", LogCategory.CONNECTION),
            ("WARNING: latência simulada acima do esperado.", LogCategory.WARNING),
            ("Tick do servidor processado normalmente.", LogCategory.NORMAL),
        )
        self._seed = tuple(
            LogEntry(f"fake:{index}", start + timedelta(seconds=index - 1), message, category)
            for index, (message, category) in enumerate(messages, start=1)
        )

    def history(self, limit: int) -> list[LogEntry]:
        validate_history_limit(limit)
        return list(self._seed[-limit:])

    def stream(self, after_cursor: str | None) -> Iterator[LogEntry | None]:
        cursor = validate_cursor(after_cursor)
        sequence = len(self._seed) + 1
        if cursor is not None and cursor.startswith("fake:"):
            try:
                sequence = max(sequence, int(cursor.removeprefix("fake:")) + 1)
            except ValueError:
                sequence = len(self._seed) + 1
        while True:
            if self._interval_seconds:
                time.sleep(self._interval_seconds)
            yield LogEntry(
                cursor=f"fake:{sequence}",
                occurred_at=self._clock(),
                message=f"Evento simulado em tempo real #{sequence}.",
                category=LogCategory.NORMAL,
            )
            sequence += 1


def create_palworld_log_source(settings: Settings) -> PalworldLogSource:
    if settings.environment is not AppEnvironment.PRODUCTION:
        return FakePalworldLogSource()
    sensitive_values = []
    for secret in (settings.palworld_rest_username, settings.palworld_rest_password):
        if secret is not None:
            sensitive_values.append(secret.get_secret_value())
    return JournalctlPalworldLogSource(
        settings.palworld_service,
        redactor=LogRedactor(sensitive_values),
    )

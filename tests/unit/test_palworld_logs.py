import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from io import StringIO
from ipaddress import ip_address
from typing import IO

import pytest
from pydantic import SecretStr

from app.config import AppEnvironment, Settings
from app.logs.service import (
    JOURNALCTL_PATH,
    FakePalworldLogSource,
    JournalctlPalworldLogSource,
    LogCategory,
    LogEntry,
    LogRedactor,
    PalworldLogError,
    StreamProcess,
    create_palworld_log_source,
    parse_journal_entry,
    validate_cursor,
    validate_history_limit,
)


class RecordingRunner:
    def __init__(
        self,
        result: subprocess.CompletedProcess[str] | None = None,
        error: OSError | subprocess.TimeoutExpired | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.command: tuple[str, ...] | None = None
        self.timeout_seconds: float | None = None

    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FiniteStreamProcess:
    def __init__(self, output: str) -> None:
        self.stdout: IO[str] | None = StringIO(output)
        self.command: tuple[str, ...] | None = None
        self.stopped = False

    def poll(self) -> int | None:
        return 0 if self.stopped else None

    def terminate(self) -> None:
        self.stopped = True

    def kill(self) -> None:
        self.stopped = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.stopped = True
        return 0


class RecordingStreamOpener:
    def __init__(self, process: FiniteStreamProcess) -> None:
        self.process = process
        self.command: tuple[str, ...] | None = None

    def __call__(self, command: Sequence[str]) -> StreamProcess:
        self.command = tuple(command)
        return self.process


def journal_json(
    cursor: str,
    message: object,
    *,
    timestamp: str = "1786705200000000",
    priority: str = "6",
) -> str:
    return json.dumps(
        {
            "__CURSOR": cursor,
            "__REALTIME_TIMESTAMP": timestamp,
            "MESSAGE": message,
            "PRIORITY": priority,
        }
    )


def completed_process(
    *,
    stdout: str,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[JOURNALCTL_PATH],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_journal_history_uses_fixed_read_only_arguments() -> None:
    runner = RecordingRunner(completed_process(stdout=journal_json("s=abc;i=1", "Ready") + "\n"))
    source = JournalctlPalworldLogSource("palworld.service", runner=runner)

    entries = source.history(100)

    assert [entry.message for entry in entries] == ["Ready"]
    assert runner.command == (
        "/usr/bin/journalctl",
        "--unit",
        "palworld.service",
        "--output",
        "json",
        "--output-fields",
        "MESSAGE,PRIORITY",
        "--no-pager",
        "--quiet",
        "--lines",
        "100",
    )
    assert runner.timeout_seconds == 10.0


@pytest.mark.parametrize("limit", [0, 99, 101, 499, 501, 999, 1001, True])
def test_history_rejects_arbitrary_line_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="linhas"):
        validate_history_limit(limit)


@pytest.mark.parametrize(
    "cursor",
    ["--since=now", "cursor com espaco", "cursor\nmalicioso", "x" * 1025],
)
def test_stream_rejects_arbitrary_cursor_values(cursor: str) -> None:
    with pytest.raises(ValueError, match="cursor"):
        validate_cursor(cursor)


def test_journal_stream_resumes_strictly_after_validated_cursor() -> None:
    process = FiniteStreamProcess(journal_json("s=abc;i=2", "Next") + "\n")
    opener = RecordingStreamOpener(process)
    source = JournalctlPalworldLogSource(
        "palworld.service",
        stream_opener=opener,
        heartbeat_seconds=0.01,
    )

    entries = [entry for entry in source.stream("s=abc;i=1") if entry is not None]

    assert [entry.cursor for entry in entries] == ["s=abc;i=2"]
    assert opener.command is not None
    assert "--follow" in opener.command
    assert "--lines" in opener.command
    assert "0" in opener.command
    assert "--after-cursor=s=abc;i=1" in opener.command
    assert process.stopped is True


def test_parser_preserves_message_classifies_and_redacts_secrets() -> None:
    secret = "senha-super-privada"
    entry = parse_journal_entry(
        journal_json(
            "s=abc;i=1",
            f"ERROR login failed password={secret}",
            priority="3",
        ),
        LogRedactor([secret]),
    )

    assert entry is not None
    assert entry.category is LogCategory.ERROR
    assert entry.occurred_at.tzinfo is UTC
    assert secret not in entry.message
    assert entry.message == "ERROR login failed password=[SEGREDO PROTEGIDO]"


def test_parser_supports_journal_binary_message_without_html_interpretation() -> None:
    original = "Player <script>alert(1)</script> connected"
    entry = parse_journal_entry(
        journal_json("s=abc;i=1", list(original.encode())),
        LogRedactor(),
    )

    assert entry is not None
    assert entry.message == original
    assert entry.category is LogCategory.CONNECTION


def test_redactor_protects_authorization_and_url_credentials() -> None:
    message = (
        "Authorization: Basic dXN1YXJpbzpzZW5oYQ==; endpoint=https://usuario:senha@127.0.0.1/info"
    )

    redacted = LogRedactor().redact(message)

    assert "dXN1YXJpbzpzZW5oYQ==" not in redacted
    assert "usuario:senha" not in redacted
    assert redacted == (
        "Authorization: [SEGREDO PROTEGIDO]; endpoint=https://[SEGREDO PROTEGIDO]@127.0.0.1/info"
    )


def test_redactor_protects_additional_secret_key_names() -> None:
    redacted = LogRedactor().redact("secret=um cookie:dois credential=tres api_key=quatro")

    assert redacted == (
        "secret=[SEGREDO PROTEGIDO] cookie:[SEGREDO PROTEGIDO] "
        "credential=[SEGREDO PROTEGIDO] api_key=[SEGREDO PROTEGIDO]"
    )


def test_invalid_journal_records_are_ignored() -> None:
    source = JournalctlPalworldLogSource(
        "palworld.service",
        runner=RecordingRunner(
            completed_process(
                stdout="\n".join(
                    [
                        "not-json",
                        journal_json("cursor invalido", "ignored"),
                        journal_json("s=abc;i=1", None),
                        journal_json("s=abc;i=2", "valid"),
                    ]
                )
            )
        ),
    )

    assert [entry.message for entry in source.history(500)] == ["valid"]


def test_journal_failure_does_not_expose_stderr() -> None:
    private_detail = "credencial-interna-do-host"
    source = JournalctlPalworldLogSource(
        "palworld.service",
        runner=RecordingRunner(completed_process(stdout="", stderr=private_detail, returncode=1)),
    )

    with pytest.raises(PalworldLogError) as error:
        source.history(100)

    assert private_detail not in str(error.value)


def test_fake_supports_history_and_reconnection_without_host_access() -> None:
    source = FakePalworldLogSource(
        clock=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        interval_seconds=0,
    )

    history = source.history(100)
    stream = source.stream("fake:8")
    first = next(stream)
    second = next(stream)

    assert len(history) == 5
    assert history[-1].cursor == "fake:5"
    assert isinstance(first, LogEntry) and first.cursor == "fake:9"
    assert isinstance(second, LogEntry) and second.cursor == "fake:10"


@pytest.mark.parametrize("environment", [AppEnvironment.DEVELOPMENT, AppEnvironment.TEST])
def test_non_production_uses_complete_fake(environment: AppEnvironment) -> None:
    source = create_palworld_log_source(Settings(environment=environment))

    assert isinstance(source, FakePalworldLogSource)


def test_production_uses_journalctl_source() -> None:
    source = create_palworld_log_source(
        Settings(
            environment=AppEnvironment.PRODUCTION,
            app_host=ip_address("127.0.0.1"),
            palworld_rest_username=SecretStr("usuario-ficticio"),
            palworld_rest_password=SecretStr("senha-ficticia"),
        )
    )

    assert isinstance(source, JournalctlPalworldLogSource)

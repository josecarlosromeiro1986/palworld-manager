import os
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.logs import FileJobLogStore

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def test_file_job_log_uses_managed_relative_path_and_tail(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = FileJobLogStore(tmp_path)

    log_path = store.create(12, "PALWORLD_RESTART", occurred_at=NOW)
    store.append(log_path, "Execução finalizada.", occurred_at=NOW)

    assert log_path == "jobs/2026/palworld-restart-000012.log"
    lines = store.tail(log_path)
    assert lines[0].endswith("Job adquirido pelo worker.")
    assert lines[1].endswith("Execução finalizada.")


@pytest.mark.parametrize("path", ["../secret.log", "/tmp/secret.log", "jobs/2026/file.txt"])
def test_file_job_log_rejects_paths_outside_managed_area(tmp_path, path: str) -> None:  # type: ignore[no-untyped-def]
    store = FileJobLogStore(tmp_path)

    with pytest.raises(ValueError):
        store.tail(path)


def test_file_job_log_prunes_only_expired_managed_logs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = FileJobLogStore(tmp_path)
    expired = store.create(1, "OLD_JOB", occurred_at=NOW - timedelta(days=91))
    current = store.create(2, "NEW_JOB", occurred_at=NOW)
    expired_path = tmp_path / expired
    old_timestamp = (NOW - timedelta(days=91)).timestamp()
    os.utime(expired_path, (old_timestamp, old_timestamp))

    assert store.prune(now=NOW) == 1
    assert store.tail(expired) == ()
    assert store.tail(current)


def test_file_job_log_reuses_regular_target_and_rejects_non_regular_target(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = FileJobLogStore(tmp_path)
    year = tmp_path / "jobs" / "2026"
    year.mkdir(parents=True)
    preexisting = year / "test-job-000001.log"
    preexisting.write_text("conteúdo externo", encoding="utf-8")

    log_path = store.create(1, "TEST_JOB", occurred_at=NOW)

    assert store.tail(log_path)[0] == "conteúdo externo"
    assert "Job adquirido pelo worker." in store.tail(log_path)[-1]

    fifo = year / "test-job-000002.log"
    os.mkfifo(fifo)
    with pytest.raises(OSError):
        store.append("jobs/2026/test-job-000002.log", "mensagem", occurred_at=NOW)
    assert store.tail("jobs/2026/test-job-000002.log") == ()


def test_file_job_log_rejects_symlink_alias_inside_managed_directory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = FileJobLogStore(tmp_path)
    original = store.create(3, "TEST_JOB", occurred_at=NOW)
    alias = tmp_path / "jobs/2026/test-job-000004.log"
    alias.symlink_to(tmp_path / original)

    with pytest.raises(OSError):
        store.append("jobs/2026/test-job-000004.log", "mensagem", occurred_at=NOW)
    assert store.tail("jobs/2026/test-job-000004.log") == ()


def test_file_job_log_retention_never_follows_symlinked_directory(tmp_path) -> None:  # type: ignore[no-untyped-def]
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "external-000001.log"
    external.write_text("arquivo externo", encoding="utf-8")
    expired_timestamp = (NOW - timedelta(days=91)).timestamp()
    os.utime(external, (expired_timestamp, expired_timestamp))
    (tmp_path / "jobs").symlink_to(outside, target_is_directory=True)
    store = FileJobLogStore(tmp_path)

    assert store.prune(now=NOW) == 0
    assert external.read_text(encoding="utf-8") == "arquivo externo"

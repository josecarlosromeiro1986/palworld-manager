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

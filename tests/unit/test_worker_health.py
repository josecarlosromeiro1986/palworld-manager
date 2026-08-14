import subprocess
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import create_session_factory, session_scope
from app.db.models import Base, WorkerHeartbeat
from app.jobs.health import (
    FakeWorkerService,
    SystemdWorkerService,
    WorkerHealthChecker,
    WorkerHealthState,
)
from app.jobs.heartbeat import WORKER_HEARTBEAT_KEY

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class RecordingRunner:
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((tuple(command), timeout_seconds))
        return self.result


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.mark.parametrize(
    ("active", "active_for", "expected"),
    [
        (False, 120.0, WorkerHealthState.OFFLINE),
        (True, 29.9, WorkerHealthState.STARTING),
        (True, 30.0, WorkerHealthState.UNRESPONSIVE),
    ],
)
def test_worker_health_without_heartbeat(
    session_factory: sessionmaker[Session],
    active: bool,
    active_for: float,
    expected: WorkerHealthState,
) -> None:
    def monotonic() -> float:
        return active_for

    service = FakeWorkerService(
        active=active,
        active_for_seconds=active_for,
        monotonic=monotonic,
    )

    snapshot = WorkerHealthChecker(session_factory, service).check(checked_at=NOW)

    assert snapshot.state is expected
    assert snapshot.heartbeat_age_seconds is None


@pytest.mark.parametrize(
    ("heartbeat_age", "expected"),
    [
        (29.9, WorkerHealthState.HEALTHY),
        (30.0, WorkerHealthState.UNRESPONSIVE),
    ],
)
def test_worker_health_uses_recent_persisted_heartbeat(
    session_factory: sessionmaker[Session],
    heartbeat_age: float,
    expected: WorkerHealthState,
) -> None:
    with session_scope(session_factory) as session:
        session.add(
            WorkerHeartbeat(
                key=WORKER_HEARTBEAT_KEY,
                worker_id="worker-test",
                started_at=NOW - timedelta(seconds=60),
                heartbeat_at=NOW - timedelta(seconds=heartbeat_age),
            )
        )
    service = FakeWorkerService(
        active=True,
        active_for_seconds=60,
        monotonic=lambda: 60.0,
    )

    snapshot = WorkerHealthChecker(session_factory, service).check(checked_at=NOW)

    assert snapshot.state is expected
    assert snapshot.worker_id == "worker-test"
    assert snapshot.heartbeat_age_seconds == pytest.approx(heartbeat_age)


def test_heartbeat_from_previous_service_activation_is_ignored(
    session_factory: sessionmaker[Session],
) -> None:
    with session_scope(session_factory) as session:
        session.add(
            WorkerHeartbeat(
                key=WORKER_HEARTBEAT_KEY,
                worker_id="old-worker",
                started_at=NOW - timedelta(minutes=5),
                heartbeat_at=NOW - timedelta(seconds=20),
            )
        )
    service = FakeWorkerService(
        active=True,
        active_for_seconds=5,
        monotonic=lambda: 5.0,
    )

    snapshot = WorkerHealthChecker(session_factory, service).check(checked_at=NOW)

    assert snapshot.state is WorkerHealthState.STARTING
    assert snapshot.heartbeat_age_seconds is None


def test_systemd_worker_service_uses_fixed_unit_and_monotonic_activation() -> None:
    runner = RecordingRunner(
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="ActiveState=active\nActiveEnterTimestampMonotonic=100000000\n",
            stderr="",
        )
    )

    status = SystemdWorkerService(
        runner=runner,
        boot_time_seconds=lambda: 125.0,
    ).get_status()

    assert status.active is True
    assert status.active_for_seconds == 25.0
    assert runner.calls == [
        (
            (
                "/usr/bin/systemctl",
                "show",
                "--property=ActiveState,ActiveEnterTimestampMonotonic",
                "palworld-manager-worker.service",
            ),
            5.0,
        )
    ]

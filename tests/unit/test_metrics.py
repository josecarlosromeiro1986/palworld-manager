from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app.dashboard.metrics import HostMetricsService, RawHostMetrics


class SequenceMetricsSource:
    def __init__(self, values: list[RawHostMetrics]) -> None:
        self._values: Iterator[RawHostMetrics] = iter(values)

    def read(self) -> RawHostMetrics:
        return next(self._values)


def raw_metrics(
    *,
    cpu: float = 25.0,
    memory: float = 40.0,
    received: int = 1_000,
    sent: int = 500,
) -> RawHostMetrics:
    return RawHostMetrics(
        cpu_percent=cpu,
        memory_percent=memory,
        memory_used_bytes=4_000,
        memory_total_bytes=10_000,
        disk_percent=60.0,
        disk_used_bytes=6_000,
        disk_total_bytes=10_000,
        disk_free_bytes=4_000,
        network_received_bytes=received,
        network_sent_bytes=sent,
    )


def test_collects_current_metrics_and_calculates_network_rates() -> None:
    moments = iter(
        [
            datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 12, 0, 5, tzinfo=UTC),
        ]
    )
    service = HostMetricsService(
        SequenceMetricsSource(
            [
                raw_metrics(),
                raw_metrics(cpu=30.0, memory=45.0, received=2_000, sent=1_000),
            ]
        ),
        clock=lambda: next(moments),
    )

    first = service.collect()
    second = service.collect()

    assert first.current.network_received_bytes_per_second == 0
    assert first.current.network_sent_bytes_per_second == 0
    assert second.current.cpu_percent == 30.0
    assert second.current.memory_percent == 45.0
    assert second.current.disk_free_bytes == 4_000
    assert second.current.network_received_bytes_per_second == 200.0
    assert second.current.network_sent_bytes_per_second == 100.0
    assert len(second.history) == 2


def test_history_discards_samples_older_than_fifteen_minutes() -> None:
    current_time = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def clock() -> datetime:
        return current_time

    service = HostMetricsService(
        SequenceMetricsSource([raw_metrics()] * 4),
        clock=clock,
    )

    service.collect()
    current_time += timedelta(minutes=14, seconds=59)
    service.collect()
    current_time += timedelta(seconds=1)
    boundary = service.collect()
    current_time += timedelta(seconds=1)
    expired = service.collect()

    assert len(boundary.history) == 3
    assert len(expired.history) == 3
    assert expired.history[0].measured_at == datetime(2026, 8, 13, 12, 14, 59, tzinfo=UTC)


def test_network_counter_reset_never_produces_negative_rate() -> None:
    moments = iter(
        [
            datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 12, 0, 5, tzinfo=UTC),
        ]
    )
    service = HostMetricsService(
        SequenceMetricsSource(
            [
                raw_metrics(received=2_000, sent=1_000),
                raw_metrics(received=100, sent=50),
            ]
        ),
        clock=lambda: next(moments),
    )

    service.collect()
    reset = service.collect()

    assert reset.current.network_received_bytes_per_second == 0
    assert reset.current.network_sent_bytes_per_second == 0


@pytest.mark.parametrize(
    ("history_window", "interval_seconds"),
    [
        (timedelta(0), 5),
        (timedelta(minutes=15), 0),
    ],
)
def test_rejects_invalid_buffer_configuration(
    history_window: timedelta,
    interval_seconds: int,
) -> None:
    with pytest.raises(ValueError, match="positiv"):
        HostMetricsService(
            SequenceMetricsSource([raw_metrics()]),
            history_window=history_window,
            interval_seconds=interval_seconds,
        )


def test_rejects_naive_metric_timestamp() -> None:
    service = HostMetricsService(
        SequenceMetricsSource([raw_metrics()]),
        clock=lambda: datetime(2026, 8, 13, 12, 0),
    )

    with pytest.raises(ValueError, match="timezone"):
        service.collect()

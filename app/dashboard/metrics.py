from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol

import psutil

METRICS_INTERVAL_SECONDS = 5
METRICS_HISTORY_MINUTES = 15


@dataclass(frozen=True, slots=True)
class RawHostMetrics:
    cpu_percent: float
    memory_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    disk_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    network_received_bytes: int
    network_sent_bytes: int


class HostMetricsSource(Protocol):
    def read(self) -> RawHostMetrics: ...


class PsutilHostMetricsSource:
    def __init__(self, disk_path: str = "/") -> None:
        self._disk_path = disk_path

    def read(self) -> RawHostMetrics:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self._disk_path)
        network = psutil.net_io_counters()
        return RawHostMetrics(
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=memory.percent,
            memory_used_bytes=memory.used,
            memory_total_bytes=memory.total,
            disk_percent=disk.percent,
            disk_used_bytes=disk.used,
            disk_total_bytes=disk.total,
            disk_free_bytes=disk.free,
            network_received_bytes=network.bytes_recv,
            network_sent_bytes=network.bytes_sent,
        )


@dataclass(frozen=True, slots=True)
class MetricsPoint:
    measured_at: datetime
    cpu_percent: float
    memory_percent: float
    network_received_bytes_per_second: float
    network_sent_bytes_per_second: float


@dataclass(frozen=True, slots=True)
class CurrentHostMetrics:
    measured_at: datetime
    cpu_percent: float
    memory_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    disk_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    network_received_bytes_per_second: float
    network_sent_bytes_per_second: float


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    current: CurrentHostMetrics
    history: tuple[MetricsPoint, ...]


class HostMetricsService:
    def __init__(
        self,
        source: HostMetricsSource | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        history_window: timedelta = timedelta(minutes=METRICS_HISTORY_MINUTES),
        interval_seconds: int = METRICS_INTERVAL_SECONDS,
    ) -> None:
        if history_window <= timedelta(0):
            raise ValueError("a janela do histórico deve ser positiva")
        if interval_seconds <= 0:
            raise ValueError("o intervalo de métricas deve ser positivo")

        self._source = source or PsutilHostMetricsSource()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._history_window = history_window
        maximum_points = int(history_window.total_seconds() / interval_seconds) + 1
        self._history: deque[MetricsPoint] = deque(maxlen=maximum_points)
        self._previous_network: tuple[datetime, int, int] | None = None
        self._lock = Lock()

    def collect(self) -> MetricsSnapshot:
        with self._lock:
            measured_at = self._clock()
            if measured_at.tzinfo is None or measured_at.utcoffset() is None:
                raise ValueError("o relógio de métricas deve retornar um timestamp com timezone")
            measured_at = measured_at.astimezone(UTC)

            raw = self._source.read()
            received_rate, sent_rate = self._network_rates(measured_at, raw)
            point = MetricsPoint(
                measured_at=measured_at,
                cpu_percent=raw.cpu_percent,
                memory_percent=raw.memory_percent,
                network_received_bytes_per_second=received_rate,
                network_sent_bytes_per_second=sent_rate,
            )
            self._history.append(point)
            self._discard_expired(measured_at)

            current = CurrentHostMetrics(
                measured_at=measured_at,
                cpu_percent=raw.cpu_percent,
                memory_percent=raw.memory_percent,
                memory_used_bytes=raw.memory_used_bytes,
                memory_total_bytes=raw.memory_total_bytes,
                disk_percent=raw.disk_percent,
                disk_used_bytes=raw.disk_used_bytes,
                disk_total_bytes=raw.disk_total_bytes,
                disk_free_bytes=raw.disk_free_bytes,
                network_received_bytes_per_second=received_rate,
                network_sent_bytes_per_second=sent_rate,
            )
            return MetricsSnapshot(current=current, history=tuple(self._history))

    def _network_rates(
        self,
        measured_at: datetime,
        raw: RawHostMetrics,
    ) -> tuple[float, float]:
        previous = self._previous_network
        self._previous_network = (
            measured_at,
            raw.network_received_bytes,
            raw.network_sent_bytes,
        )
        if previous is None:
            return 0.0, 0.0

        previous_at, previous_received, previous_sent = previous
        elapsed_seconds = (measured_at - previous_at).total_seconds()
        if elapsed_seconds <= 0:
            return 0.0, 0.0

        received_delta = max(0, raw.network_received_bytes - previous_received)
        sent_delta = max(0, raw.network_sent_bytes - previous_sent)
        return received_delta / elapsed_seconds, sent_delta / elapsed_seconds

    def _discard_expired(self, measured_at: datetime) -> None:
        oldest_allowed = measured_at - self._history_window
        while self._history and self._history[0].measured_at < oldest_allowed:
            self._history.popleft()

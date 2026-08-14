from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock

from app.integrations.palworld_rest import PalworldPlayer, PalworldRestClient


@dataclass(frozen=True, slots=True)
class PlayersSnapshot:
    players: tuple[PalworldPlayer, ...]
    queried_at: datetime


class ManualPlayersService:
    def __init__(
        self,
        client: PalworldRestClient,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cached: PlayersSnapshot | None = None
        self._lock = RLock()

    def cached(self) -> PlayersSnapshot | None:
        with self._lock:
            return self._cached

    def refresh(self) -> PlayersSnapshot:
        players = self._client.players()
        snapshot = PlayersSnapshot(players=players, queried_at=self._clock())
        with self._lock:
            self._cached = snapshot
        return snapshot

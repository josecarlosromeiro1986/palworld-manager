from datetime import UTC, datetime

from app.integrations.palworld_rest import FakePalworldRestClient, PalworldPlayer
from app.players.service import ManualPlayersService


def test_players_are_only_queried_on_refresh_and_cached_in_memory() -> None:
    player = PalworldPlayer(
        name="Jogador",
        account_name="conta",
        player_id="player-id",
        user_id="user-id",
        ip="127.0.0.1",
        ping=8.5,
        location_x=1.0,
        location_y=2.0,
        level=20,
        building_count=None,
    )
    client = FakePalworldRestClient(players=(player,))
    queried_at = datetime(2026, 8, 14, 15, 30, tzinfo=UTC)
    service = ManualPlayersService(client, clock=lambda: queried_at)

    assert service.cached() is None
    assert client.player_queries == 0

    snapshot = service.refresh()

    assert snapshot.players == (player,)
    assert snapshot.queried_at == queried_at
    assert service.cached() is snapshot
    assert client.player_queries == 1

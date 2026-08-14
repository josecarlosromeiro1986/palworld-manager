from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Palworld Manager Mock Services",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

announcements: list[str] = []
kicks: list[tuple[str, str | None]] = []
bans: list[tuple[str, str | None]] = []
unbans: list[str] = []


class Announcement(BaseModel):
    message: str


class PlayerAction(BaseModel):
    userid: str
    message: str | None = None


class PlayerReference(BaseModel):
    userid: str


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/api/info", include_in_schema=False)
def palworld_info() -> dict[str, str]:
    return {
        "version": "v0.0.0-fake",
        "servername": "Servidor Palworld simulado",
        "description": "Ambiente local sem acesso ao servidor real.",
        "worldguid": "00000000000000000000000000000000",
    }


@app.get("/v1/api/players", include_in_schema=False)
def palworld_players() -> dict[str, list[dict[str, str | float | int]]]:
    return {
        "players": [
            {
                "name": "Jogador simulado",
                "accountName": "fake-account",
                "playerId": "00000000000000000000000000000000",
                "userId": "steam_00000000000000000",
                "ip": "127.0.0.1",
                "ping": 3.14,
                "location_x": 123.45,
                "location_y": 67.89,
                "level": 1,
                "building_count": 0,
            }
        ]
    }


@app.post("/v1/api/announce", include_in_schema=False)
def palworld_announce(payload: Announcement) -> dict[str, str]:
    announcements.append(payload.message)
    return {"status": "ok"}


@app.post("/v1/api/kick", include_in_schema=False)
def palworld_kick(payload: PlayerAction) -> dict[str, str]:
    kicks.append((payload.userid, payload.message))
    return {"status": "ok"}


@app.post("/v1/api/ban", include_in_schema=False)
def palworld_ban(payload: PlayerAction) -> dict[str, str]:
    bans.append((payload.userid, payload.message))
    return {"status": "ok"}


@app.post("/v1/api/unban", include_in_schema=False)
def palworld_unban(payload: PlayerReference) -> dict[str, str]:
    unbans.append(payload.userid)
    return {"status": "ok"}

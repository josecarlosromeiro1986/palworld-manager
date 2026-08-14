from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.service import (
    AUDIT_ORIGIN_ADMINISTRATOR,
    AUDIT_RESULT_FAILURE,
    AUDIT_RESULT_SUCCESS,
    record_audit_event,
)
from app.db.engine import session_scope
from app.db.models import BanHistory
from app.integrations.palworld_rest import PalworldRestClient, PalworldRestError

PLAYER_HISTORY_LIMIT = 50


class PlayerAction(StrEnum):
    KICK = "KICK"
    BAN = "BAN"
    UNBAN = "UNBAN"


class PlayerAdministrationValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlayerHistoryEntry:
    occurred_at: datetime
    action: PlayerAction
    user_id: str
    target: str
    reason: str
    result: str


class PlayerAdministrationService:
    def __init__(
        self,
        client: PalworldRestClient,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        action: PlayerAction,
        *,
        user_id: str,
        target: str | None,
        reason: str,
        administrator_user_id: int,
    ) -> None:
        normalized_user_id = self._validate_user_id(user_id)
        normalized_target = self._validate_target(target, normalized_user_id)
        normalized_reason = reason if reason.strip() else ""
        self._validate_reason(action, normalized_reason)

        try:
            if action is PlayerAction.KICK:
                self._client.kick(
                    normalized_user_id,
                    normalized_reason if normalized_reason else None,
                )
            elif action is PlayerAction.BAN:
                self._client.ban(normalized_user_id, normalized_reason)
            else:
                self._client.unban(normalized_user_id)
        except PalworldRestError as error:
            self._record(
                action,
                user_id=normalized_user_id,
                target=normalized_target,
                reason=normalized_reason,
                administrator_user_id=administrator_user_id,
                result=AUDIT_RESULT_FAILURE,
                error_kind=error.kind.value,
            )
            raise

        self._record(
            action,
            user_id=normalized_user_id,
            target=normalized_target,
            reason=normalized_reason,
            administrator_user_id=administrator_user_id,
            result=AUDIT_RESULT_SUCCESS,
        )

    def history(self) -> tuple[PlayerHistoryEntry, ...]:
        with session_scope(self._session_factory) as session:
            records = session.scalars(
                select(BanHistory)
                .order_by(BanHistory.occurred_at.desc(), BanHistory.id.desc())
                .limit(PLAYER_HISTORY_LIMIT)
            ).all()
            return tuple(
                PlayerHistoryEntry(
                    occurred_at=self._as_utc(record.occurred_at),
                    action=PlayerAction(record.action),
                    user_id=record.palworld_user_id,
                    target=record.target_name or record.palworld_user_id,
                    reason=record.reason,
                    result=record.result,
                )
                for record in records
            )

    def _record(
        self,
        action: PlayerAction,
        *,
        user_id: str,
        target: str,
        reason: str,
        administrator_user_id: int,
        result: str,
        error_kind: str | None = None,
    ) -> None:
        occurred_at = self._clock()
        details: dict[str, object] = {"palworld_user_id": user_id}
        if error_kind is not None:
            details["error_kind"] = error_kind
        with session_scope(self._session_factory) as session:
            session.add(
                BanHistory(
                    occurred_at=occurred_at,
                    action=action.value,
                    palworld_user_id=user_id,
                    target_name=target,
                    administrator_user_id=administrator_user_id,
                    reason=reason,
                    result=result,
                )
            )
            record_audit_event(
                session,
                occurred_at=occurred_at,
                action=action.value,
                result=result,
                origin=AUDIT_ORIGIN_ADMINISTRATOR,
                user_id=administrator_user_id,
                target=target,
                reason=reason or None,
                details=details,
            )

    @staticmethod
    def _validate_user_id(user_id: str) -> str:
        normalized = user_id.strip()
        if not normalized or len(normalized) > 255:
            raise PlayerAdministrationValidationError("Informe um User ID válido.")
        if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise PlayerAdministrationValidationError("Informe um User ID válido.")
        return normalized

    @staticmethod
    def _validate_target(target: str | None, user_id: str) -> str:
        normalized = target.strip() if target is not None else ""
        if len(normalized) > 255:
            raise PlayerAdministrationValidationError("O alvo informado é inválido.")
        return normalized or user_id

    @staticmethod
    def _validate_reason(action: PlayerAction, reason: str) -> None:
        if action in {PlayerAction.BAN, PlayerAction.UNBAN} and not reason.strip():
            raise PlayerAdministrationValidationError("O motivo é obrigatório para Ban e Unban.")

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

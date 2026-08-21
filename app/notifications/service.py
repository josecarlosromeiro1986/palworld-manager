from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Select, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import session_scope
from app.db.models import NotificationEvent
from app.integrations.discord import (
    DiscordDeliveryError,
    DiscordDeliveryErrorKind,
    DiscordMessage,
    DiscordWebhook,
)

NOTIFICATION_CHANNEL_DISCORD: Final = "DISCORD"
NOTIFICATION_STATUS_PENDING: Final = "PENDING"
NOTIFICATION_STATUS_SENDING: Final = "SENDING"
NOTIFICATION_STATUS_SENT: Final = "SENT"
NOTIFICATION_STATUS_FAILED: Final = "FAILED"
MAX_NOTIFICATION_ATTEMPTS: Final = 3
NOTIFICATION_RETRY_DELAYS: Final = (timedelta(seconds=5), timedelta(seconds=30))

DISCORD_TEST: Final = "DISCORD_TEST"

BACKUP_FAILED: Final = "BACKUP_FAILED"
DISK_CRITICAL: Final = "DISK_CRITICAL"
DRIVE_FAILED: Final = "DRIVE_FAILED"
FORCED_SHUTDOWN: Final = "FORCED_SHUTDOWN"
LOGIN_BLOCKED: Final = "LOGIN_BLOCKED"
OPERATION_INTERRUPTED: Final = "OPERATION_INTERRUPTED"
RESTORE_COMPLETED: Final = "RESTORE_COMPLETED"
RESTORE_FAILED: Final = "RESTORE_FAILED"
SERVER_CRASH: Final = "SERVER_CRASH"
SERVER_RECOVERED: Final = "SERVER_RECOVERED"
UPDATE_COMPLETED: Final = "UPDATE_COMPLETED"
UPDATE_FAILED: Final = "UPDATE_FAILED"

SUPPORTED_DISCORD_EVENT_TYPES: Final = frozenset(
    {
        DISCORD_TEST,
        BACKUP_FAILED,
        DISK_CRITICAL,
        DRIVE_FAILED,
        FORCED_SHUTDOWN,
        LOGIN_BLOCKED,
        OPERATION_INTERRUPTED,
        RESTORE_COMPLETED,
        RESTORE_FAILED,
        SERVER_CRASH,
        SERVER_RECOVERED,
        UPDATE_COMPLETED,
        UPDATE_FAILED,
    }
)

EVENT_TEXT: Final = {
    DISCORD_TEST: (
        "Teste de notificação",
        "A integração do Palworld Manager com o Discord está funcionando.",
    ),
    BACKUP_FAILED: ("Falha no backup automático", "O backup diário não foi concluído."),
    DISK_CRITICAL: ("Espaço em disco crítico", "Uma operação foi bloqueada por falta de espaço."),
    DRIVE_FAILED: ("Falha no Google Drive", "Uma operação de backup remoto falhou."),
    FORCED_SHUTDOWN: ("Encerramento forçado", "Uma ação forçada foi executada no Palworld."),
    LOGIN_BLOCKED: ("Login bloqueado", "O limite de tentativas de acesso foi atingido."),
    OPERATION_INTERRUPTED: (
        "Operação crítica interrompida",
        "O worker foi reiniciado durante uma operação que exige revisão.",
    ),
    RESTORE_COMPLETED: ("Restore concluído", "O Restore terminou e o Palworld está saudável."),
    RESTORE_FAILED: ("Falha no Restore", "O Restore não pôde ser concluído."),
    SERVER_CRASH: ("Falha do servidor", "O Palworld não se recuperou automaticamente."),
    SERVER_RECOVERED: ("Servidor recuperado", "O Palworld se recuperou após uma falha."),
    UPDATE_COMPLETED: ("Update concluído", "O Update terminou e o Palworld está saudável."),
    UPDATE_FAILED: ("Falha no Update", "O Update não pôde ser concluído."),
}


@dataclass(frozen=True, slots=True)
class NotificationReconciliation:
    requeued: int
    failed: int


def enqueue_discord_notification(
    session: Session,
    event_type: str,
    *,
    job_id: int | None = None,
    created_at: datetime | None = None,
) -> NotificationEvent:
    if event_type not in SUPPORTED_DISCORD_EVENT_TYPES:
        raise ValueError("tipo de notificação Discord não suportado")
    event = NotificationEvent(
        event_type=event_type,
        channel=NOTIFICATION_CHANNEL_DISCORD,
        status=NOTIFICATION_STATUS_PENDING,
        created_at=created_at or datetime.now(UTC),
        job_id=job_id,
        attempts=0,
    )
    session.add(event)
    session.flush()
    return event


def _eligible_notification(now: datetime) -> Select[tuple[int]]:
    return (
        select(NotificationEvent.id)
        .where(
            NotificationEvent.channel == NOTIFICATION_CHANNEL_DISCORD,
            NotificationEvent.status == NOTIFICATION_STATUS_PENDING,
            NotificationEvent.attempts < MAX_NOTIFICATION_ATTEMPTS,
            or_(
                NotificationEvent.next_attempt_at.is_(None),
                NotificationEvent.next_attempt_at <= now,
            ),
        )
        .order_by(NotificationEvent.created_at, NotificationEvent.id)
        .limit(1)
    )


def claim_next_notification(
    session: Session,
    *,
    claimed_at: datetime | None = None,
) -> NotificationEvent | None:
    now = claimed_at or datetime.now(UTC)
    statement = (
        update(NotificationEvent)
        .where(
            NotificationEvent.id == _eligible_notification(now).scalar_subquery(),
            NotificationEvent.status == NOTIFICATION_STATUS_PENDING,
        )
        .values(
            status=NOTIFICATION_STATUS_SENDING,
            attempts=NotificationEvent.attempts + 1,
            updated_at=now,
            next_attempt_at=None,
            delivered_at=None,
            last_error=None,
        )
        .returning(NotificationEvent)
    )
    return session.scalars(statement).one_or_none()


def reconcile_sending_notifications(
    session: Session,
    *,
    reconciled_at: datetime | None = None,
) -> NotificationReconciliation:
    now = reconciled_at or datetime.now(UTC)
    events = tuple(
        session.scalars(
            select(NotificationEvent)
            .where(
                NotificationEvent.channel == NOTIFICATION_CHANNEL_DISCORD,
                NotificationEvent.status == NOTIFICATION_STATUS_SENDING,
            )
            .order_by(NotificationEvent.id)
        )
    )
    requeued = 0
    failed = 0
    for event in events:
        event.updated_at = now
        if event.attempts < MAX_NOTIFICATION_ATTEMPTS:
            event.status = NOTIFICATION_STATUS_PENDING
            event.next_attempt_at = now
            event.last_error = DiscordDeliveryErrorKind.INTERRUPTED.value
            requeued += 1
        else:
            event.status = NOTIFICATION_STATUS_FAILED
            event.next_attempt_at = None
            event.last_error = DiscordDeliveryErrorKind.INTERRUPTED.value
            failed += 1
    return NotificationReconciliation(requeued, failed)


def render_discord_message(event: NotificationEvent) -> DiscordMessage:
    title, description = EVENT_TEXT.get(
        event.event_type,
        ("Evento crítico", "O Palworld Manager registrou um evento importante."),
    )
    created_at = _utc(event.created_at)
    references = [f"Evento #{event.id}"]
    if event.job_id is not None:
        references.append(f"Job #{event.job_id}")
    references.append(created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))
    return DiscordMessage(
        content=f"**Palworld Manager — {title}**\n{description}\n{' · '.join(references)}"
    )


class DiscordNotificationDispatcher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        webhook: DiscordWebhook,
    ) -> None:
        self._session_factory = session_factory
        self._webhook = webhook

    def process_next(self, *, now: datetime | None = None) -> bool:
        attempted_at = now or datetime.now(UTC)
        with session_scope(self._session_factory) as session:
            event = claim_next_notification(session, claimed_at=attempted_at)
            if event is None:
                return False
            event_id = event.id
            message = render_discord_message(event)
        try:
            self._webhook.send(message)
        except DiscordDeliveryError as error:
            self._finish_failure(event_id, error, attempted_at)
        except Exception:
            self._finish_failure(
                event_id,
                DiscordDeliveryError(DiscordDeliveryErrorKind.UNAVAILABLE),
                attempted_at,
            )
        else:
            self._finish_success(event_id, attempted_at)
        return True

    def _finish_success(self, event_id: int, delivered_at: datetime) -> None:
        with session_scope(self._session_factory) as session:
            event = session.get_one(NotificationEvent, event_id)
            if event.status != NOTIFICATION_STATUS_SENDING:
                return
            event.status = NOTIFICATION_STATUS_SENT
            event.updated_at = delivered_at
            event.delivered_at = delivered_at
            event.next_attempt_at = None
            event.last_error = None

    def _finish_failure(
        self,
        event_id: int,
        error: DiscordDeliveryError,
        failed_at: datetime,
    ) -> None:
        with session_scope(self._session_factory) as session:
            event = session.get_one(NotificationEvent, event_id)
            if event.status != NOTIFICATION_STATUS_SENDING:
                return
            event.updated_at = failed_at
            event.delivered_at = None
            event.last_error = error.kind.value
            if error.transient and event.attempts < MAX_NOTIFICATION_ATTEMPTS:
                event.status = NOTIFICATION_STATUS_PENDING
                event.next_attempt_at = failed_at + NOTIFICATION_RETRY_DELAYS[event.attempts - 1]
            else:
                event.status = NOTIFICATION_STATUS_FAILED
                event.next_attempt_at = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

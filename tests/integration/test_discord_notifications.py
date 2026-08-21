from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import Job, NotificationEvent
from app.integrations.discord import (
    DiscordDeliveryErrorKind,
    DiscordHttpResponse,
    FakeDiscordWebhook,
    OfficialDiscordWebhook,
    UnconfiguredDiscordWebhook,
)
from app.notifications.service import (
    BACKUP_FAILED,
    LOGIN_BLOCKED,
    NOTIFICATION_STATUS_FAILED,
    NOTIFICATION_STATUS_PENDING,
    NOTIFICATION_STATUS_SENDING,
    NOTIFICATION_STATUS_SENT,
    UPDATE_FAILED,
    DiscordNotificationDispatcher,
    claim_next_notification,
    enqueue_discord_notification,
    reconcile_sending_notifications,
)


@pytest.fixture
def notification_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def test_notification_claim_is_atomic(notification_engine: Engine) -> None:
    factory = create_session_factory(notification_engine)
    with session_scope(factory) as session:
        event_id = enqueue_discord_notification(session, BACKUP_FAILED).id

    def claim(_index: int) -> int | None:
        with session_scope(factory) as session:
            event = claim_next_notification(session)
            return event.id if event is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(claim, range(2)))

    assert claimed.count(event_id) == 1
    assert claimed.count(None) == 1
    with session_scope(factory) as session:
        event = session.get_one(NotificationEvent, event_id)
        assert event.status == NOTIFICATION_STATUS_SENDING
        assert event.attempts == 1


def test_successful_delivery_marks_event_sent_without_job_details(
    notification_engine: Engine,
) -> None:
    factory = create_session_factory(notification_engine)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    with session_scope(factory) as session:
        job = Job(
            kind="TEST_JOB",
            status="FAILED",
            result={"private_detail": "must-not-reach-discord"},
        )
        session.add(job)
        session.flush()
        event_id = enqueue_discord_notification(
            session,
            UPDATE_FAILED,
            job_id=job.id,
            created_at=now,
        ).id
    webhook = FakeDiscordWebhook()
    dispatcher = DiscordNotificationDispatcher(factory, webhook)

    assert dispatcher.process_next(now=now)

    assert len(webhook.messages) == 1
    assert "Falha no Update" in webhook.messages[0].content
    assert "must-not-reach-discord" not in webhook.messages[0].content
    with session_scope(factory) as session:
        event = session.get_one(NotificationEvent, event_id)
        assert event.status == NOTIFICATION_STATUS_SENT
        assert event.attempts == 1
        assert event.delivered_at is not None
        assert event.last_error is None


def test_transient_failures_use_exact_backoff_and_stop_after_three_attempts(
    notification_engine: Engine,
) -> None:
    factory = create_session_factory(notification_engine)
    started_at = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    with session_scope(factory) as session:
        event_id = enqueue_discord_notification(
            session,
            BACKUP_FAILED,
            created_at=started_at,
        ).id
    webhook = FakeDiscordWebhook()
    for _attempt in range(3):
        webhook.queue_failure(DiscordDeliveryErrorKind.UNAVAILABLE)
    dispatcher = DiscordNotificationDispatcher(factory, webhook)

    assert dispatcher.process_next(now=started_at)
    _assert_delivery_state(
        factory,
        event_id,
        status=NOTIFICATION_STATUS_PENDING,
        attempts=1,
        next_attempt_at=started_at + timedelta(seconds=5),
    )
    assert not dispatcher.process_next(now=started_at + timedelta(seconds=4))
    assert dispatcher.process_next(now=started_at + timedelta(seconds=5))
    _assert_delivery_state(
        factory,
        event_id,
        status=NOTIFICATION_STATUS_PENDING,
        attempts=2,
        next_attempt_at=started_at + timedelta(seconds=35),
    )
    assert dispatcher.process_next(now=started_at + timedelta(seconds=35))
    _assert_delivery_state(
        factory,
        event_id,
        status=NOTIFICATION_STATUS_FAILED,
        attempts=3,
        next_attempt_at=None,
    )
    assert not dispatcher.process_next(now=started_at + timedelta(days=1))


def test_permanent_failure_does_not_retry(notification_engine: Engine) -> None:
    factory = create_session_factory(notification_engine)
    with session_scope(factory) as session:
        event_id = enqueue_discord_notification(session, LOGIN_BLOCKED).id
    dispatcher = DiscordNotificationDispatcher(factory, UnconfiguredDiscordWebhook())

    assert dispatcher.process_next()

    with session_scope(factory) as session:
        event = session.get_one(NotificationEvent, event_id)
        assert event.status == NOTIFICATION_STATUS_FAILED
        assert event.attempts == 1
        assert event.next_attempt_at is None
        assert event.last_error == DiscordDeliveryErrorKind.NOT_CONFIGURED.value


def test_webhook_and_remote_error_are_not_persisted(notification_engine: Engine) -> None:
    marker = "private-marker-must-not-be-persisted"

    class RejectingTransport:
        def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            body: bytes,
            timeout_seconds: float,
        ) -> DiscordHttpResponse:
            del url, headers, body, timeout_seconds
            return DiscordHttpResponse(403, marker.encode())

    factory = create_session_factory(notification_engine)
    with session_scope(factory) as session:
        event_id = enqueue_discord_notification(session, BACKUP_FAILED).id
    webhook = OfficialDiscordWebhook(
        f"https://discord.com/api/webhooks/123456789012345678/{marker}",
        transport=RejectingTransport(),
    )
    dispatcher = DiscordNotificationDispatcher(factory, webhook)

    assert dispatcher.process_next()

    with session_scope(factory) as session:
        event = session.get_one(NotificationEvent, event_id)
        persisted = " ".join(
            value
            for value in (event.event_type, event.channel, event.status, event.last_error)
            if value is not None
        )
        assert event.last_error == DiscordDeliveryErrorKind.REJECTED.value
        assert marker not in persisted


def test_startup_reconciles_sending_events_for_at_least_once_delivery(
    notification_engine: Engine,
) -> None:
    factory = create_session_factory(notification_engine)
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    with session_scope(factory) as session:
        session.add_all(
            [
                NotificationEvent(
                    event_type=BACKUP_FAILED,
                    channel="DISCORD",
                    status=NOTIFICATION_STATUS_SENDING,
                    attempts=1,
                ),
                NotificationEvent(
                    event_type=UPDATE_FAILED,
                    channel="DISCORD",
                    status=NOTIFICATION_STATUS_SENDING,
                    attempts=3,
                ),
            ]
        )
    with session_scope(factory) as session:
        recovery = reconcile_sending_notifications(session, reconciled_at=now)

    assert recovery.requeued == 1
    assert recovery.failed == 1
    with session_scope(factory) as session:
        events = tuple(session.scalars(select(NotificationEvent).order_by(NotificationEvent.id)))
        assert events[0].status == NOTIFICATION_STATUS_PENDING
        assert events[0].next_attempt_at is not None
        assert events[0].last_error == DiscordDeliveryErrorKind.INTERRUPTED.value
        assert events[1].status == NOTIFICATION_STATUS_FAILED
        assert events[1].next_attempt_at is None
        assert events[1].last_error == DiscordDeliveryErrorKind.INTERRUPTED.value


def _assert_delivery_state(
    factory: sessionmaker[Session],
    event_id: int,
    *,
    status: str,
    attempts: int,
    next_attempt_at: datetime | None,
) -> None:
    with session_scope(factory) as session:
        event = session.get_one(NotificationEvent, event_id)
        assert event.status == status
        assert event.attempts == attempts
        actual = _utc(event.next_attempt_at) if event.next_attempt_at is not None else None
        assert actual == next_attempt_at


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

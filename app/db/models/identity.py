from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IntegerPrimaryKeyMixin


class User(IntegerPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
    )


class SessionRecord(IntegerPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAttempt(IntegerPrimaryKeyMixin, Base):
    __tablename__ = "login_attempts"

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    username: Mapped[str | None] = mapped_column(String(100), index=True)
    source_address: Mapped[str | None] = mapped_column(String(45))
    successful: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        index=True,
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

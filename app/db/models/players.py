from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IntegerPrimaryKeyMixin


class BanHistory(IntegerPrimaryKeyMixin, Base):
    __tablename__ = "ban_history"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(20), index=True)
    palworld_user_id: Mapped[str] = mapped_column(String(255), index=True)
    target_name: Mapped[str | None] = mapped_column(String(255))
    administrator_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    reason: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(String(30), index=True)

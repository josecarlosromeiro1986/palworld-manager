from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IntegerPrimaryKeyMixin


class Job(IntegerPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        Index(
            "uq_jobs_active_coordination_key",
            "coordination_key",
            unique=True,
            sqlite_where=text("coordination_key IS NOT NULL AND status IN ('PENDING', 'RUNNING')"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    progress: Mapped[int] = mapped_column(server_default=text("0"))
    is_cancellable: Mapped[bool] = mapped_column(Boolean, server_default=text("1"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    execute_now_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    requires_maintenance_lock: Mapped[bool] = mapped_column(Boolean, server_default=text("0"))
    coordination_key: Mapped[str | None] = mapped_column(String(100))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    log_path: Mapped[str | None] = mapped_column(Text)

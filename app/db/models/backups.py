from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IntegerPrimaryKeyMixin


class BackupRecord(IntegerPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "backup_records"
    __table_args__ = (
        UniqueConstraint(
            "location",
            "filename",
            name="uq_backup_records_location_filename",
        ),
    )

    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"),
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None]
    storage_path: Mapped[str] = mapped_column(Text)

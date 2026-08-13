"""add lifecycle job guard

Revision ID: 0003_lifecycle_job_guard
Revises: 0002_session_csrf
Create Date: 2026-08-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_lifecycle_job_guard"
down_revision: str | None = "0002_session_csrf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("coordination_key", sa.String(length=100), nullable=True))

    op.create_index(
        "uq_jobs_active_coordination_key",
        "jobs",
        ["coordination_key"],
        unique=True,
        sqlite_where=sa.text("coordination_key IS NOT NULL AND status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_active_coordination_key", table_name="jobs")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("coordination_key")

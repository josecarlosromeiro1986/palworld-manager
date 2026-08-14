"""add persistent job system

Revision ID: 0005_persistent_job_system
Revises: 0004_assisted_shutdown_controls
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_persistent_job_system"
down_revision: str | None = "0004_assisted_shutdown_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "step",
                sa.String(length=50),
                server_default=sa.text("'WAITING'"),
                nullable=False,
            )
        )

    op.create_table(
        "maintenance_locks",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_maintenance_locks_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_maintenance_locks")),
        sa.UniqueConstraint("job_id", name=op.f("uq_maintenance_locks_job_id")),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_worker_heartbeats")),
    )
    with op.batch_alter_table("worker_heartbeats", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_worker_heartbeats_heartbeat_at"),
            ["heartbeat_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("worker_heartbeats", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_worker_heartbeats_heartbeat_at"))
    op.drop_table("worker_heartbeats")
    op.drop_table("maintenance_locks")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("step")

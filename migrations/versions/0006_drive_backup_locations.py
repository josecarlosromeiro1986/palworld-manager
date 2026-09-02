"""allow one backup artifact per storage location

Revision ID: 0006_drive_backup_locations
Revises: 0005_persistent_job_system
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_drive_backup_locations"
down_revision: str | None = "0005_persistent_job_system"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_records", schema=None) as batch_op:
        batch_op.drop_constraint("uq_backup_records_filename", type_="unique")
        batch_op.create_unique_constraint(
            batch_op.f("uq_backup_records_location_filename"),
            ["location", "filename"],
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_records", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("uq_backup_records_location_filename"),
            type_="unique",
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_backup_records_filename"),
            ["filename"],
        )

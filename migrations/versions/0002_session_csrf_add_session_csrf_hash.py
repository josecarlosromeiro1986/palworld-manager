"""add session csrf hash

Revision ID: 0002_session_csrf
Revises: 0001_initial_schema
Create Date: 2026-08-13 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_session_csrf"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the CSRF hash required by authenticated sessions."""
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("csrf_token_hash", sa.String(length=64), nullable=True))

    # No valid sessions existed before authentication was implemented.
    op.execute(sa.text("DELETE FROM sessions"))

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.alter_column(
            "csrf_token_hash",
            existing_type=sa.String(length=64),
            nullable=False,
        )


def downgrade() -> None:
    """Remove the session CSRF hash."""
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("csrf_token_hash")

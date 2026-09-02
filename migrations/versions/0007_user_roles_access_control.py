"""add user roles, normalized usernames and job ownership

Revision ID: 0007_user_roles_access_control
Revises: 0006_drive_backup_locations
Create Date: 2026-09-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_roles_access_control"
down_revision: str | None = "0006_drive_backup_locations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("username_key", sa.String(length=100), nullable=True))
        batch_op.add_column(
            sa.Column(
                "role",
                sa.String(length=20),
                server_default=sa.text("'ADMIN'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "password_change_required",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )

    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, username FROM users")).mappings()
    seen: set[str] = set()
    for user in users:
        key = str(user["username"]).casefold()
        if key in seen:
            raise RuntimeError("usuarios existentes diferem apenas por maiusculas e minusculas")
        seen.add(key)
        connection.execute(
            sa.text("UPDATE users SET username_key = :username_key WHERE id = :user_id"),
            {"username_key": key, "user_id": user["id"]},
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username_key", existing_type=sa.String(length=100), nullable=False)
        batch_op.create_index(batch_op.f("ix_users_username_key"), ["username_key"], unique=True)
        batch_op.create_index(batch_op.f("ix_users_role"), ["role"], unique=False)
        batch_op.create_check_constraint("valid_role", "role IN ('ADMIN', 'USER')")

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("requested_by_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_jobs_requested_by_user_id_users"),
            "users",
            ["requested_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_jobs_requested_by_user_id"),
            ["requested_by_user_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_requested_by_user_id"))
        batch_op.drop_constraint(
            batch_op.f("fk_jobs_requested_by_user_id_users"),
            type_="foreignkey",
        )
        batch_op.drop_column("requested_by_user_id")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_users_valid_role"), type_="check")
        batch_op.drop_index(batch_op.f("ix_users_role"))
        batch_op.drop_index(batch_op.f("ix_users_username_key"))
        batch_op.drop_column("password_change_required")
        batch_op.drop_column("role")
        batch_op.drop_column("username_key")

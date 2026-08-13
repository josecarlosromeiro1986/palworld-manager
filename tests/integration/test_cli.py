from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select

from app.auth.passwords import verify_password
from app.cli import main
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import User


@pytest.fixture
def migrated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    database_path = tmp_path / "manager.db"
    monkeypatch.setenv("MANAGER_DATABASE", str(database_path))
    command.upgrade(Config("alembic.ini"), "head")

    engine = create_database_engine(database_path)
    yield engine
    engine.dispose()


def _password_reader(*values: str) -> Callable[[str], str]:
    passwords = iter(values)
    return lambda _prompt: next(passwords)


def test_cli_creates_and_resets_administrator_without_printing_password(
    migrated_engine: Engine,
) -> None:
    output = StringIO()
    error_output = StringIO()

    result = main(
        ["create-admin", "--username", "admin"],
        password_reader=_password_reader("senha-inicial", "senha-inicial"),
        output=output,
        error_output=error_output,
    )
    reset_result = main(
        ["reset-password", "--username", "admin"],
        password_reader=_password_reader("senha-alterada", "senha-alterada"),
        output=output,
        error_output=error_output,
    )

    assert result == 0
    assert reset_result == 0
    assert error_output.getvalue() == ""
    assert "senha-inicial" not in output.getvalue()
    assert "senha-alterada" not in output.getvalue()

    factory = create_session_factory(migrated_engine)
    with session_scope(factory) as session:
        administrator = session.scalar(select(User).where(User.username == "admin"))
        assert administrator is not None
        assert verify_password("senha-alterada", administrator.password_hash)


def test_cli_rejects_mismatched_passwords_without_touching_database(
    migrated_engine: Engine,
) -> None:
    output = StringIO()
    error_output = StringIO()

    result = main(
        ["create-admin", "--username", "admin"],
        password_reader=_password_reader("uma-senha", "outra-senha"),
        output=output,
        error_output=error_output,
    )

    assert result == 1
    assert output.getvalue() == ""
    assert "uma-senha" not in error_output.getvalue()
    assert "outra-senha" not in error_output.getvalue()

    factory = create_session_factory(migrated_engine)
    with session_scope(factory) as session:
        assert session.scalar(select(User)) is None

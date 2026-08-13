import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from app.auth.passwords import PasswordTooShortError
from app.auth.service import (
    AdministratorAlreadyExistsError,
    AdministratorNotFoundError,
    InvalidUsernameError,
    create_administrator,
    normalize_username,
    reset_administrator_password,
)
from app.config import Settings
from app.db.engine import create_database_engine, create_session_factory, session_scope

InputReader = Callable[[str], str]


class PasswordConfirmationError(ValueError):
    """Raised when the two password entries differ."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-admin",
        help="Cria o administrador inicial.",
    )
    create_parser.add_argument("--username", help="Nome do administrador.")

    reset_parser = subparsers.add_parser(
        "reset-password",
        help="Redefine a senha de um administrador pelo terminal.",
    )
    reset_parser.add_argument("--username", help="Nome do administrador.")
    return parser


def _read_username(value: str | None, input_reader: InputReader) -> str:
    return normalize_username(value if value is not None else input_reader("Usuario: "))


def _read_confirmed_password(password_reader: InputReader) -> str:
    password = password_reader("Senha: ")
    confirmation = password_reader("Confirme a senha: ")
    if password != confirmation:
        raise PasswordConfirmationError("As senhas informadas nao coincidem.")
    return password


def main(
    argv: Sequence[str] | None = None,
    *,
    input_reader: InputReader = input,
    password_reader: InputReader = getpass.getpass,
    output: TextIO = sys.stdout,
    error_output: TextIO = sys.stderr,
) -> int:
    arguments = build_parser().parse_args(argv)

    try:
        username = _read_username(arguments.username, input_reader)
        password = _read_confirmed_password(password_reader)
        settings = Settings()
        engine = create_database_engine(settings.manager_database)
        try:
            factory = create_session_factory(engine)
            with session_scope(factory) as session:
                if arguments.command == "create-admin":
                    create_administrator(session, username, password)
                    message = "Administrador criado com sucesso."
                else:
                    reset_administrator_password(session, username, password)
                    message = "Senha redefinida com sucesso."
        finally:
            engine.dispose()
    except (
        AdministratorAlreadyExistsError,
        AdministratorNotFoundError,
        InvalidUsernameError,
        PasswordConfirmationError,
        PasswordTooShortError,
    ) as error:
        print(f"Erro: {error}", file=error_output)
        return 1

    print(message, file=output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

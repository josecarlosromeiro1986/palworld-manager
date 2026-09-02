import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event

from alembic import command
from alembic.config import Config as AlembicConfig

from app.auth.service import create_administrator
from app.db.engine import create_database_engine, create_session_factory, session_scope
from app.db.models import AppSetting

E2E_USERNAME = "admin-e2e"
E2E_PASSWORD = "senha-ficticia-e2e"
BACKUP_ENABLED_KEY = "backup_enabled"
shutdown_requested = Event()


def _request_shutdown(_signum: int, _frame: object) -> None:
    shutdown_requested.set()


def _prepare_database(database_path: Path) -> None:
    command.upgrade(AlembicConfig("alembic.ini"), "head")
    engine = create_database_engine(database_path)
    factory = create_session_factory(engine)
    try:
        with session_scope(factory) as session:
            create_administrator(session, E2E_USERNAME, E2E_PASSWORD)
            session.add(AppSetting(key=BACKUP_ENABLED_KEY, value=False))
    finally:
        engine.dispose()


def _stop_processes(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _serve(environment: dict[str, str]) -> int:
    processes = (
        subprocess.Popen([sys.executable, "-m", "app.worker"], env=environment),
        subprocess.Popen([sys.executable, "-m", "app.web"], env=environment),
    )
    try:
        while not shutdown_requested.wait(0.25):
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    return return_code or 1
        return 0
    finally:
        _stop_processes(processes)


def main() -> int:
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    with tempfile.TemporaryDirectory(prefix="palworld-manager-e2e-") as temporary:
        database_path = Path(temporary).resolve() / "manager.db"
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENVIRONMENT": "test",
                "APP_HOST": environment.get("E2E_HOST", "127.0.0.1"),
                "APP_PORT": environment.get("E2E_PORT", "8081"),
                "MANAGER_DATABASE": str(database_path),
            }
        )
        os.environ.update(environment)
        _prepare_database(database_path)
        return _serve(environment)


if __name__ == "__main__":
    raise SystemExit(main())

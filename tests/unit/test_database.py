from pathlib import Path

from app.db.engine import build_sqlite_url


def test_sqlite_url_preserves_absolute_database_path() -> None:
    database_path = Path("/var/lib/palworld-manager/manager.db")

    url = build_sqlite_url(database_path)

    assert url.drivername == "sqlite+pysqlite"
    assert url.database == str(database_path)

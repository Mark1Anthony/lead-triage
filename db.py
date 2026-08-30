"""
Database access for both SQLite and Postgres.

Which one is used depends on DATABASE_URL:
    unset                       -> SQLite at data/leads.db
    postgresql://user@host/db   -> Postgres

SQLite keeps `uvicorn app:app` a one-command start with nothing to install.
Postgres is what docker compose and the deployment use. The differences between
the two are small enough to handle here rather than pulling in an ORM: the app
has seven statements in total.

Application code writes `?` placeholders throughout; `sql()` rewrites them for
Postgres. Both drivers hand back rows that can be read by column name.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger("lead-triage.db")

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "data" / "leads.db"


def database_url() -> str | None:
    """Read on every call rather than at import, so tests can set it."""
    return os.getenv("DATABASE_URL") or None


def is_postgres() -> bool:
    url = database_url()
    return bool(url and url.startswith(("postgres://", "postgresql://")))


def backend() -> str:
    """For /health and the startup log."""
    return "postgres" if is_postgres() else "sqlite"


def sql(statement: str) -> str:
    """Translate the `?` placeholders used in app code for the active driver."""
    return statement.replace("?", "%s") if is_postgres() else statement


# ─── Schema ──────────────────────────────────────────────────────

# The only real difference is the primary key: SQLite wants AUTOINCREMENT on an
# INTEGER column, Postgres uses an identity column.
_COLUMNS = """
    received_at  TEXT NOT NULL,
    name         TEXT NOT NULL,
    company      TEXT NOT NULL,
    email        TEXT NOT NULL,
    source       TEXT NOT NULL,
    message      TEXT NOT NULL,
    priority     TEXT NOT NULL,
    category     TEXT NOT NULL,
    next_action  TEXT NOT NULL,
    summary      TEXT NOT NULL,
    reasoning    TEXT NOT NULL,
    mode         TEXT NOT NULL,
    status       TEXT DEFAULT 'new'
"""

_SQLITE_ID = "id INTEGER PRIMARY KEY AUTOINCREMENT,"
_POSTGRES_ID = "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"

SCHEMA_SQLITE = f"CREATE TABLE IF NOT EXISTS leads (\n    {_SQLITE_ID}{_COLUMNS});"
SCHEMA_POSTGRES = f"CREATE TABLE IF NOT EXISTS leads (\n    {_POSTGRES_ID}{_COLUMNS});"


def schema() -> str:
    return SCHEMA_POSTGRES if is_postgres() else SCHEMA_SQLITE


# ─── Connections ─────────────────────────────────────────────────

@contextmanager
def connect() -> Iterator[Any]:
    """Hand out a connection and always close it, exception or not."""
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(database_url(), row_factory=dict_row)
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(SQLITE_PATH))
        conn.row_factory = sqlite3.Row

    try:
        yield conn
    finally:
        conn.close()


def _create_schema() -> None:
    with connect() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(schema())
        else:
            conn.executescript(schema())
        conn.commit()


def init(attempts: int = 10, delay: float = 1.0) -> None:
    """Create the schema at startup, waiting for the database if it is not up yet.

    Compose already holds the app back until Postgres reports healthy, so the
    retry is not for that case. It is for the ones nothing can gate: a hosted
    database restarting during a redeploy, or a connection refused for the second
    it takes to fail over. Losing the process to a race it would win on the next
    try is worse than waiting.

    Only connection failures are retried. A broken statement or wrong credentials
    fail immediately - retrying those just delays the error by ten seconds.
    """
    if not is_postgres():
        _create_schema()
        return

    import psycopg

    for attempt in range(1, attempts + 1):
        try:
            _create_schema()
            if attempt > 1:
                log.info("database reachable after %d attempts", attempt)
            return
        except psycopg.OperationalError as exc:
            if attempt == attempts:
                raise
            log.warning(
                "database not reachable (attempt %d/%d): %s - retrying in %.0fs",
                attempt, attempts, exc, delay,
            )
            time.sleep(delay)


# ─── Query helpers ───────────────────────────────────────────────
#
# sqlite3 lets you call execute() on the connection and returns a cursor;
# psycopg wants an explicit cursor. These three wrap that difference so the
# routes read the same either way.

def all_rows(conn: Any, statement: str, params: tuple = ()) -> list[dict]:
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
            return [dict(r) for r in cur.fetchall()]
    return [dict(r) for r in conn.execute(statement, params).fetchall()]


def one_value(conn: Any, statement: str, params: tuple = ()) -> Any:
    """First column of the first row - used for COUNT(*)."""
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
            row = cur.fetchone()
            return next(iter(row.values())) if row else None
    return conn.execute(statement, params).fetchone()[0]


def execute(conn: Any, statement: str, params: tuple = ()) -> None:
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
    else:
        conn.execute(statement, params)


def insert_returning_id(conn: Any, statement: str, params: tuple = ()) -> int:
    """INSERT that yields the new id. Postgres needs RETURNING, SQLite has lastrowid."""
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement) + " RETURNING id", params)
            return int(cur.fetchone()["id"])
    return int(conn.execute(statement, params).lastrowid)

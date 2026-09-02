"""
Storage for lead records, across three very different backends.

Which one is used depends on the environment, checked in this order:

    DYNAMODB_TABLE set            -> DynamoDB      (AWS Lambda deployment)
    DATABASE_URL is a postgres URL -> PostgreSQL   (compose, Render)
    neither                        -> SQLite       (a plain `uvicorn app:app`)

The application does not know which. It calls the six functions at the bottom
of this file and gets dictionaries back.

Those six exist because of DynamoDB. With only SQLite and Postgres this module
could hand SQL through to a driver and translate the placeholder style, which
is what it used to do - the two dialects differ in almost nothing. DynamoDB has
no SQL at all, so the seam had to move up to what the application actually
needs: count them, add one, list them, change a status, remove one. That is the
whole vocabulary.
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

# Child of the app logger, so it inherits whatever app.py configures.
log = logging.getLogger("lead_triage.db")

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "data" / "leads.db"

# Column order is the record's shape. Everything below depends on it, and the
# DynamoDB backend uses it to return items with the same keys a SQL row has.
FIELDS = (
    "received_at",
    "name",
    "company",
    "email",
    "source",
    "message",
    "priority",
    "category",
    "next_action",
    "summary",
    "reasoning",
    "mode",
)


# ─── Which backend ────────────────────────────────────────────────

def dynamodb_table() -> str | None:
    """Read on every call rather than at import, so tests can set it."""
    return os.getenv("DYNAMODB_TABLE") or None


def database_url() -> str | None:
    return os.getenv("DATABASE_URL") or None


def is_dynamodb() -> bool:
    return dynamodb_table() is not None


def is_postgres() -> bool:
    url = database_url()
    return bool(url and url.startswith(("postgres://", "postgresql://")))


def backend() -> str:
    """For /health and the startup log."""
    if is_dynamodb():
        return "dynamodb"
    return "postgres" if is_postgres() else "sqlite"


# ─── SQL backends ─────────────────────────────────────────────────

def sql(statement: str) -> str:
    """Translate the `?` placeholders used below for the active driver."""
    return statement.replace("?", "%s") if is_postgres() else statement


# The only real difference between the two schemas is the primary key: SQLite
# wants AUTOINCREMENT on an INTEGER column, Postgres uses an identity column.
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


# sqlite3 lets you call execute() on the connection and hands back a cursor;
# psycopg wants an explicit one. These wrap that difference.

def _rows(conn: Any, statement: str, params: tuple = ()) -> list[dict]:
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
            return [dict(r) for r in cur.fetchall()]
    return [dict(r) for r in conn.execute(statement, params).fetchall()]


def _one(conn: Any, statement: str, params: tuple = ()) -> Any:
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
            row = cur.fetchone()
            return next(iter(row.values())) if row else None
    return conn.execute(statement, params).fetchone()[0]


def _run(conn: Any, statement: str, params: tuple = ()) -> None:
    if is_postgres():
        with conn.cursor() as cur:
            cur.execute(sql(statement), params)
    else:
        conn.execute(statement, params)


def _create_schema() -> None:
    with connect() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(schema())
        else:
            conn.executescript(schema())
        conn.commit()


# ─── DynamoDB ─────────────────────────────────────────────────────
#
# One table, partitioned on a numeric id. DynamoDB has no auto-increment, so
# item 0 is a counter and every insert bumps it with an atomic ADD - which is a
# single write that cannot interleave, unlike read-then-write. Real records
# start at 1, and the counter is filtered out of every read.
#
# Listing is a Scan. At a hundred records that is one request and cheaper than
# maintaining an index; at a hundred thousand it would be the wrong answer, and
# a global secondary index partitioned on status is where that goes. The demo
# does not have that problem and pretending otherwise would cost money.

_COUNTER_ID = 0


def _table() -> Any:
    import boto3

    return boto3.resource("dynamodb").Table(dynamodb_table())


def _next_id(table: Any) -> int:
    result = table.update_item(
        Key={"id": _COUNTER_ID},
        UpdateExpression="ADD next_id :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(result["Attributes"]["next_id"])


def _item_to_row(item: dict) -> dict:
    """DynamoDB returns numbers as Decimal; the templates and JSON want an int."""
    row = {"id": int(item["id"]), "status": item.get("status", "new")}
    for field in FIELDS:
        row[field] = item.get(field, "")
    return row


# ─── What the application calls ───────────────────────────────────

def init(attempts: int = 10, delay: float = 1.0) -> None:
    """Prepare storage at startup.

    For SQL that means creating the table. For DynamoDB it means checking the
    one Terraform created is really there - the application does not create its
    own infrastructure, and a Lambda that could would need permission to, which
    is permission it should not have.

    The retry is for the case nothing can gate: a hosted database restarting
    during a redeploy, or a connection refused for the second it takes to fail
    over. Only connection failures are retried; a broken statement or wrong
    credentials fail immediately, because retrying those just delays the error.
    """
    if is_dynamodb():
        _table().load()
        return

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


def count_leads() -> int:
    if is_dynamodb():
        return len(list_leads(limit=None))
    with connect() as conn:
        return int(_one(conn, "SELECT COUNT(*) FROM leads"))


def insert_lead(lead: dict) -> int:
    """Store one classified lead and return the id it was given."""
    if is_dynamodb():
        table = _table()
        new_id = _next_id(table)
        item = {"id": new_id, "status": "new"}
        item.update({field: lead[field] for field in FIELDS})
        table.put_item(Item=item)
        return new_id

    columns = ", ".join(FIELDS)
    placeholders = ", ".join("?" for _ in FIELDS)
    values = tuple(lead[field] for field in FIELDS)
    statement = f"INSERT INTO leads ({columns}) VALUES ({placeholders})"

    with connect() as conn:
        if is_postgres():
            with conn.cursor() as cur:
                cur.execute(sql(statement) + " RETURNING id", values)
                new_id = int(cur.fetchone()["id"])
        else:
            new_id = int(conn.execute(statement, values).lastrowid)
        conn.commit()
    return new_id


def list_leads(limit: int | None = 100) -> list[dict]:
    """Newest first, which is the order the dashboard renders."""
    if is_dynamodb():
        table = _table()
        items, kwargs = [], {}
        while True:
            page = table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

        rows = [_item_to_row(i) for i in items if int(i["id"]) != _COUNTER_ID]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return rows[:limit] if limit else rows

    statement = "SELECT * FROM leads ORDER BY id DESC"
    if limit:
        statement += f" LIMIT {int(limit)}"
    with connect() as conn:
        return _rows(conn, statement)


def set_status(lead_id: int, status: str) -> None:
    if is_dynamodb():
        # "status" is reserved in DynamoDB expressions, hence the alias.
        _table().update_item(
            Key={"id": int(lead_id)},
            UpdateExpression="SET #s = :v",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":v": status},
        )
        return
    with connect() as conn:
        _run(conn, "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        conn.commit()


def delete_lead(lead_id: int) -> None:
    if is_dynamodb():
        _table().delete_item(Key={"id": int(lead_id)})
        return
    with connect() as conn:
        _run(conn, "DELETE FROM leads WHERE id = ?", (lead_id,))
        conn.commit()

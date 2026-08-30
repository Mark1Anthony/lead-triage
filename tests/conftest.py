"""Shared fixtures.

Tests run against SQLite by default. Set TEST_DATABASE_URL to a Postgres
instance and the same suite runs against Postgres instead - that is what CI
does, so both backends are covered by one set of tests.
"""

import os

import pytest
from fastapi.testclient import TestClient

import db
import security

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TRIAGE_TOKEN", TOKEN)
    # Force demo mode: no test may reach the OpenAI API.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    postgres_url = os.getenv("TEST_DATABASE_URL")
    if postgres_url:
        monkeypatch.setenv("DATABASE_URL", postgres_url)
        _drop_leads_table()
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(db, "SQLITE_PATH", tmp_path / "leads.db")

    import app as app_module

    security.reset_generated_token()
    app_module.demo_lead_limiter.reset()

    # The context manager form runs the lifespan handler, which creates the
    # schema and the seed rows.
    with TestClient(app_module.app) as test_client:
        yield test_client


def _drop_leads_table() -> None:
    """Each Postgres test starts from an empty database, like a fresh SQLite file."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS leads")
        conn.commit()


@pytest.fixture
def auth():
    return {"X-Api-Token": TOKEN}

"""Shared fixtures. Every test runs against a fresh temporary database."""

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TRIAGE_TOKEN", TOKEN)
    # Force demo mode: no test may reach the OpenAI API.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    import app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "leads.db")
    app_module.demo_lead_limiter.reset()

    # The context manager form runs the lifespan handler, which creates the
    # schema and the seed rows.
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def auth():
    return {"X-Api-Token": TOKEN}

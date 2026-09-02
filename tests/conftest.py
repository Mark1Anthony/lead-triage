"""Shared fixtures.

The same suite runs against all three backends. Which one is decided by the
environment, so CI can run it three times without a different test file each:

    (nothing set)          SQLite in a temporary directory
    TEST_DATABASE_URL      a real PostgreSQL
    TEST_DYNAMODB=1        DynamoDB, faked in-process by moto

The DynamoDB run needs no AWS account and touches no network. moto intercepts
botocore, so the code under test is the same code that runs in Lambda.
"""

import os

import pytest
from fastapi.testclient import TestClient

import db
import security

TOKEN = "test-token"
TEST_TABLE = "leads-test"


def _create_table(name: str) -> None:
    """The table Terraform creates, created here the same way.

    Keep this in step with terraform/dynamodb.tf - a mismatch would make the
    tests pass against a table the deployment does not have.
    """
    import boto3

    boto3.client("dynamodb").create_table(
        TableName=name,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAD_TRIAGE_TOKEN", TOKEN)
    # Force demo mode: no test may reach the OpenAI API.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    postgres_url = os.getenv("TEST_DATABASE_URL")
    use_dynamodb = os.getenv("TEST_DYNAMODB")
    mock = None

    if use_dynamodb:
        from moto import mock_aws

        # Credentials have to exist before botocore builds a client, and must
        # not be real ones - moto answers everything locally either way.
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
        monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DYNAMODB_TABLE", TEST_TABLE)

        mock = mock_aws()
        mock.start()
        _create_table(TEST_TABLE)
    else:
        monkeypatch.delenv("DYNAMODB_TABLE", raising=False)
        if postgres_url:
            monkeypatch.setenv("DATABASE_URL", postgres_url)
            _drop_leads_table()
        else:
            monkeypatch.delenv("DATABASE_URL", raising=False)
            monkeypatch.setattr(db, "SQLITE_PATH", tmp_path / "leads.db")

    import app as app_module

    security.reset_generated_token()
    app_module.demo_lead_limiter.reset()

    try:
        # The context manager form runs the lifespan handler, which prepares
        # storage and writes the seed rows.
        with TestClient(app_module.app) as test_client:
            yield test_client
    finally:
        if mock is not None:
            mock.stop()


def _drop_leads_table() -> None:
    """Each Postgres test starts from an empty database, like a fresh SQLite file."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS leads")
        conn.commit()


@pytest.fixture
def auth():
    return {"X-Api-Token": TOKEN}

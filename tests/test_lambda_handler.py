"""The Lambda entry point, invoked the way Lambda invokes it.

No AWS and no container: this builds an API Gateway HTTP API v2 payload, calls
the handler with it, and checks what comes back. It catches the failures that
otherwise only appear after a deployment - a handler that does not import, a
lifespan that never runs so the seed is missing, a response shape API Gateway
cannot use.
"""

import json

import pytest

pytest.importorskip("mangum")
pytest.importorskip("moto")

TABLE = "leads-lambda-test"


@pytest.fixture
def handler(monkeypatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("LEAD_TRIAGE_TOKEN", "test-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)

    mock = mock_aws()
    mock.start()
    import boto3

    boto3.client("dynamodb").create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )

    import lambda_handler

    yield lambda_handler.handler
    mock.stop()


def event(method: str = "GET", path: str = "/health", headers=None, body=None) -> dict:
    """An API Gateway HTTP API (payload format 2.0) request."""
    return {
        "version": "2.0",
        "routeKey": f"{method} {path}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.execute-api.eu-central-1.amazonaws.com",
                    **(headers or {})},
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
            },
            "stage": "$default",
        },
        "body": body,
        "isBase64Encoded": False,
    }


class TestTheHandlerAnswers:
    def test_health_reports_dynamodb(self, handler):
        response = handler(event(), None)
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["database"] == "dynamodb"

    def test_the_response_has_the_shape_api_gateway_needs(self, handler):
        response = handler(event(), None)
        assert isinstance(response["statusCode"], int)
        assert "headers" in response
        assert isinstance(response["body"], str)

    def test_the_dashboard_renders(self, handler):
        # Proves the templates were copied into the image path and that the
        # lifespan ran - without it the board would be empty.
        response = handler(event(path="/"), None)
        assert response["statusCode"] == 200
        assert "Nord Capital" in response["body"]


class TestAuthStillApplies:
    def test_writes_are_closed_without_a_token(self, handler):
        response = handler(event("DELETE", "/leads/1"), None)
        assert response["statusCode"] == 401

    def test_the_token_opens_them(self, handler):
        response = handler(
            event("GET", "/api/leads", headers={"x-api-token": "test-token"}), None
        )
        assert response["statusCode"] == 200
